import html, os, time, threading, requests

API = lambda m: f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{m}"
CHAT = lambda: os.environ["TELEGRAM_CHAT_ID"]


def _log(msg):
    print(f"[telegram] {msg}", flush=True)


def _safe(text):
    return html.escape(str(text), quote=False)


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


def send_candidate(c):
    emo = {"rage": "🤬", "schock": "😱", "funny": "😂", "wholesome": "🥹",
           "drama": "🍿", "hype": "🔥"}.get(c.get("category") or "", "🎬")
    text = (f"{c.get('flag','')} {emo} <b>{_safe(c['streamer'])}</b> — Score <b>{c['score']}</b> ({_safe(c['category'])})\n"
            f"Hook: <i>{_safe(c['hook'])}</i>\n"
            f"Titel: {_safe(c['title'])}\n"
            f"Views: {c['views']} | Velocity: {c.get('velocity','?')}/h | Schnitt: {c['start_s']}s–{c['end_s']}s\n"
            f"▶️ {_safe(c['url'])}")
    send(text, [[
        {"text": "✅ Freigeben", "callback_data": f"ok:{c['id']}"},
        {"text": "❌ Ablehnen", "callback_data": f"no:{c['id']}"},
    ]])


def _answer(cb_id, text):
    try:
        r = requests.post(API("answerCallbackQuery"),
                          json={"callback_query_id": cb_id, "text": text}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        _log(f"answerCallbackQuery Fehler: {e}")


def poll_loop(on_approve, on_reject, get_stats):
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
                    action, clip_id = cq["data"].split(":", 1)
                    if action == "ok":
                        on_approve(clip_id)
                        _answer(cq["id"], "In der Upload-Warteschlange ✅")
                    else:
                        on_reject(clip_id)
                        _answer(cq["id"], "Abgelehnt ❌")
                elif "message" in u:
                    msg_chat_id = u["message"]["chat"]["id"]
                    txt = (u["message"].get("text") or "").strip()
                    if txt == "/status":
                        send(f"📊 <code>{_safe(get_stats())}</code>", chat_id=msg_chat_id)
                    elif txt == "/id":
                        send(f"Deine Chat-ID: <code>{msg_chat_id}</code>", chat_id=msg_chat_id)
        except Exception as e:
            _log(f"Polling-Fehler: {e}")
            time.sleep(5)


def start_polling(on_approve, on_reject, get_stats):
    t = threading.Thread(target=poll_loop, args=(on_approve, on_reject, get_stats), daemon=True)
    t.start()
