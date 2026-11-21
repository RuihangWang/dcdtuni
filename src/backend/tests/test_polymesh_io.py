"""polymesh_io must read ASCII and OpenFOAM-binary polyMesh identically.

The mesh pipeline switched ``constant/polyMesh`` to binary for speed, so every
reader that used to assume ASCII has to agree byte-for-byte across both formats.
These tests build small synthetic polyMesh files in both encodings rather than
depending on a meshed case.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

from backend.aircooling.runner import polymesh_io as P


HEADER = """\
FoamFile
{{
    version     2.0;
    format      {fmt};
    arch        "LSB;label=32;scalar=64";
    class       {cls};
    location    "constant/polyMesh";
    object      {obj};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

"""


def _header(fmt: str, cls: str, obj: str, note: str | None = None) -> bytes:
    text = HEADER.format(fmt=fmt, cls=cls, obj=obj)
    if note:
        text = text.replace('    class ', f'    note        "{note}";\n    class ', 1)
    return text.encode()


POINTS = np.array(
    [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 2.0, 0.0], [0.5, -1.25, 3.0]]
)


def _write_points(path, fmt):
    if fmt == "binary":
        body = b"%d\n(" % len(POINTS) + POINTS.astype(np.float64).tobytes() + b")\n"
    else:
        rows = "\n".join(f"({p[0]} {p[1]} {p[2]})" for p in POINTS)
        body = (f"{len(POINTS)}\n(\n{rows}\n)\n").encode()
    path.write_bytes(_header(fmt, "vectorField", "points") + body)


LABELS = np.array([0, 1, 1, 2, 3, 5, 8, 13], dtype=np.int32)


def _write_labels(path, fmt, obj="owner", note=None):
    if fmt == "binary":
        body = b"%d\n(" % len(LABELS) + LABELS.astype(np.int32).tobytes() + b")\n"
    else:
        body = (f"{len(LABELS)}\n(\n" + "\n".join(str(v) for v in LABELS) + "\n)\n").encode()
    path.write_bytes(_header(fmt, "labelList", obj, note) + body)


FACES = [[0, 1, 2, 3], [4, 5, 6], [7, 8, 9, 10, 11]]


def _write_faces_binary(path):
    offsets = [0]
    for f in FACES:
        offsets.append(offsets[-1] + len(f))
    verts = [v for f in FACES for v in f]
    body = (
        b"%d\n(" % len(offsets)
        + struct.pack(f"<{len(offsets)}i", *offsets)
        + b")\n\n"
        + b"%d\n(" % len(verts)
        + struct.pack(f"<{len(verts)}i", *verts)
        + b")\n"
    )
    path.write_bytes(_header("binary", "faceCompactList", "faces") + body)


@pytest.mark.parametrize("fmt", ["ascii", "binary"])
def test_read_points_matches_source(tmp_path, fmt):
    path = tmp_path / "points"
    _write_points(path, fmt)
    assert P.foam_header_info(path.read_bytes())["format"] == fmt
    np.testing.assert_allclose(P.read_points(path), POINTS)


def test_points_ascii_and_binary_agree(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _write_points(a, "ascii")
    _write_points(b, "binary")
    np.testing.assert_allclose(P.read_points(a), P.read_points(b))


@pytest.mark.parametrize("fmt", ["ascii", "binary"])
def test_read_label_list(tmp_path, fmt):
    path = tmp_path / "owner"
    _write_labels(path, fmt)
    np.testing.assert_array_equal(P.read_label_list(path), LABELS)


def test_read_owner_note(tmp_path):
    path = tmp_path / "owner"
    _write_labels(
        path, "binary", note="nPoints:727027  nCells:607344  nFaces:1941413  nInternalFaces:1818601"
    )
    assert P.read_owner_note(path) == {
        "nPoints": 727027,
        "nCells": 607344,
        "nFaces": 1941413,
        "nInternalFaces": 1818601,
    }


def test_read_owner_note_absent(tmp_path):
    path = tmp_path / "owner"
    _write_labels(path, "binary")
    assert P.read_owner_note(path) == {}


def test_read_faces_range_binary(tmp_path):
    path = tmp_path / "faces"
    _write_faces_binary(path)
    assert P.read_faces_range(path, 0, 3) == FACES
    assert P.read_faces_range(path, 1, 1) == [FACES[1]]
    # out-of-range slices clamp instead of raising
    assert P.read_faces_range(path, 2, 99) == [FACES[2]]
    assert P.read_faces_range(path, 99, 5) == []


def _write_two_region_mesh(tmp_path, extra_isolated=False):
    """4 cells: {0,1} joined, {2,3} joined, the two pairs disconnected."""
    owner = [0, 2]
    neighbour = [1, 3]
    if extra_isolated:
        # cell 4 hangs off cell 0 by a single internal face -> "isolated"
        owner.append(0)
        neighbour.append(4)
    n_cells = 5 if extra_isolated else 4
    poly = tmp_path / "polyMesh"
    poly.mkdir()
    for name, values in (("owner", owner), ("neighbour", neighbour)):
        arr = np.array(values, dtype=np.int32)
        note = f"nPoints:0  nCells:{n_cells}  nFaces:{len(owner)}  nInternalFaces:{len(neighbour)}"
        body = b"%d\n(" % len(arr) + arr.tobytes() + b")\n"
        (poly / name).write_bytes(_header("binary", "labelList", name, note) + body)
    return poly


def test_topology_summary_counts_regions(tmp_path):
    poly = _write_two_region_mesh(tmp_path)
    summary = P.mesh_topology_summary(poly)
    assert summary is not None
    assert summary["nCells"] == 4
    assert summary["nRegions"] == 2
    assert summary["regionSizes"] == [2, 2]


def test_topology_summary_flags_isolated_cells(tmp_path):
    poly = _write_two_region_mesh(tmp_path, extra_isolated=True)
    summary = P.mesh_topology_summary(poly)
    assert summary is not None
    # cells 1, 2, 3 and 4 each touch exactly one internal face
    assert summary["nIsolatedCells"] >= 1
    assert summary["nRegions"] == 2


def test_topology_summary_missing_mesh_returns_none(tmp_path):
    assert P.mesh_topology_summary(tmp_path / "nope") is None
