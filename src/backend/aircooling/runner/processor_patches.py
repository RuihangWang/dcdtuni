"""Do a partitioned mesh's processor interfaces pair up across ranks?

Every face on rank A's ``procBoundaryAtoB`` must have its twin on rank B's
``procBoundaryBtoA`` (same count, same order); a ``processorCyclic`` patch
through a baffle pairs A's ``..._master`` with B's ``..._slave``. OpenFOAM
assumes this when it constructs the mesh: a one-face mismatch is a segfault
in processorPolyPatch::updateMesh, before any tool can say what is wrong
(Base604, 2026-09-30: dcCart left rank 9 one face short on both of its plain
interfaces). The boundary files are enough to check it, and cheap.
"""
from __future__ import annotations

import re
from pathlib import Path

_PATCH = re.compile(r"\n\s*(\S+)\s*\n\s*\{(.*?)\n\s*\}", re.S)
_ENTRY = re.compile(r"(\w+)\s+([^;]+);")


def _processor_patches(boundary: Path) -> dict[str, tuple[int, int]]:
    """name -> (nFaces, neighbProcNo) for the processor patches of one rank."""
    out: dict[str, tuple[int, int]] = {}
    for m in _PATCH.finditer(boundary.read_text(encoding="utf-8", errors="replace")):
        kv = dict(_ENTRY.findall(m.group(2)))
        if kv.get("type", "").strip() in ("processor", "processorCyclic"):
            out[m.group(1)] = (int(kv["nFaces"]), int(kv["neighbProcNo"]))
    return out


def _partner(name: str, me: int, nb: int) -> str:
    p = name.replace(f"procBoundary{me}to{nb}", f"procBoundary{nb}to{me}", 1)
    if p.endswith("_master"):
        return p[: -len("_master")] + "_slave"
    if p.endswith("_slave"):
        return p[: -len("_slave")] + "_master"
    return p


def processor_patch_mismatches(case_dir: Path, n_procs: int) -> list[str]:
    """One line per unpaired interface; empty when every interface pairs up
    (or when there is no complete partitioned mesh to judge)."""
    if n_procs <= 1:
        return []
    paths = [Path(case_dir) / f"processor{r}" / "constant" / "polyMesh" / "boundary" for r in range(n_procs)]
    if not all(p.is_file() for p in paths):
        return []
    ranks = {r: _processor_patches(p) for r, p in enumerate(paths)}
    problems: list[str] = []
    for r, patches in ranks.items():
        for name, (n, nb) in patches.items():
            if nb not in ranks:
                problems.append(f"{name} on rank {r} points at rank {nb}, which does not exist")
                continue
            twin = _partner(name, r, nb)
            if twin not in ranks[nb]:
                problems.append(f"{name} on rank {r} ({n} faces): partner {twin} missing on rank {nb}")
            elif r < nb and ranks[nb][twin][0] != n:
                problems.append(
                    f"{name}: {n} faces on rank {r} but {ranks[nb][twin][0]} on rank {nb} ({twin})"
                )
    return problems


def assert_processor_patches_paired(case_dir: Path, n_procs: int) -> None:
    problems = processor_patch_mismatches(case_dir, n_procs)
    if not problems:
        return
    first = problems[0]
    ranks = re.findall(r"rank (\d+)", first)
    pair = f"ranks {ranks[0]} and {ranks[1]}" if len(ranks) >= 2 else "two ranks"
    more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
    raise RuntimeError(
        f"Partitioned mesh is inconsistent between {pair}: {first}{more}. "
        f"No OpenFOAM tool can load it (checkMesh -parallel would crash). "
        f"The mesher disagreed across ranks; re-mesh with a different process "
        f"count (meshProcess) or serially while this is fixed."
    )
