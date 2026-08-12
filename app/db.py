import sqlite3, json, threading, time

DB_PATH = "/data/clipfactory.db"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
  id TEXT PRIMARY KEY,
  profile TEXT, velocity REAL, heat_score REAL, alter_h REAL, frisch INTEGER DEFAULT 0,
  streamer TEXT, url TEXT, twitch_title TEXT,
  views INTEGER, duration REAL, language TEXT,
  status TEXT DEFAULT 'new',
  transcript TEXT, score INTEGER, category TEXT,
  hook TEXT, title TEXT, description TEXT,
  start_s REAL, end_s REAL, loopable INTEGER DEFAULT 0,
  cam_x INTEGER, cam_y INTEGER, cam_w INTEGER, cam_h INTEGER, cam_mode TEXT,
  layout_json TEXT,
  yt_id TEXT, ig_id TEXT, tt_id TEXT,
  error TEXT, created_ts INTEGER
);
"""

def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c

def init():
    with conn() as c:
        c.executescript(SCHEMA)
        cols = {row["name"] for row in c.execute("PRAGMA table_info(clips)").fetchall()}
        if "heat_score" not in cols:
            c.execute("ALTER TABLE clips ADD COLUMN heat_score REAL DEFAULT 0")
        if "alter_h" not in cols:
            c.execute("ALTER TABLE clips ADD COLUMN alter_h REAL DEFAULT 0")
        if "frisch" not in cols:
            c.execute("ALTER TABLE clips ADD COLUMN frisch INTEGER DEFAULT 0")
        if "cam_x" not in cols:
            c.execute("ALTER TABLE clips ADD COLUMN cam_x INTEGER")
        if "cam_y" not in cols:
            c.execute("ALTER TABLE clips ADD COLUMN cam_y INTEGER")
        if "cam_w" not in cols:
            c.execute("ALTER TABLE clips ADD COLUMN cam_w INTEGER")
        if "cam_h" not in cols:
            c.execute("ALTER TABLE clips ADD COLUMN cam_h INTEGER")
        if "cam_mode" not in cols:
            c.execute("ALTER TABLE clips ADD COLUMN cam_mode TEXT")
        if "layout_json" not in cols:
            c.execute("ALTER TABLE clips ADD COLUMN layout_json TEXT")

def known(clip_id):
    with conn() as c:
        return c.execute("SELECT 1 FROM clips WHERE id=?", (clip_id,)).fetchone() is not None

def insert_candidate(d):
    with _lock, conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO clips (id, profile, velocity, heat_score, alter_h, frisch, streamer, url, twitch_title, views, duration, created_ts, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?, ?, 'new')",
            (
                d["id"], d["profile"], d.get("velocity", 0), d.get("heat_score", 0),
                d.get("alter_h", 0), 1 if d.get("frisch") else 0,
                d["streamer"], d["url"], d["title"], d["views"], d["duration"], int(time.time()),
            ),
        )

def update(clip_id, **kw):
    keys = ", ".join(f"{k}=?" for k in kw)
    with _lock, conn() as c:
        c.execute(f"UPDATE clips SET {keys} WHERE id=?", (*kw.values(), clip_id))

def get(clip_id):
    with conn() as c:
        r = c.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        return dict(r) if r else None

def by_status(status, limit=100):
    with conn() as c:
        rs = c.execute(
            "SELECT * FROM clips WHERE status=? ORDER BY score DESC, views DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rs]

def by_statuses(statuses, limit=100):
    if not statuses:
        return []
    marks = ", ".join("?" for _ in statuses)
    with conn() as c:
        rs = c.execute(
            f"SELECT * FROM clips WHERE status IN ({marks}) "
            "ORDER BY created_ts DESC, score DESC, views DESC LIMIT ?",
            (*statuses, limit),
        ).fetchall()
        return [dict(r) for r in rs]

def next_approved(profile):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM clips WHERE status='approved' AND profile=? "
            "ORDER BY score DESC, created_ts ASC LIMIT 1", (profile,)
        ).fetchone()
        return dict(r) if r else None

def next_preview_ready(profile):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM clips WHERE status='preview_ready' AND profile=? "
            "ORDER BY score DESC, created_ts ASC LIMIT 1", (profile,)
        ).fetchone()
        return dict(r) if r else None

def stats():
    with conn() as c:
        rs = c.execute("SELECT status, COUNT(*) n FROM clips GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rs}
