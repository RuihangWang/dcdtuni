"""The post-processing load path: what it must still give after the speed-ups.

- Field files are trimmed to this rank's patches before the VTK reader sees
  them (Base604's ranks list ~4,300 empty entries per field, looked up
  linearly per enabled patch): the internalField and every kept entry stay
  byte for byte.
- Boundary patches are triangulated in one batched pass: per patch the same
  triangles and point fields as the per-patch path.
- A result the server prefetched when the case was selected is handed to the
  init asking for the same run/time, and only to it, with its boundary already
  triangulated exactly as get_mesh would.
"""

from pathlib import Path

import numpy as np
import pytest

import backend.aircooling.runner.postprocess as pp

FIELD = b"""FoamFile
{
    format      ascii;
    class       volScalarField;
    object      T;
}
internalField   nonuniform List<scalar> 2(300 301);
boundaryField
{
    wall_a
    {
        type            zeroGradient;
        value           nonuniform List<scalar> 0();
    }
    rack_b
    {
        type            fixedValue;
        value           uniform 295;
    }
    "procBoundary.*"
    {
        type            processor;
    }
    empty_c
    {
        type            zeroGradient;
    }
}
"""


def test_field_trim_keeps_the_internal_field_kept_entries_and_patterns():
    trimmed = pp._trim_field_boundary_entries(FIELD, {"rack_b"})
    assert trimmed is not None
    head = FIELD[:FIELD.find(b"boundaryField")]
    assert trimmed.startswith(head)
    assert b"rack_b" in trimmed and b"uniform 295" in trimmed
    assert b'"procBoundary.*"' in trimmed
    assert b"wall_a" not in trimmed and b"empty_c" not in trimmed
    assert trimmed.rstrip().endswith(b"}")


def test_field_trim_leaves_a_file_alone_when_nothing_goes_or_the_layout_is_unknown():
    assert pp._trim_field_boundary_entries(FIELD, {"wall_a", "rack_b", "empty_c"}) is None
    assert pp._trim_field_boundary_entries(b"no boundary here", {"x"}) is None


def test_batched_triangulation_equals_the_per_patch_path():
    pv = pytest.importorskip("pyvista")
    named = []
    for k, (nx, ny) in enumerate([(3, 2), (4, 4), (2, 5)]):
        plane = pv.Plane(center=(k * 3.0, 0, 0), i_resolution=nx, j_resolution=ny)
        box_side = plane.copy()
        box_side.cell_data["T"] = np.linspace(290 + k, 300 + k, box_side.n_cells)
        named.append((f"patch_{k}", box_side))
    # A folded patch, so the 45-degree split duplicates points along the fold.
    fold = pv.Box(bounds=(10, 11, 0, 1, 0, 1)).extract_surface()
    fold.cell_data["T"] = np.arange(fold.n_cells, dtype=float)
    named.append(("folded", fold))
    batched = pp._surface_triangle_caches(named)
    for name, mesh in named:
        single = pp._surface_triangle_cache(mesh)

        def triangles(cache):
            tri = cache["points"][cache["indices"]].reshape(-1, 9).astype(np.float64)
            t = np.asarray(cache["tri"].point_data["T"], dtype=np.float64)[cache["indices"]]
            rows = np.concatenate([tri, t], axis=1)
            return rows[np.lexsort(rows.T[::-1])]

        assert np.allclose(triangles(single), triangles(batched[name]), atol=1e-9), name


def test_prefetch_is_handed_to_the_matching_init_only(monkeypatch, tmp_path):
    reads = []

    def fake_read(run, time_value):
        reads.append((Path(run), time_value))
        return {"internalMesh": None}, {"sourceFormat": "openfoam"}

    import backend.aircooling.runner.native_numeric_input as nni
    monkeypatch.setattr(pp, "read_openfoam_case", fake_read)
    monkeypatch.setattr(nni, "capture_native_inputs", lambda run, t: {"bytes": "pinned"})
    prefetch = pp._ResultPrefetch()
    assert prefetch.start(tmp_path, 600) is True
    assert prefetch.start(tmp_path, 600.0) is False   # already reading this one
    assert prefetch.take(tmp_path, 500, timeout=5) is None
    snapshot, multiblock, info, triangles, volume = prefetch.take(tmp_path, 600, timeout=5)
    assert snapshot == {"bytes": "pinned"} and multiblock == {"internalMesh": None}
    assert triangles == {} and volume is None
    assert prefetch.take(tmp_path, 600, timeout=5) is None  # handed over once
    assert reads == [(tmp_path, 600.0)]
