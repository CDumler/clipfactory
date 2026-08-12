import base64
import json
import os
import re
import subprocess

import requests

from .score import _extract_output_text

WIDTH = 1920
HEIGHT = 1080
MAX_AREA_RATIO = 0.40
MARK_MAX_AREA_RATIO = 0.50
TMP = "/data/tmp"

PROMPT = (
    "Dies ist ein Standbild aus einem Twitch-Stream (1920x1080). "
    "Finde die Facecam/Webcam des Streamers - das ist meist ein rechteckiger "
    "Bereich mit einer Person, oft in einer Ecke. Antworte NUR mit JSON: "
    '{"found": true/false, "x": int, "y": int, "w": int, "h": int}. '
    "Koordinaten in Pixeln bezogen auf 1920x1080. Wenn keine klare Facecam "
    "erkennbar ist, found=false."
)

MARK_PROMPT = (
    "Auf diesem 1920x1080-Standbild aus einem Twitch-Stream ist ein Bereich "
    "handschriftlich mit einem Kreis oder einer Markierung umrandet. Auch wenn "
    "Telegram das Foto skaliert oder komprimiert hat, gib die Box in Pixeln "
    "bezogen auf das Original 1920x1080 an. Gib mir das umschliessende Rechteck "
    'dieser Markierung als JSON zurueck: {"found":true/false,"x":int,"y":int,'
    '"w":int,"h":int}. Falls keine Markierung erkennbar ist, setze found=false '
    'und x=0, y=0, w=0, h=0.'
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "found": {"type": "boolean"},
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "w": {"type": "integer"},
        "h": {"type": "integer"},
    },
    "required": ["found", "x", "y", "w", "h"],
}


def _strip_fences(text):
    text = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.IGNORECASE | re.DOTALL)
    return (m.group(1) if m else text).strip()


def _clip_duration(path):
    p = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    )
    try:
        return max(0.0, float((p.stdout or "").strip()))
    except ValueError:
        return 0.0


def extract_midframe(clip_id):
    src = f"/data/raw/{clip_id}.mp4"
    dst = f"{TMP}/{clip_id}.facecam.jpg"
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    os.makedirs(TMP, exist_ok=True)
    mid = _clip_duration(src) / 2.0
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{mid:.2f}",
            "-i", src,
            "-frames:v", "1",
            "-vf", f"scale={WIDTH}:{HEIGHT}",
            dst,
        ],
        check=True,
        timeout=120,
    )
    return dst


def _parse_box_response(text, max_area_ratio):
    try:
        data = json.loads(_strip_fences(text))
    except Exception:
        return None

    if not data.get("found"):
        return None

    try:
        box = {k: int(data[k]) for k in ("x", "y", "w", "h")}
    except Exception:
        return None

    if box["w"] <= 0 or box["h"] <= 0:
        return None
    if box["x"] < 0 or box["y"] < 0:
        return None
    if box["x"] + box["w"] > WIDTH or box["y"] + box["h"] > HEIGHT:
        return None
    if (box["w"] * box["h"]) > int(WIDTH * HEIGHT * max_area_ratio):
        return None
    return box


def parse_facecam_response(text):
    return _parse_box_response(text, MAX_AREA_RATIO)


def parse_marked_box_response(text):
    return _parse_box_response(text, MARK_MAX_AREA_RATIO)


def _mime_for_path(path):
    path = (path or "").lower()
    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def _ensure_image_ready(image_path):
    if not os.path.exists(image_path):
        raise RuntimeError("⚠️ Bild noch nicht vollständig empfangen, bitte nochmal senden.")
    size = os.path.getsize(image_path)
    if size <= 0:
        raise RuntimeError("⚠️ Bild noch nicht vollständig empfangen, bitte nochmal senden.")
    return size


def _detect_box_from_image(image_path, prompt, schema_name, parser):
    _ensure_image_ready(image_path)
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")
    if not image_b64:
        raise RuntimeError("⚠️ Bild noch nicht vollständig empfangen, bitte nochmal senden.")

    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
    body = {
        "model": model,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {
                    "type": "input_image",
                    "image_url": f"data:{_mime_for_path(image_path)};base64,{image_b64}",
                    "detail": "high",
                },
            ],
        }],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": SCHEMA,
                "strict": True,
            }
        },
    }
    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "content-type": "application/json",
        },
        json=body,
        timeout=90,
    )
    if not r.ok:
        raise RuntimeError(f"OpenAI Fehler {r.status_code}: {r.text}")
    data = r.json()
    text = _extract_output_text(data)
    if not text:
        raise RuntimeError(f"OpenAI lieferte keinen Text-Output: {json.dumps(data)[:1200]}")
    return parser(text)


def detect_facecam(clip_id, streamer_login):
    jpg_path = extract_midframe(clip_id)
    try:
        return _detect_box_from_image(jpg_path, PROMPT, "facecam_box", parse_facecam_response)
    except Exception as e:
        raise RuntimeError(f"Facecam-Erkennung fuer {streamer_login} fehlgeschlagen: {e}") from e


def detect_marked_box(image_path):
    return _detect_box_from_image(image_path, MARK_PROMPT, "marked_box", parse_marked_box_response)
