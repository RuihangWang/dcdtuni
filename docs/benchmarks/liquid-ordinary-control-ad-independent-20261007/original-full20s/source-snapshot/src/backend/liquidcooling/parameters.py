"""Versioned equipment/material data, resolved before physics compilation.

Profiles use the compiler's documented units. A pack supplies defaults and
explicit project values override them. The resolved run is a self-contained
snapshot; replace the pack on the *unresolved project input* to recompile.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_UNITS = {
    "D_m": "m", "roughness_m": "m", "waypoints_m": "m", "n_cells": "1",
    "K_minor_per_bend": "1", "heat_source_W_per_m": "W/m", "Q_W": "W", "UA_W_per_K": "W/K",
    "C_solid_J_per_K": "J/K", "V_fluid_m3": "m3", "T_secondary_K": "K",
    "dp_a": "Pa/(kg/s)^2", "dp_b": "Pa/(kg/s)",
    "H0_m": "m", "a_coef": "m/(m3/s)^2", "rpm_ratio": "1",
    "Kv_max": "m3/h@1bar", "opening": "1", "characteristic": "enum",
    "rho_kg_m3": "kg/m3", "cp_J_kgK": "J/(kg*K)",
    "mu_Pa_s": "Pa*s", "k_W_mK": "W/(m*K)",
}

_PROFILE_FIELDS = {
    "pipe": {"D_m", "roughness_m", "waypoints_m", "n_cells", "K_minor_per_bend", "heat_source_W_per_m"},
    "pump": {"H0_m", "a_coef", "rpm_ratio"},
    "valve": {"D_m", "Kv_max", "opening", "characteristic"},
    "cold_plate": {"dp_a", "dp_b", "UA_W_per_K", "C_solid_J_per_K", "V_fluid_m3", "Q_W"},
    "heat_exchanger": {"dp_a", "dp_b", "UA_W_per_K", "T_secondary_K"},
    "fluid": {"rho_kg_m3", "cp_J_kgK", "mu_Pa_s", "k_W_mK"},
}


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _number(value: Any, location: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{location} must be a number, not {type(value).__name__}")
    try:
        valid = math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{location} must be finite")


# .liquidrun v4 endpoint forms: per-edge inputs of one run, never part of an
# equipment profile, so they are accepted as edge overrides only.
_EDGE_ONLY_FIELDS = {
    "pump": {"mode": ("curve", "flow"), "flow_set_kg_s": None},
    "cold_plate": {"heat_mode": ("power", "temperature_rise"), "dT_set_K": None},
    "heat_exchanger": {"mode": ("physics", "setpoint"), "T_set_K": None},
}


def _validate_params(kind: str, values: dict, location: str, *, edge_only: bool = False) -> None:
    if not isinstance(values, dict):
        raise ValueError(f"{location} must be a parameter object")
    extra = _EDGE_ONLY_FIELDS.get(kind, {}) if edge_only else {}
    unsupported = set(values) - _PROFILE_FIELDS[kind] - set(extra)
    if unsupported:
        raise ValueError(f"unsupported parameter for {kind}: {sorted(unsupported)}")
    for name, value in values.items():
        where = f"{location}.{name}"
        if name in extra and extra[name] is not None:
            if value not in extra[name]:
                raise ValueError(f"{where} must be one of {list(extra[name])}")
            continue
        if name == "n_cells":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{where} must be a non-negative integer")
        elif name == "characteristic":
            if value not in ("linear", "equal_percentage", "quick_opening"):
                raise ValueError(f"{where} has an unsupported valve characteristic")
        elif name == "waypoints_m":
            if not isinstance(value, list) or len(value) < 2:
                raise ValueError(f"{where} must contain at least two 3D waypoints")
            for point in value:
                if not isinstance(point, list) or len(point) != 3:
                    raise ValueError(f"{where} must contain 3D waypoints")
                for coordinate in point:
                    _number(coordinate, where)
        elif name in ("Q_W", "rpm_ratio", "opening", "heat_source_W_per_m",
                      "flow_set_kg_s", "dT_set_K", "T_set_K") and isinstance(value, list):
            if not value:
                raise ValueError(f"{where} must not be an empty series")
            for item in value:
                if name == "heat_source_W_per_m" and isinstance(item, list):
                    if not item:
                        raise ValueError(f"{where} must not contain an empty series")
                    for scalar in item:
                        _number(scalar, where)
                else:
                    _number(item, where)
        else:
            _number(value, where)


class ParameterProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["pipe", "pump", "valve", "cold_plate", "heat_exchanger", "fluid"]
    params: dict[str, Any] = Field(min_length=1)
    units: dict[str, str]
    description: str = ""

    @model_validator(mode="after")
    def validate_units_and_values(self):
        _validate_params(self.type, self.params, "profile.params")
        if set(self.params) != set(self.units):
            raise ValueError("profile units must describe every parameter exactly once")
        for name, unit in self.units.items():
            if not unit.strip():
                raise ValueError(f"units for {name} must not be empty")
            if name in _UNITS and unit != _UNITS[name]:
                raise ValueError(f"units for {name} must be {_UNITS[name]!r}, got {unit!r}")
        return self


class ParameterPack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    source: Literal["mock", "vendor", "calibrated"]
    description: str = Field(min_length=1)
    profiles: dict[str, ParameterProfile] = Field(min_length=1)


def resolve_parameter_pack(data: Any) -> Any:
    """Materialize profile references without mutating caller-owned data.

    No file access, network access or dynamic code execution occurs here.
    Unreferenced legacy manifests remain unchanged. Resolved snapshots retain
    their provenance and contain no active references, so reload is idempotent.
    """
    if not isinstance(data, dict):
        return data
    refs = any(isinstance(edge, dict) and edge.get("parameter_ref") is not None
               for edge in data.get("edges", [])) or data.get("fluid_ref") is not None
    if data.get("parameter_pack") is None:
        if refs:
            raise ValueError("parameter_pack is required when a profile is referenced")
        return data

    pack = ParameterPack.model_validate(data["parameter_pack"])
    result = deepcopy(data)
    result.pop("parameter_pack")
    bindings: dict[str, Any] = {"edges": {}}

    def bind(ref: str, expected_type: str, overrides: dict, identity: str) -> dict:
        if not isinstance(ref, str) or not ref:
            raise ValueError(f"parameter reference for {identity} must be a non-empty string")
        if ref not in pack.profiles:
            raise ValueError(f"unknown parameter profile {ref!r} for {identity}")
        profile = pack.profiles[ref]
        if profile.type != expected_type:
            raise ValueError(f"profile type {profile.type!r} does not match {expected_type!r} for {identity}")
        target_bindings = bindings if expected_type == "fluid" else bindings["edges"]
        if identity in target_bindings:
            raise ValueError(f"duplicate parameter binding identity {identity!r}")
        _validate_params(expected_type, overrides, f"{identity}.overrides", edge_only=True)
        values = deepcopy(profile.params)
        values.update(deepcopy(overrides))
        target_bindings[identity] = {"profile": ref, "overrides": deepcopy(overrides)}
        return values

    if result.get("fluid_ref") is not None:
        ref = result.pop("fluid_ref")
        result["fluid"] = bind(ref, "fluid", result.get("fluid", {}), "fluid")
    for edge in result.get("edges", []):
        if edge.get("parameter_ref") is not None:
            ref = edge.pop("parameter_ref")
            edge["params"] = bind(ref, edge["type"], edge.get("params", {}), edge["id"])

    snapshot = pack.model_dump(mode="json")
    effective_edges = [
        {"id": edge.get("id"), "type": edge.get("type"), "params": edge.get("params", {})}
        for edge in result.get("edges", [])
    ]
    effective_edges.sort(key=lambda edge: str(edge["id"]))
    result["parameter_provenance"] = {
        "id": pack.id, "revision": pack.revision, "source": pack.source,
        "description": pack.description, "sha256": _digest(snapshot),
        "bindings": bindings,
        "resolved_sha256": _digest({"fluid": result.get("fluid"), "edges": effective_edges}),
    }
    return result
