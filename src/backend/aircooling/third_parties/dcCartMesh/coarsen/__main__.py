"""python -m backend.aircooling.third_parties.dcCartMesh.coarsen FINE_MESH_DIR OUT_MESH_DIR (--level L1|L0 | --ratio R)

Writes the patch- and geometry-preserving coarse mesh of a dcCart mesh directory (a case's ``Mesh/``, decomposed or
not) into another directory, with its receipt ``dccart-coarsen.json``. See ``coarsen.py``."""
from __future__ import annotations

import argparse
import json
import sys

from .coarsen import LEVEL_RATIOS, coarsen_mesh


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.aircooling.third_parties.dcCartMesh.coarsen",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("fine", help="the fine mesh directory (processor*/constant/polyMesh or constant/polyMesh)")
    ap.add_argument("out", help="the directory the coarse mesh is written to (replaced)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--level", choices=sorted(LEVEL_RATIOS), help="L1: 1/100 of the fine cells, L0: 1/1000")
    g.add_argument("--ratio", type=float, help="fine-to-coarse cell-count ratio")
    ap.add_argument("--absorb", type=float, default=None,
                    help="agglomerates below this share of the mean coarse volume join a neighbour (L1 0.25, L0 0.5)")
    ap.add_argument("--tolerance", type=float, default=0.05, help="relative tolerance on the cell count")
    ap.add_argument("--max-nonortho", type=float, default=None,
                    help="quality bound of a merged coarse face, deg (L1 75; L0 does not split)")
    ap.add_argument("--passes", type=int, default=None, help="box-level raises of failing agglomerates (L1 8, L0 0)")
    ap.add_argument("--workers", type=int, default=None, help="threads (default: one per rank, at most 16)")
    a = ap.parse_args(argv)
    rec = coarsen_mesh(a.fine, a.out, ratio=a.ratio, level=a.level, absorb=a.absorb, tolerance=a.tolerance,
                       workers=a.workers, max_nonortho_deg=a.max_nonortho, passes=a.passes)
    summary = {k: rec[k] for k in ("ok", "fineCells", "cells", "achievedRatio", "boxCells", "boxEdge_m",
                                   "physicalPatches",
                                   "patchAudit", "volume", "quality", "seconds")}
    print(json.dumps(summary, indent=1, default=float))
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
