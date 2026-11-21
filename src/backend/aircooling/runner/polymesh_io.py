"""Native readers for ``constant/polyMesh``, ASCII **and** OpenFOAM binary.

The mesh pipeline writes polyMesh in binary (much faster to write and re-read
than ASCII — measured on Base_leakage: ``reconstructParMesh`` 10.3 s -> 1.8 s).
Every file records its own format in its ``FoamFile`` header, so readers only
have to branch on that. ``boundary`` is a dictionary and stays human-readable in
both modes.

Binary layout, for reference::

    // * * * ... * //
    <count>
    (<count fixed-width records>)

``points`` is a ``vectorField`` (3 scalars per record), ``owner`` / ``neighbour``
are ``labelList`` (1 label per record), and ``faces`` is a ``faceCompactList``:
an offsets list of length ``nFaces + 1`` followed by the flat vertex list.
Record widths come from the header's ``arch "LSB;label=32;scalar=64"``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

__all__ = [
    "foam_header_info",
    "read_points",
    "read_label_list",
    "read_faces_range",
    "read_owner_note",
    "mesh_topology_summary",
    "read_all_faces",
    "cell_centres",
]

_LABEL_DTYPES = {1: np.int8, 2: np.int16, 4: np.int32, 8: np.int64}
_SCALAR_DTYPES = {4: np.float32, 8: np.float64}

_RE_FORMAT = re.compile(r"format\s+(\w+)\s*;")
_RE_ARCH = re.compile(r'arch\s+"([^"]*)"')
_RE_NOTE = re.compile(r'note\s+"([^"]*)"')


def foam_header_info(raw: bytes) -> Dict[str, Any]:
    """Return ``{format, label_bytes, scalar_bytes}`` from a FoamFile header."""
    head = raw[:2048].decode("utf-8", errors="replace")
    fmt_match = _RE_FORMAT.search(head)
    info: Dict[str, Any] = {
        "format": fmt_match.group(1) if fmt_match else "ascii",
        "label_bytes": 4,
        "scalar_bytes": 8,
    }
    arch_match = _RE_ARCH.search(head)
    if arch_match:
        arch = arch_match.group(1)
        label = re.search(r"label=(\d+)", arch)
        if label:
            info["label_bytes"] = int(label.group(1)) // 8
        scalar = re.search(r"scalar=(\d+)", arch)
        if scalar:
            info["scalar_bytes"] = int(scalar.group(1)) // 8
    return info


def _payload_start(raw: bytes) -> int:
    """Offset just past the ``// * * * … * //`` separator that ends the header."""
    marker = raw.find(b"// * * *")
    if marker < 0:
        return 0
    newline = raw.find(b"\n", marker)
    return len(raw) if newline < 0 else newline + 1


def _binary_list_at(raw: bytes, pos: int):
    """Locate one binary list at/after *pos*; return ``(count, data_offset)``."""
    n = len(raw)
    while pos < n and raw[pos : pos + 1] in b" \t\r\n":
        pos += 1
    start = pos
    while pos < n and raw[pos : pos + 1].isdigit():
        pos += 1
    if pos == start:
        raise ValueError("binary polyMesh list: no count before '('")
    count = int(raw[start:pos])
    while pos < n and raw[pos : pos + 1] in b" \t\r\n":
        pos += 1
    if raw[pos : pos + 1] != b"(":
        raise ValueError("binary polyMesh list: missing '(' after count")
    return count, pos + 1


def _ascii_payload(raw: bytes):
    """Return ``(count, text_between_the_outer_parens)`` for an ASCII list."""
    text = raw.decode("utf-8", errors="replace")
    start = _payload_start(raw)
    # first standalone integer after the header is the element count
    match = re.compile(r"\s(\d+)\s*\n\s*\(", re.S).search(text, max(0, start - 1))
    if not match:
        raise ValueError("ASCII polyMesh list: no '<count>\\n(' found")
    count = int(match.group(1))
    body_start = match.end()
    closing = text.find("\n)", body_start)
    if closing < 0:
        closing = len(text)
    return count, text[body_start:closing]


