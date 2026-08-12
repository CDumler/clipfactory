import os, time, json, math, datetime, threading, requests

_token = {"value": None, "exp": 0}
HISTORY_PATH = "/data/viewer_history.json"
HISTORY_KEEP = 7
_config_lock = threading.Lock()

def _app_token():
    if _token["value"] and time.time() < _token["exp"] - 60:
        return _token["value"]
    r = requests.post("https://id.twitch.tv/oauth2/token", data={
        "client_id": os.environ["TWITCH_CLIENT_ID"],
        "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
        "grant_type": "client_credentials",
    }, timeout=20)
    r.raise_for_status()
    j = r.json()
    _token["value"] = j["access_token"]
    _token["exp"] = time.time() + j.get("expires_in", 3600)
    return _token["value"]

def _hdr():
    return {"Client-ID": os.environ["TWITCH_CLIENT_ID"],
            "Authorization": f"Bearer {_app_token()}"}

def _config_path():
    return os.environ.get("DISCOVERY_CONFIG_PATH", "/config/discovery.json")

def load_config():
    with open(_config_path()) as f:
        return json.load(f)

def _write_config(cfg):
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")

def _live_broadcasters(language, limit=None, min_viewers=0):
    """Findet LIVE-Streams der Sprache oberhalb einer Viewer-Schwelle."""
    out, cursor = [], None
    while True:
        params = {"language": language, "first": 100}
        if cursor:
            params["after"] = cursor
        r = requests.get("https://api.twitch.tv/helix/streams",
                         params=params, headers=_hdr(), timeout=20)
        r.raise_for_status()
        j = r.json()
        for s in j.get("data", []):
            if s.get("viewer_count", 0) < min_viewers:
                return out  # Liste ist nach Viewern sortiert -> ab hier zu klein
            out.append({"id": s["user_id"], "login": s["user_login"],
                        "viewers": s["viewer_count"]})
            if limit and len(out) >= limit:
                return out
        cursor = j.get("pagination", {}).get("cursor")
        if not cursor:
            break
    return out

def user_ids(logins):
    ids = []
    for i in range(0, len(logins), 100):
        r = requests.get("https://api.twitch.tv/helix/users",
                         params=[("login", l) for l in logins[i:i+100]],
                         headers=_hdr(), timeout=20)
        r.raise_for_status()
        ids += [{"id": u["id"], "login": u["login"], "viewers": 0}
                for u in r.json().get("data", [])]
    return ids

def validate_logins(logins):
    vorhandene, gefunden = [], set()
    for i in range(0, len(logins), 100):
        batch = logins[i:i+100]
        if not batch:
            continue
        r = requests.get("https://api.twitch.tv/helix/users",
                         params=[("login", l) for l in batch],
                         headers=_hdr(), timeout=20)
        r.raise_for_status()
        hits = {u["login"].lower(): u["login"] for u in r.json().get("data", [])}
        for login in batch:
            hit = hits.get(login.lower())
            if hit:
                vorhandene.append(hit)
                gefunden.add(login.lower())
    fehlend = [login for login in logins if login.lower() not in gefunden]
    return {"vorhanden": vorhandene, "fehlend": fehlend}

def _load_viewer_history():
    try:
        with open(HISTORY_PATH) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def _save_viewer_history(history):
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f)

def hype_broadcasters(language, hype_cfg, feste_logins):
    feste = {x.lower() for x in feste_logins}
    min_viewers = int(hype_cfg.get("min_live_viewers", 0))
    spike_ratio = float(hype_cfg.get("spike_ratio", 2.5))
    max_new = int(hype_cfg.get("max_new_per_run", 5))
    history = _load_viewer_history()
    out = []

    for s in _live_broadcasters(language, min_viewers=min_viewers):
        login = (s.get("login") or "").lower()
        if not login or login in feste:
            continue
        vals = history.get(login, [])
        avg = (sum(vals) / len(vals)) if vals else 0.0
        if vals and avg > 0 and (s["viewers"] / avg) > spike_ratio and len(out) < max_new:
            out.append({**s, "hype": True})
        vals = (vals + [s["viewers"]])[-HISTORY_KEEP:]
        history[login] = vals

    _save_viewer_history(history)
    return out

