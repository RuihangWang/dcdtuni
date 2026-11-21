"""TDD: patch coord migration for Z-up→Y-up.

When the Y-up refactor changed the face layouts, `model.faces[face_id].patches`
stored as face-local (x, y) did NOT get flipped. Pre-migration Z-up top face
had face-local y = world +Y; post-migration Y-up top face has face-local y
= world -Z. Same numeric (x, y) lands at a DIFFERENT physical point after
the y↔z world swap.

Per-face flip table (W=model.base.W, D=model.base.D):

  front / back:     rect  (x, y)     → (W - x - w, y)
                    circle (cx, cy)  → (W - cx, cy)
  left / right:     rect  (x, y)     → (D - x - w, y)
                    circle (cx, cy)  → (D - cx, cy)
  top / bottom:     rect  (x, y)     → (x, D - y - h)
                    circle (cx, cy)  → (cx, D - cy)

Normalized server patches use dim=1 in the same formulas.

These tests import migrate_patches() from migrate_yz.py.
"""
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import migrate_yz as M


def test_top_face_rect_patch_y_flips():
    model = {
        "kind": "floor",
        "base": {"W": 4, "D": 6, "H": 0},
        "faces": {
            "top": {"patches": [{"id": "p1", "type": "rect", "x": 2, "y": 3, "w": 1, "h": 1}]}
        },
    }
    M.migrate_patches(model)
    p = model["faces"]["top"]["patches"][0]
    assert p["x"] == 2  # unchanged
    assert p["y"] == 6 - 3 - 1  # D - y - h = 2
    assert p["w"] == 1
    assert p["h"] == 1


def test_top_face_circle_patch_cy_flips():
    model = {
        "kind": "floor",
        "base": {"W": 4, "D": 6, "H": 0},
        "faces": {
            "top": {"patches": [{"id": "c1", "type": "circle", "cx": 2, "cy": 3, "r": 0.5}]}
        },
    }
    M.migrate_patches(model)
    p = model["faces"]["top"]["patches"][0]
    assert p["cx"] == 2
    assert p["cy"] == 6 - 3  # D - cy = 3


def test_bottom_face_rect_patch_y_flips():
    model = {
        "kind": "box",
        "base": {"W": 2, "D": 3, "H": 1},
        "faces": {
            "bottom": {"patches": [{"type": "rect", "x": 0.5, "y": 1, "w": 0.25, "h": 0.5}]}
        },
    }
    M.migrate_patches(model)
    p = model["faces"]["bottom"]["patches"][0]
    assert p["x"] == 0.5
    assert p["y"] == pytest.approx(3 - 1 - 0.5)  # = 1.5


def test_front_face_rect_patch_x_flips():
    model = {
        "kind": "wall",
        "base": {"W": 5, "D": 0, "H": 3},
        "faces": {
            "front": {"patches": [{"type": "rect", "x": 1, "y": 0.5, "w": 0.5, "h": 0.5}]}
        },
    }
    M.migrate_patches(model)
    p = model["faces"]["front"]["patches"][0]
    assert p["x"] == pytest.approx(5 - 1 - 0.5)  # = 3.5
    assert p["y"] == 0.5  # unchanged


def test_back_face_circle_patch_cx_flips():
    model = {
        "kind": "box",
        "base": {"W": 4, "D": 2, "H": 2},
        "faces": {
            "back": {"patches": [{"type": "circle", "cx": 1, "cy": 1, "r": 0.3}]}
        },
    }
    M.migrate_patches(model)
    p = model["faces"]["back"]["patches"][0]
    assert p["cx"] == pytest.approx(4 - 1)  # = 3
    assert p["cy"] == 1


def test_left_face_rect_uses_D():
    model = {
        "kind": "box",
        "base": {"W": 2, "D": 4, "H": 3},
        "faces": {
            "left": {"patches": [{"type": "rect", "x": 0.5, "y": 1, "w": 0.5, "h": 0.5}]}
        },
    }
    M.migrate_patches(model)
    p = model["faces"]["left"]["patches"][0]
    assert p["x"] == pytest.approx(4 - 0.5 - 0.5)  # D - x - w = 3
    assert p["y"] == 1


