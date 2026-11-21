#!/usr/bin/env python3
"""Generate document.xml for AIFLUIDs 2026 paper from updated paper.md content."""

import xml.sax.saxutils as saxutils
import shutil, os, glob

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
IMG_DIR = "png"
MEDIA_DIR = "unpacked/word/media"
RELS_FILE = "unpacked/word/_rels/document.xml.rels"

# Page content width in EMU (6.30 inches for A4 with 0.98-inch margins per guidelines)
PAGE_W_EMU = 5760085


def esc(text):
    return saxutils.escape(text)


def run_xml(text, bold=False, italic=False, sz=None, superscript=False, font=True):
    rpr_parts = []
    if font:
        rpr_parts.append(
            '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
        )
    if bold:
        rpr_parts.append("<w:b/><w:bCs/>")
    if italic:
        rpr_parts.append("<w:i/><w:iCs/>")
    if sz:
        rpr_parts.append(f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>')
    if superscript:
        rpr_parts.append('<w:vertAlign w:val="superscript"/>')
    rpr = f'<w:rPr>{"".join(rpr_parts)}</w:rPr>' if rpr_parts else ""
    space = ' xml:space="preserve"' if text.startswith(" ") or text.endswith(" ") else ""
    return f"<w:r>{rpr}<w:t{space}>{esc(text)}</w:t></w:r>"


def para(runs_xml, center=False, justified=True, spacing_before=None, spacing_after=None,
         style=None, ind_left=None, ind_right=None, line_spacing=None, keep_next=False):
    ppr_parts = []
    if style:
        ppr_parts.append(f'<w:pStyle w:val="{style}"/>')
    if keep_next:
        # must sit after pStyle and before spacing (OOXML pPr element order)
        ppr_parts.append('<w:keepNext/>')
    sp_attrs = []
    if spacing_before:
        sp_attrs.append(f'w:before="{spacing_before}"')
    if spacing_after is not None:
        sp_attrs.append(f'w:after="{spacing_after}"')
    if line_spacing is not None:
        sp_attrs.append(f'w:line="{line_spacing}" w:lineRule="auto"')
    if sp_attrs:
        ppr_parts.append(f'<w:spacing {" ".join(sp_attrs)}/>')
    ind_attrs = []
    if ind_left:
        ind_attrs.append(f'w:left="{ind_left}"')
    if ind_right:
        ind_attrs.append(f'w:right="{ind_right}"')
    if ind_attrs:
        ppr_parts.append(f'<w:ind {" ".join(ind_attrs)}/>')
    if center:
        ppr_parts.append('<w:jc w:val="center"/>')
    elif justified:
        ppr_parts.append('<w:jc w:val="both"/>')
    ppr = f"<w:pPr>{''.join(ppr_parts)}</w:pPr>" if ppr_parts else ""
    return f"<w:p>{ppr}{runs_xml}</w:p>"


def title_para(text):
    return para(run_xml(text, bold=True, sz=24), center=True, justified=False)

def author_runs(entries):
    parts = []
    for num, name in entries:
        parts.append(run_xml(num, sz=20, superscript=True))
        parts.append(run_xml(name + "; ", sz=20))
    return "".join(parts)

def affil_para(num, text):
    return para(
        run_xml(num, italic=True, sz=18, superscript=True)
        + run_xml(text, italic=True, sz=18),
        center=True, justified=False, spacing_before=120, spacing_after=0,
    )

def abstract_heading():
    return para(run_xml("Abstract", bold=True, sz=20),
                spacing_before=240, spacing_after=0, justified=True)

def abstract_body(text):
    return para(run_xml(text, sz=20), justified=True)

def keywords_para(kw_text):
    return para(run_xml("Keywords", bold=True, sz=18) + run_xml(": " + kw_text, sz=18),
                spacing_before=120, spacing_after=567, justified=True)

def section_heading(text):
    return para(run_xml(text, bold=True, sz=22), spacing_before=240, justified=True,
                keep_next=True)

def subsection_heading(text):
    return para(run_xml(text, italic=True, sz=22),
                spacing_before=240, justified=True, keep_next=True)

def body_para(text):
    return para(run_xml(text, sz=22), justified=True)

def body_para_with_bold(segments):
    runs = []
    for seg in segments:
        t, b = seg[0], seg[1]
        i = seg[2] if len(seg) == 3 else False
        runs.append(run_xml(t, bold=b, italic=i, sz=22))
    return para("".join(runs), justified=True)

def bullet_para(text_segments):
    runs = []
    for seg in text_segments:
        if isinstance(seg, str):
            runs.append(run_xml(seg, sz=22))
        else:
            t, b = seg[0], seg[1]
            i = seg[2] if len(seg) == 3 else False
            runs.append(run_xml(t, bold=b, italic=i, sz=22))
    return para("".join(runs), justified=True)

def table_xml(headers, rows, col_widths=None):
    ncols = len(headers)
    if not col_widths:
        total = 9360
        col_widths = [total // ncols] * ncols
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in col_widths)
    def cell(text, bold=False):
        return (f"<w:tc><w:tcPr><w:tcW w:w=\"0\" w:type=\"auto\"/></w:tcPr>"
                f"<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr>"
                f"{run_xml(text, bold=bold, sz=18)}</w:p></w:tc>")
    header_row = "<w:tr>" + "".join(cell(h, bold=True) for h in headers) + "</w:tr>"
    data_rows = "".join("<w:tr>" + "".join(cell(c) for c in row) + "</w:tr>" for row in rows)
    return ("<w:tbl><w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/>"
            '<w:jc w:val="center"/>'
            "<w:tblBorders>"
            '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            f"</w:tblBorders></w:tblPr><w:tblGrid>{grid}</w:tblGrid>"
            f"{header_row}{data_rows}</w:tbl>")

def table_caption(label, description):
    runs = (run_xml(label, bold=True, sz=16)
            + run_xml(" " + description, sz=16))
    return para(runs, center=True, justified=False, spacing_before=120, spacing_after=120)

def figure_caption(text):
    return para(run_xml(text, italic=True, sz=16), center=True, justified=False, spacing_before=120, spacing_after=120)

def ref_para(text):
    return para(run_xml(text, sz=18), justified=True,
                spacing_before=280, spacing_after=280, line_spacing=240)

def ref_para_styled(number, authors_year, title, venue):
    runs = (run_xml(f"[{number}] {authors_year} ", sz=18)
            + run_xml(title, italic=True, sz=18)
            + run_xml(f". {venue}", sz=18))
    return para(runs, justified=True,
                spacing_before=280, spacing_after=280, line_spacing=240)


# ── Image embedding helpers ──

_img_id_counter = 100
_img_rels = []

def get_image_size(path):
    try:
        from PIL import Image
        im = Image.open(path)
        return im.size
    except Exception:
        return (1200, 800)

def add_image(src_filename, caption, width_emu=None, rid_prefix="rIdImg"):
    global _img_id_counter
    src = os.path.join(IMG_DIR, src_filename)
    if not os.path.exists(src):
        return figure_caption(f"[Image not found: {src_filename}] {caption}")

    _img_id_counter += 1
    img_id = _img_id_counter
    rid = f"{rid_prefix}{img_id}"
    dest_name = f"paper_img_{img_id}.png"
    dest = os.path.join(MEDIA_DIR, dest_name)
    shutil.copy2(src, dest)

    w_px, h_px = get_image_size(src)
    if width_emu is None:
        width_emu = PAGE_W_EMU
    height_emu = int(width_emu * h_px / w_px)

    _img_rels.append((rid, f"media/{dest_name}"))

    drawing = (
        f'<w:drawing>'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0" '
        f'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        f'<wp:extent cx="{width_emu}" cy="{height_emu}"/>'
        f'<wp:docPr id="{img_id}" name="Picture {img_id}"/>'
        f'<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:nvPicPr><pic:cNvPr id="{img_id}" name="{dest_name}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill>'
        f'<a:blip r:embed="{rid}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
        f'<a:stretch><a:fillRect/></a:stretch>'
        f'</pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width_emu}" cy="{height_emu}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        f'</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>'
    )

    img_para = f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r>{drawing}</w:r></w:p>'
    cap_para = figure_caption(caption)
    return img_para + "\n    " + cap_para



# --- Multi-panel figure grid helpers (2-up layout, borderless) ---
def image_drawing(src_filename, width_emu):
    """Return only the <w:drawing> XML for an image (registers media + rel); None if missing."""
    global _img_id_counter
    src = os.path.join(IMG_DIR, src_filename)
    if not os.path.exists(src):
        return None
    _img_id_counter += 1
    img_id = _img_id_counter
    rid = "rIdImg%d" % img_id
    dest_name = "paper_img_%d.png" % img_id
    shutil.copy2(src, os.path.join(MEDIA_DIR, dest_name))
    w_px, h_px = get_image_size(src)
    height_emu = int(width_emu * h_px / w_px)
    _img_rels.append((rid, "media/%s" % dest_name))
    return (
        '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        '<wp:extent cx="%d" cy="%d"/><wp:docPr id="%d" name="Picture %d"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:nvPicPr><pic:cNvPr id="%d" name="%s"/><pic:cNvPicPr/></pic:nvPicPr>'
        '<pic:blipFill><a:blip r:embed="%s" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
        '<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>'
        % (width_emu, height_emu, img_id, img_id, img_id, dest_name, rid, width_emu, height_emu)
    )

def _fig_img_p(drawing):
    inner = drawing if drawing else run_xml("[image not found]")
    return ('<w:p><w:pPr><w:spacing w:before="60" w:after="20"/>'
            '<w:jc w:val="center"/></w:pPr><w:r>%s</w:r></w:p>' % inner)

def _fig_sub_p(text):
    return para(run_xml(text, bold=True, sz=16), center=True, justified=False,
                spacing_before=0, spacing_after=60)

def _fig_cap_p(text):
    return para(run_xml(text, italic=True, sz=16), center=True, justified=False,
                spacing_before=0, spacing_after=120)

def _tc(inner, w, span=1):
    sp = ('<w:gridSpan w:val="%d"/>' % span) if span > 1 else ""
    mar = ('<w:tcMar><w:top w:w="0" w:type="dxa"/><w:left w:w="0" w:type="dxa"/>'
           '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tcMar>')
    return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s%s'
            '<w:vAlign w:val="bottom"/></w:tcPr>%s</w:tc>' % (w, sp, mar, inner))

