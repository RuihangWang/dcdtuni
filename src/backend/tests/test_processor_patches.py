"""A partitioned mesh whose processor interfaces do not pair up is refused by name.

Base604 (2026-09-30, 16 ranks): dcCart wrote rank 9 one face short on both of
its plain processor interfaces (rank 8 and rank 10 each held a face whose
cell they both claimed). OpenFOAM cannot even construct such a mesh —
`checkMesh -parallel` segfaulted in processorPolyPatch::updateMesh and the job
ended with "Partitioned checkMesh did not produce a complete quality report".
The pairing is checked from the boundary files first, so the message names
the ranks and the face counts instead.
"""
from pathlib import Path

import pytest

from backend.aircooling.runner.processor_patches import (
    assert_processor_patches_paired,
    processor_patch_mismatches,
)


def _boundary(case: Path, rank: int, patches: dict[str, tuple[str, int, int]]):
    """patches: name -> (type, nFaces, neighbProcNo)."""
    d = case / f"processor{rank}" / "constant" / "polyMesh"
    d.mkdir(parents=True, exist_ok=True)
    body = [f"{len(patches) + 1}", "(", "    wall_1", "    {", "        type            wall;",
            "        nFaces          10;", "        startFace       0;", "    }"]
    start = 10
    for name, (ptype, n, nb) in patches.items():
        body += [f"    {name}", "    {", f"        type            {ptype};", f"        inGroups        1({ptype});",
                 f"        nFaces          {n};", f"        startFace       {start};",
                 "        matchTolerance  0.0001;", f"        myProcNo        {rank};", f"        neighbProcNo    {nb};"]
        if ptype == "processorCyclic":
            body.append(f"        referPatch      {name.split('through')[1]};")
        body.append("    }")
        start += n
    body.append(")")
    (d / "boundary").write_text(
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n    class       polyBoundaryMesh;\n"
        '    location    "constant/polyMesh";\n    object      boundary;\n}\n\n' + "\n".join(body) + "\n"
    )


def _paired(case: Path):
    _boundary(case, 0, {"procBoundary0to1": ("processor", 20, 1),
                        "procBoundary0to1throughfloor_master": ("processorCyclic", 2, 1),
                        "procBoundary0to1throughfloor_slave": ("processorCyclic", 1, 1)})
    _boundary(case, 1, {"procBoundary1to0": ("processor", 20, 0),
                        "procBoundary1to0throughfloor_slave": ("processorCyclic", 2, 0),
                        "procBoundary1to0throughfloor_master": ("processorCyclic", 1, 0)})


def test_paired_interfaces_pass(tmp_path):
    _paired(tmp_path)
    assert processor_patch_mismatches(tmp_path, 2) == []
    assert_processor_patches_paired(tmp_path, 2)


def test_a_face_count_mismatch_names_both_ranks(tmp_path):
    _paired(tmp_path)
    _boundary(tmp_path, 1, {"procBoundary1to0": ("processor", 19, 0),
                            "procBoundary1to0throughfloor_slave": ("processorCyclic", 2, 0),
                            "procBoundary1to0throughfloor_master": ("processorCyclic", 1, 0)})
    problems = processor_patch_mismatches(tmp_path, 2)
    assert any("procBoundary0to1" in p and "20" in p and "19" in p for p in problems)
    with pytest.raises(RuntimeError, match=r"ranks 0 and 1.*20.*19"):
        assert_processor_patches_paired(tmp_path, 2)


def test_processor_cyclic_pairs_master_with_the_neighbours_slave(tmp_path):
    _paired(tmp_path)
    # the neighbour lost its slave half: rank 0's master side has no partner
    _boundary(tmp_path, 1, {"procBoundary1to0": ("processor", 20, 0),
                            "procBoundary1to0throughfloor_master": ("processorCyclic", 1, 0)})
    problems = processor_patch_mismatches(tmp_path, 2)
    assert any("procBoundary0to1throughfloor_master" in p and "missing" in p for p in problems)


def test_a_serial_or_unwritten_mesh_is_not_judged(tmp_path):
    assert processor_patch_mismatches(tmp_path, 1) == []
    assert processor_patch_mismatches(tmp_path, 4) == []       # no processor dirs at all
