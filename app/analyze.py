import os, subprocess, json

RAW = "/data/raw"
_model = None

def download(clip):
    path = f"{RAW}/{clip['id']}.mp4"
    if os.path.exists(path):
        return path
    subprocess.run(
        ["yt-dlp", "-q", "--no-warnings", "-o", path, clip["url"]],
        check=True, timeout=300,
    )
    return path

def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(os.environ.get("WHISPER_MODEL", "small"),
                              device="cpu", compute_type="int8")
    return _model

def transcribe(path):
    """Liefert (volltext, woerter[list of {w, start, end}], sprache)."""
    model = _get_model()
    segments, info = model.transcribe(path, word_timestamps=True, vad_filter=True)
    words, text_parts = [], []
    for seg in segments:
        text_parts.append(seg.text.strip())
        for w in (seg.words or []):
            words.append({"w": w.word.strip(), "start": round(w.start, 2), "end": round(w.end, 2)})
    return " ".join(text_parts).strip(), words, info.language

def save_words(clip_id, words):
    with open(f"{RAW}/{clip_id}.words.json", "w") as f:
        json.dump(words, f)

def load_words(clip_id):
    p = f"{RAW}/{clip_id}.words.json"
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return json.load(f)
