"""Fan inlet/outlet patch pairing in FieldGenerator (fields.py).

`_find_server_inlet_patch` / `_find_server_outlet_patch` resolve the sibling
patch of a fan-server face. Stacked servers in one U range make stems that
are in prefix relation (`rack_fan` vs `rack_fan_2`), so the pairing tries an
exact `{stem}_inlet_patch` / `{stem}_outlet_patch` match among the registered
fan patches first and only falls back to the historic `startswith` prefix
scan when no exact whole-face sibling exists. With no exact hit, behaviour
must be identical to the prefix-scan-only implementation (including the
legacy string-substitution fallback when nothing is registered).
"""
import pytest

from backend.aircooling.runner.fields import FieldGenerator


def _make_gen(tmp_path, bc_data):
    """FieldGenerator with a hand-rolled bridge-BC registry dict."""
    gen = FieldGenerator(
        case_dir=tmp_path,
        simulation_config={"roomTemperature": 22.0},
        room_config={},
        patch_geometry={},
        bridge_registry=None,
    )
    gen._imported_bc_data = bc_data
    return gen


def _fan_pair(stem, *, flow_rate=0.1, power=300.0):
    """Whole-face fan_inlet + fan_outlet registry entries for one server."""
    return {
        f"{stem}_inlet_patch": {"bc_type": "fan_inlet", "flow_rate": flow_rate},
        f"{stem}_outlet_patch": {
            "bc_type": "fan_outlet",
            "power": power,
            "flow_rate": flow_rate,
        },
    }


# ── prefix-related stems (stacked servers) each pair with their own face ──

def test_prefix_related_stems_pair_with_own_whole_face_patches(tmp_path):
    """`rack_fan` and `rack_fan_2` must never cross-pair, regardless of
    registry insertion order (the stacked sibling is registered first)."""
    bc_data = {}
    bc_data.update(_fan_pair("rack_fan_2"))
    bc_data.update(_fan_pair("rack_fan"))
    gen = _make_gen(tmp_path, bc_data)

    assert gen._find_server_inlet_patch("rack_fan_outlet_patch") \
        == "rack_fan_inlet_patch"
    assert gen._find_server_outlet_patch("rack_fan_inlet_patch") \
        == "rack_fan_outlet_patch"
    assert gen._find_server_inlet_patch("rack_fan_2_outlet_patch") \
        == "rack_fan_2_inlet_patch"
    assert gen._find_server_outlet_patch("rack_fan_2_inlet_patch") \
        == "rack_fan_2_outlet_patch"


def test_prefix_related_stems_pair_with_own_per_patch_faces(tmp_path):
    """Per-patch (`_p{idx}`) naming: no whole-face sibling exists, so the
    prefix scan runs — each stem must still stay within its own server."""
    gen = _make_gen(tmp_path, {
        # Stacked sibling registered FIRST to stress insertion order.
        "rack_fan_2_inlet_p0": {"bc_type": "fan_inlet", "flow_rate": 0.1},
        "rack_fan_2_outlet_p0": {"bc_type": "fan_outlet", "flow_rate": 0.1},
        "rack_fan_inlet_p0": {"bc_type": "fan_inlet", "flow_rate": 0.1},
        "rack_fan_inlet_p1": {"bc_type": "fan_inlet", "flow_rate": 0.1},
        "rack_fan_outlet_p0": {"bc_type": "fan_outlet", "flow_rate": 0.1},
        "rack_fan_outlet_p1": {"bc_type": "fan_outlet", "flow_rate": 0.1},
    })

    assert gen._find_server_inlet_patch("rack_fan_outlet_p0") \
        == "rack_fan_inlet_p0"
    assert gen._find_server_inlet_patch("rack_fan_outlet_p1") \
        == "rack_fan_inlet_p0"  # documented first-match behaviour
    assert gen._find_server_outlet_patch("rack_fan_inlet_p1") \
        == "rack_fan_outlet_p0"
    assert gen._find_server_inlet_patch("rack_fan_2_outlet_p0") \
        == "rack_fan_2_inlet_p0"
    assert gen._find_server_outlet_patch("rack_fan_2_inlet_p0") \
        == "rack_fan_2_outlet_p0"


# ── exact match takes precedence over prefix-scan order ──

def test_exact_whole_face_inlet_wins_over_earlier_per_patch_entry(tmp_path):
    """When both `X_inlet_p0` (registered first) and the whole-face
    `X_inlet_patch` exist, the exact match must win — the old prefix scan
    returned whichever came first in registry order."""
    gen = _make_gen(tmp_path, {
        "srv_inlet_p0": {"bc_type": "fan_inlet", "flow_rate": 0.1},
        "srv_inlet_patch": {"bc_type": "fan_inlet", "flow_rate": 0.1},
        "srv_outlet_patch": {"bc_type": "fan_outlet", "flow_rate": 0.1},
    })
    assert gen._find_server_inlet_patch("srv_outlet_patch") \
        == "srv_inlet_patch"