_FIG_BORDERS = ('<w:tblBorders><w:top w:val="none"/><w:left w:val="none"/>'
                '<w:bottom w:val="none"/><w:right w:val="none"/>'
                '<w:insideH w:val="none"/><w:insideV w:val="none"/></w:tblBorders>')

def figure_row2(dL, subL, capL, dR, subR, capR):
    grid = '<w:tblGrid><w:gridCol w:w="4536"/><w:gridCol w:w="4536"/></w:tblGrid>'
    r1 = '<w:tr>%s%s</w:tr>' % (_tc(_fig_img_p(dL)+_fig_sub_p(subL), 4536),
                                _tc(_fig_img_p(dR)+_fig_sub_p(subR), 4536))
    r2 = '<w:tr>%s%s</w:tr>' % (_tc(_fig_cap_p(capL), 4536), _tc(_fig_cap_p(capR), 4536))
    return ('<w:tbl><w:tblPr><w:tblW w:w="9072" w:type="dxa"/><w:jc w:val="center"/>'
            '%s<w:tblLayout w:type="fixed"/></w:tblPr>%s%s%s</w:tbl>'
            % (_FIG_BORDERS, grid, r1, r2))

def figure_row_5_6(dL, subL, capL, dA, subA, dB, subB, capR):
    grid = ('<w:tblGrid><w:gridCol w:w="4536"/><w:gridCol w:w="2268"/>'
            '<w:gridCol w:w="2268"/></w:tblGrid>')
    r1 = '<w:tr>%s%s%s</w:tr>' % (_tc(_fig_img_p(dL)+_fig_sub_p(subL), 4536),
                                  _tc(_fig_img_p(dA)+_fig_sub_p(subA), 2268),
                                  _tc(_fig_img_p(dB)+_fig_sub_p(subB), 2268))
    r2 = '<w:tr>%s%s</w:tr>' % (_tc(_fig_cap_p(capL), 4536),
                                _tc(_fig_cap_p(capR), 4536, span=2))
    return ('<w:tbl><w:tblPr><w:tblW w:w="9072" w:type="dxa"/><w:jc w:val="center"/>'
            '%s<w:tblLayout w:type="fixed"/></w:tblPr>%s%s%s</w:tbl>'
            % (_FIG_BORDERS, grid, r1, r2))


