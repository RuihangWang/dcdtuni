"""Bounding is accepted only as quiescent air in a recorded sealed region."""
import json
from types import SimpleNamespace

from backend.aircooling.runner import convergence as cv

HEAD = "FoamFile {{ version 2.0; format ascii; class {cls}; object {obj}; }}\n"


def _run(tmp_path, k_values, sealed_cells, bound_min=1e-15):
    (tmp_path / "constant" / "polyMesh" / "sets").mkdir(parents=True)
    (tmp_path / "constant" / "polyMesh" / "sets" / "region1").write_text(
        HEAD.format(cls="cellSet", obj="region1") + f"{len(sealed_cells)}\n(\n"
        + "\n".join(map(str, sealed_cells)) + "\n)\n")
    (tmp_path / "200").mkdir()
    (tmp_path / "200" / "k").write_text(
        HEAD.format(cls="volScalarField", obj="k") + "dimensions [0 2 -2 0 0 0 0];\n"
        f"internalField nonuniform List<scalar> {len(k_values)}\n(\n"
        + "\n".join(map(str, k_values)) + "\n)\n;\nboundaryField {}\n")
    (tmp_path / "sealed-regions.json").write_text(json.dumps(
        {"format": "dcdtuni.sealed-regions/v1", "regions": [{"name": "region1", "cells": 2}]}))
    return SimpleNamespace(bounding={"k": [199.0, 200.0]}, bounding_min={"k": bound_min})


def test_bounded_cells_all_in_the_sealed_region_are_quiescent(tmp_path):
    log = _run(tmp_path, [0.2, 0.3, 1e-15, 1e-15], [2, 3])
    evidence = cv.sealed_region_bounding(tmp_path, log)
    assert evidence["fields"] == ["k"] and evidence["quiescent_cells"] == 2


def test_a_quiescent_cell_in_the_flow_keeps_the_rule(tmp_path):
    log = _run(tmp_path, [1e-15, 0.3, 1e-15, 1e-15], [2, 3])
    assert cv.sealed_region_bounding(tmp_path, log) is None


def test_a_negative_clip_keeps_the_rule(tmp_path):
    log = _run(tmp_path, [0.2, 0.3, 1e-15, 1e-15], [2, 3], bound_min=-0.4)
    assert cv.sealed_region_bounding(tmp_path, log) is None


def test_no_record_keeps_the_rule(tmp_path):
    log = _run(tmp_path, [0.2, 0.3, 1e-15, 1e-15], [2, 3])
    (tmp_path / "sealed-regions.json").unlink()
    assert cv.sealed_region_bounding(tmp_path, log) is None
