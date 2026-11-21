"""TDD: mirror_outlet_patches.mirror_outlet_patches should flip server
outlet patches from left-edge to right-edge along the face's x-axis.

User screenshot after Y-up migration: every rack's server outlet patch
sits at x=0 (hard left). The intent is to relocate them to the opposite
edge of the face (mirror flip, not translate — so a patch at x=0 w=0.25
becomes x=0.75 w=0.25, filling the right quarter of the face).

Face coords are normalized [0,1] per side: x horizontal, y vertical.
Only the rack's `equipments[*].faces.outlet.patches` are touched —
inlet, top, bottom, left, right faces are left alone, and non-rack
models are untouched.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mirror_outlet_patches as M


def test_rect_outlet_patch_x_flips_using_one_minus_x_minus_w():
    model = {
        "kind": "rack",
        "rack": {
            "equipments": [
                {
                    "faces": {
                        "outlet": {"patches": [
                            {"type": "rect", "x": 0.0, "y": 0, "w": 0.25, "h": 1},
                        ]}
                    }
                }
            ]
        },
    }
    changed = M.mirror_outlet_patches(model)
    p = model["rack"]["equipments"][0]["faces"]["outlet"]["patches"][0]
    assert p["x"] == 0.75  # 1 - 0 - 0.25
    assert p["y"] == 0
    assert p["w"] == 0.25
    assert p["h"] == 1
    assert changed == 1


def test_circle_outlet_patch_cx_flips_using_one_minus_cx():
    model = {
        "kind": "rack",
        "rack": {
            "equipments": [
                {
                    "faces": {
                        "outlet": {"patches": [
                            {"type": "circle", "cx": 0.3, "cy": 0.5, "r": 0.1},
                        ]}
                    }
                }
            ]
        },
    }
    M.mirror_outlet_patches(model)
    p = model["rack"]["equipments"][0]["faces"]["outlet"]["patches"][0]
    assert p["cx"] == 0.7  # 1 - 0.3
    assert p["cy"] == 0.5
    assert p["r"] == 0.1


def test_inlet_and_other_faces_unchanged():
    model = {
        "kind": "rack",
        "rack": {
            "equipments": [
                {
                    "faces": {
                        "inlet": {"patches": [{"type": "rect", "x": 0.1, "y": 0, "w": 0.2, "h": 1}]},
                        "outlet": {"patches": [{"type": "rect", "x": 0.0, "y": 0, "w": 0.25, "h": 1}]},
                        "top":    {"patches": [{"type": "rect", "x": 0.0, "y": 0, "w": 0.3, "h": 0.3}]},
                    }
                }
            ]
        },
    }
    M.mirror_outlet_patches(model)
    inlet = model["rack"]["equipments"][0]["faces"]["inlet"]["patches"][0]
    top = model["rack"]["equipments"][0]["faces"]["top"]["patches"][0]
    assert inlet == {"type": "rect", "x": 0.1, "y": 0, "w": 0.2, "h": 1}
    assert top == {"type": "rect", "x": 0.0, "y": 0, "w": 0.3, "h": 0.3}


def test_non_rack_model_is_untouched():
    model = {
        "kind": "box",
        "faces": {
            "outlet": {"patches": [{"type": "rect", "x": 0.0, "y": 0, "w": 0.25, "h": 1}]}
        },
    }
    changed = M.mirror_outlet_patches(model)
    # mirror_outlet_patches only touches rack.equipments[].faces.outlet.
    # A box with a face literally called "outlet" is unrelated.
    assert model["faces"]["outlet"]["patches"][0]["x"] == 0.0
    assert changed == 0


def test_project_walker_touches_all_rack_equipments_across_models():
    proj = {
        "models": [
            {
                "kind": "rack",
                "rack": {"equipments": [
                    {"faces": {"outlet": {"patches": [{"type": "rect", "x": 0.0, "y": 0, "w": 0.25, "h": 1}]}}},
                    {"faces": {"outlet": {"patches": [{"type": "circle", "cx": 0.2, "cy": 0.5, "r": 0.1}]}}},
                ]},
            },
            {"kind": "floor"},
            {
                "kind": "rack",
                "rack": {"equipments": [
                    {"faces": {"outlet": {"patches": [{"type": "rect", "x": 0.1, "y": 0, "w": 0.3, "h": 0.5}]}}},
                ]},
            },
        ]
    }
    changed = M.mirror_outlet_patches_in_project(proj)
    assert changed == 3
    r0 = proj["models"][0]["rack"]["equipments"]
    assert r0[0]["faces"]["outlet"]["patches"][0]["x"] == 0.75
    assert r0[1]["faces"]["outlet"]["patches"][0]["cx"] == 0.8
    r2 = proj["models"][2]["rack"]["equipments"][0]["faces"]["outlet"]["patches"][0]
    assert r2["x"] == pytest.approx(0.6)  # 1 - 0.1 - 0.3


def test_double_apply_produces_original():
    # Mirroring twice must be the identity — lets the script be re-runnable
    # without creeping drift.
    model = {
        "kind": "rack",
        "rack": {"equipments": [
            {"faces": {"outlet": {"patches": [{"type": "rect", "x": 0.1, "y": 0, "w": 0.2, "h": 1}]}}},
        ]},
    }
    M.mirror_outlet_patches(model)
    M.mirror_outlet_patches(model)
    p = model["rack"]["equipments"][0]["faces"]["outlet"]["patches"][0]
    assert p["x"] == pytest.approx(0.1)  # back to the start
    assert p["w"] == 0.2