def update_rels():
    import re
    with open(RELS_FILE, "r") as f:
        content = f.read()
    content = re.sub(
        r'\s*<Relationship[^>]*Type="[^"]*relationships/image"[^>]*/>\s*',
        '', content)
    insert_point = content.rindex("</Relationships>")
    new_rels = ""
    for rid, target in _img_rels:
        new_rels += (f'  <Relationship Id="{rid}" '
                     f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                     f'Target="{target}"/>\n')
    content = content[:insert_point] + new_rels + content[insert_point:]
    with open(RELS_FILE, "w") as f:
        f.write(content)

def update_content_types():
    ct_file = "unpacked/[Content_Types].xml"
    with open(ct_file, "r") as f:
        content = f.read()
    if 'Extension="png"' not in content:
        insert_point = content.rindex("</Types>")
        content = content[:insert_point] + '  <Default Extension="png" ContentType="image/png"/>\n' + content[insert_point:]
        with open(ct_file, "w") as f:
            f.write(content)


# ══════════════════════════════════════════════════════
# BUILD DOCUMENT CONTENT
# ══════════════════════════════════════════════════════

parts = []

# ── Title ──
parts.append(title_para(
    "An Autonomous Multi-Agent System for CFD-Based Data Center "
    "Thermal Modeling"
))

# ── Authors (7 authors, 4 affiliations) ──
parts.append(para(
    author_runs([
        ("1,2", "Tianyu Li"),
        ("2", "Qingang Zhang"),
        ("2", "Hoanghuy Nguyen"),
        ("2", "Zhiwei Cao"),
        ("3", "Ruihang Wang"),
        ("4", "Binyang Song"),
        ("2", "Yonggang Wen*"),
    ]),
    center=True, justified=False, spacing_before=120, spacing_after=0,
))

# ── Affiliations ──
parts.append(affil_para("1",
    "AI-X Programme, Interdisciplinary Graduate Programme, Nanyang Technological University, "
    "50 Nanyang Avenue, Singapore 639798;"))
parts.append(affil_para("2",
    "College of Computing and Data Science, Nanyang Technological University, "
    "50 Nanyang Avenue, Singapore 639798;"))
parts.append(affil_para("3",
    "School of Information Science and Engineering, Yunnan University, "
    "Chenggong District, Kunming, China 650500;"))
parts.append(affil_para("4",
    "School of Mechanical and Aerospace Engineering, Nanyang Technological University, "
    "50 Nanyang Avenue, Singapore 639798"))

# ── Abstract ──
parts.append(abstract_heading())
parts.append(abstract_body(
    "Data center thermal modeling usually requires complex Computational Fluid Dynamics (CFD) case "
    "setup and execution. This manual process typically spans hours to days. This paper presents a multi-agent "
    "framework that automates this pipeline, converting floor plans, equipment spreadsheets, "
    "and natural language prompts into thermal simulations. The framework uses "
    "an Orchestrator–Builder–Evaluator architecture that generates geometry and flags "
    "discrepancies for a human in the loop to refine by re-prompting. A Playwright-based headless browser API lets AI agents interact with "
    "the same Three.js parametric geometry system used by human engineers, supporting a "
    "sketch-to-simulation workflow. To improve reliability and eliminate hallucination, the "
    "Large Language Model generates only a schema-validated Intermediate Representation (IR), while "
    "a deterministic compiler produces all geometry, meshing, and solver files. On nine data "
    "center cases ranging from 2 to 210 racks, single-round generation reproduces layouts with "
    "accuracy that tracks input fidelity: 99.7% from a spreadsheet of exact coordinates, 50.6% "
    "from a coarse 0.5 m block grid, and 26.0% from a floor-plan image alone."
))
parts.append(keywords_para(
    "Data center CFD; Multi-agent System; Large Language Models; OpenFOAM; "
    "Parametric Geometry; Thermal Simulation"
))

# ══════════════════════
# 1. Introduction
# ══════════════════════
parts.append(section_heading("1. Introduction"))

parts.append(body_para(
    "Thermal modeling via Computational Fluid Dynamics (CFD) is widely used to predict hotspots "
    "and optimize cooling in data centers. However, preparing a CFD case with a numerical solver "
    "such as OpenFOAM [1] remains highly manual: an engineer must produce watertight "
    "STereoLithography (STL) geometry, assign boundary conditions for every rack and cooling "
    "unit, and assemble consistent solver dictionaries. A large data center can contain over "
    "200 racks housing more than 1,500 servers, each requiring individually parameterized "
    "geometry and thermal boundary conditions. At this scale, manual setup spans hours to "
    "days. Errors such as a misoriented rack or a mismatched power value do not crash the "
    "solver but silently produce meaningless results."
))

parts.append(body_para(
    "Recent work demonstrates that Large Language Models (LLMs) can automate aspects of CFD "
    "workflows. OpenFOAMGPT [2] and its successor [3] use retrieval-augmented generation for "
    "tutorial-scale cases. Multi-agent frameworks such as Foam-Agent [4], MetaOpenFOAM [5], "
    "and SwarmFoam [6] report 84–88% execution success on benchmark suites of 8–110 cases. "
    "CFD-copilot [7] and OptMetaOpenFOAM [8] extend automation to post-processing and "
    "parameter optimization, and domain-specific fine-tuning [9] reaches 88.7% solution accuracy. "
    "As ChatCFD [10] observes across 315 benchmark cases, 82% execute successfully but only "
    "68% achieve physical fidelity. This gap underscores the importance of verification "
    "beyond solver convergence. For data "
    "centers specifically, ChatTwin [11] and ChatDC [12] generate digital-twin scene descriptions "
    "with high layout accuracy, DCoPilot [13] applies generative AI to operational policy, and "
    "Fusion Intelligence [14] combines generative and physics-based AI for digital twin "
    "creation. Few of these approaches, however, extend automation to complex geometry "
    "construction at facility scale and per-component boundary condition assignment within "
    "a closed-loop, end-to-end CFD pipeline."
))

