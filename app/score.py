import os, json, requests

PROMPT = """Du bist Experte fuer virale Streaming-Clips (YouTube Shorts / TikTok / Reels).
Bewerte den folgenden Twitch-Clip anhand von Transkript und Metadaten.

STREAMER: {streamer}
TWITCH-TITEL: {title}
TWITCH-VIEWS (24h): {views}
DAUER: {duration}s
TRANSKRIPT (mit Wort-Zeitstempeln in Sekunden):
{transcript}

Bewerte 0-100:
- EMOTION (40 P): starke echte Reaktion? Rage/Ausraster, Schock/Unglaube,
  Lachanfall, Jubel = hoch. Bonus fuer Kontrast (ruhig -> Eskalation).
- VERSTAENDLICH OHNE KONTEXT (25 P): funktioniert der Moment, ohne das Spiel
  oder den Stream zu kennen? Reaction > reines Gameplay.
- PAYOFF-DICHTE (20 P): laesst sich der Clip so schneiden, dass die Eskalation
  in Sekunde 1-3 liegt? Klare Pointe vorhanden?
- SHAREABILITY (15 P): wuerde man das einem Freund schicken?
  (absurd, wholesome, dramatisch, zitierfaehig)

Antworte NUR mit einem JSON-Objekt, ohne Markdown, exakt diese Felder:
{{"score": int, "category": "rage|schock|funny|wholesome|drama|hype",
"hook": "max 6 Woerter, Sprache: {lang}, clickbaity aber wahr",
"title": "YouTube-Shorts-Titel, max 80 Zeichen, Sprache: {lang}, Streamername drin, 1 Emoji",
"description": "2 Saetze + 5 Hashtags, Sprache: {lang}",
"start_s": float (ab welcher Sekunde schneiden, damit Eskalation frueh kommt),
"end_s": float (Ende; Ziel-Laenge 15-45s, nie ueber {duration}),
"loopable": true/false}}"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "category": {"type": "string", "enum": ["rage", "schock", "funny", "wholesome", "drama", "hype"]},
        "hook": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "start_s": {"type": "number"},
        "end_s": {"type": "number"},
        "loopable": {"type": "boolean"},
    },
    "required": ["score", "category", "hook", "title", "description", "start_s", "end_s", "loopable"],
}


def _extract_output_text(data):
    texts = []
    for item in data.get("output", []):
        if item.get("type") == "output_text" and item.get("text"):
            texts.append(item["text"])
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text" and part.get("text"):
                texts.append(part["text"])
    return "".join(texts).strip()


def score_clip(clip, transcript_with_times, out_lang="de"):
    lang = "Deutsch" if out_lang == "de" else "Englisch"
    model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
    body = {
        "model": model,
        "input": PROMPT.format(
            streamer=clip["streamer"], title=clip["twitch_title"],
            views=clip["views"], duration=clip["duration"],
            transcript=transcript_with_times[:6000], lang=lang,
        ),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "clip_score",
                "schema": SCHEMA,
                "strict": True,
            }
        },
    }
    r = requests.post("https://api.openai.com/v1/responses", headers={
        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
        "content-type": "application/json",
    }, json=body, timeout=90)
    r.raise_for_status()
    data = r.json()
    text = _extract_output_text(data)
    if not text:
        raise RuntimeError(f"OpenAI lieferte keinen Text-Output: {json.dumps(data)[:1200]}")
    j = json.loads(text)
    # Grenzen absichern
    j["score"] = max(0, min(100, int(j.get("score", 0))))
    j["start_s"] = max(0.0, float(j.get("start_s", 0)))
    j["end_s"] = min(float(clip["duration"]), float(j.get("end_s", clip["duration"])))
    if j["end_s"] - j["start_s"] < 6:
        j["start_s"], j["end_s"] = 0.0, float(clip["duration"])
    return j


def words_to_prompt_text(words):
    return " ".join(f"[{w['start']}]{w['w']}" for w in words)
