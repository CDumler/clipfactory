import os, json, subprocess, math

OUT = "/data/out"
TMP = "/data/tmp"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _ass_time(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(words, start_s, end_s, path):
    """Wort-fuer-Wort-Karaoke-Captions, 2-3 Woerter pro Einblendung."""
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # Primary=gelb (wird beim Sprechen gefuellt), Secondary=weiss, dicke schwarze Outline
        "Style: Cap,DejaVu Sans,88,&H0000E5FF,&H00FFFFFF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,7,2,2,60,60,560,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Text\n"
    )
    lines = []
    group = []
    for w in words:
        if w["end"] <= start_s or w["start"] >= end_s:
            continue
        group.append(w)
        if len(group) >= 3 or (len(group) >= 2 and len(" ".join(x["w"] for x in group)) > 18):
            lines.append(group); group = []
    if group:
        lines.append(group)

    events = []
    for g in lines:
        t0 = max(0.0, g[0]["start"] - start_s)
        t1 = max(t0 + 0.3, g[-1]["end"] - start_s + 0.12)
        parts = []
        for w in g:
            dur_cs = max(8, int((w["end"] - w["start"]) * 100))
            txt = w["w"].replace("{", "").replace("}", "").replace("\\", "")
            parts.append(f"{{\\kf{dur_cs}}}{txt}")
        text = "{\\fscx115\\fscy115\\t(0,90,\\fscx100\\fscy100)}" + " ".join(parts)
        events.append(f"Dialogue: 0,{_ass_time(t0)},{_ass_time(t1)},Cap,,0,0,0,,{text}")

    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")


def _detect_peaks(path, start_s, end_s, max_peaks=6):
    """Findet Lautstaerke-Peaks fuer Zoom-Punches (grob, per RMS auf 0.5s-Fenstern)."""
    try:
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(start_s), "-to", str(end_s), "-i", path,
             "-af", "astats=metadata=1:reset=0.5,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        pts, cur_t = [], None
        for line in p.stdout.splitlines():
            if line.startswith("frame:"):
                cur_t = float(line.split("pts_time:")[1])
            elif "RMS_level=" in line and cur_t is not None:
                try:
                    pts.append((cur_t, float(line.split("=")[1])))
                except ValueError:
                    pass
        if not pts:
            return []
        pts.sort(key=lambda x: -x[1])
        chosen = []
        for t, _ in pts:
            if all(abs(t - c) > 2.5 for c in chosen):
                chosen.append(t)
            if len(chosen) >= max_peaks:
                break
        return sorted(chosen)
    except Exception:
        return []


def _esc_drawtext(s):
    return s.replace("\\", "").replace(":", "\\:").replace("'", "\u2019").replace("%", "")


def render(clip, facecam, ass_path):
    """Schneidet, layoutet (9:16), brennt Hook + Captions, normalisiert Audio."""
    src = f"/data/raw/{clip['id']}.mp4"
    dst = f"{OUT}/{clip['id']}.mp4"
    start, end = float(clip["start_s"] or 0), float(clip["end_s"] or clip["duration"])
    hook = _esc_drawtext((clip.get("hook") or "").upper())

    if facecam:
        fx, fy, fw, fh = facecam["x"], facecam["y"], facecam["w"], facecam["h"]
        layout = (
            f"[0:v]split=2[a][b];"
            f"[a]crop={fw}:{fh}:{fx}:{fy},scale=1080:600:force_original_aspect_ratio=increase,"
            f"crop=1080:600[face];"
            f"[b]crop='min(iw,ih*1080/1320)':ih:'(iw-min(iw,ih*1080/1320))/2':0,"
            f"scale=1080:1320[game];"
            f"[face][game]vstack[v0]"
        )
    else:
        layout = (
            "[0:v]crop='min(iw,ih*9/16)':ih:'(iw-min(iw,ih*9/16))/2':0,"
            "scale=1080:1920[v0]"
        )

    filters = [layout]
    last = "v0"

    if hook:
        filters.append(
            f"[{last}]drawtext=fontfile={FONT}:text='{hook}':"
            f"fontsize=62:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=22:"
            f"x=(w-tw)/2:y=200:enable='lt(t,2.6)'[v1]"
        )
        last = "v1"

    if os.environ.get("ENABLE_ZOOM", "0") == "1":
        peaks = _detect_peaks(src, start, end)
        if peaks:
            bumps = "+".join(f"0.07*exp(-14*pow(on/30-{p - start:.2f},2))" for p in peaks)
            filters.append(
                f"[{last}]zoompan=z='1+{bumps}':d=1:x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30[v2]"
            )
            last = "v2"

    filters.append(f"[{last}]subtitles={ass_path}[vout]")
    fc = ";".join(filters)

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", str(start), "-to", str(end), "-i", src,
        "-filter_complex", fc, "-map", "[vout]", "-map", "0:a?",
        "-af", "loudnorm=I=-15:TP=-1.5",
        "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", dst,
    ]
    subprocess.run(cmd, check=True, timeout=1800)
    return dst
