import os, json, traceback, subprocess, html
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from apscheduler.schedulers.background import BackgroundScheduler

from . import db, twitch, analyze, score, render, upload, tg

api = FastAPI(title="ClipFactory")
sched = BackgroundScheduler(timezone=os.environ.get("TZ", "Europe/Berlin"))

FLAG = {"de": "🇩🇪", "en": "🇺🇸"}


def _profiles():
    return list(twitch.load_config()["profiles"].keys())


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


# ---------------- Pipeline ----------------

def collect_and_score(profile):
    """Discovery -> Transkription -> KI-Scoring -> Telegram-Karten (pro Profil)."""
    tg.send(f"🌅 {FLAG.get(profile,'')} Discovery laeuft: suche die heissesten "
            f"{profile.upper()}-Streams und ranke Clips nach Velocity ...")
    try:
        clips, n_bc = twitch.discover_candidates(profile)
    except Exception as e:
        tg.send(f"⚠️ Discovery-Fehler ({profile}): <code>{html.escape(str(e)[:800])}</code>")
        return

    min_v = int(os.environ.get("MIN_CLIP_VIEWS", 300))
    cands = []
    for c in clips:
        if db.known(c["id"]) or c["views"] < min_v:
            continue
        if not (float(os.environ.get("MIN_CLIP_SECONDS", 8)) <= c["duration"]
                <= float(os.environ.get("MAX_CLIP_SECONDS", 75))):
            continue
        c["profile"] = profile
        cands.append(c)
        if len(cands) >= int(os.environ.get("CANDIDATES_PER_DAY", 25)):
            break

    for c in cands:
        db.insert_candidate(c)

    sent, min_score = 0, int(os.environ.get("MIN_SCORE", 60))
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
                      status="pending_review" if j["score"] >= min_score else "rejected")
            if j["score"] >= min_score:
                tg.send_candidate({**c, **j, "flag": FLAG.get(profile, "")})
                sent += 1
        except Exception as e:
            db.update(c["id"], status="failed", error=str(e)[:500])
    tg.send(f"✅ {FLAG.get(profile,'')} Fertig: {n_bc} Streams gescannt, "
            f"{len(cands)} Kandidaten analysiert, {sent} zur Freigabe geschickt.")


def process_and_upload(profile):
    platforms = _platforms(profile)
    if not platforms:
        return
    clip = db.next_approved(profile)
    if not clip:
        return
    cid = clip["id"]
    try:
        db.update(cid, status="rendering")
        words = analyze.load_words(cid)
        ass = f"/data/tmp/{cid}.ass"
        render.build_ass(words, clip["start_s"] or 0, clip["end_s"] or clip["duration"], ass)
        out = render.render(clip, twitch.facecam_for(clip["streamer"]), ass)

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
                + "\n" + "\n".join(results))
    except Exception as e:
        db.update(cid, status="failed", error=str(e)[:500])
        tg.send(f"❌ {FLAG.get(profile,'')} Upload-Fehler bei {cid}:\n<code>{html.escape(str(e)[:800])}</code>")
        traceback.print_exc()


# ---------------- Telegram ----------------

def _approve(cid):
    db.update(cid, status="approved")

def _reject(cid):
    db.update(cid, status="rejected")

def _stats():
    return json.dumps(db.stats(), ensure_ascii=False)


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
    src, dst = f"/data/raw/{clip_id}.mp4", f"/data/tmp/{clip_id}.jpg"
    if not os.path.exists(src):
        return JSONResponse({"error": "Clip nicht (mehr) im raw-Ordner"}, status_code=404)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "2", "-i", src,
                    "-frames:v", "1", "-vf", "scale=1920:1080", dst], check=True)
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
    db.init()
    for prof in _profiles():
        ct = (os.environ.get(f"COLLECT_TIME_{prof.upper()}")
              or os.environ.get("COLLECT_TIME", "06:30"))
        h, m = ct.split(":")
        sched.add_job(collect_and_score, "cron", hour=int(h), minute=int(m), args=[prof])
        if _platforms(prof):
            ut = (os.environ.get(f"UPLOAD_TIMES_{prof.upper()}")
                  or os.environ.get("UPLOAD_TIMES", "17:00"))
            for slot in ut.split(","):
                sh, sm = slot.strip().split(":")
                sched.add_job(process_and_upload, "cron", hour=int(sh), minute=int(sm), args=[prof])
    sched.start()
    tg.start_polling(_approve, _reject, _stats)
    tg.send("🟢 ClipFactory laeuft (Profile: " + ", ".join(_profiles()) + ").\n"
            "Befehle: /status /id — manuell: POST /run/collect/de usw.")
