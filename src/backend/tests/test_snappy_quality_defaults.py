"""DC-tuned snappy quality defaults (2026-04-28).

Snap settings tuned for orthogonal data-center geometry: lower tolerance,
fewer solver iterations, less aggressive feature snapping. With multi-block
blockMesh aligned to rack edges, snap has less work to do.
"""
from pathlib import Path

from .test_refinement_redesign import _bootstrap_runner, _write_dummy_stl


def test_snappy_quality_defaults_dc_tuned(tmp_path):
    """Render a minimal snappyHexMeshDict and assert the DC-tuned params."""
    stl_solids = {"merged_box.stl": ["Merged_Box"]}
    mesh_config = {"baseSize": 0.35, "meshEngine": "snappy"}
    tri_dir = tmp_path / "Mesh" / "constant" / "triSurface"
    tri_dir.mkdir(parents=True, exist_ok=True)
    for fn, names in stl_solids.items():
        _write_dummy_stl(tri_dir, fn, names)

    runner = _bootstrap_runner(tmp_path, mesh_config, {}, stl_solids)
    system_dir = tmp_path / "Mesh" / "system"
    runner._generate_snappy_hex_mesh_dict(system_dir)

    text = (system_dir / "snappyHexMeshDict").read_text()

    # castellatedMeshControls
    assert "nCellsBetweenLevels 1;" in text

    # snapControls — reduced snap aggressiveness for orthogonal DC geometry
    assert "nSmoothPatch 1;" in text
    assert "tolerance 1.5;" in text
    assert "nSolveIter 20;" in text
    assert "nRelaxIter 5;" in text
    assert "nFeatureSnapIter 10;" in text
    assert "implicitFeatureSnap true;" in text
    assert "multiRegionFeatureSnap true;" in text
    assert "nFaceSplitInterval 5;" in text
    assert "releasePoints true;" in text

    # meshQualityControls
    assert "maxNonOrtho 60;" in text
    assert "maxNonOrtho 70;" in text, "relaxed maxNonOrtho should be 70"
    # The strict checkMesh gate flags any face, boundary included, past
    # skewness 4. With snappy's usual 20 for boundary faces the mesher kept
    # what the gate then rejected (DCTS: two wall faces at 4.32).
    assert "maxBoundarySkewness 4;" in text
    assert "maxInternalSkewness 4;" in text
    assert "nSmoothScale 4;" in text
    assert "errorReduction 0.75;" in text

    # Old aggressive values must NOT survive
    assert "tolerance 2.0;" not in text
    assert "nSolveIter 30;" not in text
    assert "nFeatureSnapIter 15;" not in text
    assert "nFaceSplitInterval 7;" not in text
