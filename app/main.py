import os, json, traceback, html, threading
import re
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from apscheduler.schedulers.background import BackgroundScheduler

from . import db, twitch, analyze, score, render, upload, tg, facecam_detect

api = FastAPI(title="ClipFactory")
sched = BackgroundScheduler(timezone=os.environ.get("TZ", "Europe/Berlin"))
pending_edits = {}
_pending_edits_lock = threading.Lock()

FLAG = {"de": "🇩🇪", "en": "🇺🇸"}
CAM_SHIFT_STEP = 70
GAME_SHIFT_STEP = 90
LAYOUT_ZOOM_STEP = 1.08


def _profiles():
    return list(twitch.load_config()["profiles"].keys())


def _run_async(target, *args):
    t = threading.Thread(target=target, args=args, daemon=True)
    t.start()
    return t


def _set_pending_edit(chat_id, state):
    with _pending_edits_lock:
        pending_edits[str(chat_id)] = state


def _get_pending_edit(chat_id):
    with _pending_edits_lock:
        return pending_edits.get(str(chat_id))


def _clear_pending_edit(chat_id):
    with _pending_edits_lock:
        return pending_edits.pop(str(chat_id), None)


def _platforms(profile):
    key = f"PLATFORMS_{profile.upper()}"
    if key in os.environ:
        raw = os.environ.get(key, "")
    elif "PLATFORMS" in os.environ:
        raw = os.environ.get("PLATFORMS", "")
    else:
        raw = "youtube"
    vals = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if any(p in {"none", "off", "disabled", "review-only"} for p in vals):
        return []
    return vals


def _schedule_slots(raw, default):
    value = (raw or "").strip()
    if not value:
        value = default
    slots = [slot.strip() for slot in value.split(",") if slot.strip()]
    if not slots:
        return []
    if any(slot.lower() in {"none", "off", "disabled", "manual"} for slot in slots):
        return []
    parsed = []
    for slot in slots:
        hour_s, minute_s = slot.split(":", 1)
        parsed.append((int(hour_s), int(minute_s)))
    return parsed


# ---------------- Pipeline ----------------

def _candidate_target(profile):
    prof_cfg = twitch.load_config()["profiles"].get(profile, {})
    return int(prof_cfg.get("candidates_target", os.environ.get("CANDIDATES_PER_DAY", 25)))


def _select_candidates(profile, clips, allow_known=False):
    target = _candidate_target(profile)
    out = []
    for c in clips:
        if not allow_known and db.known(c["id"]):
            continue
        if c["views"] < 3:
            continue
        if not (float(os.environ.get("MIN_CLIP_SECONDS", 8)) <= c["duration"]
                <= float(os.environ.get("MAX_CLIP_SECONDS", 75))):
            continue
        if c.get("alter_h", 999) < 2:
            c["frisch"] = True
        c["profile"] = profile
        out.append(c)
        if len(out) >= target:
            break
    return out


def _analyze_and_send(profile, clips, n_bc, missing_logins=None, chat_id=None, allow_known=False):
    missing_logins = missing_logins or []
    if missing_logins:
        tg.send("⚠️ Diese Logins existieren nicht auf Twitch: "
                f"<code>{html.escape(', '.join(missing_logins))}</code>", chat_id=chat_id)

    cands = _select_candidates(profile, clips, allow_known=allow_known)
    for c in cands:
        db.insert_candidate(c)

    sent = 0
    for c in cands:
        try:
            path = analyze.download(c)
            text, words, lang = analyze.transcribe(path)
            analyze.save_words(c["id"], words)
            db.update(c["id"], transcript=text, language=lang, status="analyzed")
            j = score.score_clip(
                {**c, "twitch_title": c["title"]},
                score.words_to_prompt_text(words), out_lang=profile)
            db.update(c["id"], score=j["score"], category=j["category"], hook=j["hook"],
                      title=j["title"], description=j["description"],
                      start_s=j["start_s"], end_s=j["end_s"],
                      loopable=1 if j.get("loopable") else 0,
                      status="pending_review")
            tg.send_candidate({**c, **j, "flag": FLAG.get(profile, "")}, chat_id=chat_id)
            sent += 1
        except Exception as e:
            db.update(c["id"], status="failed", error=str(e)[:500])
    tg.send(f"✅ {FLAG.get(profile,'')} Fertig: {n_bc} Streams gescannt, "
            f"{len(cands)} Kandidaten analysiert, {sent} zur Freigabe geschickt.",
            chat_id=chat_id)


def collect_and_score(profile, chat_id=None):
    """Discovery -> Transkription -> KI-Scoring -> Telegram-Karten (pro Profil)."""
    tg.send(f"🌅 {FLAG.get(profile,'')} Discovery laeuft: suche die heissesten "
            f"{profile.upper()}-Streams und ranke Clips per Heat-Score ...",
            chat_id=chat_id)
    try:
        clips, n_bc, missing_logins = twitch.discover_candidates(profile)
    except Exception as e:
        tg.send(f"⚠️ Discovery-Fehler ({profile}): <code>{html.escape(str(e)[:800])}</code>",
                chat_id=chat_id)
        return
    _analyze_and_send(profile, clips, n_bc, missing_logins=missing_logins, chat_id=chat_id)


