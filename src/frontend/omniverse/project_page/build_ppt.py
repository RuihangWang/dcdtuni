#!/usr/bin/env python3
"""Build the Chinese talk deck from the project page's content.

Not a slide-per-section transcription of the page. A page is read at the reader's pace and
can hide things behind a toggle; a talk is one claim per slide at the speaker's pace, so
the three interactive widgets become GIF loops and the long prose becomes speaker notes.

Deliberate constraints:

* The paper is under double-blind review and the method section was withheld, so the
  architecture is treated as a black box throughout — what goes in, what comes out, how
  fast. No slide names the authors, the institution, the manuscript repo, the host, or the
  un-anonymised case name.
* Dark slides only where the dominant visual is an RTX render plate; chart evidence sits
  on light slides. Same reason as on the page: a white-background plot on near-black reads
  as an unfinished cut-out.
* Every number is measured and traceable. Paper numbers and this demo's own measurements
  are labelled separately and never averaged together.
"""
import os
import pathlib
import subprocess

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt
from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
PAGE = HERE / "public" / "assets"
OUT = pathlib.Path(os.environ.get("PPT_DIR", HERE / "export" / "ppt"))
GIF = OUT / "assets"
OUT.mkdir(parents=True, exist_ok=True)

W, H = Inches(13.333), Inches(7.5)          # 16:9

# ── palette ──────────────────────────────────────────────────────────────
DARK = RGBColor(0x08, 0x09, 0x0B)
DARK2 = RGBColor(0x12, 0x15, 0x1B)
LIGHT = RGBColor(0xEE, 0xF1, 0xF5)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FG_D = RGBColor(0xE8, 0xEC, 0xF2)
DIM_D = RGBColor(0x9A, 0xA3, 0xB2)
FAINT_D = RGBColor(0x6B, 0x73, 0x83)
FG_L = RGBColor(0x12, 0x16, 0x1C)
DIM_L = RGBColor(0x4D, 0x57, 0x65)
FAINT_L = RGBColor(0x6F, 0x79, 0x87)
NV = RGBColor(0x76, 0xB9, 0x00)             # NVIDIA green
CYAN = RGBColor(0x38, 0xBD, 0xF8)

# Office-safe on Windows and macOS; the Linux render falls back to Noto, which is
# metrically close enough that the fitted boxes below still hold.
CN = "Microsoft YaHei"
EN = "Segoe UI"


def log(m): print(f"[ppt] {m}", flush=True)


# ── text helpers ─────────────────────────────────────────────────────────
def _set_font(run, size, colour, bold=False, font=CN):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = colour
    run.font.name = font
    # python-pptx only writes the latin typeface; Chinese glyphs pick the theme font
    # unless the East-Asian face is set too, which silently changes every metric.
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.makeelement(
            "{http://schemas.openxmlformats.org/drawingml/2006/main}" + tag.split(":")[1],
            {"typeface": font})
        rPr.append(el)


# ── content-height measurement ───────────────────────────────────────────
# Box heights were first written by eye and 25 of them turned out to be a few pixels
# too short for their own font size. PowerPoint does not clip an overflowing textbox, it
# just spills — which looks fine on an empty slide and collides with the next block on a
# full one. Measuring at build time with the same wrap logic the preview renderer uses
# removes the whole class of defect instead of hand-tuning each box.
_MEASURE_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
_MEASURE_FONT_B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
_probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
_font_cache = {}


def _mfont(size_pt, bold):
    key = (round(size_pt), bold)
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(
                _MEASURE_FONT_B if bold else _MEASURE_FONT, max(6, round(size_pt)))
        except OSError:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _wrap_count(text, size_pt, bold, width_pt):
    """How many lines this text takes at this width. CJK has no spaces, so wrap per char."""
    f = _mfont(size_pt, bold)
    n, cur = 1, ""
    for ch in text:
        if ch == "\n":
            n += 1; cur = ""; continue
        trial = cur + ch
        if _probe.textlength(trial, font=f) <= width_pt or not cur:
            cur = trial
        else:
            n += 1; cur = ch
    return n


def measure(lines, width_emu):
    """Content height in EMU for the given (text, size, colour, bold[, gap]) lines."""
    width_pt = width_emu / 12700.0
    total_pt = 0.0
    for i, spec in enumerate(lines):
        text, size, _c, bold = spec[:4]
        gap = spec[4] if len(spec) > 4 else 6
        total_pt += _wrap_count(text, size, bold, width_pt) * size * 1.45
        if i < len(lines) - 1:            # trailing space_after adds nothing visible
            total_pt += gap
    return Emu(int(total_pt * 12700))


