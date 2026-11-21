"""Shared helpers for tests that exercise the STLGenRunner MESH pipeline with a
hand-built geometry blob (P3 T6).

After the V2 cutover, STLGenRunner._load() / _load_cfd_json_only() require a V2
manifest (schemaVersion=2) and read the geometry from the transitional
`meshGeometry` block (the v1 geometry domain model). These tests build a v1
geometry blob and only care about the MESH pipeline (blockMesh / snappy /
createBaffles / partition-hole fvOptions / room enclosure / geometry validation),
NOT the BC registry. `wrap_meshgeometry` wraps such a blob into the minimal V2
envelope the loader accepts.

boundaryBindings/porousBindings/sourceTerms are left empty: these tests do not
assert on the BC registry (that equivalence is covered by test_cfdrun_reader.py
and test_cfdrun_v2_runner_path.py).
"""
from __future__ import annotations

import copy
from typing import Any, Dict


def wrap_meshgeometry(v1_blob: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap a v1 geometry blob into a minimal V2 .cfdrun envelope.

    The runner validates meshGeometry separately via CFDExport, so the blob is
    passed through verbatim under the `meshGeometry` key.
    """
    return {
        "schemaVersion": 2,
        "runId": "",
        "name": v1_blob.get("projectName", "") if isinstance(v1_blob, dict) else "",
        "boundaryBindings": {},
        "porousBindings": {},
        "sourceTerms": {},
        "meshGeometry": copy.deepcopy(v1_blob),
    }