def test_right_face_circle_uses_D():
    model = {
        "kind": "box",
        "base": {"W": 2, "D": 4, "H": 3},
        "faces": {
            "right": {"patches": [{"type": "circle", "cx": 1, "cy": 1.5, "r": 0.2}]}
        },
    }
    M.migrate_patches(model)
    p = model["faces"]["right"]["patches"][0]
    assert p["cx"] == pytest.approx(4 - 1)  # = 3
    assert p["cy"] == 1.5


def test_partition_wall_patches_stored_on_front_face():
    """Partition walls use model.faces.front.patches too (same front flip)."""
    model = {
        "kind": "partitionWall",
        "base": {"W": 4, "D": 0.05, "H": 2.7},
        "faces": {
            "front": {"patches": [{"type": "rect", "x": 0.5, "y": 0.5, "w": 1, "h": 1}]}
        },
    }
    M.migrate_patches(model)
    p = model["faces"]["front"]["patches"][0]
    assert p["x"] == pytest.approx(4 - 0.5 - 1)  # = 2.5
    assert p["y"] == 0.5


def test_server_equipment_patches_normalized_dim1():
    """Rack equipment patches use normalized [0,1] coords; dim = 1."""
    model = {
        "kind": "rack",
        "base": {"W": 0.6, "D": 1.2, "H": 1.956},
        "rack": {
            "equipments": [
                {
                    "id": "eq1",
                    "faces": {
                        "inlet": {
                            "patches": [{"type": "rect", "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}]
                        },
                        "top": {
                            "patches": [{"type": "circle", "cx": 0.5, "cy": 0.6, "r": 0.1}]
                        },
                    },
                }
            ]
        },
    }
    M.migrate_patches(model)
    inlet = model["rack"]["equipments"][0]["faces"]["inlet"]["patches"][0]
    # inlet is the rack's front-facing side of the server → uses W-equivalent = 1
    assert inlet["x"] == pytest.approx(1 - 0.1 - 0.3)  # = 0.6
    assert inlet["y"] == 0.2
    top = model["rack"]["equipments"][0]["faces"]["top"]["patches"][0]
    # top uses D-equivalent = 1 for y
    assert top["cx"] == 0.5
    assert top["cy"] == pytest.approx(1 - 0.6)  # = 0.4


def test_cutouts_polygon_rings_flip_per_face():
    """Cutouts stored as polygon rings [[[x,y],...]] — flip vertices per face."""
    model = {
        "kind": "floor",
        "base": {"W": 4, "D": 6, "H": 0},
        "cutouts": [
            {"faceId": "top", "polygon": [[[[2, 3], [3, 3], [3, 4], [2, 4], [2, 3]]]]}
        ],
    }
    M.migrate_patches(model)
    ring = model["cutouts"][0]["polygon"][0][0]
    # y → D - y so 3→3, 4→2 (D=6)
    assert ring[0] == [2, 6 - 3]  # [2, 3]
    assert ring[1] == [3, 6 - 3]  # [3, 3]
    assert ring[2] == [3, 6 - 4]  # [3, 2]
    assert ring[3] == [2, 6 - 4]  # [2, 2]


def test_floor_polygon_flips_y_with_polyMaxY_rule():
    """Floor `model.polygon` vertices are face-local on 'top' → y flips too."""
    model = {
        "kind": "floor",
        "base": {"W": 5, "D": 7, "H": 0},
        "polygon": [[1, 2], [4, 2], [4, 5], [1, 5]],
    }
    M.migrate_patches(model)
    # D - y flip
    assert model["polygon"] == [[1, 7 - 2], [4, 7 - 2], [4, 7 - 5], [1, 7 - 5]]


def test_non_top_level_models_recursed():
    """Migration must walk all models in state.models[]."""
    proj = {
        "models": [
            {
                "kind": "floor",
                "base": {"W": 4, "D": 6, "H": 0},
                "faces": {"top": {"patches": [{"type": "rect", "x": 1, "y": 2, "w": 1, "h": 1}]}},
            }
        ]
    }
    M.migrate_patches_in_project(proj)
    p = proj["models"][0]["faces"]["top"]["patches"][0]
    assert p["y"] == 6 - 2 - 1  # = 3