parts.append(body_para(
    "This paper presents a multi-agent framework that extends CFD automation beyond "
    "tutorial-scale cases to the complete simulation pipeline at facility scale. "
    "The framework makes three contributions. "
    "First, the Orchestrator–Builder–Evaluator architecture pairs single-pass generation with a "
    "verification step: the Evaluator flags geometric discrepancies, and a human in the loop "
    "refines the result by re-prompting the Builder. "
    "Second, a Playwright-based [15] headless browser API lets AI agents operate the same "
    "Three.js parametric GUI used by human engineers, reusing an existing tool rather than "
    "building a domain-specific geometry generator. "
    "Third, a strict LLM–compiler separation confines hallucination risk to a schema-validated "
    "IR while a deterministic compiler produces all OpenFOAM files. "
    "We evaluate on nine data center cases (2–210 racks) and report geometry accuracy, "
    "CFD convergence, and compare against manual modeling with commercial software."
))

# ══════════════════════
# 2. Method
# ══════════════════════
parts.append(section_heading("2. Method"))

# Figure 1
parts.append(add_image("workflow.png",
    "Figure 1. End-to-end pipeline of the multi-agent data center CFD framework."
))

parts.append(body_para(
    "We refer to the complete framework as DCTwin, encompassing the multi-agent system, the "
    "deterministic compiler, and the OpenFOAM simulation backend. "
    "As illustrated in Figure 1, the pipeline proceeds through five stages. "
    "In Stage 1, the Orchestrator dispatches the Builder to interpret heterogeneous inputs "
    "(floor plans, equipment spreadsheets, and natural language prompts) and construct an initial "
    ".cfdproj project file via the Three.js parametric GUI. "
    "The Orchestrator then invokes the Evaluator, which calls the deterministic compiler to "
    "render a 2D top-view of the generated geometry and compares it against the original input. "
    "Any discrepancies the Evaluator reports are then resolved by a human in the loop, who reviews "
    "the geometry through the GUI and re-prompts the Builder to refine it until correct."
))

parts.append(body_para(
    "In Stage 2, the Orchestrator invokes the deterministic compiler "
    "to export multi-solid STL files, boundary condition definitions, mesh configuration, and "
    "solver dictionaries from the validated .cfdproj. "
    "Stage 3 assembles these artifacts into a complete, runnable OpenFOAM case directory. "
    "In Stage 4, the Orchestrator triggers parallel mesh generation via "
    "snappyHexMesh. "
    "In Stage 5, the Orchestrator launches the CFD solver "
    "buoyantBoussinesqSimpleFoam and monitors convergence through residual tracking and "
    "temperature readings at predefined sensor locations."
))

parts.append(body_para(
    "By design, the architecture separates LLM reasoning from file generation: agents handle only "
    "semantic extraction and API scripting, while all geometry construction and CFD file production "
    "follow deterministic, schema-validated code paths."
))

# 2.1 Multi-Agent Architecture
parts.append(subsection_heading("2.1 Multi-Agent Architecture"))
parts.append(body_para(
    "The framework uses three agents: Orchestrator, Builder, and Evaluator. Each agent is "
    "driven by a dedicated skill document that defines its role, available tools, and output "
    "constraints. The Orchestrator communicates with the Builder and Evaluator through "
    "independent channels; the two sub-agents do not interact directly."
))

# Orchestrator
parts.append(body_para_with_bold([
    ("Orchestrator. ", True),
    ("The Orchestrator is the primary conversational agent (Claude Opus 4.6) that coordinates "
     "all five pipeline stages described above. It dispatches the Builder and Evaluator defined by specific skill documents "
     "in Stage 1. Once geometry is finalized, it invokes the deterministic compiler and OpenFOAM toolchain in "
     "Stages 2–5.", False),
]))

# Builder Agent
parts.append(body_para_with_bold([
    ("Builder. ", True),
    ("The Builder (Claude Sonnet 4.6, without domain-specific fine-tuning) operates in two "
     "phases. In the ", False),
    ("interpretation phase", False, True),
    (", the model analyses multi-modal input: room dimensions from floor plan images, rack "
     "positions from layout drawings, server power ratings from Comma-Separated Values (CSV) "
     "tables, and Air Conditioning Unit (ACU) specifications from text descriptions. In the ", False),
    ("construction phase", False, True),
    (", these parameters are translated into API calls to the parametric geometry system "
     "(Section 2.2), generating a .cfdproj file conforming to a schema "
     "termed the Data Center CFD Intermediate Representation (DataCenterCFD-IR).", False),
]))

# IR description
parts.append(body_para(
    "The DataCenterCFD-IR is a hierarchical schema that encodes room geometry, rack objects "
    "with per-server thermal loads, ACUs with multi-face supply/return arrays, baffles, "
    "partition walls, floor openings, and solver parameters. Built-in validators enforce "
    "structural correctness: unique IDs, consistent patch names, and valid physical ranges. "
    "Malformed agent output is rejected before it reaches the compiler."
))

# Evaluator
parts.append(body_para_with_bold([
    ("Evaluator. ", True),
    ("The Evaluator (Claude Sonnet 4.6) invokes the deterministic compiler to render a 2D "
     "top-view of the constructed geometry and compares it against the original input floor "
     "plan. It identifies discrepancies: missing or extra equipment, positional errors, "
     "orientation mismatches, and dimensional inconsistencies. Its output is a structured "
     "correction report specifying which models need adjustment and how.", False),
]))


# 2.2 Deterministic Compiler
parts.append(subsection_heading("2.2 Deterministic Compiler"))

parts.append(body_para(
    "Data center CFD requires multi-solid watertight STL files where each equipment item is a "
    "separate named solid with outward-facing normals. A 210-rack facility produces thousands "
    "of surface regions that must remain topologically consistent. At this scale, direct LLM "
    "generation is impractical. We therefore separate the two concerns: the Builder produces "
    "only the schema-validated DataCenterCFD-IR, and a deterministic compiler handles "
    "all file generation. The IR provides data-center-specific parametric primitives: racks, "
    "cooling units, baffles, perforated floor tiles, and partition walls. Each primitive carries "
    "both geometric and physical semantics. This coupling of geometry with physics distinguishes the "
    "DataCenterCFD-IR from pure-geometry CAD IRs such as Structured Parametric CAD Code (SPCC) [16]. "
    "The compiler is implemented as a set of deterministic Python modules that process the IR "
    "through fixed code paths, with no LLM involvement in file generation."
))

