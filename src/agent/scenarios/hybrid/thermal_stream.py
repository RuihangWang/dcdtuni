"""Strict sensible-enthalpy primitives for scalar or ordered 1D air streams.

This is a lumped calorimetric interface, not a room model, fan-power model or
CFD result. SI units are declared explicitly; no implicit unit conversion,
two-dimensional broadcasting or UID reordering is performed. Signed heat is
supported. Absolute temperatures and heat-capacity rates must stay positive.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import numpy as np
import torch


@dataclass(frozen=True)
class ThermalStreamUnits:
    temperature: str = "K"
    heat: str = "W"
    volume_flow: str = "m3/s"
    density: str = "kg/m3"
    specific_heat: str = "J/(kg*K)"


SI_UNITS = ThermalStreamUnits()


def _units(units: ThermalStreamUnits) -> None:
    if not isinstance(units, ThermalStreamUnits) or units != SI_UNITS:
        raise ValueError("Exactly K, W, m3/s, kg/m3, J/(kg*K) SI units required; convert explicitly before this API")


def _array(value: Any, name: str) -> np.ndarray:
    try:
        result = np.asarray(value)
    except (TypeError, ValueError) as error:
        raise ValueError(name + " must be a real scalar or one-dimensional numeric array") from error
    if result.dtype.kind not in "iuf":
        raise ValueError(name + " must contain real numeric values; bool, complex and strings are invalid")
    return result.astype(np.float64, copy=False)


def _shape(value: Any, name: str) -> tuple[int, ...]:
    shape = tuple(value.shape) if isinstance(value, torch.Tensor) else _array(value, name).shape
    if len(shape) > 1 or (len(shape) == 1 and shape[0] == 0):
        raise ValueError(name + " must be a scalar or nonempty one-dimensional vector; 2D broadcasting is forbidden")
    return shape


def _finite(value: Any, name: str, positive: bool = False) -> None:
    if isinstance(value, torch.Tensor):
        valid = bool(torch.isfinite(value).all().item())
        if positive:
            valid = valid and bool((value > 0).all().item())
    else:
        valid = bool(np.isfinite(value).all())
        if positive:
            valid = valid and bool((value > 0).all())
    if not valid:
        raise ValueError(name + " must be finite" + (" and positive" if positive else ""))


def _prepare(values: dict[str, Any], positive: tuple[str, ...]) -> dict[str, Any]:
    shapes = {name: _shape(value, name) for name, value in values.items()}
    lengths = {shape[0] for shape in shapes.values() if shape}
    if len(lengths) > 1:
        raise ValueError("All 1D vectors must have exactly the same length; only scalars broadcast")

    tensors = [value for value in values.values() if isinstance(value, torch.Tensor)]
    reference = tensors[0] if tensors else None
    if reference is not None:
        for value in tensors:
            if not value.is_floating_point():
                raise ValueError("Torch inputs must use a floating-point dtype")
            if value.device != reference.device or value.dtype != reference.dtype:
                raise ValueError("Torch inputs must share an explicit dtype and device")
        prepared = {
            name: value if isinstance(value, torch.Tensor) else
            torch.as_tensor(_array(value, name), dtype=reference.dtype, device=reference.device)
            for name, value in values.items()
        }
    else:
        prepared = {name: _array(value, name) for name, value in values.items()}
    for name, value in prepared.items():
        _finite(value, name, name in positive)
    return prepared


def _capacity(values: dict[str, Any]) -> Any:
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        rate = values["rho_kg_m3"] * values["cp_J_kgK"] * values["flow_m3s"]
    _finite(rate, "heat_capacity_rate_W_K", True)
    return rate


def server_exhaust_temperature(
    T_in_K: Any, Q_air_W: Any, flow_m3s: Any, *,
    rho_kg_m3: Any = 1.19, cp_J_kgK: Any = 1005.0,
    units: ThermalStreamUnits = SI_UNITS,
) -> Any:
    """Return T_out = T_in + Q/(rho*cp*flow), preserving Torch autograd.

    Each input is a scalar or a 1D vector; all vector lengths must agree,
    including length-one vectors. NumPy output uses float64. If any input is a
    Torch tensor, output keeps the tensors' common dtype/device and graph.
    Positive flow fixes stream direction; signed Q permits heating/cooling.
    """
    _units(units)
    values = _prepare({
        "T_in_K": T_in_K, "Q_air_W": Q_air_W, "flow_m3s": flow_m3s,
        "rho_kg_m3": rho_kg_m3, "cp_J_kgK": cp_J_kgK,
    }, ("T_in_K", "flow_m3s", "rho_kg_m3", "cp_J_kgK"))
    capacity = _capacity(values)
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        result = values["T_in_K"] + values["Q_air_W"] / capacity
    _finite(result, "T_out_K", True)
    return result


def sensible_heat_W(
    T_in_K: Any, T_out_K: Any, flow_m3s: Any, *,
    rho_kg_m3: Any = 1.19, cp_J_kgK: Any = 1005.0,
    units: ThermalStreamUnits = SI_UNITS,
) -> Any:
    """Reconstruct signed sensible heat from the same strict SI stream."""
    _units(units)
    values = _prepare({
        "T_in_K": T_in_K, "T_out_K": T_out_K, "flow_m3s": flow_m3s,
        "rho_kg_m3": rho_kg_m3, "cp_J_kgK": cp_J_kgK,
    }, ("T_in_K", "T_out_K", "flow_m3s", "rho_kg_m3", "cp_J_kgK"))
    with np.errstate(over="ignore", invalid="ignore"):
        result = _capacity(values) * (values["T_out_K"] - values["T_in_K"])
    _finite(result, "sensible_heat_W")
    return result


def _uid_order(order: tuple[str, ...]) -> None:
    if (not isinstance(order, tuple) or not order or
        any(not isinstance(uid, str) or not uid or uid != uid.strip() for uid in order) or
        len(set(order)) != len(order)):
        raise ValueError("UID order must be a nonempty tuple of unique, nonblank UID strings")


@dataclass(frozen=True)
class UIDField:
    """1D values bound to an immutable UID tuple; no implicit permutation.

    Tensor/array values retain their graph and ownership. A caller must not
    mutate either values or identity between calls; evaluation validates again.
    """
    order: tuple[str, ...]
    values: Any

    def __post_init__(self) -> None:
        _uid_order(self.order)
        if _shape(self.values, "UIDField.values") != (len(self.order),):
            raise ValueError("UIDField values must be 1D with one value per UID")
        if isinstance(self.values, torch.Tensor):
            if not self.values.is_floating_point():
                raise ValueError("UIDField Torch values must use a floating-point dtype")
            _finite(self.values, "UIDField.values")
        else:
            _finite(_array(self.values, "UIDField.values"), "UIDField.values")


def _ordered(value: Any, order: tuple[str, ...], name: str, required: bool = False) -> Any:
    if isinstance(value, UIDField):
        if value.order != order:
            raise ValueError(name + " UID order differs from the explicit batch order")
        # Revalidate to catch caller mutation without cloning/detaching the graph.
        value.__post_init__()
        return value.values
    if required:
        raise ValueError(name + " must be an explicit UIDField")
    if _shape(value, name) != ():
        raise ValueError(name + " must be a shared scalar or explicit UIDField")
    return value


def server_exhaust_temperature_by_uid(
    uid_order: tuple[str, ...], T_in_K: Any, Q_air_W: UIDField, flow_m3s: UIDField, *,
    rho_kg_m3: Any = 1.19, cp_J_kgK: Any = 1005.0,
    units: ThermalStreamUnits = SI_UNITS,
) -> UIDField:
    """Evaluate explicit per-device heat/flow; scalar inlet/fluid data may be shared."""
    _uid_order(uid_order)
    result = server_exhaust_temperature(
        _ordered(T_in_K, uid_order, "T_in_K"),
        _ordered(Q_air_W, uid_order, "Q_air_W", True),
        _ordered(flow_m3s, uid_order, "flow_m3s", True),
        rho_kg_m3=_ordered(rho_kg_m3, uid_order, "rho_kg_m3"),
        cp_J_kgK=_ordered(cp_J_kgK, uid_order, "cp_J_kgK"), units=units,
    )
    return UIDField(uid_order, result)
