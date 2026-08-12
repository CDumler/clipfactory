import html, json, os, time, threading, requests

API = lambda m: f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{m}"
CHAT = lambda: os.environ.get("TELEGRAM_CHAT_ID", "")


def _log(msg):
    print(f"[telegram] {msg}", flush=True)


def _safe(text):
    return html.escape(str(text), quote=False)


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


def send(text, buttons=None, chat_id=None):
    chat_id = str(chat_id or CHAT() or "").strip()
    if not chat_id:
        _log("TELEGRAM_CHAT_ID ist leer; Nachricht wird uebersprungen")
        return False
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": False}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        r = requests.post(API("sendMessage"), json=payload, timeout=20)
        r.raise_for_status()
        body = r.json()
        if not body.get("ok", False):
            _log(f"sendMessage fehlgeschlagen: {body}")
            return False
        return True
    except Exception as e:
        _log(f"sendMessage Fehler: {e}")
        return False


def send_photo(path, caption, buttons=None, chat_id=None):
    chat_id = str(chat_id or CHAT() or "").strip()
    if not chat_id:
        _log("TELEGRAM_CHAT_ID ist leer; Foto wird uebersprungen")
        return False
    payload = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        with open(path, "rb") as f:
            r = requests.post(
                API("sendPhoto"),
                data=payload,
                files={"photo": (os.path.basename(path), f, "image/jpeg")},
                timeout=60,
            )
        r.raise_for_status()
        body = r.json()
        if not body.get("ok", False):
            _log(f"sendPhoto fehlgeschlagen: {body}")
            return False
        return True
    except Exception as e:
        _log(f"sendPhoto Fehler: {e}")
        return False


def download_telegram_photo(file_id, dst_path):
    try:
        info = requests.get(API("getFile"), params={"file_id": file_id}, timeout=20)
        info.raise_for_status()
        body = info.json()
        if not body.get("ok") or not body.get("result", {}).get("file_path"):
            _log(f"getFile fehlgeschlagen: {body}")
            return None
        file_path = body["result"]["file_path"]
        data = requests.get(
            f"https://api.telegram.org/file/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{file_path}",
            timeout=60,
        )
        data.raise_for_status()
        if not data.content:
            _log("download_telegram_photo: leere Antwort beim Datei-Download")
            return None
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        tmp_path = f"{dst_path}.part"
        with open(tmp_path, "wb") as f:
            f.write(data.content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dst_path)
        if not os.path.exists(dst_path) or os.path.getsize(dst_path) <= 0:
            _log("download_telegram_photo: Zieldatei ist leer")
            return None
        return dst_path
    except Exception as e:
        _log(f"download_telegram_photo Fehler: {e}")
        return None


def send_video(clip_id, caption, buttons=None, chat_id=None):
    chat_id = str(chat_id or CHAT() or "").strip()
    if not chat_id:
        _log("TELEGRAM_CHAT_ID ist leer; Video wird uebersprungen")
        return False
    path = f"/data/out/{clip_id}.mp4"
    payload = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        with open(path, "rb") as f:
            r = requests.post(
                API("sendVideo"),
                data=payload,
                files={"video": (os.path.basename(path), f, "video/mp4")},
                timeout=180,
            )
        r.raise_for_status()
        body = r.json()
        if not body.get("ok", False):
            _log(f"sendVideo fehlgeschlagen: {body}")
            return False
        return True
    except Exception as e:
        _log(f"sendVideo Fehler: {e}")
        return False


def send_candidate(c, chat_id=None):
    emo = {"rage": "🤬", "schock": "😱", "funny": "😂", "wholesome": "🥹",
           "drama": "🍿", "hype": "🔥"}.get(c.get("category") or "", "🎬")
    streamer = f"🚀 HYPE {_safe(c['streamer'])}" if c.get("hype") else _safe(c["streamer"])
    markers = []
    if c.get("frisch"):
        markers.append("🆕 FRISCH")
    markers.append(f"{c.get('velocity','?')} Views/h")
    markers.append(_age_label(c.get("alter_h")))
    markers.append(f"Heat {round(float(c.get('heat_score', 0)), 1)}")
    text = (f"{c.get('flag','')} {emo} <b>{streamer}</b> — Score <b>{c['score']}</b> ({_safe(c['category'])})\n"
            f"Hook: <i>{_safe(c['hook'])}</i>\n"
            f"Titel: {_safe(c['title'])}\n"
            f"{' | '.join(markers)}\n"
            f"Views: {c['views']} | Schnitt: {c['start_s']}s–{c['end_s']}s\n"
            f"▶️ {_safe(c['url'])}")
    send(text, [[
        {"text": "✅ Freigeben", "callback_data": f"ok:{c['id']}"},
        {"text": "❌ Ablehnen", "callback_data": f"no:{c['id']}"},
    ], [
        {"text": "📷 Cam justieren", "callback_data": f"cam:{c['id']}"},
    ]], chat_id=chat_id)


