"""A secondary region walled in on every side carries no flow and is removed;
one that reaches any non-wall patch keeps its boundary conditions."""
from backend.aircooling.runner.stlgen_runner import STLGenRunner

HEADER = "FoamFile {{ version 2.0; format binary; class {cls}; object {obj}; }}\n"


def _write(poly, owner, patches, sets):
    poly.mkdir(parents=True)
    (poly / "owner").write_text(HEADER.format(cls="labelList", obj="owner").replace("binary", "ascii")
                                + f"{len(owner)}\n(\n" + "\n".join(map(str, owner)) + "\n)\n")
    rows = "".join(f"    {n}\n    {{\n        type {t};\n        nFaces {c};\n        startFace {s};\n    }}\n"
                   for n, t, s, c in patches)
    (poly / "boundary").write_text(HEADER.format(cls="polyBoundaryMesh", obj="boundary")
                                   + f"{len(patches)}\n(\n{rows})\n")
    (poly / "sets").mkdir()
    for name, cells in sets.items():
        # checkMesh's own spelling: a binary header over a text body
        (poly / "sets" / name).write_text(HEADER.format(cls="cellSet", obj=name)
                                          + f"{len(cells)}\n(\n" + "\n".join(map(str, cells)) + "\n)\n")


def test_walled_region_is_sealed_and_an_inlet_region_is_not(tmp_path):
    # faces 0-3 internal; boundary: room walls (cells 0,1), inlet (cell 1),
    # plenum walls (cells 2,3)
    owner = [0, 0, 2, 2, 0, 1, 1, 2, 3]
    patches = [("Room_Wall", "wall", 4, 2), ("ACU_supply", "patch", 6, 1), ("Plenum_Floor", "wall", 7, 2)]
    _write(tmp_path / "constant" / "polyMesh", owner, patches, {"region0": [0, 1], "region1": [2, 3]})
    sealed = STLGenRunner._sealed_wall_regions(
        tmp_path, [{"name": "region0", "cells": 2}, {"name": "region1", "cells": 2}], 1)
    assert [(r["name"], r["patches"]) for r in sealed] == [("region1", ["Plenum_Floor"])]


def test_missing_sets_give_no_verdict(tmp_path):
    _write(tmp_path / "constant" / "polyMesh", [0, 0], [("W", "wall", 0, 2)], {})
    assert STLGenRunner._sealed_wall_regions(tmp_path, [{"name": "region1", "cells": 1}], 1) == []