def test_exact_whole_face_outlet_wins_over_earlier_per_patch_entry(tmp_path):
    gen = _make_gen(tmp_path, {
        "srv_outlet_p0": {"bc_type": "fan_outlet", "flow_rate": 0.1},
        "srv_outlet_patch": {"bc_type": "fan_outlet", "flow_rate": 0.1},
        "srv_inlet_patch": {"bc_type": "fan_inlet", "flow_rate": 0.1},
    })
    assert gen._find_server_outlet_patch("srv_inlet_patch") \
        == "srv_outlet_patch"


def test_exact_match_requires_fan_bc_type(tmp_path):
    """A patch named `{stem}_inlet_patch` that is NOT a fan_inlet must not
    satisfy the exact match; the prefix scan picks the real fan inlet."""
    gen = _make_gen(tmp_path, {
        "srv_inlet_patch": {"bc_type": "wall"},
        "srv_inlet_p0": {"bc_type": "fan_inlet", "flow_rate": 0.1},
        "srv_outlet_patch": {"bc_type": "fan_outlet", "flow_rate": 0.1},
    })
    assert gen._find_server_inlet_patch("srv_outlet_patch") \
        == "srv_inlet_p0"


# ── no exact hit ⇒ behaviour identical to the legacy prefix scan ──

def test_n_outlets_to_one_whole_face_inlet(tmp_path):
    gen = _make_gen(tmp_path, {
        "srv_inlet_patch": {"bc_type": "fan_inlet", "flow_rate": 0.1},
        "srv_outlet_p0": {"bc_type": "fan_outlet", "flow_rate": 0.1},
        "srv_outlet_p1": {"bc_type": "fan_outlet", "flow_rate": 0.1},
    })
    assert gen._find_server_inlet_patch("srv_outlet_p0") == "srv_inlet_patch"
    assert gen._find_server_inlet_patch("srv_outlet_p1") == "srv_inlet_patch"


def test_no_registered_sibling_falls_back_to_string_substitution(tmp_path):
    """Nothing registered ⇒ the legacy replacement result, verbatim."""
    gen = _make_gen(tmp_path, {})
    assert gen._find_server_inlet_patch("srv_outlet_patch") \
        == "srv_inlet_patch"
    assert gen._find_server_inlet_patch("srv_outlet_p2") == "srv_inlet_p2"
    assert gen._find_server_outlet_patch("srv_inlet_patch") \
        == "srv_outlet_patch"
    assert gen._find_server_outlet_patch("srv_inlet_p2") == "srv_outlet_p2"


def test_names_without_infix_marker_use_pure_substitution(tmp_path):
    """No `_outlet_`/`_inlet_` infix ⇒ rfind misses, substitution applies
    (registry contents irrelevant, same as before the change)."""
    gen = _make_gen(tmp_path, _fan_pair("srv"))
    assert gen._find_server_inlet_patch("srvX_outlet") == "srvX_inlet"
    assert gen._find_server_outlet_patch("srvX_inlet") == "srvX_outlet"


# ── a liquid-cooled fan server's air faces are named front / outlet_p{i} ──

def test_front_named_fan_inlet_pairs_with_its_own_server(tmp_path):
    """The React export names a liquidServer's air inlet `{stem}_front` (role
    fan_inlet) beside `{stem}_outlet_p0`; `server_1` must not take
    `server_10`'s face."""
    bc_data = {
        "rack_server_10_front": {"bc_type": "fan_inlet", "flow_rate": 0.1},
        "rack_server_10_outlet_p0": {"bc_type": "fan_outlet", "power": 300.0, "flow_rate": 0.1},
        "rack_server_1_front": {"bc_type": "fan_inlet", "flow_rate": 0.1},
        "rack_server_1_outlet_p0": {"bc_type": "fan_outlet", "power": 300.0, "flow_rate": 0.1},
    }
    gen = _make_gen(tmp_path, bc_data)
    assert gen._find_server_inlet_patch("rack_server_1_outlet_p0") == "rack_server_1_front"
    assert gen._find_server_inlet_patch("rack_server_10_outlet_p0") == "rack_server_10_front"
    assert gen._find_server_outlet_patch("rack_server_1_front") == "rack_server_1_outlet_p0"
    assert gen._find_server_outlet_patch("rack_server_10_front") == "rack_server_10_outlet_p0"