def _clip_out_path(cid):
    return f"/data/out/{cid}.mp4"


def _invalidate_render(cid):
    out = _clip_out_path(cid)
    try:
        if os.path.exists(out):
            os.remove(out)
    except OSError:
        traceback.print_exc()


def _clear_clip_facecam(cid):
    db.update(cid, cam_x=None, cam_y=None, cam_w=None, cam_h=None, cam_mode=None)


def _set_clip_facecam(cid, coords):
    db.update(
        cid,
        cam_x=int(coords["x"]),
        cam_y=int(coords["y"]),
        cam_w=int(coords["w"]),
        cam_h=int(coords["h"]),
        cam_mode=None,
    )


def _set_clip_center(cid):
    db.update(cid, cam_x=None, cam_y=None, cam_w=None, cam_h=None, cam_mode="center")


def _clip_facecam_choice(clip):
    if (clip.get("cam_mode") or "").lower() == "center":
        return "center"
    vals = [clip.get(k) for k in ("cam_x", "cam_y", "cam_w", "cam_h")]
    if any(v is None for v in vals):
        return None
    try:
        box = {k: int(clip[k]) for k in ("cam_x", "cam_y", "cam_w", "cam_h")}
    except (TypeError, ValueError, KeyError):
        return None
    if box["cam_w"] <= 0 or box["cam_h"] <= 0:
        return None
    return {
        "x": box["cam_x"],
        "y": box["cam_y"],
        "w": box["cam_w"],
        "h": box["cam_h"],
    }


def _clip_needs_cam_input(clip):
    return _clip_facecam_choice(clip) is None


def _cam_prompt_text():
    return "Kreise die Facecam ein und schick das Bild zurück – oder tippe /center für Mittig-Crop ohne Split."


def _clip_layout(clip):
    layout = dict(render.DEFAULT_LAYOUT)
    raw = clip.get("layout_json")
    if not raw:
        return layout
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return layout
    if not isinstance(data, dict):
        return layout
    for key in layout:
        if key in data:
            layout[key] = data[key]
    return layout


def _save_clip_layout(cid, layout):
    db.update(cid, layout_json=json.dumps(layout, ensure_ascii=False, separators=(",", ":")))


def _layout_menu_text(clip):
    layout = _clip_layout(clip)
    return (
        f"🎛️ <b>Layout fuer {html.escape(clip['streamer'])}</b>\n"
        f"Cam: Zoom <b>{float(layout['cam_zoom']):.2f}x</b> | X <code>{int(layout['cam_shift_x']):+}</code> | "
        f"Y <code>{int(layout['cam_shift_y']):+}</code>\n"
        f"Bild: Zoom <b>{float(layout['game_zoom']):.2f}x</b> | X <code>{int(layout['game_shift_x']):+}</code> | "
        f"Y <code>{int(layout['game_shift_y']):+}</code>\n"
        "Tippe mehrfach fuer Feintuning. Jede Aenderung rendert sofort eine neue Vorschau."
    )


def _layout_buttons(cid):
    return [
        [
            {"text": "🧑 Cam +", "callback_data": f"layci:{cid}"},
            {"text": "🧑 Cam -", "callback_data": f"layco:{cid}"},
        ],
        [
            {"text": "🧑 ←", "callback_data": f"laycl:{cid}"},
            {"text": "🧑 →", "callback_data": f"laycr:{cid}"},
            {"text": "🧑 ↑", "callback_data": f"laycu:{cid}"},
            {"text": "🧑 ↓", "callback_data": f"laycd:{cid}"},
        ],
        [
            {"text": "🎮 Bild +", "callback_data": f"laygi:{cid}"},
            {"text": "🎮 Bild -", "callback_data": f"laygo:{cid}"},
        ],
        [
            {"text": "🎮 ←", "callback_data": f"laygl:{cid}"},
            {"text": "🎮 →", "callback_data": f"laygr:{cid}"},
            {"text": "🎮 ↑", "callback_data": f"laygu:{cid}"},
            {"text": "🎮 ↓", "callback_data": f"laygd:{cid}"},
        ],
        [
            {"text": "🔄 Reset Layout", "callback_data": f"layrs:{cid}"},
        ],
    ]


def _age_label(hours):
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        return "vor ?"
    if hours < 1:
        mins = max(1, int(round(hours * 60)))
        return f"vor {mins} Min"
    if abs(hours - round(hours)) < 0.05:
        return f"vor {int(round(hours))}h"
    return f"vor {round(hours, 1)}h"