def _answer(cb_id, text):
    try:
        r = requests.post(API("answerCallbackQuery"),
                          json={"callback_query_id": cb_id, "text": text}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        _log(f"answerCallbackQuery Fehler: {e}")

def _dispatch_command(txt, chat_id, handlers):
    parts = txt.split()
    head = (parts[0] if parts else "").split("@")[0].lower()

    if head in {"/clips", "/sammeln"}:
        profile = (parts[1].lower() if len(parts) > 1 else "de")
        if handlers.get("on_collect"):
            handlers["on_collect"](profile, chat_id)
        return True

    if head == "/clip":
        if len(parts) < 2:
            if handlers.get("on_clip_menu"):
                handlers["on_clip_menu"](chat_id)
            else:
                send("Nutzung: <code>/clip STREAMERNAME</code>", chat_id=chat_id)
        elif handlers.get("on_single_clip"):
            handlers["on_single_clip"](parts[1].strip(), chat_id)
        return True

    if head == "/status":
        if handlers.get("on_status"):
            handlers["on_status"](chat_id)
        elif handlers.get("get_stats"):
            send(f"📊 <code>{_safe(handlers['get_stats']())}</code>", chat_id=chat_id)
        return True

    if head == "/pending":
        if handlers.get("on_pending"):
            handlers["on_pending"](chat_id)
        return True

    if head == "/warteschlange":
        if handlers.get("on_queue"):
            handlers["on_queue"](chat_id)
        return True

    if head == "/hilfe":
        if handlers.get("on_help"):
            handlers["on_help"](chat_id)
        return True

    if head == "/id":
        if handlers.get("on_id"):
            handlers["on_id"](chat_id)
        else:
            send(f"Deine Chat-ID: <code>{chat_id}</code>", chat_id=chat_id)
        return True

    if head == "/center":
        if handlers.get("on_center"):
            handlers["on_center"](chat_id)
        else:
            send("ℹ️ <code>/center</code> funktioniert nur waehrend der Cam-Abfrage.", chat_id=chat_id)
        return True

    if head in {"/camreset", "/camset", "/camoff"}:
        if handlers.get("on_legacy_cam_command"):
            handlers["on_legacy_cam_command"](chat_id)
        else:
            send(
                "ℹ️ Facecam wird jetzt pro Clip gesetzt. Bitte Clip freigeben und dann "
                "das Standbild einkreisen oder <code>/center</code> tippen.",
                chat_id=chat_id,
            )
        return True

    send("Unbekannter Befehl. Mit <code>/hilfe</code> siehst du alle Optionen.", chat_id=chat_id)
    return True


def _dispatch_text_message(txt, chat_id, handlers):
    txt = (txt or "").strip()
    if not txt:
        return False
    if txt.startswith("/"):
        return _dispatch_command(txt, chat_id, handlers)
    if handlers.get("on_text"):
        handlers["on_text"](txt, chat_id)
        return True
    return False


def poll_loop(handlers):
    offset = 0
    while True:
        try:
            resp = requests.get(API("getUpdates"),
                                params={"timeout": 50, "offset": offset}, timeout=60)
            resp.raise_for_status()
            r = resp.json()
            if not r.get("ok", False):
                _log(f"getUpdates fehlgeschlagen: {r}")
                time.sleep(5)
                continue
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                if "callback_query" in u:
                    cq = u["callback_query"]
                    data = cq.get("data", "")
                    if ":" not in data:
                        continue
                    action, clip_id = data.split(":", 1)
                    if action == "ok":
                        handlers["on_approve"](clip_id, cq["message"]["chat"]["id"])
                        _answer(cq["id"], "Cam-Abfrage oder Vorschau startet 🎬")
                    elif action in {"cam", "editcam"}:
                        if handlers.get("on_cam_adjust"):
                            handlers["on_cam_adjust"](clip_id, cq["message"]["chat"]["id"])
                        _answer(cq["id"], "Frame kommt 📷")
                    elif action == "camoff":
                        if handlers.get("on_center_clip"):
                            handlers["on_center_clip"](clip_id, cq["message"]["chat"]["id"])
                        _answer(cq["id"], "Mittig-Crop wird gesetzt ✂️")
                    elif action == "clipstreamer":
                        if handlers.get("on_clipstreamer"):
                            handlers["on_clipstreamer"](clip_id, cq["message"]["chat"]["id"])
                        _answer(cq["id"], "Clip-Suche startet 🔍")
                    elif action in {"up", "upload"}:
                        if handlers.get("on_upload_now"):
                            handlers["on_upload_now"](clip_id, cq["message"]["chat"]["id"])
                        _answer(cq["id"], "Upload startet 🚀")
                    elif action in {"no", "reject"}:
                        handlers["on_reject"](clip_id)
                        _answer(cq["id"], "Abgelehnt ❌")
                    elif action == "editlen":
                        if handlers.get("on_edit_length"):
                            handlers["on_edit_length"](clip_id, cq["message"]["chat"]["id"])
                        _answer(cq["id"], "Laengen-Bearbeitung vorbereitet ✂️")
                    elif action == "laymenu":
                        if handlers.get("on_layout_menu"):
                            handlers["on_layout_menu"](clip_id, cq["message"]["chat"]["id"])
                        _answer(cq["id"], "Layout-Regler offen 🎛️")
                    elif action.startswith("lay"):
                        if handlers.get("on_layout_adjust"):
                            handlers["on_layout_adjust"](action, clip_id, cq["message"]["chat"]["id"])
                        _answer(cq["id"], "Layout wird neu gerendert 🎬")
                elif "message" in u:
                    msg = u["message"]
                    msg_chat_id = msg["chat"]["id"]
                    if "photo" in msg and handlers.get("on_photo"):
                        handlers["on_photo"](msg, msg_chat_id)
                        continue
                    txt = (msg.get("text") or "").strip()
                    _dispatch_text_message(txt, msg_chat_id, handlers)
        except Exception as e:
            _log(f"Polling-Fehler: {e}")
            time.sleep(5)


def start_polling(handlers):
    t = threading.Thread(
        target=poll_loop,
        args=(handlers,),
        daemon=True,
    )
    t.start()