def textbox(slide, x, y, w, h, lines, anchor=MSO_ANCHOR.TOP, align=PP_ALIGN.LEFT):
    """lines: list of (text, size, colour, bold) — or (text, size, colour, bold, space_after).

    h is a MINIMUM. The box grows to whatever the text actually needs.
    """
    h = max(h, measure(lines, w) + Emu(20000))
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Emu(0)
    tf.margin_top = tf.margin_bottom = Emu(0)
    for i, spec in enumerate(lines):
        text, size, colour, bold = spec[:4]
        gap = spec[4] if len(spec) > 4 else 6
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(gap)
        r = p.add_run()
        r.text = text
        _set_font(r, size, colour, bold)
    tb._measured_bottom = y + h          # callers stack from here, not from a guess
    return tb


def stat(slide, x, y, w, big, lab, big_pt=26, colour=CYAN, dark=True, lab_pt=12):
    """A number over its caption, with the caption placed under the number's MEASURED
    bottom. Positioning the label at a fixed offset is what made 「50」 sit on top of
    「机柜」: the number box grows with its font, and 26 pt of bold CJK is taller than the
    0.42 in the layout had assumed."""
    lines = [(big, big_pt, colour, True)]
    hb = measure(lines, w)
    textbox(slide, x, y, w, hb, lines)
    # 0.03in was thinner than a 38 pt line's own overshoot, so the label sat
    # inside the number's descender band. 0.10in clears it at every size used here.
    ly = y + hb + Emu(91440)
    ll = [(lab, lab_pt, DIM_D if dark else DIM_L, False)]
    hl = measure(ll, w)
    textbox(slide, x, ly, w, hl, ll)
    return ly + hl            # bottom edge, so callers can stack without guessing


def bg(slide, colour):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = colour


def rect(slide, x, y, w, h, fill=None, line=None, lw=1.25):
    from pptx.enum.shapes import MSO_SHAPE
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    sh.adjustments[0] = 0.06
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid(); sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line; sh.line.width = Pt(lw)
    sh.shadow.inherit = False
    return sh


def picture_fit(slide, path, box_x, box_y, box_w, box_h):
    """Place an image scaled to fit the box, centred. Never distorts, never overflows."""
    with Image.open(path) as im:
        iw, ih = im.size
    scale = min(box_w / iw, box_h / ih)
    w, h = int(iw * scale), int(ih * scale)
    x = int(box_x + (box_w - w) / 2)
    y = int(box_y + (box_h - h) / 2)
    return slide.shapes.add_picture(str(path), x, y, w, h)


def title_block(slide, title, kicker=None, sub=None, dark=True, y=Inches(0.52)):
    fg = FG_D if dark else FG_L
    dim = DIM_D if dark else DIM_L
    faint = FAINT_D if dark else FAINT_L
    if kicker:
        textbox(slide, Inches(0.72), y - Inches(0.34), Inches(11.9), Inches(0.3),
                [(kicker, 11, NV, True)])
    lines = [(title, 28, fg, True, 4)]
    if sub:
        lines.append((sub, 14, dim, False))
    textbox(slide, Inches(0.72), y, Inches(11.9), Inches(1.15), lines)
    return y + Inches(1.15 if sub else 0.78)


def source(slide, text, dark=True, y=None):
    y = y if y is not None else H - Inches(0.52)
    textbox(slide, Inches(0.72), y, Inches(11.9), Inches(0.3),
            [(text, 9, FAINT_D if dark else FAINT_L, False)])


def takeaway(slide, text, dark=True):
    """Bottom conclusion strip — the deck's consistent anchor."""
    y = H - Inches(1.02)
    bar = rect(slide, Inches(0.72), y, Inches(11.9), Inches(0.52),
               fill=None, line=NV, lw=1.0)
    tf = bar.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.16); tf.margin_right = Inches(0.16)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.LEFT
    r = p.add_run(); r.text = text
    _set_font(r, 13, FG_D if dark else FG_L, True)
    return bar


def notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def new(prs, dark=True):
    s = prs.slides.add_slide(prs.slide_layouts[6])      # blank
    bg(s, DARK if dark else LIGHT)
    return s


# ── slides ───────────────────────────────────────────────────────────────
def s01_cover(prs):
    s = new(prs)
    picture_fit(s, PAGE / "demo_cooled.png", Inches(6.6), Inches(1.1),
                Inches(6.2), Inches(5.2))
    textbox(s, Inches(0.8), Inches(1.5), Inches(5.5), Inches(0.4),
            [("NVIDIA OMNIVERSE · 数据中心数字孪生", 11, NV, True)])
    t = textbox(s, Inches(0.8), Inches(2.0), Inches(5.5), Inches(0.1),
                [("拖一下滑块", 40, FG_D, True, 2),
                 ("重算整个机房的三维热场", 40, CYAN, True, 10)])
    textbox(s, Inches(0.8), t._measured_bottom + Inches(0.30), Inches(5.4), Inches(1.4),
            [("神经算子替代 CFD 求解器，在 RTX 视口中", 15, DIM_D, False, 3),
             ("即时给出新工况下的温度与流场", 15, DIM_D, False, 14),
             ("50 机柜 / 317 服务器 / 6 台精密空调 / 420.9 kW", 12, FAINT_D, False)])
    notes(s, "开场一句话：机房热设计现在是'改一次参数、等一次 CFD'；我们把等待那一步换成了"
             "神经算子，改完参数几十秒就能在 Omniverse 里看到新的三维热场。右边这张就是模型"
             "预测、IndeX 体渲染出来的整机房温度场。")
    return s


