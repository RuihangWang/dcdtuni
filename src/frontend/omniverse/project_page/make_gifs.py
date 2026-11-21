#!/usr/bin/env python3
"""Build the animated GIFs the slide deck uses.

A talk can show motion where the page could only show a toggle, so the three things a
reader had to click for become loops:

  hotspot_*.gif the hotspot appearing and being removed — built upstream by
                cfd_demo/scripts/hotspot_assemble.py from real Omniverse renders, and
                only copied in here. It replaced a cross-fade between two stills whose
                whole difference was 0.94 K: real, but invisible from the back of a room.
  viz.gif       the five Omniverse visualisations in sequence (geometry, temperature,
                velocity, ROI clip, streamlines), i.e. what the viewport actually does.
  tta.gif       the pivot pool splitting across 1 -> 2 -> 6 ranks.

GIF, not video: PowerPoint plays animated GIFs in slideshow view with no codec or linked
file to go missing, and python-pptx embeds them as ordinary picture parts.

Frame budget is deliberate. Every frame is a full palette-quantised image inside the
.pptx, so these hold at ~900 px wide with short cross-fades rather than smooth 30 fps.
"""
import os
import pathlib
import shutil
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "public" / "assets"
OUT = pathlib.Path(os.environ.get("PPT_DIR", HERE / "export" / "ppt")) / "assets"
OUT.mkdir(parents=True, exist_ok=True)

WIDE = 900          # GIF width; height follows the source aspect
# Office-safe is irrelevant here — the label is rasterised into the GIF, so only this
# machine needs the font. Noto Sans CJK ships with the distro.
CJK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"


def label(im, text, colour):
    """Burn a state caption into a frame.

    A caption says WHAT is on screen without touching the pixels that carry the evidence.
    Only `build_viz` needs it now — the hotspot GIFs arrive with their captions already
    burnt in by the assembler that rendered them.
    """
    im = im.copy()
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype(CJK, 30)
    except OSError:
        f = ImageFont.load_default()
    box = d.textbbox((0, 0), text, font=f)
    w, h = box[2] - box[0], box[3] - box[1]
    x, y = 26, im.height - h - 40
    d.rounded_rectangle([x - 14, y - 12, x + w + 14, y + h + 14], radius=9,
                        fill=(10, 12, 16), outline=colour, width=2)
    d.text((x, y - box[1]), text, font=f, fill=colour)
    return im


def log(m): print(f"[gif] {m}", flush=True)


def load(name, width=WIDE):
    im = Image.open(SRC / name).convert("RGB")
    h = round(im.height * width / im.width)
    return im.resize((width, h), Image.LANCZOS)


def save(frames, name, durations):
    """Write an animated GIF.

    optimize=False is intentional: Pillow's optimiser rewrites frames against a shared
    palette and on these dark renders that produced visible banding in the volume.
    """
    path = OUT / name
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=False, disposal=2)
    log(f"{name}  {len(frames)} 帧  {path.stat().st_size / 1048576:.1f} MB")
    return path


def quantise(im):
    # A shared adaptive palette per frame keeps the volume gradients smooth.
    return im.convert("P", palette=Image.ADAPTIVE, colors=256)


def build_cooling():
    """Publish the hotspot animations the deck uses.

    Nothing is composed here any more. The frames are real Omniverse viewport renders of
    three CFD steady states, captured and assembled by
    `cfd_demo/scripts/hotspot_record.py` + `hotspot_assemble.py`; this only copies the
    finished GIFs next to the deck's other assets. Rebuilding them means re-running that
    pipeline, not editing this file.
    """
    src = pathlib.Path(os.environ.get(
        "HOTSPOT_VIDEO", HERE.parents[3] / "case/GTC/forOVdemo/hotspot_video"))
    out = []
    for name in ("hotspot_thermal.gif", "hotspot_flow.gif"):
        p = src / name
        if not p.exists():
            log(f"缺 {p} —— 先跑 hotspot_assemble.py")
            continue
        dst = OUT / name
        shutil.copyfile(p, dst)
        log(f"{name}  {dst.stat().st_size / 1048576:.1f} MB  <- {p}")
        out.append(dst)
    return out


def build_viz():
    order = ["ov_geometry.png", "ov_temp.png", "ov_velocity.png",
             "ov_roi.png", "ov_streamlines.png"]
    names = ["几何 · OpenUSD + RTX", "温度场 · NVIDIA IndeX",
             "速度场", "ROI 裁剪", "流线 · GPU 上 RK4 积分"]
    imgs = [load(n) for n in order]
    ACC = (150, 230, 90)
    frames, durs = [], []
    for i, im in enumerate(imgs):
        frames.append(quantise(label(im, names[i], ACC))); durs.append(1700)
        nxt = imgs[(i + 1) % len(imgs)]
        for k in range(1, 5):
            frames.append(quantise(label(Image.blend(im, nxt, k / 5),
                                         names[(i + 1) % len(names)], ACC)))
            durs.append(70)
    return save(frames, "viz.gif", durs)


def build_tta(shots):
    """shots: the 1/2/6-card diagram captured from the live page."""
    imgs = []
    for p in shots:
        im = Image.open(p).convert("RGB")
        h = round(im.height * WIDE / im.width)
        imgs.append(im.resize((WIDE, h), Image.LANCZOS))
    # Pad every frame to the tallest so the loop does not jump vertically between
    # card counts (the 6-card diagram is shorter than the 1-card one).
    H = max(i.height for i in imgs)
    padded = []
    for im in imgs:
        canvas = Image.new("RGB", (WIDE, H), (18, 21, 27))
        canvas.paste(im, (0, 0))
        padded.append(canvas)
    imgs = padded
    frames, durs = [], []
    for im in imgs:
        frames.append(quantise(im)); durs.append(1800)
    return save(frames, "tta.gif", durs)


if __name__ == "__main__":
    build_cooling()
    build_viz()
    shots = sys.argv[1:]
    if len(shots) == 3:
        build_tta(shots)
    else:
        log("跳过 tta.gif（未提供 1/2/6 卡的三张截图）")
