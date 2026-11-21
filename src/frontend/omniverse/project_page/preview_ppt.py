#!/usr/bin/env python3
"""Render an approximate preview of each slide, and audit it.

LibreOffice on this host cannot convert PPTX at all — it fails the same way on a
one-shape empty deck — so the usual "convert to PDF and look at it" QA path is closed.
The skill's fallback is to build the preview from the slide objects themselves, which is
what this does: walk every shape, draw its box, its picture, and its wrapped text with the
real font metrics, at 1280x720.

It is an approximation of PowerPoint's renderer, not a substitute for it. What it IS good
for is exactly the defects that matter here and that a structural check cannot see:

  * text that overflows its box (measured with the actual font, not guessed),
  * shapes that fall outside the slide,
  * boxes that overlap something they should not,
  * the slide-sorter view, to check the deck is not one layout repeated 15 times.

Anything it reports as a bound/overflow failure is real. Anything it renders prettily is
merely "not obviously broken".
"""
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.util import Emu

HERE = pathlib.Path(__file__).resolve().parent
DECK = HERE / "export" / "ppt" / "SCANO_演示汇报.pptx"
OUT = pathlib.Path("/tmp/ppt_preview")
OUT.mkdir(parents=True, exist_ok=True)

PX_W, PX_H = 1280, 720
CJK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
CJK_B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

issues = []


def log(m): print(f"[prev] {m}", flush=True)


def font(size_pt, bold):
    # 1 pt at 96 dpi over a 13.333in slide rendered to 1280 px -> 1 pt == 1.333 px
    px = max(8, round(size_pt * PX_W / (13.333 * 72)))
    try:
        return ImageFont.truetype(CJK_B if bold else CJK, px)
    except OSError:
        return ImageFont.load_default()


def wrap(draw, text, f, max_w):
    """Greedy wrap that works for CJK (no spaces) and latin alike."""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur); cur = ""; continue
        trial = cur + ch
        if draw.textlength(trial, font=f) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur); cur = ch
    if cur:
        lines.append(cur)
    return lines


def emu2px(v, total_emu, total_px):
    return round(v * total_px / total_emu)


