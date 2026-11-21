"""The automation-contract verb table is generated from contract.ts, and the docs only name verbs that exist.

The static parse (``agent.scripted_api.contract_verbs``) is checked against the LIVE
contract: the headless compiler loads ``automation/contract.ts`` in JSDOM
(operation ``contract-verbs``) and lists ``Object.keys(window.__dcdtuni)``.
"""
from __future__ import annotations

import re

import pytest

from agent.scripted_api import contract_verbs as cv
from agent.scripted_api.headless import compile_project
from agent.scripted_api.identity import REPO

SKILLS = sorted((REPO / ".claude" / "commands").glob("*.md"))
DOCS = SKILLS + [REPO / "README.md", REPO / "docs" / "user-guide" / "quick-start.md",
                 REPO / "docs" / "reference" / "scripted-api.md"]
DC_BUILDER = REPO / ".claude" / "commands" / "dc-builder.md"
ROOM2D_API = REPO / "src" / "frontend" / "react" / "src" / "room2d" / "room2dApi.ts"


@pytest.fixture(scope="module")
def live():
    return compile_project("contract-verbs", None, None, timeout=300)


def test_static_parse_lists_exactly_the_live_contract(live):
    static = set(cv.verb_names())
    runtime = {row["name"] for row in live["verbs"]}
    assert static == runtime, {"missed by the parser": sorted(runtime - static),
                               "not in the live contract": sorted(static - runtime)}
    assert all(row["type"] == "function" for row in live["verbs"]), \
        [row for row in live["verbs"] if row["type"] != "function"]


def test_build_verbs_match_the_live_list(live):
    assert cv.build_verbs() == live["buildVerbs"]
    assert set(cv.build_verbs()) <= set(cv.verb_names())


def test_rows_carry_signatures_from_the_source():
    rows = {row["name"]: row for row in cv.contract_rows()}
    assert rows["applyRoom"]["params"] == "plane: [number, number][], height: number"
    assert rows["applyRoom"]["returns"] == "{ walls: string[]; floor: string; ceiling: string } | false"
    assert rows["buildGeometryData"]["returns"] == "Promise<number>"
    assert rows["setProjectMeta"]["params"] == (
        "meta: { name?: string; inputUnit?: 'mm' | 'cm' | 'm'; roomTemperatureC?: number; casePath?: string }")
    assert rows["subscribe"]["returns"] == "() => void"
    assert rows["addPatch"]["build"] and not rows["buildGeometryData"]["build"]
    assert rows["placeModel"]["group"] == "placement"
    assert rows["setRoomPolygon"]["source"] == "contract" and rows["edit"]["source"] == "store"


def test_dc_builder_carries_the_generated_table():
    assert cv.current_block(DC_BUILDER) == cv.markdown(), \
        "regenerate (from src/): python -m agent.scripted_api.contract_verbs --update ../.claude/commands/dc-builder.md"


@pytest.mark.parametrize("path", DOCS, ids=lambda p: str(p.relative_to(REPO)))
def test_every_contract_verb_a_doc_names_exists(path):
    known = set(cv.verb_names())
    text = path.read_text(encoding="utf-8")
    named = set(re.findall(r"__dcdtuni\.([A-Za-z_$][\w$]*)", text))
    assert named <= known, sorted(named - known)
    spec_verbs = set(re.findall(r'"verb"\s*:\s*"([A-Za-z_$][\w$]*)"', text))
    spec_verbs |= set(re.findall(r"""\bstep\(\s*["']([A-Za-z_$][\w$]*)["']""", text))
    assert spec_verbs <= set(cv.build_verbs()), sorted(spec_verbs - set(cv.build_verbs()))


@pytest.mark.parametrize("path", DOCS, ids=lambda p: str(p.relative_to(REPO)))
def test_every_room2d_call_a_doc_names_exists(path):
    api = ROOM2D_API.read_text(encoding="utf-8")
    known = {re.match(r"[A-Za-z_$][\w$]*", entry).group(0)
             for _, entry, _ in cv._object_entries(api, "const api = {")}
    assert {"enterEditor", "exitEditor", "setAnnotations", "zoomToFit", "exportCanvasPNG"} <= known
    named = set(re.findall(r"\broom2d\.([A-Za-z_$][\w$]*)\s*\(", path.read_text(encoding="utf-8")))
    assert named <= known, sorted(named - known)