def heat_score(clip):
    """Heat-Score mit starkem Velocity-Fokus und Daempfung fuer sehr junge Clips."""
    try:
        created = datetime.datetime.fromisoformat(clip["created_at"].replace("Z", "+00:00"))
        alter_h = max(0.05, (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds() / 3600)
        views = max(0, int(clip.get("view_count", 0)))
        velocity = views / alter_h
        vertrauen_zeit = min(1.0, alter_h / 0.5)
        vertrauen_views = min(1.0, views / 15.0)
        vertrauen = vertrauen_zeit * vertrauen_views
        absolut_bonus = math.log10(views + 1)
        score = velocity * vertrauen * 1.0 + absolut_bonus * 0.3
        return {
            "score": round(score, 2),
            "velocity": round(velocity, 1),
            "alter_h": round(alter_h, 2),
        }
    except Exception:
        return {"score": 0.0, "velocity": 0.0, "alter_h": 0.0}


def _velocity(clip):
    return heat_score(clip)["velocity"]

def top_clips_for(broadcaster, hours=24, first=20):
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))
    r = requests.get("https://api.twitch.tv/helix/clips", params={
        "broadcaster_id": broadcaster["id"], "started_at": started, "first": first,
    }, headers=_hdr(), timeout=20)
    r.raise_for_status()
    out = []
    for c in r.json().get("data", []):
        metrics = heat_score(c)
        out.append({
            "id": c["id"],
            "streamer": broadcaster["login"],
            "url": c["url"],
            "title": c.get("title", ""),
            "views": c.get("view_count", 0),
            "velocity": metrics["velocity"],
            "alter_h": metrics["alter_h"],
            "heat_score": metrics["score"],
            "duration": float(c.get("duration", 0)),
            "language": c.get("language", ""),
        })
    return out

def discover_candidates(profile_name):
    """Kompletter Discovery-Lauf fuer ein Profil: Streams finden, Clips holen,
    nach Velocity ranken. Liefert (kandidaten, anzahl_broadcaster, fehlende_logins)."""
    cfg = load_config()
    p = cfg["profiles"][profile_name]
    clips_per_streamer = int(p.get("clips_per_streamer", 20))
    feste_logins = p.get("streamers", [])
    fehlende_logins = []
    bcs, hype_logins = [], set()

    if feste_logins:
        valid = validate_logins(feste_logins)
        fehlende_logins = valid["fehlend"]
        resolved = user_ids(valid["vorhanden"])
        pos = {login.lower(): i for i, login in enumerate(valid["vorhanden"])}
        bcs = sorted(resolved, key=lambda b: pos.get(b["login"].lower(), 10**9))
        hype_cfg = p.get("hype_filter", {})
        if hype_cfg.get("enabled"):
            extra = hype_broadcasters(p["language"], hype_cfg, valid["vorhanden"])
            known = {b["login"].lower() for b in bcs}
            for b in extra:
                if b["login"].lower() in known:
                    continue
                bcs.append(b)
                hype_logins.add(b["login"].lower())
                known.add(b["login"].lower())
    else:
        bl = set(x.lower() for x in p.get("blocklist", []))
        bcs = _live_broadcasters(p["language"], p.get("top_n_streams", 25),
                                 p.get("min_live_viewers", 2000))
        extra = [l for l in p.get("extra_streamers", [])
                 if l.lower() not in {b["login"].lower() for b in bcs}]
        if extra:
            bcs += user_ids(extra)
        bcs = [b for b in bcs if b["login"].lower() not in bl]

    clips = []
    for b in bcs:
        try:
            new_clips = top_clips_for(b, first=clips_per_streamer)
            if b["login"].lower() in hype_logins:
                new_clips = [{**c, "hype": True} for c in new_clips]
            clips += new_clips
        except Exception:
            continue
    clips.sort(key=lambda c: (-c.get("heat_score", 0), -c.get("velocity", 0), -c.get("views", 0)))
    return clips, len(bcs), fehlende_logins

def facecam_for(login):
    entry = load_config().get("facecams", {}).get((login or "").lower())
    if not isinstance(entry, dict):
        return None
    if entry.get("mode") == "center":
        return "center"
    if not all(k in entry for k in ("x", "y", "w", "h")):
        return None
    return {k: int(entry[k]) for k in ("x", "y", "w", "h")}

def facecam_known(login):
    return (login or "").lower() in load_config().get("facecams", {})

def save_facecam(streamer_login, coords):
    login = (streamer_login or "").strip().lower()
    if not login:
        raise ValueError("streamer_login fehlt")
    with _config_lock:
        cfg = load_config()
        facecams = cfg.setdefault("facecams", {})
        if coords is None:
            facecams[login] = {"found": False}
        elif coords == "center":
            facecams[login] = {"mode": "center"}
        else:
            facecams[login] = {k: int(coords[k]) for k in ("x", "y", "w", "h")}
        _write_config(cfg)
        return facecams[login]

def reset_facecam(streamer_login):
    login = (streamer_login or "").strip().lower()
    if not login:
        return False
    with _config_lock:
        cfg = load_config()
        facecams = cfg.setdefault("facecams", {})
        existed = login in facecams
        if existed:
            del facecams[login]
            _write_config(cfg)
        return existed