def _preview_caption(clip):
    title = clip.get("title") or clip.get("twitch_title") or "(ohne Titel)"
    markers = []
    if clip.get("frisch"):
        markers.append("🆕 FRISCH")
    markers.append(f"{clip.get('velocity', '?')} Views/h")
    markers.append(_age_label(clip.get("alter_h")))
    markers.append(f"Heat {round(float(clip.get('heat_score', 0)), 1)}")
    return (
        f"🎬 <b>{html.escape(clip['streamer'])}</b>\n"
        f"Titel: {html.escape(title[:160])}\n"
        f"KI-Score: <b>{clip.get('score', '?')}</b>\n"
        f"Hook: <i>{html.escape(clip.get('hook') or '-')}</i>\n"
        f"{' | '.join(markers)}"
    )


def _render_output(clip):
    cid = clip["id"]
    words = analyze.load_words(cid)
    ass = f"/data/tmp/{cid}.ass"
    render.build_ass(words, clip["start_s"] or 0, clip["end_s"] or clip["duration"], ass)
    facecam = _resolve_facecam(clip)
    return render.render(clip, facecam, ass)


def _request_cam_input(cid, chat_id=None):
    clip = db.get(cid)
    if not clip:
        tg.send(f"⚠️ Clip <code>{html.escape(cid)}</code> nicht gefunden.", chat_id=chat_id)
        return None
    try:
        frame_path = facecam_detect.extract_midframe(cid)
        _set_pending_edit(chat_id, {"clip_id": cid, "waiting_for": "camcircle"})
        tg.send_photo(frame_path, _cam_prompt_text(), chat_id=chat_id)
        return frame_path
    except Exception as e:
        tg.send(
            f"⚠️ Frame konnte nicht erzeugt werden: <code>{html.escape(str(e)[:400])}</code>",
            chat_id=chat_id,
        )
        return None


def render_preview(clip_id, chat_id=None):
    clip = db.get(clip_id)
    if not clip:
        tg.send(f"⚠️ Clip <code>{html.escape(clip_id)}</code> nicht gefunden.", chat_id=chat_id)
        return
    cid = clip["id"]
    if _clip_needs_cam_input(clip):
        db.update(cid, status="approved")
        return _request_cam_input(cid, chat_id)
    try:
        db.update(cid, status="rendering")
        _invalidate_render(cid)
        out = _render_output(clip)
        db.update(cid, status="preview_ready")
        preview = db.get(cid) or {**clip, "status": "preview_ready"}
        facecam_choice = _clip_facecam_choice(preview)
        buttons = [
            [
                {"text": "🚀 Hochladen", "callback_data": f"upload:{cid}"},
                {"text": "✂️ Länge", "callback_data": f"editlen:{cid}"},
            ],
        ]
        control_row = []
        if facecam_choice and facecam_choice != "center":
            control_row.append({"text": "🎛️ Layout", "callback_data": f"laymenu:{cid}"})
        control_row.append({"text": "📷 Cam neu", "callback_data": f"editcam:{cid}"})
        control_row.append({"text": "✂️ Kein Split", "callback_data": f"camoff:{cid}"})
        buttons.append(control_row)
        buttons.append([
                {"text": "❌ Verwerfen", "callback_data": f"reject:{cid}"},
            ],
        )
        if not tg.send_video(cid, _preview_caption(preview), buttons, chat_id=chat_id):
            raise RuntimeError(f"Telegram-Vorschau fuer {cid} konnte nicht gesendet werden.")
        return out
    except Exception as e:
        db.update(cid, status="pending_review", error=str(e)[:500])
        tg.send(
            f"❌ Vorschau-Fehler bei {cid}:\n<code>{html.escape(str(e)[:800])}</code>",
            chat_id=chat_id,
        )
        traceback.print_exc()
        return None


def process_and_upload(profile, clip_id=None, chat_id=None):
    platforms = _platforms(profile)
    if not platforms:
        if chat_id is not None:
            tg.send(f"ℹ️ Fuer {profile.upper()} sind keine Upload-Plattformen aktiviert.", chat_id=chat_id)
        return
    clip = db.get(clip_id) if clip_id else db.next_preview_ready(profile)
    if clip and clip["profile"] != profile:
        clip = None
    if not clip:
        if chat_id is not None:
            tg.send(f"ℹ️ Kein passender Clip fuer {profile.upper()} bereit.", chat_id=chat_id)
        return
    cid = clip["id"]
    if clip.get("status") != "preview_ready":
        if chat_id is not None:
            tg.send(
                f"ℹ️ Clip <code>{html.escape(cid)}</code> ist noch nicht upload-bereit "
                f"(<code>{html.escape(clip.get('status') or '?')}</code>).",
                chat_id=chat_id,
            )
        return
    try:
        out = _clip_out_path(cid)
        if not os.path.exists(out):
            db.update(cid, status="rendering")
            out = _render_output(clip)
            db.update(cid, status="preview_ready")

        results = []
        if "youtube" in platforms:
            vid = upload.youtube(out, clip["title"], clip["description"], profile)
            db.update(cid, yt_id=vid)
            results.append(f"YouTube ✅ https://youtube.com/shorts/{vid}")
        if "instagram" in platforms:
            ig = upload.instagram(cid, f"{clip['title']}\n\n{clip['description']}", profile)
            db.update(cid, ig_id=ig)
            results.append("Instagram ✅")
        if "tiktok" in platforms:
            tt = upload.tiktok(out, clip["title"], profile)
            db.update(cid, tt_id=tt)
            results.append("TikTok ✅ (Entwurf in der App bestaetigen)")

        db.update(cid, status="uploaded")
        tg.send(f"🚀 {FLAG.get(profile,'')} <b>Hochgeladen:</b> " + clip["title"]
                + "\n" + "\n".join(results), chat_id=chat_id)
    except Exception as e:
        db.update(cid, status="failed", error=str(e)[:500])
        tg.send(f"❌ {FLAG.get(profile,'')} Upload-Fehler bei {cid}:\n<code>{html.escape(str(e)[:800])}</code>",
                chat_id=chat_id)
        traceback.print_exc()