def read_points(points_path: Path) -> np.ndarray:
    """Read ``points`` → ``(N, 3)`` float array."""
    raw = Path(points_path).read_bytes()
    info = foam_header_info(raw)
    if info["format"] == "binary":
        count, offset = _binary_list_at(raw, _payload_start(raw))
        dtype = _SCALAR_DTYPES[info["scalar_bytes"]]
        return np.frombuffer(raw, dtype=dtype, count=count * 3, offset=offset).reshape(-1, 3)
    count, body = _ascii_payload(raw)
    nums = np.fromstring(body.replace("(", " ").replace(")", " "), dtype=np.float64, sep=" ")
    return nums[: (len(nums) // 3) * 3].reshape(-1, 3)


def read_label_list(path: Path) -> np.ndarray:
    """Read a ``labelList`` (``owner`` / ``neighbour``) → 1-D integer array."""
    raw = Path(path).read_bytes()
    info = foam_header_info(raw)
    if info["format"] == "binary":
        count, offset = _binary_list_at(raw, _payload_start(raw))
        dtype = _LABEL_DTYPES[info["label_bytes"]]
        return np.frombuffer(raw, dtype=dtype, count=count, offset=offset)
    count, body = _ascii_payload(raw)
    return np.fromstring(body, dtype=np.int64, sep=" ")[:count]


def read_faces_range(faces_path: Path, start: int, count: int) -> List[List[int]]:
    """Read faces ``[start, start + count)`` as lists of point indices."""
    raw = Path(faces_path).read_bytes()
    info = foam_header_info(raw)
    if info["format"] != "binary":
        raise ValueError("read_faces_range: ASCII faces handled by MeshPreviewLoader")
    dtype = _LABEL_DTYPES[info["label_bytes"]]
    width = info["label_bytes"]
    n_offsets, off_start = _binary_list_at(raw, _payload_start(raw))
    offsets = np.frombuffer(raw, dtype=dtype, count=n_offsets, offset=off_start)
    pos = off_start + n_offsets * width
    while pos < len(raw) and raw[pos : pos + 1] != b")":
        pos += 1
    n_verts, vert_start = _binary_list_at(raw, pos + 1)
    verts = np.frombuffer(raw, dtype=dtype, count=n_verts, offset=vert_start)
    n_faces = n_offsets - 1
    lo = max(0, min(start, n_faces))
    hi = max(lo, min(start + count, n_faces))
    return [verts[offsets[i] : offsets[i + 1]].astype(int).tolist() for i in range(lo, hi)]


def read_owner_note(owner_path: Path) -> Dict[str, int]:
    """Parse the ``note "nPoints:… nCells:… nFaces:… nInternalFaces:…"`` header."""
    head = Path(owner_path).read_bytes()[:4096].decode("utf-8", errors="replace")
    match = _RE_NOTE.search(head)
    if not match:
        return {}
    return {k: int(v) for k, v in re.findall(r"(\w+):(\d+)", match.group(1))}


def mesh_topology_summary(poly_dir: Path) -> Optional[Dict[str, int]]:
    """Connected-region count and isolated-cell count, computed from polyMesh.

    ``checkMesh -allTopology`` reports both, but it costs ~9 s on a 600k-cell
    mesh against ~3 s for a plain ``checkMesh``. The two numbers that actually
    drive the runner's cleanup are cheap to get directly: region count is a
    connected-components pass over the cell adjacency graph built from
    ``owner``/``neighbour``, and "isolated" cells are those with at most one
    internal face. Callers use this to decide whether the expensive
    ``-allTopology`` pass (which also WRITES the cellSets that ``subsetMesh``
    consumes) is needed at all.

    ``regionSizes`` is the cell count of each connected region, largest first —
    the runner uses it to tell a tiny snappy orphan (auto-removed) from a
    legitimately separate domain such as a raised-floor plenum (kept).

    Returns ``None`` when the mesh cannot be read, so the caller falls back to
    running ``checkMesh -allTopology`` unconditionally.
    """
    poly_dir = Path(poly_dir)
    owner_path = poly_dir / "owner"
    neighbour_path = poly_dir / "neighbour"
    if not owner_path.exists() or not neighbour_path.exists():
        return None
    try:
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components

        owner = read_label_list(owner_path).astype(np.int64)
        neighbour = read_label_list(neighbour_path).astype(np.int64)
        if owner.size == 0:
            return None
        note = read_owner_note(owner_path)
        n_cells = note.get("nCells") or int(max(owner.max(), neighbour.max() if neighbour.size else 0) + 1)

        # Internal faces are the first nInternalFaces entries of owner; they are
        # exactly the ones that have a neighbour.
        n_internal = neighbour.size
        own_int = owner[:n_internal]

        internal_face_count = np.bincount(own_int, minlength=n_cells) + np.bincount(
            neighbour, minlength=n_cells
        )
        n_isolated = int(np.count_nonzero(internal_face_count <= 1))

        data = np.ones(n_internal, dtype=np.int8)
        graph = coo_matrix((data, (own_int, neighbour)), shape=(n_cells, n_cells))
        n_regions, labels = connected_components(graph, directed=False)
        sizes = np.bincount(labels, minlength=n_regions)
        sizes_desc = np.sort(sizes)[::-1]

        return {
            "nCells": int(n_cells),
            "nRegions": int(n_regions),
            "nIsolatedCells": n_isolated,
            "regionSizes": [int(v) for v in sizes_desc],
        }
    except Exception:  # unreadable / unexpected layout → let checkMesh decide
        return None


def read_all_faces(faces_path: Path):
    """Read the whole ``faces`` file as ``(offsets, vertices)`` arrays.

    Returned flat so callers can slice face *i* as
    ``vertices[offsets[i]:offsets[i + 1]]`` without building 4M Python lists.
    """
    raw = Path(faces_path).read_bytes()
    info = foam_header_info(raw)
    if info["format"] == "binary":
        dtype = _LABEL_DTYPES[info["label_bytes"]]
        width = info["label_bytes"]
        n_offsets, off_start = _binary_list_at(raw, _payload_start(raw))
        offsets = np.frombuffer(raw, dtype=dtype, count=n_offsets, offset=off_start)
        pos = off_start + n_offsets * width
        while pos < len(raw) and raw[pos: pos + 1] != b")":
            pos += 1
        n_verts, vert_start = _binary_list_at(raw, pos + 1)
        verts = np.frombuffer(raw, dtype=dtype, count=n_verts, offset=vert_start)
        return offsets.astype(np.int64), verts.astype(np.int64)

    # ASCII: "<n>\n(\n4(a b c d)\n...)". OpenFOAM writes a face of more than
    # ten points as "<k>\n(\na\nb\n...\n)", so faces are read token by
    # token, never line by line.
    text = raw[_payload_start(raw):].decode("utf-8", errors="replace")
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    header = text.find("FoamFile")
    if header >= 0:
        text = text[text.index("}", header) + 1:]
    tokens = re.findall(r"\d+|[()]", text)
    if len(tokens) < 2 or tokens[1] != "(":
        raise ValueError(f"ascii faces: no list in {faces_path}")
    n_faces = int(tokens[0])
    sizes = np.empty(n_faces, dtype=np.int64)
    flat: List[int] = []
    k = 2
    for i in range(n_faces):
        size = int(tokens[k])
        if tokens[k + 1] != "(" or tokens[k + 2 + size] != ")":
            raise ValueError(f"ascii faces: malformed face {i} in {faces_path}")
        flat.extend(int(v) for v in tokens[k + 2: k + 2 + size])
        sizes[i] = size
        k += size + 3
    offsets = np.zeros(n_faces + 1, dtype=np.int64)
    np.cumsum(sizes, out=offsets[1:])
    return offsets, np.asarray(flat, dtype=np.int64)


def cell_centres(poly_dir: Path) -> Optional[np.ndarray]:
    """Approximate cell centres as the mean of the cell's face centroids.

    Good enough for occupancy questions ("is there any fluid cell here?"), and
    far cheaper than the exact volume-weighted centroid, which needs the full
    face-pyramid decomposition. Returns None when the mesh cannot be read.
    """
    poly_dir = Path(poly_dir)
    try:
        points = read_points(poly_dir / "points")
        offsets, verts = read_all_faces(poly_dir / "faces")
        owner = read_label_list(poly_dir / "owner").astype(np.int64)
        neighbour = read_label_list(poly_dir / "neighbour").astype(np.int64)
    except Exception:
        return None

    n_faces = len(offsets) - 1
    if n_faces <= 0 or owner.size != n_faces:
        return None

    # centroid of each face = mean of its points, via a segment sum
    face_of_vertex = np.repeat(np.arange(n_faces), np.diff(offsets))
    sums = np.zeros((n_faces, 3), dtype=np.float64)
    np.add.at(sums, face_of_vertex, points[verts])
    counts = np.diff(offsets).astype(np.float64)[:, None]
    face_centres = sums / counts

    note = read_owner_note(poly_dir / "owner")
    n_cells = note.get("nCells")
    if not n_cells:
        n_cells = int(max(owner.max(), neighbour.max() if neighbour.size else 0) + 1)

    acc = np.zeros((n_cells, 3), dtype=np.float64)
    cnt = np.zeros(n_cells, dtype=np.float64)
    np.add.at(acc, owner, face_centres)
    np.add.at(cnt, owner, 1.0)
    if neighbour.size:
        np.add.at(acc, neighbour, face_centres[: neighbour.size])
        np.add.at(cnt, neighbour, 1.0)
    cnt[cnt == 0] = 1.0
    return acc / cnt[:, None]
