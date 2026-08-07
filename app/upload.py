import os, time, requests


# ---------------- YouTube ----------------

def _env(name, profile):
    """Liest profil-spezifische Variable, z.B. IG_USER_ID_DE / IG_USER_ID_EN."""
    import os
    return os.environ.get(f"{name}_{profile.upper()}") or os.environ.get(name, "")


def _public_base_url():
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/")
    host = (os.environ.get("PUBLIC_HOSTNAME") or "").strip()
    if host:
        return f"https://{host}"
    raise RuntimeError("PUBLIC_BASE_URL oder PUBLIC_HOSTNAME fehlt")


def _meta_graph_base():
    ver = (os.environ.get("META_GRAPH_API_VERSION") or "v21.0").strip().strip("/")
    return f"https://graph.facebook.com/{ver}"


def youtube(path, title, description, profile="de"):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    token_path = f"/secrets/{profile}/token.json"
    creds = Credentials.from_authorized_user_file(
        token_path, ["https://www.googleapis.com/auth/youtube.upload"])
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    yt = build("youtube", "v3", credentials=creds)
    privacy = (_env("YOUTUBE_PRIVACY_STATUS", profile) or "private").strip().lower()
    if privacy not in {"private", "unlisted", "public"}:
        privacy = "private"
    body = {
        "snippet": {"title": title[:100], "description": description[:4900],
                     "categoryId": "20"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(path, mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


# ---------------- Instagram Reels ----------------

def instagram(clip_id, caption, profile="de"):
    base = _public_base_url()
    video_url = f"{base}/media/{clip_id}.mp4"
    uid = _env("IG_USER_ID", profile)
    tok = _env("IG_ACCESS_TOKEN", profile)
    graph = _meta_graph_base()

    r = requests.post(f"{graph}/{uid}/media", data={
        "media_type": "REELS", "video_url": video_url,
        "caption": caption[:2100], "share_to_feed": "false", "access_token": tok,
    }, timeout=60)
    r.raise_for_status()
    container = r.json()["id"]

    for _ in range(60):  # bis ~5 Min auf Verarbeitung warten
        s = requests.get(f"{graph}/{container}",
                         params={"fields": "status_code", "access_token": tok}, timeout=30).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            raise RuntimeError(f"IG-Verarbeitung fehlgeschlagen: {s}")
        time.sleep(5)

    r = requests.post(f"{graph}/{uid}/media_publish", data={
        "creation_id": container, "access_token": tok,
    }, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


# ---------------- TikTok (Inbox-Upload -> landet als Entwurf in der App) ----------------

def tiktok(path, title, profile="de"):
    tok = _env("TIKTOK_ACCESS_TOKEN", profile)
    size = os.path.getsize(path)
    r = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        json={"source_info": {"source": "FILE_UPLOAD", "video_size": size,
                              "chunk_size": size, "total_chunk_count": 1}},
        timeout=60,
    )
    r.raise_for_status()
    j = r.json()["data"]
    with open(path, "rb") as f:
        up = requests.put(j["upload_url"], data=f, headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{size - 1}/{size}",
        }, timeout=600)
    up.raise_for_status()
    return j["publish_id"]