# ---------------- Telegram ----------------

def _approve(cid, chat_id=None):
    clip = db.get(cid)
    if not clip:
        tg.send(f"⚠️ Clip <code>{html.escape(cid)}</code> nicht gefunden.", chat_id=chat_id)
        return
    db.update(cid, status="approved")
    _invalidate_render(cid)
    clip = db.get(cid) or clip
    if _clip_needs_cam_input(clip):
        _clear_clip_facecam(cid)
        _request_cam_input(cid, chat_id)
        return
    tg.send("🎬 Rendere Vorschau…", chat_id=chat_id)
    threading.Thread(target=render_preview, args=(cid, chat_id), daemon=True).start()

def _reject(cid):
    db.update(cid, status="rejected")

def _stats():
    return json.dumps(db.stats(), ensure_ascii=False)


def _status_text():
    stats = db.stats()
    if not stats:
        return "📊 Noch keine Clips in der Pipeline."
    lines = ["📊 <b>Pipeline-Status</b>"]
    for status, count in sorted(stats.items()):
        lines.append(f"{html.escape(status)}: <b>{count}</b>")
    return "\n".join(lines)


def _clip_label(clip):
    title = clip.get("title") or clip.get("twitch_title") or "(ohne Titel)"
    return f"<b>{html.escape(clip['streamer'])}</b> | {html.escape(title[:90])} | <code>{html.escape(clip['status'])}</code>"


def _help_text():
    return "\n".join([
        "🧭 <b>Befehle</b>",
        "/clips - DE-Discovery sofort starten",
        "/clips en - EN-Discovery sofort starten",
        "/sammeln - Alias fuer /clips",
        "/clip STREAMERNAME - beste Clips nur fuer diesen Streamer holen",
        "/status - aktuelle Pipeline-Statistik",
        "/pending - Clips mit wartenden Schritten anzeigen",
        "/warteschlange - fertige preview_ready-Clips mit Upload-Button",
        "/center - waehrend der Cam-Abfrage: Mittig-Crop ohne Split",
        "/id - eigene Chat-ID anzeigen",
    ])


def _pending(chat_id):
    clips = db.by_statuses(["new", "analyzed", "pending_review", "approved", "rendering", "preview_ready"], limit=20)
    if not clips:
        tg.send("ℹ️ Aktuell wartet kein Clip auf Vorschau, Bearbeitung oder Upload.", chat_id=chat_id)
        return
    lines = ["⏳ <b>Pending</b>"]
    for clip in clips:
        lines.append(_clip_label(clip))
    tg.send("\n".join(lines), chat_id=chat_id)


def _queue(chat_id):
    clips = db.by_status("preview_ready", limit=20)
    if not clips:
        tg.send("ℹ️ Keine Clips im Status <code>preview_ready</code>.", chat_id=chat_id)
        return
    for clip in clips:
        title = clip.get("title") or clip.get("twitch_title") or "(ohne Titel)"
        tg.send(
            f"🗂️ <b>{html.escape(clip['streamer'])}</b>\n{html.escape(title[:120])}\n"
            f"Status: <code>{html.escape(clip['status'])}</code>",
            buttons=[[{"text": "🚀 Hochladen", "callback_data": f"upload:{clip['id']}"}]],
            chat_id=chat_id,
        )


def _collect_command(profile, chat_id):
    profile = (profile or "de").lower()
    if profile not in _profiles():
        tg.send(f"⚠️ Unbekanntes Profil <code>{html.escape(profile)}</code>. Verfuegbar: {', '.join(_profiles())}",
                chat_id=chat_id)
        return
    tg.send(f"🔍 Sammle Clips fuer <b>{profile.upper()}</b>…", chat_id=chat_id)
    _run_async(collect_and_score, profile, chat_id)


