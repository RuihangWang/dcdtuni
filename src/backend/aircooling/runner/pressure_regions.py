"""Which disconnected mesh region each boundary patch sits in.

Full-block blanking panels and aisle containment can split the air into
separate regions that touch each other only through server fans and ACUs.
Every such region needs a pressure reference of its own: with all of its ACU
returns pinned to a fixed airflow, the pressure equation in that region has
no solution unless the fixed flows balance to round-off, and the solver stops
at "Incompatible assembled RHS in free pressure component".

checkMesh (``-allTopology``) writes each region as a cellSet ``region<N>``
under ``constant/polyMesh/sets``, numbered globally across ranks. A patch
belongs to the regions of its faces' owner cells. The mapping depends only on
the mesh, not on how the solve later partitions it, so it is read from the
case's ``Mesh/`` directory, serial or decomposed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set

import numpy as np
from loguru import logger

_REGION_SET = re.compile(r"^region(\d+)$")
_PATCH = re.compile(
    r"\n\s*([A-Za-z0-9_.\-]+)\s*\n\s*\{(.*?)\n\s*\}", re.S)


def read_label_list(path: Path) -> np.ndarray:
    """A labelList/cellSet file, ASCII or binary (32-bit labels)."""
    raw = path.read_bytes()
    header_end = raw.find(b"}", raw.find(b"FoamFile"))
    body = raw[header_end + 1:]
    m = re.search(rb"(\d+)\s*\(", body)
    if m is None:
        m0 = re.search(rb"(\d+)\s*\{", body)
        if m0 is not None and int(m0.group(1)) == 0:
            return np.zeros(0, dtype=np.int64)
        raise ValueError(f"{path}: no list found")
    n = int(m.group(1))
    start = m.end()
    # The header is not enough: OpenFOAM writes cellSets as text even under
    # "format binary". Raw labels are exactly 4n bytes followed by ')'.
    if body[start + 4 * n:start + 4 * n + 1] == b")" and len(body) >= start + 4 * n:
        values = np.frombuffer(body, dtype="<i4", count=n, offset=start).astype(np.int64)
        text_ok = False
        try:
            end = body.index(b")", start)
            text = body[start:end].split()
            text_ok = len(text) == n and all(t.isdigit() for t in text[:64])
        except ValueError:
            pass
        if not text_ok:
            return values
    end = body.index(b")", start)
    values = np.array(body[start:end].split(), dtype=np.int64)
    if values.size != n:
        raise ValueError(f"{path}: expected {n} labels, read {values.size}")
    return values


def _boundary_patches(boundary: Path) -> List[tuple]:
    text = boundary.read_text(encoding="utf-8", errors="replace")
    out = []
    for name, body in _PATCH.findall(text):
        t = re.search(r"\btype\s+(\w+)\s*;", body)
        nf = re.search(r"\bnFaces\s+(\d+)\s*;", body)
        sf = re.search(r"\bstartFace\s+(\d+)\s*;", body)
        if t and nf and sf:
            out.append((name, t.group(1), int(nf.group(1)), int(sf.group(1))))
    return out


def _n_cells(poly: Path, owner: np.ndarray) -> int:
    """Cell count: the owner header's note, else the highest owner/neighbour label.

    The highest-numbered cells can own no face at all, so owner alone undercounts.
    """
    head = (poly / "owner").read_bytes()[:4096]
    m = re.search(rb"nCells:\s*(\d+)", head)
    if m:
        return int(m.group(1))
    n = int(owner.max()) + 1 if owner.size else 0
    if (poly / "neighbour").is_file():
        nb = read_label_list(poly / "neighbour")
        if nb.size:
            n = max(n, int(nb.max()) + 1)
    return n


def _mesh_roots(mesh_dir: Path) -> List[Path]:
    procs = sorted(
        (p for p in mesh_dir.glob("processor*") if p.name[9:].isdigit()),
        key=lambda p: int(p.name[9:]))
    roots = [p for p in procs if (p / "constant" / "polyMesh" / "owner").is_file()]
    if roots:
        return roots
    if (mesh_dir / "constant" / "polyMesh" / "owner").is_file():
        return [mesh_dir]
    return []


def patch_regions(mesh_dir: Path) -> Optional[Dict[str, Set[int]]]:
    """Patch name -> the checkMesh regions its faces touch.

    ``None`` when the mesh is one region, or when the region sets are missing
    or do not belong to this mesh (a label outside the rank's cells) — callers
    then keep the single-region behaviour.
    """
    roots = _mesh_roots(Path(mesh_dir))
    if not roots:
        return None
    result: Dict[str, Set[int]] = {}
    region_ids: Set[int] = set()
    for root in roots:
        poly = root / "constant" / "polyMesh"
        sets_dir = poly / "sets"
        sets = {}
        if sets_dir.is_dir():
            for f in sets_dir.iterdir():
                m = _REGION_SET.match(f.name)
                if m and f.is_file():
                    sets[int(m.group(1))] = f
        if not sets:
            return None
        owner = read_label_list(poly / "owner")
        n_cells = _n_cells(poly, owner)
        region = np.full(n_cells, -1, dtype=np.int64)
        assigned = 0
        for rid, f in sets.items():
            cells = read_label_list(f)
            if cells.size and (cells.min() < 0 or cells.max() >= n_cells):
                logger.warning(f"{f}: region set does not match the mesh in {poly}; ignored")
                return None
            if cells.size and (region[cells] >= 0).any():
                logger.warning(f"{f}: overlaps another region set in {poly} (a stale set "
                               f"from an earlier mesh?); region sets ignored")
                return None
            region[cells] = rid
            assigned += int(cells.size)
            region_ids.add(rid)
        if assigned != n_cells:
            # One checkMesh writes regions that cover every cell exactly once.
            logger.warning(f"{poly}: region sets cover {assigned} of {n_cells} cells "
                           f"(not one checkMesh's output); region sets ignored")
            return None
        for name, ptype, n_faces, start in _boundary_patches(poly / "boundary"):
            if n_faces == 0 or ptype.lower().startswith("processor"):
                continue
            rids = np.unique(region[owner[start:start + n_faces]])
            result.setdefault(name, set()).update(int(r) for r in rids if r >= 0)
    if len(region_ids) < 2:
        return None
    return result