parts.append(body_para(
    "In Stages 2–5, the Orchestrator follows the procedure defined in its skill document to "
    "invoke each tool in sequence; the tools themselves are deterministic. "
    "In Stage 2, the compiler processes the validated IR through "
    "several deterministic sub-steps: parametric STL construction with per-region naming, "
    "background mesh generation, snappyHexMesh configuration with automatic thin-gap detection, "
    "boundary condition assignment via a registry mapping IR physics to 11 OpenFOAM Boundary "
    "Condition (BC) types, field initialization, solver dictionary generation, and parallel "
    "decomposition. Stage 3 assembles these outputs into a complete OpenFOAM case directory. "
    "Identical IR input always produces identical output, making failures reproducible."
))

parts.append(body_para(
    "AI agents access the geometry system through a Playwright-based headless browser API. "
    "It exposes endpoints for project management, model manipulation via JavaScript "
    "evaluation in the browser context, STL export, and full CFD compilation. Because the "
    "agent operates through the same interface as a human engineer, any geometry it constructs "
    "is directly inspectable and correctable through the GUI."
))

# ══════════════════════
# 3. Experiments and Results
# ══════════════════════
parts.append(section_heading("3. Experiments and Results"))

# Setup paragraph
parts.append(body_para(
    "We evaluate the framework on nine data center configurations spanning a wide range of "
    "complexity, from a 2-rack cold-aisle containment room to a 210-rack large-scale facility. "
    "Each case includes input documents consisting of a floor plan image, an optional "
    "equipment spreadsheet, CSV power and ACU tables, and a natural language prompt. A ground-truth .cfdproj file "
    "manually authored by a CFD engineer serves as the reference. The cases cover cold-aisle and hot-aisle containment, "
    "leakage scenarios, compact and standard layouts, and dense high-rack-count facilities. "
    "The Orchestrator agent uses Claude Opus 4.6, while the Builder and Evaluator agents use "
    "Claude Sonnet 4.6 as the underlying LLM. OpenFOAM is used for mesh generation and "
    "solving, with "
    "buoyantBoussinesqSimpleFoam as the steady-state thermal solver. All cases use parallel "
    "execution (8 cores) with 1000 maximum Semi-Implicit Method for Pressure-Linked Equations (SIMPLE) iterations."
))

# Quality evaluation: 4 dimensions on 8 cases
parts.append(body_para(
    "To quantify generation quality, we compare each generated .cfdproj against a ground-truth "
    "counterpart authored by a CFD engineer across four dimensions: "
    "Geometry match evaluates position-based model matching (same type within a 0.5 m threshold), "
    "with four sub-scores per matched pair: position accuracy, size correctness, orientation "
    "matching, and equipment/patch completeness. "
    "Physical condition checks room polygon and ceiling height accuracy, plus per-model "
    "boundary condition parameters. "
    "Meshing configuration compares base cell size, maximum global cell count, refinement levels, "
    "and parallel decomposition settings. "
    "Solver configuration checks solver type, iteration count, room temperature, discretisation "
    "schemes, and relaxation factors. "
    "Complete scoring formulas and thresholds for all four dimensions are detailed in "
    "Appendix B."
))

# Figure 2
parts.append(add_image("evaluation_combined.png",
    "Figure 2. (a) Geometry accuracy across three input modes — image-only, coarse 0.5 m-grid "
    "spreadsheet, and exact-coordinate spreadsheet — over nine test cases sorted by rack count. "
    "(b) Multi-dimensional quality heatmap of the exact-coordinate pipeline, evaluated across "
    "four dimensions using a unified continuous-deduction scoring scheme."))

# Three experiment setups
parts.append(body_para(
    "We evaluate three input setups of increasing positional fidelity. The image-only setup gives "
    "the Builder a raster floor plan, from which it must infer every rack position. The coarse-grid "
    "setup gives a spreadsheet that marks each footprint on a fixed 0.5 m grid, which the compiler "
    "then aligns to grid cells. The exact-coordinate setup gives a spreadsheet that lists each "
    "model's precise position and dimensions. Figure 2(a) reports the resulting geometry accuracy. "
    "The three setups separate cleanly: image-only averages 26.0%, the coarse grid averages 50.6%, "
    "and exact coordinates average 99.7%."
))
# Key findings
parts.append(body_para(
    "Three findings follow. First, accuracy tracks input fidelity on average, rising from 26.0% "
    "under image-only input to 50.6% under the coarse grid and 99.7% under exact coordinates. "
    "The gap between image-only and coarse input is small and inconsistent, and the two modes "
    "trade places on Cases 2 and 6; the decisive jump comes only with exact coordinates. "
    "Second, the coarse grid suffices for grid-friendly rooms, where Cases 1, 4, and 5 exceed "
    "92%, but degrades with both scale and grid alignment. Among the large facilities, accuracy "
    "falls from 28.1% to 5.3% across Cases 7, 8, and 9 as quantisation errors accumulate. Rack "
    "count alone does not explain the outcome: Cases 5 and 6 both hold 24 racks but score 98.8% "
    "and 8.1%, because a 0.5 m cell cannot resolve rack rows misaligned to the grid. Third, exact "
    "coordinates reduce reconstruction to a deterministic spreadsheet-to-JSON conversion. Every "
    "case then reaches 97% or above, and the Builder's role narrows to schema-valid extraction."
))
# Figure 2(b): exact-coordinate pipeline across four dimensions
parts.append(body_para(
    "Figure 2(b) profiles the exact-coordinate pipeline across all four quality dimensions. "
    "Geometry match and physical condition are near-perfect, with means of 99.7% and 99.2%, "
    "because both are transcribed directly from the spreadsheet. Meshing and solver configuration "
    "sit lower, with means of 86.1% and 89.2%, since these parameters come from the natural "
    "language prompt and built-in defaults rather than from the spreadsheet. The deterministic "
    "compiler therefore adds minimal error of its own, and the residual deficits trace to prompt "
    "interpretation. These scores reflect single-round output; refining the geometry requires a "
    "human in the loop, who reviews it through the GUI and re-prompts with targeted corrections in natural language. "
    "Table C1 in Appendix C reports the detailed case complexity metrics and execution times."
))