def overlaps(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy


def render(prs, idx, slide):
    sw, sh = prs.slide_width, prs.slide_height
    painted = []          # (shape, x, y, w, painted_h, label) for text shapes
    boxes = []            # (x, y, w, h, kind) for everything drawable
    # Slide background: python-pptx exposes it awkwardly; read the solid fill if present.
    bgc = (8, 9, 11)
    try:
        f = slide.background.fill
        if f.type is not None and f.fore_color and f.fore_color.rgb:
            r = f.fore_color.rgb
            bgc = (r[0], r[1], r[2])
    except Exception:
        pass
    img = Image.new("RGB", (PX_W, PX_H), bgc)
    d = ImageDraw.Draw(img, "RGBA")

    for shp in slide.shapes:
        x = emu2px(shp.left, sw, PX_W); y = emu2px(shp.top, sh, PX_H)
        w = emu2px(shp.width, sw, PX_W); h = emu2px(shp.height, sh, PX_H)

        if shp.left < 0 or shp.top < 0 or shp.left + shp.width > sw or shp.top + shp.height > sh:
            issues.append((idx, "high", f"形状越出画布: {shp.shape_type} @({x},{y},{w},{h})"))

        kind = "pic" if shp.__class__.__name__ == "Picture" else "shape"
        boxes.append((x, y, w, h, kind, shp))
        if shp.shape_type == 13 or shp.__class__.__name__ == "Picture":     # PICTURE
            try:
                blob = shp.image.blob
                import io
                pic = Image.open(io.BytesIO(blob)).convert("RGB").resize(
                    (max(1, w), max(1, h)), Image.LANCZOS)
                img.paste(pic, (x, y))
            except Exception as e:
                d.rectangle([x, y, x + w, y + h], outline=(255, 0, 0), width=2)
                issues.append((idx, "high", f"图片无法解码: {e}"))
            continue

        # autoshape fill / line
        try:
            if shp.fill.type is not None and shp.fill.type != 5:      # 5 = background
                c = shp.fill.fore_color.rgb
                d.rounded_rectangle([x, y, x + w, y + h], radius=6,
                                    fill=(c[0], c[1], c[2]))
        except Exception:
            pass
        try:
            if shp.line.fill.type is not None and shp.line.fill.type != 5:
                c = shp.line.color.rgb
                d.rounded_rectangle([x, y, x + w, y + h], radius=6,
                                    outline=(c[0], c[1], c[2]), width=2)
        except Exception:
            pass

        if not shp.has_text_frame:
            continue
        tf = shp.text_frame
        ml = emu2px(tf.margin_left or 0, sw, PX_W)
        mr = emu2px(tf.margin_right or 0, sw, PX_W)
        tx, tw = x + ml, max(10, w - ml - mr)
        ty = y + emu2px(tf.margin_top or 0, sh, PX_H)
        total_h = 0
        for p in tf.paragraphs:
            txt = "".join(r.text for r in p.runs)
            if not txt:
                total_h += 6
                continue
            r0 = p.runs[0]
            size = r0.font.size.pt if r0.font.size else 18
            bold = bool(r0.font.bold)
            col = (232, 236, 242)
            try:
                if r0.font.color and r0.font.color.rgb:
                    c = r0.font.color.rgb; col = (c[0], c[1], c[2])
            except Exception:
                pass
            f = font(size, bold)
            lines = wrap(d, txt, f, tw)
            lh = round(size * PX_W / (13.333 * 72) * 1.42)
            for ln in lines:
                lx = tx
                if p.alignment is not None and str(p.alignment).startswith("CENTER"):
                    lx = tx + (tw - d.textlength(ln, font=f)) / 2
                d.text((lx, ty + total_h), ln, font=f, fill=col)
                total_h += lh
            total_h += round((p.space_after.pt if p.space_after else 6)
                             * PX_W / (13.333 * 72))
        # A PowerPoint textbox with autofit off does NOT clip — it spills. So "taller
        # than its own box" is cosmetic; what actually breaks a slide is the spill
        # landing on top of something else. Record the real painted extent and test
        # that instead of tuning a box-vs-content threshold until it passes.
        painted.append((shp, x, y, w, max(h, total_h), tf.text[:26]))

    # Does any text's painted extent land on a picture, or on another text block?
    for shp, tx0, ty0, tw0, th0, lab in painted:
        spill = th0 - emu2px(shp.height, sh, PX_H)
        if spill <= 2:
            continue
        declared = (tx0, ty0, tw0, emu2px(shp.height, sh, PX_H))
        for bx, by, bw, bh, kind, other in boxes:
            if other is shp:
                continue
            # A card drawn behind the text is not a collision — the text belongs inside
            # it. Skip any shape that already contains the text's declared box.
            if overlaps(declared, (bx, by, bw, bh)) > 0.9 * declared[2] * declared[3]:
                continue
            # Only the spilled strip can be the culprit; the declared box was intentional.
            strip = (tx0, ty0 + th0 - spill, tw0, spill)
            if overlaps(strip, (bx, by, bw, bh)) > 0.02 * tw0 * spill:
                issues.append((idx, "high",
                               f"文字溢出 {spill}px 并压到 {kind}: 「{lab}…」"))
                d.rectangle([tx0, ty0, tx0 + tw0, ty0 + th0],
                            outline=(255, 60, 60), width=2)
                break
        else:
            issues.append((idx, "low", f"文字溢出自身框 {spill}px 但未压到任何元素: 「{lab}…」"))
    return img


def main():
    prs = Presentation(DECK)
    imgs = []
    for i, s in enumerate(prs.slides, 1):
        im = render(prs, i, s)
        im.save(OUT / f"s{i:02d}.png")
        imgs.append(im)
    # Slide-sorter contact sheet: the skill asks for a scan at this scale to catch a deck
    # built from one repeated layout.
    cols, tw, th = 5, 300, 169
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * 8, rows * th + (rows + 1) * 8),
                      (26, 28, 33))
    for i, im in enumerate(imgs):
        sheet.paste(im.resize((tw, th), Image.LANCZOS),
                    (8 + (i % cols) * (tw + 8), 8 + (i // cols) * (th + 8)))
    sheet.save(OUT / "contact_sheet.png")
    log(f"渲染 {len(imgs)} 张 -> {OUT}")
    if issues:
        log(f"发现 {len(issues)} 条缺陷:")
        for idx, sev, msg in issues:
            log(f"  [S{idx:02d}] {sev.upper()}  {msg}")
    else:
        log("越界 / 溢出 / 图片解码 检查未发现问题")
    return 1 if any(s == "high" for _, s, _ in issues) else 0


if __name__ == "__main__":
    sys.exit(main())