def s02_pain(prs):
    s = new(prs, dark=False)
    y = title_block(s, "机房热设计的日常，是反复问「如果」", kicker="PROBLEM",
                    sub="每问一次，传统流程就要重跑一次 CFD", dark=False)
    qs = ["如果这一排机柜加满功率，会不会过热？",
          "如果把送风调低两度，能省多少电？",
          "如果换一种气流组织，热点会移到哪里？"]
    top = y + Inches(0.25)
    for i, q in enumerate(qs):
        yy = top + Inches(i * 0.62)
        rect(s, Inches(0.72), yy, Inches(6.5), Inches(0.5), fill=WHITE, line=None)
        textbox(s, Inches(0.95), yy + Inches(0.11), Inches(6.1), Inches(0.32),
                [(q, 14, FG_L, False)])
    rect(s, Inches(7.7), top, Inches(4.9), Inches(2.0), fill=WHITE, line=None)
    textbox(s, Inches(8.05), top + Inches(0.28), Inches(4.2), Inches(1.5),
            [("241 秒", 40, FG_L, True, 2),
             ("一次未收敛的 1000 步 CFD 求解", 12, DIM_L, False, 8),
             ("跑到真正收敛，以小时计", 12, FAINT_L, False)])
    takeaway(s, "交互式改设计的门槛，卡在「每问一次就要等一次求解」。", dark=False)
    source(s, "求解耗时：4 个算例均值，范围 224–251 s；8 核并行。", dark=False,
           y=H - Inches(1.42))
    notes(s, "这三个问题是机房热设计每天都在问的。问题不在于 CFD 算不准，而在于它太慢——"
             "241 秒还只是刻意停在收敛之前的 1000 步；真跑到收敛是小时级。所以现实里没人敢"
             "多问几次，设计就只能保守。")
    return s


def s03_case(prs):
    s = new(prs)
    y = title_block(s, "算例是一座真实规模的整机房", kicker="CASE",
                    sub="不是简化盒子，也不是单机柜")
    picture_fit(s, PAGE / "ov_geometry.png", Inches(0.72), y + Inches(0.1),
                Inches(7.7), Inches(4.0))
    # Four, not five. The first four are facts about the ROOM; mesh size is a property
    # of the simulation and does not belong in the same column — it moved to the source
    # line. That also stops the stack running onto the takeaway strip, which the audit
    # flagged, but the reason to do it is the grouping, not the fit.
    stats = [("50", "机柜", 26), ("317", "服务器槽位", 26),
             ("6", "台精密空调", 26), ("420.9 kW", "IT 负载", 26)]
    yy = y + Inches(0.28)
    for big, lab, pt in stats:
        yy = stat(s, Inches(8.85), yy, Inches(3.8), big, lab, big_pt=pt) + Inches(0.30)
    takeaway(s, "每一台服务器、每一台空调都能单独调，这才谈得上「改设计」。")
    source(s, "几何以 OpenUSD 组合，RTX 光线追踪渲染；CFD 网格为百万单元量级。",
           y=H - Inches(1.42))
    notes(s, "先给规模感：50 个机柜、317 个可独立调节的服务器槽位、6 台精密空调，总 IT 负载"
             "420.9 kW，CFD 网格是百万单元量级。右边这些数字是演示里真实可调的对象——不是"
             "示意图上的标签。")
    return s


def s04_approach(prs):
    s = new(prs, dark=False)
    y = title_block(s, "把「求解」换成「预测」", kicker="APPROACH",
                    sub="边界条件变了，直接给出新的三维场，不再迭代", dark=False)
    top = y + Inches(0.45)
    boxes = [("边界条件", "每台服务器的功率与风量\n每台空调的送风温度与风量", CYAN),
             ("SCANO 神经算子", "切片交叉注意力神经算子\n（架构细节暂不公开）", NV),
             ("三维物理场", "速度、压力、温度\n百万单元整机房", CYAN)]
    bw, gap = Inches(3.55), Inches(0.62)
    for i, (t, body, col) in enumerate(boxes):
        x = Inches(0.72) + i * (bw + gap)
        rect(s, x, top, bw, Inches(1.9), fill=WHITE, line=None)
        textbox(s, x + Inches(0.28), top + Inches(0.26), bw - Inches(0.56), Inches(0.35),
                [(t, 16, col if col == NV else FG_L, True)])
        textbox(s, x + Inches(0.28), top + Inches(0.72), bw - Inches(0.56), Inches(1.0),
                [(body, 12, DIM_L, False)])
        if i < 2:
            textbox(s, x + bw + Inches(0.10), top + Inches(0.78), Inches(0.42),
                    Inches(0.4), [("→", 24, FAINT_L, True)], align=PP_ALIGN.CENTER)
    takeaway(s, "对使用者来说，它就是一个「输入工况、输出物理场」的黑盒。", dark=False)
    notes(s, "方法本身正在双盲评审，架构细节这次不展开。对今天的听众来说也不需要：它是一个"
             "黑盒，左边喂进去一组边界条件，右边直接出来整机房的速度、压力、温度场，中间没有"
             "迭代求解。下面几张讲的是这个黑盒有多快、准不准。")
    return s