# Success analysis
parts.append(body_para(
    "The remaining experiments assess the CFD stage on the human-refined geometry, "
    "separate from the single-round accuracy study above. The nine cases span 2 to 210 racks "
    "(5 to 1,551 servers, 240,022 to 4,776,000 "
    "mesh cells). Mesh generation time ranges from 66 seconds to 14 minutes, and solver "
    "execution (1000 iterations) from 106 seconds to 20 minutes, scaling with cell count. "
    "Case 5 demonstrates "
    "the full sketch-to-simulation capability: a hand-drawn floor plan with 24 racks, "
    "combined with CSV power data and a natural language prompt, is converted into a converged "
    "thermal simulation. Appendix A presents the complete input–output example for this case."
))

# CFD Validation Against Commercial Software
parts.append(body_para_with_bold([
    ("CFD Validation Against Commercial Software (Case 2). ", True),
    ("To assess simulation fidelity on the human-refined geometry, we compare the CFD "
     "results produced by DCTwin (OpenFOAM buoyantBoussinesqSimpleFoam) against a commercial "
     "data center CFD tool on Case 2 (leakage scenario, 4 racks, "
     "2 ACUs). Figures 3–6 present the comparison results.", False),
]))

# Figures 3-6
# --- Figures 3-6: two-up grid layout (matches reference docx) ---
_HALF_W = 2820000
_QTR_W = 1400000
_SUB_AB = "(a) Commercial solver          (b) DCTwin"
_d3 = image_drawing("case02_cutplane_x.png", _HALF_W)
_d4 = image_drawing("case02_cutplane_y.png", _HALF_W)
_d5 = image_drawing("case02_cutplane_z.png", _HALF_W)
_d6a = image_drawing("case02_acu_temperature.png", _QTR_W)
_d6b = image_drawing("case02_residual.png", _QTR_W)
parts.append(figure_row2(
    _d3, _SUB_AB, "Figure 3. CFD temperature field and velocity vector plot at x = 2.2 m cut-plane.",
    _d4, _SUB_AB, "Figure 4. CFD temperature field and velocity vector plot at y = 2.6 m cut-plane."))
parts.append("<w:p/>")
parts.append(figure_row_5_6(
    _d5, _SUB_AB, "Figure 5. CFD temperature field and velocity vector plot at z = 2.7 m cut-plane.",
    _d6a, "(a) ACU supply & return temperatures", _d6b, "(b) CFD residual convergence",
    "Figure 6. ACU supply & return temperatures and CFD residual comparisons."))
parts.append("<w:p/>")

# Validation analysis
parts.append(body_para(
    "Figures 3–5 show that DCTwin captures the same principal flow features as the commercial solver: "
    "cooling-supply jets, rack-aisle airflow, recirculation zones, and the cool-to-warm "
    "temperature gradient. Figure 6(a) compares ACU temperatures. Both solvers "
    "stabilize to near 16 °C after approximately 500 iterations. Figure 6(b) confirms "
    "multi-order residual decay in both solvers. The remaining local differences are expected: "
    "the commercial solver is a closed-source tool whose boundary condition and turbulence model "
    "implementations are not publicly documented, so the two solvers differ at the "
    "cell level. Despite these black-box differences, the agreement in flow pattern and ACU "
    "return temperature suggests that DCTwin produces CFD results of comparable fidelity "
    "to an established commercial tool, without manual case setup."
))

# ══════════════════════
# 4. Conclusions
# ══════════════════════
parts.append(section_heading("4. Conclusions"))
parts.append(body_para(
    "This work presented a multi-agent framework that automates data center CFD case generation and execution "
    "from heterogeneous engineering documents. At its core, an Orchestrator–Builder–Evaluator "
    "architecture generates the geometry and flags discrepancies for review, with a human in the "
    "loop refining the result by re-prompting. "
    "This architecture operates through a Playwright-based headless browser "
    "API that exposes a Three.js parametric geometry system to AI agents, bridging LLM text "
    "generation and complex 3D CFD geometry construction. Together, these components support a "
    "sketch-to-simulation workflow that converts floor plans, tabular equipment data, and "
    "natural language prompts into runnable OpenFOAM thermal simulations."
))
parts.append(body_para(
    "Experiments on nine cases spanning 2 to 210 racks validate these contributions. "
    "Geometry accuracy tracks input fidelity: single-round generation reaches 99.7% from exact "
    "spreadsheet coordinates, 50.6% from a coarse block grid, and 26.0% from images alone."
))
parts.append(body_para(
    "Geometry accuracy remains bounded by LLM vision quality: the limited resolution of "
    "raster floor plan images restricts precise coordinate extraction. The value of the "
    "GUI-based verification loop has not been formally quantified through a user study, and "
    "evaluation has been restricted to air-cooled steady-state configurations. Future work will "
    "address these gaps through multi-LLM comparison, extension to liquid cooling topologies, "
    "formal studies with practising CFD engineers, and integration with online sensor data for "
    "digital twin calibration."
))

# ── Acknowledgments ──
parts.append(section_heading("Acknowledgments"))
parts.append(body_para(
    "This work was supported in part by the Ministry of Education, Singapore, under its Academic "
    "Research Fund Tier 1 of RG104/23, in part by Singapore Agency for Science, Technology and "
    "Research (A*STAR) under its MTC Programmatic of M23L9b0052, and in part by Leoch Battery Pte. Ltd. "
    "The first author gratefully acknowledges the support of the AI-X Programme, Interdisciplinary "
    "Graduate Programme, Nanyang Technological University, Singapore."))

# ── References ──
parts.append(section_heading("References"))

