"""Read-only validation of native installed-writer byte certificates.

This oracle verifies emitted data; pure fixtures do not execute C++/polyMesh.
Compact labels may change. Native identity, alias classes, ordered coordinate
bytes, canonical point0, local DP geometry and all patch/zone roles may not.
"""
from __future__ import annotations
import argparse
import json
import math
import struct
from pathlib import Path

FACE_IDENTITY = (
    "nativeFace", "nativeOwner", "nativeNeighbour", "rawPatch", "rawZone",
    "relativeFace", "patchName", "patchType", "patchRole", "nativePoints",
    "pointAliases", "ringAliases", "coordinateBytes", "SfBytes", "FCBytes",
    "CCBytes", "VBytes", "zoneRoles",
)
BIT_VECTOR_KEYS = ("SfBytes", "FCBytes", "CCBytes")

def _finite_hex(value: str, scalar_bytes: int, byte_order: str) -> bool:
    if not isinstance(value, str) or scalar_bytes != 8 or byte_order not in ("little", "big"):
        return False
    try:
        data = bytes.fromhex(value)
        return len(data) == scalar_bytes and math.isfinite(struct.unpack("<d" if byte_order == "little" else ">d", data)[0])
    except (ValueError, struct.error):
        return False

def _face_finite(face: dict, scalar_bytes: int, byte_order: str) -> bool:
    try:
        n = len(face["coordinateBytes"])
        if n < 3 or len(face["compactPoints"]) != n or len(face["ringAliases"]) != n:
            return False
        if len(set(face["compactPoints"])) != n:
            return False
        vectors = [face[key] for key in BIT_VECTOR_KEYS] + face["coordinateBytes"]
        if not all(len(v) == 3 and all(_finite_hex(x, scalar_bytes, byte_order) for x in v) for v in vectors):
            return False
        if not _finite_hex(face["VBytes"], scalar_bytes, byte_order):
            return False
        if struct.unpack("<d" if byte_order == "little" else ">d", bytes.fromhex(face["VBytes"]))[0] <= 0:
            return False
        if any(face[k] < 0 for k in ("nativeFace", "nativeOwner", "relativeFace", "compactFace", "compactOwner")):
            return False
        if face["nativeNeighbour"] != -1:
            return False
        if len(face["pointAliases"]) != len(face["nativePoints"]):
            return False
        return True
    except (KeyError, TypeError, ValueError):
        return False

def compare_certificates(baseline: dict, actual: dict) -> dict:
    """No comparison against source-order CC/V; baseline must be earliest writer."""
    errors: list[dict] = []
    if baseline.get("format") != "installed-writer-coupled-certificate/v1" or actual.get("format") != baseline.get("format"):
        errors.append({"reason": "format"})
    if baseline.get("baselineImmutable") is not True or actual.get("baselineImmutable") is not True:
        errors.append({"reason": "earliest-baseline"})
    if baseline.get("status") != "earliest-baseline":
        errors.append({"reason": "baseline-status"})
    if actual.get("status") not in ("local-pass-awaiting-global", "local-rejected"):
        errors.append({"reason": "actual-status"})
    if baseline.get("rank") != actual.get("rank") or baseline.get("scalarBytes") != actual.get("scalarBytes") or baseline.get("byteOrder") != actual.get("byteOrder"):
        errors.append({"reason": "ABI/rank"})
    if baseline.get("failures") != 0 or actual.get("failures") != 0:
        errors.append({"reason": "native-failure"})
    for field in ("coupledPatchRoles", "coupledPatchSizes"):
        if field not in baseline or field not in actual or baseline[field] != actual[field]:
            errors.append({"reason": field})
    scalar_bytes = baseline.get("scalarBytes", 0)
    byte_order = baseline.get("byteOrder", "")
    def index(doc: dict) -> dict:
        rows: dict = {}
        for face in doc.get("faces", []):
            key = face.get("nativeFace")
            if key in rows:
                errors.append({"reason": "duplicate-native-face", "face": key})
            if not _face_finite(face, scalar_bytes, byte_order):
                errors.append({"reason": "finite/binding/cardinality", "face": key})
            rows[key] = face
        sizes = doc.get("coupledPatchSizes", {})
        if any(not isinstance(n, int) or n < 0 for n in sizes.values()) or sum(sizes.values()) != len(rows):
            errors.append({"reason": "complete-coupled-census"})
        ordinals = {}
        for face in rows.values():
            name = face.get("patchName")
            ordinal = face.get("relativeFace", -1)
            seen = ordinals.setdefault(name, set())
            if name not in sizes or not isinstance(ordinal, int) or not 0 <= ordinal < sizes[name] or ordinal in seen:
                errors.append({"reason": "relative-patch-binding", "face": face.get("nativeFace")})
            seen.add(ordinal)
        return rows
    old, new = index(baseline), index(actual)
    if old.keys() != new.keys():
        errors.append({"reason": "coupled-face-set"})
    for key in old.keys() & new.keys():
        for field in FACE_IDENTITY:
            if field not in old[key] or field not in new[key] or old[key][field] != new[key][field]:
                errors.append({"reason": field, "face": key})
    return {
        "passed": not errors,
        "scope": "actual emitted local byte/identity certificates; requires native global-zero marker, reciprocal written proof, stock full checkMesh and <=300s API separately",
        "remoteQualityRecomputed": False,
        "faces": len(old),
        "errors": errors,
    }

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--actual", type=Path, required=True)
    args = parser.parse_args()
    result = compare_certificates(json.loads(args.baseline.read_text()), json.loads(args.actual.read_text()))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)

if __name__ == "__main__":
    main()