def _clip_menu(chat_id):
    streamers = twitch.load_config().get("profiles", {}).get("de", {}).get("streamers", [])
    if not streamers:
        tg.send("ℹ️ Im DE-Profil ist keine feste Streamer-Liste hinterlegt.", chat_id=chat_id)
        return
    rows = []
    for i in range(0, len(streamers), 2):
        row = []
        for login in streamers[i:i + 2]:
            row.append({"text": login, "callback_data": f"clipstreamer:{login}"})
        rows.append(row)
    tg.send("Von wem sollen die aktuellsten Clips geholt werden?", buttons=rows, chat_id=chat_id)


def _start_single_clip(login, chat_id):
    login = (login or "").strip().lower()
    if not login:
        tg.send("Nutzung: <code>/clip STREAMERNAME</code>", chat_id=chat_id)
        return
    tg.send(f"🔍 Hole Clips von <b>{html.escape(login)}</b>…", chat_id=chat_id)
    _run_async(_single_clip_worker, login, chat_id)


def _single_clip(login, chat_id):
    _start_single_clip(login, chat_id)


def _single_clip_worker(login, chat_id):
    try:
        valid = twitch.validate_logins([login])
    except Exception as e:
        tg.send(f"⚠️ Twitch-Pruefung fehlgeschlagen: <code>{html.escape(str(e)[:500])}</code>", chat_id=chat_id)
        return
    if valid["fehlend"]:
        tg.send(f"⚠️ Den Twitch-Login <code>{html.escape(login)}</code> gibt es nicht.", chat_id=chat_id)
        return

    try:
        bcs = twitch.user_ids(valid["vorhanden"])
        if not bcs:
            tg.send(f"⚠️ Konnte <code>{html.escape(login)}</code> nicht aufloesen.", chat_id=chat_id)
            return
        profile = "de"
        clips_per_streamer = int(twitch.load_config()["profiles"][profile].get("clips_per_streamer", 10))
        clips = twitch.top_clips_for(bcs[0], first=clips_per_streamer)
    except Exception as e:
        tg.send(f"⚠️ Konnte Clips fuer <code>{html.escape(login)}</code> nicht laden: "
                f"<code>{html.escape(str(e)[:500])}</code>", chat_id=chat_id)
        return

    if not clips:
        tg.send(f"ℹ️ Fuer <b>{html.escape(login)}</b> gibt es aktuell keine passenden Clips der letzten 24h.",
                chat_id=chat_id)
        return
    clips.sort(key=lambda c: (-c.get("heat_score", 0), -c.get("velocity", 0), -c.get("views", 0)))
    _analyze_and_send("de", clips, 1, chat_id=chat_id, allow_known=True)


def _upload_now(cid, chat_id):
    clip = db.get(cid)
    if not clip:
        tg.send(f"⚠️ Clip <code>{html.escape(cid)}</code> nicht gefunden.", chat_id=chat_id)
        return
    if clip.get("status") != "preview_ready":
        tg.send(
            f"ℹ️ Clip <code>{html.escape(cid)}</code> ist noch nicht im Status "
            f"<code>preview_ready</code>.",
            chat_id=chat_id,
        )
        return
    tg.send(f"🚀 Lade <b>{html.escape(clip['streamer'])}</b> jetzt hoch…", chat_id=chat_id)
    _run_async(process_and_upload, clip["profile"], cid, chat_id)


def _help(chat_id):
    tg.send(_help_text(), chat_id=chat_id)


def _status(chat_id):
    tg.send(_status_text(), chat_id=chat_id)


def _id(chat_id):
    tg.send(f"Deine Chat-ID: <code>{chat_id}</code>", chat_id=chat_id)


def _text_reply(txt, chat_id):
    state = _get_pending_edit(chat_id)
    if state and state.get("waiting_for") == "camcircle":
        tg.send("ℹ️ Bitte schick das markierte Foto zurueck oder tippe <code>/center</code> fuer Mittig-Crop.",
                chat_id=chat_id)
        return
    if state and state.get("waiting_for") == "laenge":
        clip = db.get(state["clip_id"])
        if not clip:
            _clear_pending_edit(chat_id)
            tg.send("⚠️ Der Clip wurde nicht gefunden. Bitte starte die Bearbeitung nochmal.", chat_id=chat_id)
            return
        parsed, err = _parse_length_edit(txt, clip)
        if err:
            tg.send(err, chat_id=chat_id)
            return
        db.update(clip["id"], start_s=parsed["start_s"], end_s=parsed["end_s"])
        _invalidate_render(clip["id"])
        _clear_pending_edit(chat_id)
        tg.send("✂️ Rendere neuen Ausschnitt…", chat_id=chat_id)
        _run_async(render_preview, clip["id"], chat_id)
        return
    tg.send("ℹ️ Freitext ist aktuell keiner aktiven Bearbeitung zugeordnet. Nutze /hilfe fuer Befehle.",
            chat_id=chat_id)