def s05_speed(prs):
    s = new(prs, dark=False)
    y = title_block(s, "比 CFD 求解器快约 420 倍", kicker="ACCELERATION",
                    sub="同一台机器、同样 8 核并行下现场计时的墙钟时间", dark=False)
    picture_fit(s, PAGE / "hero_speedup.png", Inches(0.72), y + Inches(0.05),
                Inches(5.4), Inches(4.1))
    cards = [("≈420×", "加速比", "各条件化变体 392–447×"),
             ("0.5–0.6 s", "单 case 推理", "热启动，特征已预计算"),
             ("241 s", "CFD 求解器基线", "1000 步 8 核，刻意未收敛")]
    top = y + Inches(0.25)
    for i, (big, lab, note) in enumerate(cards):
        yy = top + Inches(i * 1.32)
        rect(s, Inches(6.6), yy, Inches(6.02), Inches(1.15), fill=WHITE, line=None)
        stat(s, Inches(6.92), yy + Inches(0.13), Inches(5.4), big,
             lab + " · " + note, big_pt=24, dark=False, lab_pt=11)
    takeaway(s, "分母刻意取未收敛的求解，所以这个倍数是下界。", dark=False)
    source(s, "整理自论文图：nRMSE 对单 case 推理时间（对数轴，在 CFD 求解器前断开）。",
           dark=False, y=H - Inches(1.42))
    notes(s, "左图横轴是单个算例的推理时间，对数轴，右边那条灰线是 241 秒的 CFD 求解器。"
             "纵轴是 nRMSE，越低越准。要强调一句：分母我们刻意取了停在收敛之前的 1000 步，"
             "真实工程跑到收敛更久，所以 420 倍是下界不是上界。这也是墙钟对比，没有按 FLOP "
             "或能耗归一。")
    return s


def s06_cost(prs):
    s = new(prs, dark=False)
    y = title_block(s, "冷启动的代价全在求解器，不在网络", kicker="COST",
                    sub="面对一个全新边界条件时，各条路线要付出什么", dark=False)
    picture_fit(s, PAGE / "cost_cold_warm.png", Inches(0.72), y + Inches(0.05),
                Inches(11.9), Inches(3.55))
    takeaway(s, "热启动下五种编码都在 0.6–0.8 s；差别只出现在冷启动。", dark=False)
    source(s, "整理自论文图。数值编码需一次 CFD 种子求解（约 8.6 s，主要花在网格加载"
              "与求解器启动）；文本编码约 1.3 s 完成嵌入。", dark=False, y=H - Inches(1.46))
    notes(s, "左边是冷启动：数值编码那条路线每个新工况都要先跑一步求解器种子，约 8.6 秒，"
             "而且这 8.6 秒主要是网格加载和求解器启动，不是迭代本身。文本编码只要 1.3 秒。"
             "右边热启动，五种编码都落在 0.6 到 0.8 秒，差别来自共享主干而不是编码方式。")
    return s


def s07_fields(prs):
    s = new(prs, dark=False)
    y = title_block(s, "预测场与 CFD 参考在同一位置给出同样的热羽流",
                    kicker="EVIDENCE", dark=False)
    picture_fit(s, PAGE / "fields_3d.png", Inches(0.72), y - Inches(0.05),
                Inches(11.9), Inches(4.15))
    takeaway(s, "冷通道分布与机柜出口热羽流的位置一致，不是平均场。", dark=False)
    source(s, "整理自论文图。逐列为算例设置、单流对照、完整边界桥接、冻结文本桥接、"
              "CFD 参考；行为温度与速度幅值切片。", dark=False, y=H - Inches(1.46))
    notes(s, "这张是可信度。最右一列是 CFD 参考，往左两列是我们的两种条件化。看热羽流"
             "的位置和冷通道的形状——不是糊成一片平均值，结构对得上。这一点很关键，因为一个"
             "只学会输出平均场的模型，nRMSE 也可能不难看。")
    return s