styled_refs = [
    (1, "H. Jasak, A. Jemcov, and Ž. Tuković (2007).",
     "OpenFOAM: A C++ library for complex physics simulations",
     "International Workshop on Coupled Methods in Numerical Dynamics."),
    (2, "S. Pandey, R. Xu, W. Wang, and X. Chu (2025).",
     "OpenFOAMGPT: A retrieval-augmented large language model (LLM) agent for OpenFOAM-based computational fluid dynamics",
     "Physics of Fluids, 37(3), 035120."),
    (3, "J. Feng, R. Xu, and X. Chu (2026).",
     "OpenFOAMGPT 2.0: End-to-end, trustworthy automation for computational fluid dynamics",
     "International Journal of Heat and Fluid Flow, 120, 110399."),
    (4, "L. Yue, N. Somasekharan, T. Zhang, Y. Cao, and S. Pan (2025).",
     "Foam-Agent: Towards automated intelligent CFD workflows",
     "arXiv preprint, arXiv:2505.04997."),
    (5, "Y. Chen, X. Zhu, H. Zhou, and Z. Ren (2024).",
     "MetaOpenFOAM: An LLM-based multi-agent framework for CFD",
     "arXiv preprint, arXiv:2407.21320."),
    (6, "C. Yang, Y. Wang, J. Tang, et al. (2026).",
     "SwarmFoam: An OpenFOAM multi-agent system based on multiple types of large language models",
     "arXiv preprint, arXiv:2601.07252."),
    (7, "Z. Dong, S. Du, Z. Lu, and Y. Yang (2025).",
     "CFD-copilot: Leveraging domain-adapted large language model and model context protocol to enhance simulation automation",
     "arXiv preprint, arXiv:2512.07917."),
    (8, "Y. Chen, L. Zhang, X. Zhu, H. Zhou, and Z. Ren (2025).",
     "OptMetaOpenFOAM: Large language model driven chain of thought for sensitivity analysis and parameter optimization based on CFD",
     "arXiv preprint, arXiv:2503.01273."),
    (9, "Z. Dong, Z. Lu, and Y. Yang (2025).",
     "Fine-tuning a large language model for automating computational fluid dynamics simulations",
     "Theoretical and Applied Mechanics Letters, 15(3), 100594."),
    (10, "E. Fan, K. Hu, Z. Wu, et al. (2026).",
     "ChatCFD: A large language model-driven agent for end-to-end computational fluid dynamics automation with structured knowledge and reasoning",
     "Advanced Intelligent Discovery, e202500174."),
    (11, "M. Li, R. Wang, X. Zhou, et al. (2023).",
     "ChatTwin: Toward automated digital twin generation for data center via large language models",
     "In Proceedings of the 10th ACM International Conference on Systems for Energy-Efficient Buildings, Cities, and Transportation (BuildSys), pp. 208–211, ACM."),
    (12, "M. Li, R. Wang, X. Zhou, et al. (2026).",
     "ChatDC: Geometric-aware data center digital twin generation via large language models",
     "ACM Transactions on Internet of Things, 7(2), Article 17."),
    (13, "M. Li, R. Wang, R. Tan, and Y. Wen (2026).",
     "DCoPilot: Generative AI-empowered policy adaptation for dynamic data center operations",
     "arXiv preprint, arXiv:2602.02137."),
    (14, "R. Wang, M. Li, Z. Cao, et al. (2025).",
     "Fusion Intelligence for digital twinning AI data centers: A synergistic GenAI-PhyAI approach",
     "arXiv preprint, arXiv:2505.19409."),
    (15, "Microsoft (2020).",
     "Playwright: Fast and reliable end-to-end testing for modern web apps",
     "Available: https://playwright.dev/."),
    (16, "J. Li, W. Ma, X. Li, et al. (2025).",
     "CAD-Llama: Leveraging large language models for computer-aided design parametric 3D model generation",
     "In Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR), pp. 18563–18573."),
]
for num, auth, title, venue in styled_refs:
    parts.append(ref_para_styled(num, auth, title, venue))

# ══════════════════════
# Appendix A
# ══════════════════════
parts.append(section_heading("Appendix A. Example Input and Output"))
parts.append(body_para(
    "Figure A1 presents a complete input–output example for Case 5 (hand-drawn sketch, "
    "24 racks, 4 ACUs). The left column shows the three-part user prompt: Part 1 specifies "
    "room geometry and equipment properties (dimensions, rack positions, cooling unit "
    "configurations); Part 2 defines mesh settings (surface and feature-edge refinement levels); "
    "Part 3 sets solver control parameters (iteration count, output interval). Two supplementary "
    "CSV files provide per-server power ratings and ACU operating scenarios. The center column "
    "shows the annotated 2D layout sketch used as visual input. The right column displays the "
    "framework's outputs at three pipeline stages: the generated 3D parametric geometry with "
    "all equipment placed and oriented, the computational mesh produced by snappyHexMesh, and "
    "the converged steady-state thermal simulation showing the temperature distribution across "
    "the facility."
))
parts.append(add_image("example.png",
    "Figure A1. Complete input–output example for Case 5 (sketch-to-simulation). "
    "Left: structured user prompts and supplementary CSV data. "
    "Centre: annotated 2D layout sketch. "
    "Right: generated 3D geometry, computational mesh, and converged thermal simulation."))

# ══════════════════════
# Appendix B
# ══════════════════════
parts.append(section_heading("Appendix B. Scoring Methodology"))

# B overview
parts.append(body_para(
    "This appendix details the scoring methodology used to evaluate generated .cfdproj files "
    "against ground-truth references across the four quality dimensions reported in Figure 2(b). "
    "The geometry match score (Section B.1) is the primary metric; the remaining three dimensions "
    "(Section B.2) assess physical parameters, meshing, and solver configuration."
))

# B.1 Geometry Match
parts.append(subsection_heading("B.1 Geometry Match"))

parts.append(body_para(
    "Model matching. Each generated model is matched to the closest ground-truth model of the "
    "same kind (rack, ACU, baffle, wall, floor, or ceiling) by 3D Euclidean distance on the "
    "model anchor point. If the room polygon centroids of the generated and label projects differ "
    "by more than 0.1 m, the offset is applied to all generated anchors before matching. A "
    "match is accepted only if the distance is below 0.5 m. This threshold reflects the "
    "compiler's lattice filter, which snaps adjacent racks to mitigate inter-rack gaps before "
    "geometry export. After filtering, rack positions align to a discrete grid spaced by one "
    "standard rack width (0.6 m). A positional offset exceeding 0.5 m therefore indicates a "
    "rack placed in the wrong slot rather than a minor coordinate error. Unmatched models are classified as missing (in "
    "label only) or extra (in generated only)."
))

