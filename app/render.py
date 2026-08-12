import os, json, subprocess, math

OUT = "/data/out"
TMP = "/data/tmp"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FACECAM_REF_W = 1920
FACECAM_REF_H = 1080
FACE_TARGET_W = 1080
FACE_TARGET_H = 600
GAME_TARGET_W = 1080
GAME_TARGET_H = 1320
FACECAM_PAD = 1.04
GAME_TARGET_RATIO = GAME_TARGET_W / GAME_TARGET_H
DEFAULT_LAYOUT = {
    "cam_zoom": 1.12,
    "cam_shift_x": 0,
    "cam_shift_y": 0,
    "game_zoom": 1.0,
    "game_shift_x": 0,
    "game_shift_y": 0,
}


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
        cleaned = (w["w"] or "").strip(" ,.;:!?")
        if not cleaned:
            continue
        group.append({**w, "w": cleaned})
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


def _probe_video_size(path):
    p = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    raw = (p.stdout or "").strip()
    if "x" not in raw:
        raise RuntimeError(f"Video-Groesse konnte nicht gelesen werden: {path}")
    width_s, height_s = raw.split("x", 1)
    return int(width_s), int(height_s)


def _scale_facecam_box(facecam, src_w, src_h, ref_w=FACECAM_REF_W, ref_h=FACECAM_REF_H):
    if not facecam or facecam == "center":
        return facecam

    sx = float(src_w) / float(ref_w)
    sy = float(src_h) / float(ref_h)

    x = int(round(float(facecam["x"]) * sx))
    y = int(round(float(facecam["y"]) * sy))
    w = int(round(float(facecam["w"]) * sx))
    h = int(round(float(facecam["h"]) * sy))

    x = max(0, min(src_w - 2, x))
    y = max(0, min(src_h - 2, y))
    w = max(2, min(src_w - x, w))
    h = max(2, min(src_h - y, h))
    return {"x": x, "y": y, "w": w, "h": h}


def _pad_box(box, src_w, src_h, pad=1.0):
    cx = float(box["x"]) + float(box["w"]) / 2.0
    cy = float(box["y"]) + float(box["h"]) / 2.0

    crop_w = min(float(src_w), float(box["w"]) * pad)
    crop_h = min(float(src_h), float(box["h"]) * pad)

    x = int(round(cx - crop_w / 2.0))
    y = int(round(cy - crop_h / 2.0))
    w = max(2, int(round(crop_w)))
    h = max(2, int(round(crop_h)))

    x = max(0, min(src_w - w, x))
    y = max(0, min(src_h - h, y))
    return {"x": x, "y": y, "w": w, "h": h}


def _clip_layout(clip):
    layout = dict(DEFAULT_LAYOUT)
    raw = clip.get("layout_json")
    if not raw:
        return layout
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return layout
    if not isinstance(data, dict):
        return layout
    for key in layout:
        if key in data:
            layout[key] = data[key]
    return layout


def _clamp_box(x, y, w, h, src_w, src_h):
    w = max(2, min(src_w, int(round(w))))
    h = max(2, min(src_h, int(round(h))))
    x = max(0, min(src_w - w, int(round(x))))
    y = max(0, min(src_h - h, int(round(y))))
    return {"x": x, "y": y, "w": w, "h": h}


def _shift_box(box, src_w, src_h, dx=0, dy=0):
    return _clamp_box(
        float(box["x"]) + float(dx),
        float(box["y"]) + float(dy),
        box["w"],
        box["h"],
        src_w,
        src_h,
    )


def _zoom_box(box, src_w, src_h, zoom=1.0):
    try:
        zoom = float(zoom)
    except (TypeError, ValueError):
        zoom = 1.0
    zoom = max(0.75, min(2.5, zoom))
    cx = float(box["x"]) + float(box["w"]) / 2.0
    cy = float(box["y"]) + float(box["h"]) / 2.0
    crop_w = float(box["w"]) / zoom
    crop_h = float(box["h"]) / zoom
    return _clamp_box(cx - crop_w / 2.0, cy - crop_h / 2.0, crop_w, crop_h, src_w, src_h)


def _ref_shift(dx_ref, dy_ref, src_w, src_h, ref_w=FACECAM_REF_W, ref_h=FACECAM_REF_H):
    try:
        dx_ref = float(dx_ref)
    except (TypeError, ValueError):
        dx_ref = 0.0
    try:
        dy_ref = float(dy_ref)
    except (TypeError, ValueError):
        dy_ref = 0.0
    return (
        int(round(dx_ref * float(src_w) / float(ref_w))),
        int(round(dy_ref * float(src_h) / float(ref_h))),
    )