def _edit_length(cid, chat_id):
    clip = db.get(cid)
    if not clip:
        tg.send(f"⚠️ Clip <code>{html.escape(cid)}</code> nicht gefunden.", chat_id=chat_id)
        return
    start_s = float(clip.get("start_s") or 0)
    end_s = float(clip.get("end_s") or clip.get("duration") or 0)
    duration = float(clip.get("duration") or 0)
    tg.send(
        f"✂️ Aktuell: {start_s:g}s–{end_s:g}s (Clip: {duration:g}s).\n"
        "Neuer Bereich: start-ende (z.B. 5-30).\n"
        "Oder Feinjustierung: +2/-2 verschiebt den Start, e+3/e-3 das Ende.",
        chat_id=chat_id,
    )
    _set_pending_edit(chat_id, {"clip_id": cid, "waiting_for": "laenge"})


def _parse_length_edit(txt, clip):
    text = (txt or "").strip().lower()
    start_s = float(clip.get("start_s") or 0)
    end_s = float(clip.get("end_s") or clip.get("duration") or 0)
    duration = float(clip.get("duration") or 0)

    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*-\s*([0-9]+(?:\.[0-9]+)?)", text)
    if m:
        new_start = float(m.group(1))
        new_end = float(m.group(2))
    elif re.fullmatch(r"[+-][0-9]+(?:\.[0-9]+)?", text):
        new_start = start_s + float(text)
        new_end = end_s
    else:
        m = re.fullmatch(r"e([+-][0-9]+(?:\.[0-9]+)?)", text)
        if m:
            new_start = start_s
            new_end = end_s + float(m.group(1))
        else:
            return None, (
                "⚠️ Format nicht erkannt. Nutze <code>5-30</code>, <code>+2</code>, "
                "<code>-2</code>, <code>e+3</code> oder <code>e-3</code>."
            )

    if new_start < 0:
        return None, "⚠️ Der Start darf nicht kleiner als 0 Sekunden sein."
    if new_end > duration:
        return None, f"⚠️ Das Ende darf nicht nach {duration:g}s liegen."
    if new_end - new_start < 5:
        return None, "⚠️ Der Ausschnitt muss mindestens 5 Sekunden lang sein."
    if new_end <= new_start:
        return None, "⚠️ Das Ende muss nach dem Start liegen."
    return {"start_s": round(new_start, 2), "end_s": round(new_end, 2)}, None


def _resolve_facecam(clip):
    return _clip_facecam_choice(clip)


def _cam_adjust(cid, chat_id):
    clip = db.get(cid)
    if not clip:
        tg.send(f"⚠️ Clip <code>{html.escape(cid)}</code> nicht gefunden.", chat_id=chat_id)
        return
    if clip.get("status") == "preview_ready":
        db.update(cid, status="approved")
    _clear_clip_facecam(cid)
    _invalidate_render(cid)
    _request_cam_input(cid, chat_id)


def _layout_menu(cid, chat_id):
    clip = db.get(cid)
    if not clip:
        tg.send(f"⚠️ Clip <code>{html.escape(cid)}</code> nicht gefunden.", chat_id=chat_id)
        return
    facecam = _clip_facecam_choice(clip)
    if facecam == "center":
        tg.send(
            "ℹ️ Dieser Clip ist aktuell auf Mittig-Crop gestellt. Tippe zuerst auf "
            "<b>📷 Cam neu</b>, wenn du den Split und die Layout-Regler nutzen willst.",
            chat_id=chat_id,
        )
        return
    if not facecam:
        tg.send("ℹ️ Fuer diesen Clip muss zuerst die Facecam markiert werden.", chat_id=chat_id)
        return
    tg.send(_layout_menu_text(clip), buttons=_layout_buttons(cid), chat_id=chat_id)


