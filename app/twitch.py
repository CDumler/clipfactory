import os, time, json, datetime, requests

_token = {"value": None, "exp": 0}

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

def load_config():
    with open("/config/discovery.json") as f:
        return json.load(f)

def discover_broadcasters(language, top_n=25, min_viewers=2000):
    """Findet die groessten LIVE-Streams der Sprache (Kennzahl 1: Live-Viewer)."""
    out, cursor = [], None
    while len(out) < top_n:
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
            if len(out) >= top_n:
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

def _velocity(clip):
    """Kennzahl 2: Views pro Stunde seit Clip-Erstellung."""
    try:
        created = datetime.datetime.fromisoformat(clip["created_at"].replace("Z", "+00:00"))
        hours = max(0.5, (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds() / 3600)
        return clip.get("view_count", 0) / hours
    except Exception:
        return 0.0

def top_clips_for(broadcaster, hours=24, first=20):
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))
    r = requests.get("https://api.twitch.tv/helix/clips", params={
        "broadcaster_id": broadcaster["id"], "started_at": started, "first": first,
    }, headers=_hdr(), timeout=20)
    r.raise_for_status()
    out = []
    for c in r.json().get("data", []):
        out.append({
            "id": c["id"],
            "streamer": broadcaster["login"],
            "url": c["url"],
            "title": c.get("title", ""),
            "views": c.get("view_count", 0),
            "velocity": round(_velocity(c), 1),
            "duration": float(c.get("duration", 0)),
            "language": c.get("language", ""),
        })
    return out

def discover_candidates(profile_name):
    """Kompletter Discovery-Lauf fuer ein Profil: Streams finden, Clips holen,
    nach Velocity ranken. Liefert (kandidaten, anzahl_broadcaster)."""
    cfg = load_config()
    p = cfg["profiles"][profile_name]
    bl = set(x.lower() for x in p.get("blocklist", []))

    bcs = discover_broadcasters(p["language"], p.get("top_n_streams", 25),
                                p.get("min_live_viewers", 2000))
    extra = [l for l in p.get("extra_streamers", [])
             if l.lower() not in {b["login"].lower() for b in bcs}]
    if extra:
        bcs += user_ids(extra)
    bcs = [b for b in bcs if b["login"].lower() not in bl]

    clips = []
    for b in bcs:
        try:
            clips += top_clips_for(b)
        except Exception:
            continue
    clips.sort(key=lambda c: -c["velocity"])
    return clips, len(bcs)

def facecam_for(login):
    return load_config().get("facecams", {}).get((login or "").lower())
