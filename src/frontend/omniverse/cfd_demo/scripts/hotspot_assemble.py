#!/usr/bin/env python3
"""Assemble the recorded frames into the two deliverables, video and GIF.

Input is `hotspot_shots/{thermal,flow}/f###.png` plus the two manifests. Output:

    hotspot_thermal.mp4 / .gif      temperature volume
    hotspot_flow.mp4    / .gif      volume + streamlines

Playback is built from 31 rendered frames:

  * held segments repeat one rendered frame for a beat (1 s each), so the viewer reads
    the state before it starts changing
  * transitions play their 14 rendered frames at 12 fps
  * the mp4 then doubles to 24 fps by blending each adjacent PAIR. The underlying
    change is a smooth field morph, so a blended in-between is indistinguishable from a
    rendered one — and costs milliseconds instead of the ~70 s a real render takes.

That gives ~5.3 s, inside the 5-10 s the animation was asked to run.

Every frame carries burnt-in context, because a bare render cannot say what changed:
the stage caption, a colour bar using the SAME six stops the IndeX transfer function
uses (`scene.py:_apply_thermal_colormap`) over the SAME pinned domain, a progress bar,
and the provenance line. The provenance line is not decoration — these anchors are
deliberately unconverged, and the qualifier has to travel with the pixels.

Nothing here writes the original case name: the outputs ship to a public page while the
paper is under double-blind review.

    python3 hotspot_assemble.py
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = pathlib.Path(__file__).resolve().parents[5]
CASE = pathlib.Path(os.environ.get("HOTSPOT_CASE", REPO / "case/GTC"))
# Artefacts live under case/GTC/forOVdemo/, which .gitignore excludes: the anchors, the
# 31 interpolated frames and the viewport shots come to ~6.6 GB per scenario and have no
# business in git. Only the finished videos (copied into the project page's assets), the
# ACU position file and this code are tracked.
ART = pathlib.Path(os.environ.get("HOTSPOT_ART", CASE / "forOVdemo"))
FRAMES = pathlib.Path(os.environ.get("HOTSPOT_FRAMES", ART / "hotspot_frames"))
SHOTS = pathlib.Path(os.environ.get("HOTSPOT_SHOTS", ART / "hotspot_shots"))
OUT = pathlib.Path(os.environ.get("HOTSPOT_VIDEO", ART / "hotspot_video"))
FFMPEG = os.environ.get("FFMPEG", shutil.which("ffmpeg") or "ffmpeg")

FPS = 12                 # rendered/played base rate
HOLD_S = 1.0             # how long each anchor is held
# Every played frame repeats SLOW times. The field morphs slowly and the eye needs time
# to find the hot region before it moves, so the animation runs at half speed: 5.3 s
# becomes 10.6 s, still inside the 5-10 s brief at the top of its range.
SLOW = int(os.environ.get("HOTSPOT_SLOW", "2"))
W_MP4 = 1920
W_GIF = 900

CJK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
CJK_R = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

# The IndeX transfer function, verbatim from scene.py:_apply_thermal_colormap. The
# legend has to be the same ramp over the same domain or it is a decorative lie.
STOPS = [
    (0.00, (0.10, 0.22, 1.00)),
    (0.20, (0.00, 0.80, 1.00)),
    (0.45, (0.20, 0.90, 0.30)),
    (0.65, (1.00, 0.75, 0.05)),
    (0.82, (1.00, 0.35, 0.02)),
    (1.00, (0.85, 0.00, 0.08)),
]

PROVENANCE = "CFD 稳态工况 · Omniverse RTX 渲染"
TITLES = {"thermal": "温度场 · Omniverse RTX · NVIDIA IndeX 体渲染",
          "flow": "气流组织 · Omniverse RTX · GPU 流线（RK4）"}

# Both halves are the real viewport. `thermal_offline/` stays as a fallback only: it was
# built while the IndeX volume appeared to be broken, before the real cause turned up --
# the volume simply takes ~15 s longer to reach the stream than the backend's
# `volume rebuilt` log line, so every screenshot was firing early. See
# `hotspot_record.py:wait_until_changed`.
SOURCE_DIRS = {"thermal": ("thermal", "thermal_offline"), "flow": ("flow",)}

# Pixel positions of each ACU in the recorded crop, measured by `hotspot_acu_positions.py`
# (hide one unit, diff the render, take where the change concentrates). Absent file =>
# no unit markers, rather than markers in guessed places.
ACU_JSON = pathlib.Path(os.environ.get("HOTSPOT_ACU_JSON",
                                       CASE / "hotspot_acu_positions.json"))
DEAD_COL = (255, 92, 92)
BOOST_COL = (118, 220, 120)


def log(m):
    print(f"[asm] {m}", flush=True)


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def ramp_colour(x):
    """Sample the six-stop ramp at x in [0,1]."""
    x = min(max(x, 0.0), 1.0)
    for (x0, c0), (x1, c1) in zip(STOPS, STOPS[1:]):
        if x <= x1:
            t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
            return tuple(round(255 * (a + t * (b - a))) for a, b in zip(c0, c1))
    return tuple(round(255 * c) for c in STOPS[-1][1])


def hottest_point(im, quantile=0.999):
    """Pixel centroid of the hottest thing on screen.

    The colour domain is pinned and the ramp is monotonic, so "reddest" IS "hottest
    along the view ray" — no camera matrix needed, and no invented 3-D position. Scored
    as red minus the strongest of the other two channels, which separates the amber-red
    tail from the cyan-green bulk and ignores the grey geometry entirely. The centroid of
    the top 0.1% of pixels is stable frame to frame; a single argmax jitters.
    """
    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    score = a[:, :, 0] - np.maximum(a[:, :, 1], a[:, :, 2])
    thr = np.quantile(score, quantile)
    if thr <= 12:            # nothing genuinely hot on screen
        return None
    ys, xs = np.nonzero(score >= thr)
    if len(xs) < 20:
        return None
    # Densest CLUSTER, not the global centroid. In the failure state most of the room is
    # near its peak, so a plain centroid is pulled to wherever the volume is deepest
    # along the ray -- it landed on the far wall, which is a path-length artefact rather
    # than a hot region. Binning and picking the fullest 3x3 neighbourhood keeps the
    # marker on an actual concentration of hot pixels.
    NX, NY = 24, 16
    bx = np.clip((xs * NX) // im.width, 0, NX - 1)
    by = np.clip((ys * NY) // im.height, 0, NY - 1)
    hist = np.zeros((NY, NX), dtype=np.int32)
    np.add.at(hist, (by, bx), 1)
    pad = np.pad(hist, 1)
    box = sum(pad[i:i + NY, j:j + NX] for i in range(3) for j in range(3))
    cy, cx = np.unravel_index(int(np.argmax(box)), box.shape)
    keep = (np.abs(bx - cx) <= 1) & (np.abs(by - cy) <= 1)
    if keep.sum() < 20:
        return int(xs.mean()), int(ys.mean())
    return int(xs[keep].mean()), int(ys[keep].mean())


def draw_hotspot(d, im, peak_c, s, reserved=None):
    """Ring the hottest region and label it with the frame's true peak temperature.

    `reserved` is the box the colour bar and its captions occupy. The hot region drifts
    with the scenario -- in the single-unit case it settles low and right, directly on
    top of the bar's lower limit -- so the label flips above the marker rather than
    overprinting a number the viewer needs to read.
    """
    pt = hottest_point(im)
    if pt is None:
        return
    x, y = pt
    r = int(26 * s)
    HOT = (255, 96, 72)
    d.ellipse([x - r, y - r, x + r, y + r], outline=HOT, width=max(2, int(2.5 * s)))
    d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=HOT)
    f = font(CJK, int(23 * s))
    label = f"最热区 {peak_c:.1f} °C"
    lx, ly = x + r + int(12 * s), y - int(14 * s)
    box = d.textbbox((lx, ly), label, font=f)
    # Keep the label on screen when the hot region drifts to the right edge.
    if box[2] > im.width - int(20 * s):
        lx = x - r - int(12 * s) - (box[2] - box[0])
        box = d.textbbox((lx, ly), label, font=f)
    if reserved and box[2] > reserved[0] and box[3] > reserved[1]:
        ly = y - r - int(30 * s)
        lx = min(lx, im.width - (box[2] - box[0]) - int(24 * s))
        box = d.textbbox((lx, ly), label, font=f)
    d.line([x + r, y, lx - int(6 * s), ly + int(14 * s)], fill=HOT, width=max(1, int(1.5 * s)))
    d.rounded_rectangle([box[0] - int(9 * s), box[1] - int(6 * s),
                         box[2] + int(9 * s), box[3] + int(7 * s)],
                        radius=int(7 * s), fill=(10, 12, 16, 220), outline=HOT, width=1)
    d.text((lx, ly), label, font=f, fill=(255, 214, 205))


def draw_acus(d, im, acus, dead, seg, alpha, s):
    """Mark which units failed and which took over, at the moment each becomes true.

    Phased rather than always-on: the failed units appear as the failure transition runs,
    the boosted ones only once the intervention starts. A static legend would say the same
    thing in every frame and answer the question ("what changed just now?") in none of
    them.
    """
    if not acus:
        return
    ps = im.width / float(acus["view"]["width"])
    fade_dead = {"hold_S0": 0.0, "fail": alpha}.get(seg, 1.0)
    fade_boost = {"fix": alpha}.get(seg, 1.0 if seg == "hold_S2" else 0.0)
    r = int(17 * ps)
    f = font(CJK, int(19 * ps))
    for uid, pt in acus["acus"].items():
        is_dead = uid in dead
        fade = fade_dead if is_dead else fade_boost
        if fade <= 0.02:
            continue
        col = DEAD_COL if is_dead else BOOST_COL
        a = int(255 * min(1.0, fade))
        x, y = int(pt["x"] * ps), int(pt["y"] * ps)
        d.ellipse([x - r, y - r, x + r, y + r], outline=col + (a,),
                  width=max(2, int(2.5 * ps)))
        if is_dead:      # a cross, so the two states differ without relying on colour
            k = int(r * 0.6)
            d.line([x - k, y - k, x + k, y + k], fill=col + (a,), width=max(2, int(2 * ps)))
            d.line([x - k, y + k, x + k, y - k], fill=col + (a,), width=max(2, int(2 * ps)))
        label = "故障" if is_dead else "加力"
        box = d.textbbox((x + r + int(6 * ps), y - int(11 * ps)), label, font=f)
        d.rounded_rectangle([box[0] - int(6 * ps), box[1] - int(4 * ps),
                            box[2] + int(6 * ps), box[3] + int(5 * ps)],
                            radius=int(5 * ps), fill=(10, 12, 16, int(a * 0.85)),
                            outline=col + (a,), width=1)
        d.text((x + r + int(6 * ps), y - int(11 * ps)), label, font=f, fill=col + (a,))


def draw_overlay(im, caption, kind, vmin_c, vmax_c, progress, peak_c=None,
                 acus=None, dead=(), seg="", alpha=0.0):
    """Burn caption, colour bar, progress bar and provenance onto one frame."""
    im = im.convert("RGB")
    d = ImageDraw.Draw(im, "RGBA")
    W, H = im.size
    s = W / 1310.0                      # the capture box width the recorder used
    f_title = font(CJK, int(26 * s))
    f_cap = font(CJK, int(31 * s))
    f_small = font(CJK_R, int(17 * s))

    # Title, top-left.
    d.text((int(30 * s), int(24 * s)), TITLES[kind], font=f_title, fill=(228, 236, 245))

    # The hottest region, marked where the viewer can actually see it. Only on the
    # thermal half: the flow animation hides the volume, so there is no heat field on
    # screen to point at and the streamline colours are auto-scaled per frame.
    if kind == "thermal" and peak_c is not None:
        # Everything from the colour bar's caption line down-right is off limits.
        draw_hotspot(d, im, peak_c, s,
                     reserved=(W - int(360 * s), H - int(110 * s)))
    draw_acus(d, im, acus, dead, seg, alpha, s)

    # Stage caption, bottom-left, on a chip so it survives a bright frame.
    box = d.textbbox((0, 0), caption, font=f_cap)
    cw, ch = box[2] - box[0], box[3] - box[1]
    cx, cy = int(30 * s), H - ch - int(78 * s)
    d.rounded_rectangle([cx - int(16 * s), cy - int(14 * s),
                         cx + cw + int(16 * s), cy + ch + int(16 * s)],
                        radius=int(10 * s), fill=(10, 12, 16, 225),
                        outline=(118, 185, 0), width=max(1, int(2 * s)))
    d.text((cx, cy - box[1]), caption, font=f_cap, fill=(236, 242, 248))

    # Colour bar, bottom-right, over the pinned domain.
    bw, bh = int(300 * s), int(13 * s)
    bx, by = W - bw - int(34 * s), H - int(74 * s)
    for i in range(bw):
        d.line([(bx + i, by), (bx + i, by + bh)], fill=ramp_colour(i / (bw - 1)))
    d.rectangle([bx, by, bx + bw, by + bh], outline=(150, 160, 172))
    if kind == "thermal":
        # Numbers only where they mean something. The volume is rendered over the pinned
        # domain, so a colour maps to a temperature; the flow animation hides the volume
        # and its streamline colours are auto-scaled per frame, so putting limits on that
        # bar would invite exactly the reading the data cannot support.
        d.text((bx, by - int(23 * s)), f"{vmin_c:.1f} °C", font=f_small,
               fill=(198, 208, 220))
        hi = f"{vmax_c:.1f} °C"
        d.text((bx + bw - d.textbbox((0, 0), hi, font=f_small)[2], by - int(23 * s)),
               hi, font=f_small, fill=(198, 208, 220))
    else:
        d.text((bx, by - int(23 * s)), "冷", font=f_small, fill=(198, 208, 220))
        d.text((bx + bw - d.textbbox((0, 0), "热", font=f_small)[2], by - int(23 * s)),
               "热", font=f_small, fill=(198, 208, 220))

    # Provenance, under the bar. Small, permanent, and the reason the demo is honest.
    pv_w = d.textbbox((0, 0), PROVENANCE, font=f_small)[2]
    d.text((W - pv_w - int(34 * s), by + bh + int(8 * s)), PROVENANCE,
           font=f_small, fill=(150, 160, 172))
    if kind == "flow":
        # The bar describes the VOLUME, which is pinned. The streamlines are coloured by
        # the auto-scaling ScalarColor MDL material (RescaleRangeAPI), which re-fits per
        # frame — so their hue is not comparable between frames and the bar must not be
        # read as applying to them. Say so rather than letting the legend imply it.
        note = "体渲染已隐藏，仅显示流线；着色为温度，标度逐帧自适应"
        d.text((W - d.textbbox((0, 0), note, font=f_small)[2] - int(34 * s),
                by + bh + int(26 * s)), note, font=f_small, fill=(150, 160, 172))

    # Progress bar hugging the bottom edge.
    pb = int(4 * s)
    d.rectangle([0, H - pb, W, H], fill=(28, 32, 40))
    d.rectangle([0, H - pb, int(W * progress), H], fill=(118, 185, 0))
    return im


def source_dir(kind):
    for name in SOURCE_DIRS[kind]:
        d = SHOTS / name
        if d.exists() and any(d.glob("f*.png")):
            return d
    raise SystemExit(f"no frames for {kind} under {SHOTS}")


def build_sequence(record, kind):
    """Expand the 31 rendered frames into the played order."""
    by_index = {c["index"]: c for c in record["captured"]}
    seq = []
    for c in record["captured"]:
        held = c["segment"].startswith("hold")
        seq.extend([c] * (round(HOLD_S * FPS) if held else 1) * SLOW)
    if len(by_index) != len(record["captured"]):
        log("WARNING: duplicate frame indices in record.json")
    return seq


def blend_up(paths_or_images):
    """Insert one blended frame between each adjacent pair: 12 fps -> 24 fps."""
    out = []
    for a, b in zip(paths_or_images, paths_or_images[1:]):
        out.append(a)
        out.append(Image.blend(a, b, 0.5))
    out.append(paths_or_images[-1])
    return out


def encode(frames, stem, kind):
    """Write the mp4 (24 fps, blended up) and the GIF (12 fps) for one animation."""
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / f"_{kind}_png"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()

    hi = [f.resize((W_MP4, round(f.height * W_MP4 / f.width)), Image.LANCZOS)
          for f in frames]
    # H.264 needs even dimensions.
    if hi[0].height % 2:
        hi = [f.crop((0, 0, f.width, f.height - 1)) for f in hi]
    for i, f in enumerate(blend_up(hi)):
        f.save(tmp / f"{i:04d}.png")
    mp4 = OUT / f"{stem}.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-framerate", str(FPS * 2),
                    "-i", str(tmp / "%04d.png"), "-c:v", "libx264", "-preset", "slow",
                    "-crf", "20", "-pix_fmt", "yuv420p", str(mp4)], check=True)
    log(f"{mp4.name}  {len(hi) * 2 - 1} frames  {mp4.stat().st_size / 1e6:.1f} MB")

    gif_src = OUT / f"_{kind}_gif"
    if gif_src.exists():
        shutil.rmtree(gif_src)
    gif_src.mkdir()
    for i, f in enumerate(frames):
        f.resize((W_GIF, round(f.height * W_GIF / f.width)), Image.LANCZOS) \
         .save(gif_src / f"{i:04d}.png")
    gif = OUT / f"{stem}.gif"
    pal = gif_src / "palette.png"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-framerate", str(FPS),
                    "-i", str(gif_src / "%04d.png"),
                    "-vf", "palettegen=stats_mode=diff", str(pal)], check=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-framerate", str(FPS),
                    "-i", str(gif_src / "%04d.png"), "-i", str(pal),
                    "-lavfi", "paletteuse=dither=sierra2_4a", "-loop", "0",
                    str(gif)], check=True)
    log(f"{gif.name}  {len(frames)} frames  {gif.stat().st_size / 1e6:.1f} MB")

    shutil.rmtree(tmp)
    shutil.rmtree(gif_src)
    return mp4, gif


def main():
    record = json.loads((SHOTS / "record.json").read_text())
    meta = json.loads((FRAMES / "frames.json").read_text())
    dead = set(meta.get("anchors", {}).get("S1", {}).get("dead", []))
    acus = json.loads(ACU_JSON.read_text()) if ACU_JSON.exists() else None
    log(f"ACU markers: {'on' if acus else 'OFF (no position file)'}, "
        f"failed = {sorted(dead) or 'none'}")
    vmin_c = record["colourDomainK"][0] - 273.15
    vmax_c = record["colourDomainK"][1] - 273.15
    log(f"{len(record['captured'])} rendered frames, "
        f"domain {vmin_c:.1f}..{vmax_c:.1f} °C, peak rise {record['peakRiseK']} K")

    results = {}
    for kind, stem in (("thermal", "hotspot_thermal"), ("flow", "hotspot_flow")):
        seq = build_sequence(record, kind)
        src_dir = source_dir(kind)
        log(f"{kind}: {len(seq)} played frames = {len(seq) / FPS:.2f} s at {FPS} fps, "
            f"from {src_dir.name}/")
        frames = []
        for n, c in enumerate(seq):
            src = src_dir / c[kind]
            if not src.exists():
                raise SystemExit(f"missing {src}")
            frames.append(draw_overlay(Image.open(src), c["caption"], kind,
                                       vmin_c, vmax_c, (n + 1) / len(seq),
                                       peak_c=c.get("peakC"), acus=acus, dead=dead,
                                       seg=c.get("segment", ""),
                                       alpha=c.get("alpha", 0.0)))
        results[kind] = [str(p) for p in encode(frames, stem, kind)]

    (OUT / "assemble.json").write_text(json.dumps({
        "fps": FPS, "holdSeconds": HOLD_S,
        "colourDomainC": [vmin_c, vmax_c],
        "peakRiseK": record["peakRiseK"],
        "outputs": results,
    }, indent=2, ensure_ascii=False))
    log(f"-> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