def _layout_adjust(action, cid, chat_id):
    clip = db.get(cid)
    if not clip:
        tg.send(f"⚠️ Clip <code>{html.escape(cid)}</code> nicht gefunden.", chat_id=chat_id)
        return
    facecam = _clip_facecam_choice(clip)
    if facecam == "center":
        tg.send("ℹ️ Layout-Regler sind bei Mittig-Crop deaktiviert.", chat_id=chat_id)
        return
    if not facecam:
        tg.send("ℹ️ Bitte zuerst die Facecam markieren, dann kann ich das Layout anpassen.",
                chat_id=chat_id)
        return

    layout = _clip_layout(clip)

    if action == "layci":
        layout["cam_zoom"] = min(2.5, round(float(layout["cam_zoom"]) * LAYOUT_ZOOM_STEP, 3))
    elif action == "layco":
        layout["cam_zoom"] = max(0.8, round(float(layout["cam_zoom"]) / LAYOUT_ZOOM_STEP, 3))
    elif action == "laycl":
        layout["cam_shift_x"] = int(layout["cam_shift_x"]) - CAM_SHIFT_STEP
    elif action == "laycr":
        layout["cam_shift_x"] = int(layout["cam_shift_x"]) + CAM_SHIFT_STEP
    elif action == "laycu":
        layout["cam_shift_y"] = int(layout["cam_shift_y"]) - CAM_SHIFT_STEP
    elif action == "laycd":
        layout["cam_shift_y"] = int(layout["cam_shift_y"]) + CAM_SHIFT_STEP
    elif action == "laygi":
        layout["game_zoom"] = min(2.5, round(float(layout["game_zoom"]) * LAYOUT_ZOOM_STEP, 3))
    elif action == "laygo":
        layout["game_zoom"] = max(1.0, round(float(layout["game_zoom"]) / LAYOUT_ZOOM_STEP, 3))
    elif action == "laygl":
        layout["game_shift_x"] = int(layout["game_shift_x"]) - GAME_SHIFT_STEP
    elif action == "laygr":
        layout["game_shift_x"] = int(layout["game_shift_x"]) + GAME_SHIFT_STEP
    elif action == "laygu":
        layout["game_shift_y"] = int(layout["game_shift_y"]) - GAME_SHIFT_STEP
    elif action == "laygd":
        layout["game_shift_y"] = int(layout["game_shift_y"]) + GAME_SHIFT_STEP
    elif action == "layrs":
        layout = dict(render.DEFAULT_LAYOUT)
    else:
        tg.send("⚠️ Unbekannte Layout-Aktion.", chat_id=chat_id)
        return

    _save_clip_layout(cid, layout)
    db.update(cid, status="approved")
    _invalidate_render(cid)
    tg.send("🎛️ Layout angepasst, rendere neue Vorschau…", chat_id=chat_id)
    _run_async(render_preview, cid, chat_id)


def _legacy_cam_command(chat_id):
    tg.send(
        "ℹ️ Facecam wird jetzt pro Clip gesetzt. Bitte gib den Clip frei und kreise dann "
        "die Facecam im Standbild ein oder tippe <code>/center</code> fuer Mittig-Crop.",
        chat_id=chat_id,
    )


def _center(chat_id):
    state = _get_pending_edit(chat_id)
    if not state or state.get("waiting_for") != "camcircle":
        tg.send("ℹ️ <code>/center</code> funktioniert nur waehrend der Cam-Abfrage.", chat_id=chat_id)
        return
    cid = state["clip_id"]
    clip = db.get(cid)
    _clear_pending_edit(chat_id)
    if not clip:
        tg.send(f"⚠️ Clip <code>{html.escape(cid)}</code> nicht gefunden.", chat_id=chat_id)
        return
    _set_clip_center(cid)
    _invalidate_render(cid)
    tg.send(f"✅ <b>{html.escape(clip['streamer'])}</b> nutzt fuer diesen Clip Mittig-Crop.", chat_id=chat_id)
    clip = db.get(cid) or clip
    if clip.get("status") == "pending_review":
        tg.send("Tippe jetzt auf <b>Freigeben</b>, dann rendere ich die Vorschau.", chat_id=chat_id)
        return
    db.update(cid, status="approved")
    tg.send("🎬 Rendere Vorschau…", chat_id=chat_id)
    _run_async(render_preview, cid, chat_id)


def _center_clip(cid, chat_id):
    clip = db.get(cid)
    if not clip:
        tg.send(f"⚠️ Clip <code>{html.escape(cid)}</code> nicht gefunden.", chat_id=chat_id)
        return
    _set_clip_center(cid)
    db.update(cid, status="approved")
    _invalidate_render(cid)
    tg.send(f"✂️ <b>{html.escape(clip['streamer'])}</b> nutzt fuer diesen Clip Mittig-Crop.", chat_id=chat_id)
    tg.send("🎬 Rendere Vorschau neu…", chat_id=chat_id)
    _run_async(render_preview, cid, chat_id)