parts.append(body_para(
    "Per-model scoring. Each matched model is scored across four sub-dimensions, each worth "
    "10 points (40 points maximum per model): position, size, orientation, and equipment/patch "
    "completeness. The completeness metric uses equipment count for racks and patch count for "
    "ACUs and floors. Table B1 lists the scoring formula and zero-score "
    "threshold for each sub-dimension."
))

# Table 2: Per-model scoring
parts.append(table_caption("Table B1.",
    "Per-model scoring formulas for the geometry match dimension."))
parts.append(table_xml(
    ["Sub-dimension", "Formula", "Zero threshold"],
    [["Position",
      "max(0, 10 − d × 20)",
      "d ≥ 0.5 m"],
     ["Size",
      "max(0, 10 − e_size × 20)",
      "e_size ≥ 0.5 m"],
     ["Orientation",
      "max(0, 10 − Δyaw / 9)",
      "Δyaw ≥ 90°"],
     ["Equipment (rack)",
      "max(0, 10 − |Δcount| × 3)",
      "|Δcount| ≥ 4"],
     ["Patches (ACU/floor)",
      "max(0, 10 − |Δpatches| × 5)",
      "|Δpatches| ≥ 2"]],
    col_widths=[2200, 4500, 2660],
))

parts.append(body_para(
    "In Table B1, d is the 3D Euclidean distance between matched anchors, "
    "e_size is the 3D distance in (width, depth, height) space, "
    "Δyaw is the minimum angular difference (accounting for 360° wraparound), "
    "Δcount is the difference in equipment items within a rack, and "
    "Δpatches is the difference in boundary condition patches on an ACU or floor model. "
    "Models of other kinds (wall, baffle, ceiling) receive full equipment score (10 points) "
    "by default."
))

parts.append(body_para(
    "Detection-based aggregation. Each matched model contributes its four summed sub-scores "
    "(0 to 40 points). Missing models (present in label but unmatched) and extra models "
    "(present in generated but unmatched) each contribute 40 points to the denominator with "
    "zero credit. The final geometry match percentage is: score = (Σ per-model scores) / "
    "(40 × (n_matched + n_missing + n_extra)) × 100%. This quality-weighted detection accuracy "
    "is bounded to [0, 100]: it penalises both missed and hallucinated models through the "
    "denominator and never becomes negative. Cases dominated by spurious or mislocated models "
    "score near zero, as seen for the largest layouts under image-only and coarse-grid input in Figure 2."
))

# B.2 Other Dimensions
parts.append(subsection_heading("B.2 Physical Condition, Meshing, and Solver Scoring"))

parts.append(body_para(
    "The remaining three dimensions use the same continuous-deduction scheme as geometry match "
    "but evaluate configuration fidelity rather than spatial placement. Physical condition "
    "(40 points base + per-equipment) checks room polygon vertices, ceiling height, ACU supply "
    "temperatures and flow rates, and per-rack equipment counts and power ratings. Meshing "
    "configuration (40 points) compares base cell size, maximum global cell count, refinement "
    "levels, and parallel decomposition settings. Solver configuration (40 points) checks solver "
    "type (exact match), iteration count, room temperature, and discretisation schemes. Each "
    "dimension is normalised to a percentage; physical condition scales with the number of ACUs "
    "and racks, while meshing and solver have fixed maxima."
))

# ══════════════════════
# Appendix C
# ══════════════════════
parts.append(section_heading("Appendix C. Case Complexity and CFD Timing"))
parts.append(body_para(
    "Table C1 reports the complexity metrics and execution times on the human-refined geometry "
    "for the nine data center cases evaluated in Section 3."
))
parts.append(table_caption("Table C1.",
    "Test cases with complexity metrics and CFD execution timing (on the human-refined geometry)."))
parts.append(table_xml(
    ["Case", "Description", "Num. of Racks", "Num. of Servers", "Num. of Mesh Cells", "Mesh Time (s)", "Solve Time (s)"],
    [["Case 1", "Cold aisle containment",   "2",    "5",    "240,022",   "66",   "106"],
     ["Case 2", "Rack leakage scenario",         "4",    "5",    "634,509",  "144",   "516"],
     ["Case 3", "Mid-size facility",        "14",  "56",    "241,567",   "72",   "187"],
     ["Case 4", "Compact square layout",    "20",  "22",    "368,355",   "91",   "191"],
     ["Case 5", "Hot aisle isolation",        "24",  "30",    "621,377",  "121",   "379"],
     ["Case 6", "Standard layout",          "24", "309",    "564,200",  "112",   "301"],
     ["Case 7", "Large facility",           "50", "316",  "1,704,366",  "276",   "788"],
     ["Case 8", "High-density facility",   "138", "319",  "2,729,124",  "501",   "851"],
     ["Case 9", "Extra large facility",     "210","1,551",  "4,776,000",  "813", "1,209"]],
    col_widths=[1200, 2100, 900, 1000, 1500, 1200, 1200],
))

# ══════════════════════════════════════════════════════
# ASSEMBLE document.xml
# ══════════════════════════════════════════════════════

with open("unpacked/word/document.xml", "r", encoding="utf-8") as f:
    tmpl = f.read()

doc_start_end = tmpl.index("<w:body>")
xml_header = tmpl[:doc_start_end]
sect_start = tmpl.rindex("<w:sectPr")
sect_end = tmpl.index("</w:sectPr>") + len("</w:sectPr>")
sect_pr = tmpl[sect_start:sect_end]

body_content = "\n    ".join(parts)
new_doc = f"""{xml_header}<w:body>
    {body_content}
    {sect_pr}
  </w:body>
</w:document>"""

out_path = "unpacked/word/document.xml"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(new_doc)

# Update relationships and content types for images
update_rels()
update_content_types()

print(f"Written new document.xml ({len(new_doc)} bytes)")
print(f"Embedded {len(_img_rels)} images")