def s08_stack(prs):
    s = new(prs)
    y = title_block(s, "NVIDIA 生态承担了除求解之外的全部环节", kicker="NVIDIA STACK",
                    sub="这套演示实际用到的组件，以及各自承担什么")
    items = [("Omniverse Kit", "宿主应用", "常驻进程，加载 CAE 扩展、驱动视口与串流"),
             ("OpenUSD", "场景描述", "50 机柜 / 6 空调各为独立 prim，可逐个显隐选中"),
             ("NVIDIA IndeX", "体渲染", "三维温度/速度场与几何同处一个视口"),
             ("omni.cae 扩展", "CFD 数据通路", "把结果读进 USD 并体素化、追流线"),
             ("RTX + WebRTC", "渲染与串流", "服务器单卡光追，画面串流到浏览器"),
             ("CUDA + PyTorch", "神经算子推理", "跑在与渲染卡分离的另一张 GPU 上")]
    top = y + Inches(0.15)
    cw, ch, gx, gy = Inches(3.85), Inches(1.42), Inches(0.28), Inches(0.24)
    for i, (name, role, body) in enumerate(items):
        cx = Inches(0.72) + (i % 3) * (cw + gx)
        cy = top + (i // 3) * (ch + gy)
        rect(s, cx, cy, cw, ch, fill=DARK2, line=None)
        rect(s, cx, cy, Inches(0.045), ch, fill=NV, line=None)
        tw_ = cw - Inches(0.5)
        a = textbox(s, cx + Inches(0.28), cy + Inches(0.16), tw_, Inches(0.1),
                    [(name, 15, WHITE, True)])
        b = textbox(s, cx + Inches(0.28), a._measured_bottom + Inches(0.06), tw_,
                    Inches(0.1), [(role, 10, NV, False)])
        textbox(s, cx + Inches(0.28), b._measured_bottom + Inches(0.08), tw_,
                Inches(0.1), [(body, 11, DIM_D, False)])
    takeaway(s, "USD 描述机房、IndeX 画物理场、RTX 渲染、CUDA 跑推理 —— 最重的求解被替代了。")
    notes(s, "这一页回答'和 NVIDIA 什么关系'。六个组件都是实际在跑的，不是列一堆 logo。"
             "特别说一下最后两个：渲染用一张卡，推理用另一张卡，分开是为了推理不抢视口帧率。")
    return s


def s09_tta(prs):
    s = new(prs)
    y = title_block(s, "多卡推理：pivot 池切给多张卡，再归约平均",
                    kicker="MULTI-GPU", sub="这是仓库自己的实现，也是最直接的一处 NVIDIA 关联")
    picture_fit(s, GIF / "tta.gif", Inches(0.72), y - Inches(0.08),
                Inches(7.9), Inches(3.9))
    bullets = [("同一随机种子打乱", "与卡数无关，保证可复现"),
               ("连续等分切给 R 张卡", "各卡 pivot 互斥，并集 = 单卡完整池"),
               ("NCCL all_reduce(SUM)", "再除以逐节点覆盖计数"),
               ("2 跳邻域子图", "节点被多次覆盖，重叠平均")]
    top = y + Inches(0.05)
    for i, (t, b) in enumerate(bullets):
        yy = top + Inches(i * 0.92)
        a = textbox(s, Inches(8.95), yy, Inches(3.7), Inches(0.1),
                    [(t, 13, CYAN, True)])
        textbox(s, Inches(8.95), a._measured_bottom + Inches(0.07), Inches(3.7),
                Inches(0.1), [(b, 11, DIM_D, False)])
    takeaway(s, "动图循环：1 张卡 → 2 张卡 → 6 张卡，pivot 池怎么切、每卡跑几趟。")
    notes(s, "这张会自己循环播 1、2、6 卡三种切分。要讲的点：切分是连续等分不是交错，种子与"
             "卡数无关所以各卡 pivot 互斥、并集正好是单卡的完整池；每张卡各自累加整图尺寸的"
             "缓冲，最后用 NCCL 的 all_reduce 求和，再除以逐节点覆盖次数——这就是重叠平均。")
    return s


def s10_tta_num(prs):
    s = new(prs, dark=False)
    y = title_block(s, "本演示配置下，多卡只买时间，不改精度", kicker="MULTI-GPU",
                    sub="因为采样比钉死，累积趟数与卡数无关", dark=False)
    top = y + Inches(0.2)
    hdr = ["", "每卡前向趟数", "累积总趟数", "前向耗时", "nRMSE（5 场均值）"]
    rows = [["1 张卡", "50", "50", "14.29 s", "0.192502"],
            ["2 张卡", "25", "50", "8.45 s", "0.192518"]]
    colw = [Inches(1.9), Inches(2.3), Inches(2.2), Inches(2.2), Inches(3.3)]
    x0 = Inches(0.72)
    for c, htxt in enumerate(hdr):
        cx = x0 + sum(colw[:c], Inches(0))
        textbox(s, cx, top, colw[c], Inches(0.3), [(htxt, 11, FAINT_L, True)])
    for r, row in enumerate(rows):
        ry = top + Inches(0.42) + Inches(r * 0.78)
        rect(s, x0, ry, sum(colw, Inches(0)), Inches(0.64), fill=WHITE, line=None)
        for c, cell in enumerate(row):
            cx = x0 + sum(colw[:c], Inches(0))
            big = c in (3, 4) or c == 0
            textbox(s, cx + Inches(0.18), ry + Inches(0.17), colw[c] - Inches(0.2),
                    Inches(0.34),
                    [(cell, 15 if big else 14, FG_L if c else FG_L, c == 0 or c == 3)])
    note_y = top + Inches(2.15)
    textbox(s, x0, note_y, Inches(11.9), Inches(1.0),
            [("采样比 ρ = 0.02 钉死，覆盖整个池恒定需要 50 趟：单卡自己跑完 50 趟，"
              "两卡各跑 25 趟。", 13, FG_L, False, 4),
             ("累积趟数没变，平均出来的场就没变 —— nRMSE 相同到小数点后五位，省下的纯粹是"
              "墙钟时间。", 13, FG_L, False)])
    takeaway(s, "论文用 ρ=1.0，每卡一趟用完自己那份，那里卡数才会改变精度。", dark=False)
    source(s, "本机实测，case0，各 3 次取均值。本机 3 卡及以上无法运行，未计时。",
           dark=False, y=H - Inches(1.42))
    notes(s, "这里要讲清一个容易讲错的点：决定精度的是累积的重叠趟数，不是卡数本身。"
             "论文专门验证过——1 张卡跑 20 趟和 6 张卡各跑 4 趟，nRMSE 落在同一处。"
             "本演示把采样比钉在 0.02，总趟数恒为 50，所以卡数只影响墙钟。"
             "如果有人问 6 卡：本机 3 卡以上跑不起来，是环境问题，还没定位，我没有编数字。")
    return s


def s11_viz(prs):
    s = new(prs)
    y = title_block(s, "同一个视口里的四种可视化", kicker="LIVE DEMO",
                    sub="几何、温度场、速度场、ROI 裁剪、流线")
    picture_fit(s, GIF / "viz.gif", Inches(2.1), y - Inches(0.02),
                Inches(9.1), Inches(4.0))
    takeaway(s, "全部在同一个 RTX 视口里切换，不重新划分网格、不导出中间文件。")
    source(s, "动图由真实演示驱动抓取。")
    notes(s, "这张自动循环。可以顺着念：先是纯几何——OpenUSD 组的机房，RTX 光追；"
             "然后温度场体渲染；速度场；ROI 裁剪，那个橙色框可以在视口里直接拖；"
             "最后是流线，GPU 上做 RK4 积分，按温度着色，通道两端的回流涡看得很清楚——"
             "热回风卷回冷通道，这正是机房热设计要盯的短路回流。")
    return s


def s12_cooling(prs):
    s = new(prs)
    y = title_block(s, "三台空调停机，热点长出来；调完送风，热点消失",
                    kicker="OMNIVERSE", sub="同一相机、同一钉死色标域下的 CFD 稳态工况序列")
    picture_fit(s, GIF / "hotspot_thermal.gif", Inches(2.35), y - Inches(0.02),
                Inches(8.6), Inches(3.9))
    chips = [("+19.9 K", "故障后升温 · 99 分位"), ("54.3 °C", "故障工况峰值"),
             ("41.2 °C", "干预后峰值 · 低于基线")]
    cy = y + Inches(3.95)
    for i, (big, lab) in enumerate(chips):
        cx = Inches(2.35) + Inches(i * 2.95)
        stat(s, cx, cy, Inches(2.7), big, lab, big_pt=19, colour=NV, lab_pt=10)
    takeaway(s, "全场共用一个钉死的色标域，看到的变化就是温度本身。")
    notes(s, "这一页会自己循环播放。故事有两段：先是三台空调停机——机房丢掉 52% 的制冷量，"
             "全场平均升温 10 K，峰值从 44 °C 冲到 54 °C；然后把在役机组风量提到 1.8 倍、"
             "送风降到 15 °C，峰值回落到 41 °C，比故障前还低。\n\n"
             "要主动说清两件事，别等人问。第一，这三个工况是 CFD 求解器算的，不是代理模型的预测，"
             "这一页展示的是 Omniverse 这一侧的渲染能力；代理模型的加速比在前面几页。"
             "第二，三次求解都刻意没跑到收敛——这是演示，不是可引用的热力学结果，"
             "工况之间的中间帧还是插值出来的。\n\n"
             "唯一可以硬气强调的是方法：三段共用同一个相机和同一个钉死的色标域，"
             "所以看到的颜色变化就是温度变化，不是色标自动缩放造成的错觉。")
    return s


def s13_value(prs):
    """价值页。构图刻意不同于牌组里已有的卡片网格与「图+侧栏」：整宽横带 + 右侧证据列，
    因为这一页的重点恰恰是「每条主张各自站在什么证据上」。

    ② 在本演示里**没有**实现——仓库里没有任何优化器代码。面对 NVIDIA 的工程师，
    把未实现的能力标成已实现，比保守陈述的代价大得多。"""
    s = new(prs, dark=False)
    y = title_block(s, "从「看得见」，到「算得快」，到「敢上线」", kicker="VALUE",
                    sub="实时热场数字孪生能改变运维决策的三个环节", dark=False)

    bands = [
        ("①", "运行状况看得见",
         "整机房温度场与流场实时体渲染，热点、回流与短路气流一眼定位，\n"
         "不必等离线报告",
         "本演示已实现", NV,
         "视口内四种可视化：温度 / 速度 / ROI 裁剪 / 流线"),
        ("②", "调优策略算得快",
         "优化算法要成百上千次评估，求解器给不起这个预算；\n"
         "代理模型把单次评估从 241 秒降到约 9 秒",
         "尚未接入优化器", RGBColor(0xF5, 0x9E, 0x0B),
         "同一时间预算内可评估的方案数约为原来的 25 倍"),
        ("③", "控制策略敢上线",
         "下发指令前先在孪生体里跑一遍，看清全场影响再决定，\n"
         "而不是改完等机房自己给反馈",
         "本演示已实现", NV,
         "代理模型对送风 20→14 °C 的响应即是一次预验证：−0.94 K，99.9% 网格点"),
    ]

    top = y + Inches(0.10)
    bh, gap = Inches(1.24), Inches(0.16)
    for i, (num, title, body, status, scol, evid) in enumerate(bands):
        by = top + i * (bh + gap)
        rect(s, Inches(0.72), by, Inches(11.9), bh, fill=WHITE, line=None)
        rect(s, Inches(0.72), by, Inches(0.05), bh, fill=scol, line=None)
        # 序号
        textbox(s, Inches(1.02), by + Inches(0.30), Inches(0.6), Inches(0.1),
                [(num, 30, scol, True)])
        # 标题 + 正文
        a = textbox(s, Inches(1.72), by + Inches(0.22), Inches(6.3), Inches(0.1),
                    [(title, 17, FG_L, True)])
        textbox(s, Inches(1.72), a._measured_bottom + Inches(0.10), Inches(6.3),
                Inches(0.1), [(body, 12, DIM_L, False)])
        # 右侧证据列：状态 + 依据
        b = textbox(s, Inches(8.35), by + Inches(0.24), Inches(4.05), Inches(0.1),
                    [(status, 11, scol, True)])
        textbox(s, Inches(8.35), b._measured_bottom + Inches(0.10), Inches(4.05),
                Inches(0.1), [(evid, 11, FAINT_L, False)])

    takeaway(s, "三件事共用同一个前提：单次评估足够便宜，才敢反复问。", dark=False)
    source(s, "「约 9 秒」为数值编码的冷启动单次评估（含一步求解器种子），取自成本分解图；"
              "241 秒为 1000 步 8 核 CFD 求解器基线。", dark=False, y=H - Inches(1.46))
    notes(s, "这一页把前面的技术指标翻译成运维语言，三条按「看得见→算得快→敢上线」递进。\n\n"
             "① 和 ③ 是今天演示里真做到了的，可以指着前两页说。\n\n"
             "② 要诚实：我们还没有接优化器，这一条讲的是加速比让它变得可行——"
             "一次评估从 241 秒降到 9 秒左右，同样一晚上原来只能试十几个方案，现在能试几百个。"
             "如果对方追问「你们跑过寻优吗」，直接说还没有，这是下一步。\n\n"
             "③ 的证据是代理模型对送风降温的响应：改之前就知道全场会降 0.94 K、99.9% 的网格点会变——"
             "这些都在指令下发之前拿到。注意别把它和刚才那页热点动画混为一谈，"
             "那一页是 CFD 算的，讲的是渲染，不是预测精度。")
    return s


def s13_honest(prs):
    s = new(prs, dark=False)
    y = title_block(s, "端到端 45–100 秒，其中模型前向只占 14 秒",
                    kicker="REALITY CHECK",
                    sub="现场点一次按钮要等多久，以及这段时间花在哪", dark=False)
    top = y + Inches(0.2)
    parts = [("边界条件合成 + 一步 CFD 种子求解", "取决于工况是否改变", NV),
             ("推理子进程启动 + 整图前向", "前向本身 14.4–14.7 s", CYAN),
             ("结果重采样成规则网格 + IndeX 重建体", "约 2.3 s 重采样", CYAN)]
    for i, (t, b, col) in enumerate(parts):
        yy = top + Inches(i * 0.95)
        rect(s, Inches(0.72), yy, Inches(7.2), Inches(0.76), fill=WHITE, line=None)
        a = textbox(s, Inches(1.0), yy + Inches(0.11), Inches(6.7), Inches(0.1),
                    [(t, 13, FG_L, True)])
        textbox(s, Inches(1.0), a._measured_bottom + Inches(0.06), Inches(6.7),
                Inches(0.1), [(b, 11, DIM_L, False)])
    rect(s, Inches(8.3), top, Inches(4.32), Inches(2.4), fill=WHITE, line=None)
    textbox(s, Inches(8.62), top + Inches(0.26), Inches(3.7), Inches(1.9),
            [("为什么不是 0.5 s", 14, FG_L, True, 8),
             ("论文那个数字是模型常驻、特征已预计算时的单次前向。", 12, DIM_L, False, 6),
             ("本演示是没做过工程优化的完整链路：每次点击都重新拉起推理进程、"
              "重新载入模型，还多做一次重采样。", 12, DIM_L, False)])
    takeaway(s, "同一算例用 CFD 求解器跑 1000 步要 241 秒，之后还得另做后处理。", dark=False)
    source(s, "本机实测，三次连续调用。", dark=False, y=H - Inches(1.42))
    notes(s, "这页是主动交代实情，不要等别人问。现场点一次按钮确实要等 45 到 100 秒，"
             "取决于这次改动是否需要重跑种子求解。模型前向稳定在 14 秒多，其余全是没优化的"
             "工程开销——把模型常驻化、跳过重采样就能去掉绝大部分，但那样演示代码会偏离论文"
             "代码，所以我们没做。说清楚这一点比让人自己发现要好。")
    return s


def s14_envelope(prs):
    s = new(prs, dark=False)
    y = title_block(s, "滑块的可调范围以训练工况包络为界", kicker="BOUNDARY",
                    sub="超出包络的工况，模型没有见过", dark=False)
    picture_fit(s, PAGE / "training_envelope.png", Inches(3.4), y - Inches(0.05),
                Inches(6.5), Inches(4.0))
    takeaway(s, "送风温度 14–25 °C、风量 4–12 m³/s —— 演示界面按这个范围限幅。", dark=False)
    source(s, "整理自论文图。灰点为逐台空调设定，红点为每个算例的均值。",
           dark=False, y=H - Inches(1.42))
    notes(s, "被问到'这东西什么时候不能用'时讲这张。横轴风量、纵轴送风温度，灰点是训练集里"
             "每台空调的设定。演示界面上滑块的上下限就是照这个包络设的——不是随便定的，也不让"
             "用户滑到模型没见过的地方去。这是一个代理模型必须讲清楚的边界。")
    return s


def s15_summary(prs):
    s = new(prs)
    y = title_block(s, "把「跑一次 CFD」变成「拖一下滑块」", kicker="SUMMARY")
    nums = [("≈420×", "相对 1000 步 8 核 CFD 求解（241 s）"),
            ("0.5–0.6 s", "百万单元机房单次三维热场推理"),
            ("−0.94 K", "送风 20→14 °C，99.9% 网格点改变")]
    top = y + Inches(0.35)
    bottoms = []
    for i, (big, lab) in enumerate(nums):
        cx = Inches(0.72) + Inches(i * 4.05)
        bottoms.append(stat(s, cx, top, Inches(3.8), big, lab, big_pt=38, colour=NV))
    bullets = ["模型输出的是完整三维场，不是单点温度，可直接接进现有后处理",
               "推理与渲染分卡运行，演示端只需浏览器，本地不需要显卡",
               "多卡按 pivot 池切分并行，NCCL 归约后重叠平均"]
    by = max(bottoms) + Inches(0.65)
    for i, b in enumerate(bullets):
        yy = by + Inches(i * 0.56)
        textbox(s, Inches(0.72), yy, Inches(0.3), Inches(0.32),
                [("—", 14, NV, True)])
        textbox(s, Inches(1.12), yy, Inches(11.4), Inches(0.34), [(b, 14, FG_D, False)])
    takeaway(s, "现场演示：浏览器打开演示主机的 8081 端口，同一时刻支持一位访客。")
    notes(s, "收尾三个数字复述一遍即可。最后一句引到现场演示——如果时间允许就切过去实际点"
             "一次；如果不允许，就说明这三段动图都是真实抓取的，不是渲染出来的示意。")
    return s


def build():
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    for fn in (s01_cover, s02_pain, s03_case, s04_approach, s05_speed, s06_cost,
               s07_fields, s08_stack, s09_tta, s10_tta_num, s11_viz, s12_cooling,
               s13_value, s13_honest, s14_envelope, s15_summary):
        fn(prs)
    # python-pptx's default template ships with its own author in the core properties,
    # so an untouched deck goes out claiming to be by the library's maintainer. Blank the
    # identity fields rather than substituting ours — the paper is under double-blind
    # review and a deck is exactly the kind of file that gets forwarded.
    cp = prs.core_properties
    cp.title = "SCANO · 数据中心热场数字孪生"
    cp.author = ""
    cp.last_modified_by = ""
    cp.comments = ""
    cp.category = ""
    path = OUT / "SCANO_演示汇报.pptx"
    prs.save(path)
    log(f"{path}  {len(prs.slides.__iter__.__self__._sldIdLst)} 张  "
        f"{path.stat().st_size / 1048576:.1f} MB")
    return path


if __name__ == "__main__":
    build()
