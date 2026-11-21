"""Validate explicit project source relations without certifying geometry.

This stage only checks the declaration and its references to exported source
face UIDs. It must never be used as a dcCart admission or support-area receipt:
STL components, projection coverage, thickness and contacts still need their
own geometric proof against immutable source triangles.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Literal


_UID = re.compile(r"^[A-Za-z0-9_./:-]+$")
_ROOT_FIELDS = {"version", "mode", "coverage", "wallPairs", "independentPairs", "edgeContacts"}


@dataclass(frozen=True)
class UnverifiedSourceRelations:
    """Canonical *declaration* identity; never a verified topology graph."""

    declaration_sha256: str
    face_uids: tuple[str, ...]
    missing_contour_registry: bool
    status: Literal["unverified_geometry"] = "unverified_geometry"


def _mapping(value: object, name: str, fields: set[str] | None = None) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    if fields is not None:
        unknown = set(value) - fields
        missing = fields - set(value)
        if unknown or missing:
            raise ValueError(f"{name} has unknown={sorted(unknown)} or missing={sorted(missing)} fields")
    return value


def _uid(value: object, name: str) -> str:
    if not isinstance(value, str) or not _UID.fullmatch(value):
        raise ValueError(f"{name} must be a stable nonempty UID")
    return value


def _positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return float(value)


def _faces(value: object, name: str, available: Mapping[str, str]) -> tuple[str, str]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{name} requires exactly two source faces")
    first, second = (_uid(item, name) for item in value)
    if first == second:
        raise ValueError(f"{name} has duplicate source face")
    for face in (first, second):
        if face not in available:
            raise ValueError(f"{name} references missing source face {face}")
        if available[face] != "zeroThicknessSheet":
            raise ValueError(f"{name} requires zeroThicknessSheet at {face}")
    return first, second


def validate_declared_source_relations(
    topology: Mapping | None,
    source_surfaces: Mapping[str, Mapping],
    *,
    contours: Mapping[str, str] | None = None,
) -> UnverifiedSourceRelations | None:
    """Check explicit relation syntax and UID references; return no geometry proof.

    ``None`` preserves the old no-declaration path. A present strict section is
    still unverified even if every relation map is empty, and callers must
    refuse meshing until a separate component/support validator certifies it.
    """
    if topology is None:
        return None
    doc = _mapping(topology, "geometryTopology", _ROOT_FIELDS)
    if doc["version"] != 1 or isinstance(doc["version"], bool):
        raise ValueError("geometryTopology.version must be 1")
    if doc["mode"] != "strict" or doc["coverage"] != "airInternalSheets":
        raise ValueError("geometryTopology requires strict airInternalSheets mode")

    available: dict[str, str] = {}
    for key, row in source_surfaces.items():
        record = _mapping(row, f"sourceSurfaces[{key}]")
        face = _uid(record.get("sourceFaceUid"), f"sourceSurfaces[{key}].sourceFaceUid")
        if face in available:
            raise ValueError(f"duplicate sourceFaceUid {face} in exported regions")
        available[face] = record.get("representation")

    relation_maps = {name: _mapping(doc[name], f"geometryTopology.{name}")
                     for name in ("wallPairs", "independentPairs", "edgeContacts")}
    relation_ids: set[str] = set()
    normalized: dict[str, dict] = {name: {} for name in relation_maps}
    wall_owners: dict[str, str] = {}
    wall_face_pairs: set[tuple[str, str]] = set()
    for name, relations in relation_maps.items():
        for relation_id in relations:
            _uid(relation_id, f"geometryTopology.{name} key")
            if relation_id in relation_ids:
                raise ValueError(f"duplicate relation UID {relation_id}")
            relation_ids.add(relation_id)

    for relation_id, value in relation_maps["wallPairs"].items():
        item = _mapping(value, f"wallPairs[{relation_id}]",
                        {"sides", "thickness_m", "meshReferenceFaceUid", "meshTreatment"})
        sides = _faces(item["sides"], f"wallPairs[{relation_id}].sides", available)
        thickness = _positive(item["thickness_m"], f"wallPairs[{relation_id}].thickness_m")
        reference = _uid(item["meshReferenceFaceUid"], f"wallPairs[{relation_id}].meshReferenceFaceUid")
        if reference not in sides:
            raise ValueError(f"wallPairs[{relation_id}].meshReferenceFaceUid must be one of sides")
        if item["meshTreatment"] != "collapsedAirBarrier":
            raise ValueError(f"wallPairs[{relation_id}].meshTreatment is unsupported")
        for face in sides:
            if face in wall_owners:
                raise ValueError(f"source face {face} already belongs to wallPairs[{wall_owners[face]}]")
            wall_owners[face] = relation_id
        wall_face_pairs.add(tuple(sorted(sides)))
        normalized["wallPairs"][relation_id] = {
            "sides": sorted(sides), "thickness_m": thickness,
            "meshReferenceFaceUid": reference, "meshTreatment": "collapsedAirBarrier",
        }

    seen_gaps: set[tuple[str, str]] = set()
    for relation_id, value in relation_maps["independentPairs"].items():
        item = _mapping(value, f"independentPairs[{relation_id}]", {"faces", "relation", "minGap_m"})
        faces = tuple(sorted(_faces(item["faces"], f"independentPairs[{relation_id}].faces", available)))
        if item["relation"] != "fluidGap":
            raise ValueError(f"independentPairs[{relation_id}].relation is unsupported")
        if faces in wall_face_pairs:
            raise ValueError(f"independentPairs[{relation_id}] conflicts with wallPairs for the same face pair")
        if faces in seen_gaps:
            raise ValueError(f"duplicate independentPairs face pair {faces}")
        seen_gaps.add(faces)
        normalized["independentPairs"][relation_id] = {
            "faces": faces, "relation": "fluidGap",
            "minGap_m": _positive(item["minGap_m"], f"independentPairs[{relation_id}].minGap_m"),
        }

    missing_contours = contours is None and bool(relation_maps["edgeContacts"])
    if contours is not None and not isinstance(contours, Mapping):
        raise ValueError("contour registry must map sourceEdgeUid to sourceFaceUid")

    def face_ref(value: object, name: str) -> str:
        ref = _mapping(value, name, {"sourceFaceUid"})
        face = _uid(ref["sourceFaceUid"], f"{name}.sourceFaceUid")
        if face not in available:
            raise ValueError(f"{name} references missing source face {face}")
        if available[face] != "zeroThicknessSheet":
            raise ValueError(f"{name} requires zeroThicknessSheet at {face}")
        return face

    def edge_ref(value: object, name: str) -> dict[str, str]:
        ref = _mapping(value, name, {"sourceFaceUid", "sourceEdgeUid"})
        face = _uid(ref["sourceFaceUid"], f"{name}.sourceFaceUid")
        if face not in available:
            raise ValueError(f"{name} references missing source face {face}")
        if available[face] != "zeroThicknessSheet":
            raise ValueError(f"{name} requires zeroThicknessSheet at {face}")
        edge = _uid(ref["sourceEdgeUid"], f"{name}.sourceEdgeUid")
        if contours is not None and contours.get(edge) != face:
            raise ValueError(f"{name}.sourceEdgeUid {edge} is absent or belongs to another source face")
        return {"sourceFaceUid": face, "sourceEdgeUid": edge}

    for relation_id, value in relation_maps["edgeContacts"].items():
        item = _mapping(value, f"edgeContacts[{relation_id}]")
        kind = item.get("kind")
        if kind == "edgeFace":
            _mapping(item, f"edgeContacts[{relation_id}]", {"kind", "edge", "face"})
            edge = edge_ref(item["edge"], f"edgeContacts[{relation_id}].edge")
            face = face_ref(item["face"], f"edgeContacts[{relation_id}].face")
            if edge["sourceFaceUid"] == face:
                raise ValueError(f"edgeContacts[{relation_id}] needs two distinct source faces")
            normalized["edgeContacts"][relation_id] = {
                "kind": kind,
                "edge": edge,
                "face": {"sourceFaceUid": face},
            }
        elif kind == "edgeEdge":
            _mapping(item, f"edgeContacts[{relation_id}]", {"kind", "a", "b"})
            a = edge_ref(item["a"], f"edgeContacts[{relation_id}].a")
            b = edge_ref(item["b"], f"edgeContacts[{relation_id}].b")
            if a["sourceFaceUid"] == b["sourceFaceUid"]:
                raise ValueError(f"edgeContacts[{relation_id}] needs two distinct source faces")
            normalized["edgeContacts"][relation_id] = {"kind": kind, "a": a, "b": b}
        else:
            raise ValueError(f"edgeContacts[{relation_id}].kind is unsupported")

    declaration = {"version": 1, "mode": "strict", "coverage": "airInternalSheets",
                   "sourceFaceUids": sorted(available), "relations": normalized}
    canonical = json.dumps(declaration, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return UnverifiedSourceRelations(
        declaration_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        face_uids=tuple(sorted(available)), missing_contour_registry=missing_contours,
    )