def _photo_reply(msg, chat_id):
    state = _get_pending_edit(chat_id)
    if not state or state.get("waiting_for") != "camcircle":
        return
    photos = msg.get("photo") or []
    if not photos:
        tg.send("⚠️ Kein Foto erkannt. Bitte schick das markierte Bild nochmal.", chat_id=chat_id)
        return
    best = max(photos, key=lambda p: (p.get("width", 0) * p.get("height", 0), p.get("file_size", 0)))
    tmp_path = f"/data/tmp/{state['clip_id']}.camcircle.jpg"
    downloaded = tg.download_telegram_photo(best["file_id"], tmp_path)
    if not downloaded:
        tg.send("⚠️ Konnte dein Foto nicht herunterladen, bitte nochmal senden.", chat_id=chat_id)
        return
    if not os.path.exists(downloaded) or os.path.getsize(downloaded) <= 0:
        tg.send("⚠️ Bild noch nicht vollständig empfangen, bitte nochmal senden.", chat_id=chat_id)
        return
    try:
        coords = facecam_detect.detect_marked_box(downloaded)
    except Exception as e:
        tg.send(
            f"⚠️ Konnte die Markierung nicht auswerten: <code>{html.escape(str(e)[:3000])}</code>",
            chat_id=chat_id,
        )
        return
    if not coords:
        tg.send("⚠️ Konnte die Markierung nicht erkennen, bitte nochmal deutlicher einkreisen.",
                chat_id=chat_id)
        return
    cid = state["clip_id"]
    clip = db.get(cid)
    if not clip:
        _clear_pending_edit(chat_id)
        tg.send(f"⚠️ Clip <code>{html.escape(cid)}</code> nicht gefunden.", chat_id=chat_id)
        return
    _set_clip_facecam(cid, coords)
    _invalidate_render(cid)
    _clear_pending_edit(chat_id)
    tg.send(
        f"✅ Cam fuer <b>{html.escape(clip['streamer'])}</b> gespeichert: "
        f"<code>{coords['x']} {coords['y']} {coords['w']} {coords['h']}</code>",
        chat_id=chat_id,
    )
    clip = db.get(cid) or clip
    if clip.get("status") == "pending_review":
        tg.send("Tippe jetzt auf <b>Freigeben</b>, dann rendere ich die Vorschau.", chat_id=chat_id)
        return
    db.update(cid, status="approved")
    tg.send("🎬 Rendere Vorschau neu…", chat_id=chat_id)
    _run_async(render_preview, cid, chat_id)


# ---------------- HTTP ----------------

@api.get("/health")
def health():
    return {"ok": True, "profiles": _profiles(), "stats": db.stats()}

@api.get("/media/{name}")
def media(name: str):
    p = f"/data/out/{name}"
    if not os.path.realpath(p).startswith("/data/out/") or not os.path.exists(p):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, media_type="video/mp4")

@api.get("/frame/{clip_id}")
def frame(clip_id: str):
    src = f"/data/raw/{clip_id}.mp4"
    if not os.path.exists(src):
        return JSONResponse({"error": "Clip nicht (mehr) im raw-Ordner"}, status_code=404)
    dst = facecam_detect.extract_midframe(clip_id)
    return FileResponse(dst, media_type="image/jpeg")

@api.post("/run/collect/{profile}")
def run_collect(profile: str):
    if profile not in _profiles():
        return JSONResponse({"error": f"unbekanntes Profil, verfuegbar: {_profiles()}"}, status_code=400)
    sched.add_job(collect_and_score, args=[profile])
    return {"started": profile}

@api.post("/run/upload/{profile}")
def run_upload(profile: str):
    if profile not in _profiles():
        return JSONResponse({"error": f"unbekanntes Profil, verfuegbar: {_profiles()}"}, status_code=400)
    if not _platforms(profile):
        return {"skipped": profile, "reason": "keine Upload-Plattform aktiviert"}
    sched.add_job(process_and_upload, args=[profile])
    return {"started": profile}


# ---------------- Start ----------------

@api.on_event("startup")
def startup():
    for path in ("/data/raw", "/data/out", "/data/tmp"):
        os.makedirs(path, exist_ok=True)
    db.init()
    for prof in _profiles():
        ct = (os.environ.get(f"COLLECT_TIME_{prof.upper()}")
              or os.environ.get("COLLECT_TIME", "06:30"))
        h, m = ct.split(":")
        sched.add_job(collect_and_score, "cron", hour=int(h), minute=int(m), args=[prof])
        if _platforms(prof):
            ut = (os.environ.get(f"UPLOAD_TIMES_{prof.upper()}")
                  or os.environ.get("UPLOAD_TIMES", "17:00"))
            for sh, sm in _schedule_slots(ut, "17:00"):
                sched.add_job(process_and_upload, "cron", hour=sh, minute=sm, args=[prof])
    sched.start()
    tg.start_polling({
        "on_approve": _approve,
        "on_reject": _reject,
        "get_stats": _stats,
        "on_status": _status,
        "on_id": _id,
        "on_collect": _collect_command,
        "on_pending": _pending,
        "on_queue": _queue,
        "on_help": _help,
        "on_single_clip": _single_clip,
        "on_clip_menu": _clip_menu,
        "on_clipstreamer": _start_single_clip,
        "on_center": _center,
        "on_cam_adjust": _cam_adjust,
        "on_layout_menu": _layout_menu,
        "on_layout_adjust": _layout_adjust,
        "on_legacy_cam_command": _legacy_cam_command,
        "on_center_clip": _center_clip,
        "on_upload_now": _upload_now,
        "on_edit_length": _edit_length,
        "on_photo": _photo_reply,
        "on_text": _text_reply,
    })
    tg.send("🟢 ClipFactory laeuft (Profile: " + ", ".join(_profiles()) + ").\n"
            "Befehle: /clips /clip /status /pending /warteschlange /center /id")
