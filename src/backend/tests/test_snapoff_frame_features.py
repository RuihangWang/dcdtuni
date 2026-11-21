"""Snap-off drops merged_rack_frame from feature refinement (performance).

In snap-off meshing (meshEngine dcCart; the three-way meshMode was retired on
2026-09-09) there is no feature SNAPPING, so refining
the rack frame's feature edges is pure cost: snappy's markFeatureCellLevel particle-
tracks every frame edge from the single locationInMesh on every feature iteration. On
200-rack cases this is the single biggest castellation cost (~275 s on Alibaba-
hongchou, 9 iterations whose tail refines ~0 cells yet still pays full tracing). The
frame geometry is still captured via refinementSurfaces, so dropping it from *features*
only removes the redundant edge refinement. Snap-on (meshEngine snappy, bodyFitted)
keeps it — snapping
needs the feature edges. See docs/developer/mesh-castellation-performance-findings.md.
"""
import copy
import json
import tempfile
from pathlib import Path

from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.tests._base_leakage_case import CFDRUN, make_case


def _gen_snappy(mesh_engine):
    """Generate snappyHexMeshDict text for Base_leakage with the given mesh engine."""
    data = copy.deepcopy(json.load(open(CFDRUN)))
    # meshMode is ignored since 2026-09-09: the engine alone decides snap on/off.
    data["meshGeometry"].setdefault("meshConfig", {})["meshEngine"] = mesh_engine
    with tempfile.TemporaryDirectory() as tmp:
        case_dir, cfdrun = make_case(Path(tmp), data)
        r = STLGenRunner(case_dir, cfdrun)
        r._load()
        r.validate_geometry()
        r._preflight_partition_wall_thickness()
        r._ensure_watertight_room_enclosure()
        r._prepare_runtime_metadata()
        r._generate_mesh_dicts()
        return (case_dir / "Mesh" / "system" / "snappyHexMeshDict").read_text()


def test_snapon_keeps_rack_frame_feature():
    # snappy (snap on, bodyFitted) must keep the frame feature edges for snapping.
    text = _gen_snappy("snappy")
    assert "merged_rack_frame.eMesh" in text


def test_snapoff_drops_rack_frame_feature():
    # Only meaningful if snap-on has the frame feature in the first place.
    on = _gen_snappy("snappy")
    assert "merged_rack_frame.eMesh" in on, "fixture exports no merged_rack_frame feature"
    text = _gen_snappy("dcCart")
    assert "merged_rack_frame.eMesh" not in text, (
        "dcCart: rack frame must be dropped from feature refinement"
    )
    # geometry still captured: frame remains a refinement surface
    assert "merged_rack_frame" in text, (
        "dcCart: rack frame surface must still be present"
    )