def _largest_ratio_crop(src_w, src_h, ratio):
    src_ratio = float(src_w) / float(src_h)
    if src_ratio >= ratio:
        h = src_h
        w = int(round(h * ratio))
    else:
        w = src_w
        h = int(round(w / ratio))
    return _clamp_box((src_w - w) / 2.0, (src_h - h) / 2.0, w, h, src_w, src_h)


def _smart_game_box(src_w, src_h, face_box, layout):
    base = _largest_ratio_crop(src_w, src_h, GAME_TARGET_RATIO)
    try:
        game_zoom = float(layout.get("game_zoom", 1.0))
    except (TypeError, ValueError):
        game_zoom = 1.0
    game_zoom = max(1.0, min(2.5, game_zoom))
    crop = _zoom_box(base, src_w, src_h, game_zoom)

    if face_box:
        face_cx = float(face_box["x"]) + float(face_box["w"]) / 2.0
        face_cy = float(face_box["y"]) + float(face_box["h"]) / 2.0
        face_nx = face_cx / float(src_w)
        face_ny = face_cy / float(src_h)
        bias_x = max(0.0, min(1.0, 0.5 + (0.5 - face_nx) * 0.7))
        bias_y = max(0.0, min(1.0, 0.5 + (0.5 - face_ny) * 0.45))
        auto_x = (src_w - crop["w"]) * bias_x
        auto_y = (src_h - crop["h"]) * bias_y
        crop = _clamp_box(auto_x, auto_y, crop["w"], crop["h"], src_w, src_h)

    shift_x, shift_y = _ref_shift(
        layout.get("game_shift_x", 0),
        layout.get("game_shift_y", 0),
        src_w,
        src_h,
    )
    return _shift_box(crop, src_w, src_h, shift_x, shift_y)


def render(clip, facecam, ass_path):
    """Schneidet, layoutet (9:16), brennt Captions ein, normalisiert Audio."""
    src = f"/data/raw/{clip['id']}.mp4"
    dst = f"{OUT}/{clip['id']}.mp4"
    start, end = float(clip["start_s"] or 0), float(clip["end_s"] or clip["duration"])

    if facecam and facecam != "center":
        src_w, src_h = _probe_video_size(src)
        scaled = _scale_facecam_box(facecam, src_w, src_h)
        layout_cfg = _clip_layout(clip)
        padded = _pad_box(scaled, src_w, src_h, pad=FACECAM_PAD)
        top_box = _zoom_box(padded, src_w, src_h, layout_cfg.get("cam_zoom", 1.0))
        cam_shift_x, cam_shift_y = _ref_shift(
            layout_cfg.get("cam_shift_x", 0),
            layout_cfg.get("cam_shift_y", 0),
            src_w,
            src_h,
        )
        top_box = _shift_box(top_box, src_w, src_h, cam_shift_x, cam_shift_y)
        game_box = _smart_game_box(src_w, src_h, scaled, layout_cfg)
        fx, fy, fw, fh = top_box["x"], top_box["y"], top_box["w"], top_box["h"]
        gx, gy, gw, gh = game_box["x"], game_box["y"], game_box["w"], game_box["h"]
        layout = (
            f"[0:v]split=2[a][b];"
            f"[a]crop={fw}:{fh}:{fx}:{fy},"
            f"scale={FACE_TARGET_W}:{FACE_TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={FACE_TARGET_W}:{FACE_TARGET_H}:(iw-{FACE_TARGET_W})/2:(ih-{FACE_TARGET_H})/2[face];"
            f"[b]crop={gw}:{gh}:{gx}:{gy},"
            f"scale={GAME_TARGET_W}:{GAME_TARGET_H}[game];"
            f"[face][game]vstack[v0]"
        )
    else:
        layout = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black[v0]"
        )

    filters = [layout]
    last = "v0"

    if os.environ.get("ENABLE_ZOOM", "0") == "1":
        peaks = _detect_peaks(src, start, end)
        if peaks:
            bumps = "+".join(f"0.07*exp(-14*pow(on/30-{p - start:.2f},2))" for p in peaks)
            filters.append(
                f"[{last}]zoompan=z='1+{bumps}':d=1:x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30[v2]"
            )
            last = "v2"

    out_label = last
    if os.environ.get("ENABLE_GENERATED_CAPTIONS", "0") == "1":
        filters.append(f"[{last}]subtitles={ass_path}[vout]")
        out_label = "vout"
    fc = ";".join(filters)

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", str(start), "-to", str(end), "-i", src,
        "-filter_complex", fc, "-map", f"[{out_label}]", "-map", "0:a?",
        "-af", "loudnorm=I=-15:TP=-1.5",
        "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", dst,
    ]
    subprocess.run(cmd, check=True, timeout=1800)
    return dst
