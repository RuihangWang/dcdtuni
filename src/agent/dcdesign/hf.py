"""High-fidelity (HF) metrics of a compiled hall design: the coupled CFD run of the scripted API, scored with
the same plant model and the same constraints as L0.

``agent.scripted_api air --full`` solves the liquid side first, hands each fan server's air heat (IT power minus its
cold-plate heat) to the air solve, and returns air metrics (``result.json`` -> ``metrics.fields``): per cooling
unit the supply and return patch temperatures and the heat it removes, per server the inlet-face temperature.
This module turns those into the quantities L0 reports (``l0.evaluate``):

* plant: ``agent.plant.energy.facility_power`` with every installed unit, its imposed flow and supply
  temperature, and its CFD return temperature (the flux-weighted mean over the return patch, the temperature
  the coil sees); a unit whose setpoint is above that return is seen with its coil off, as in L0 v2;
* constraints: hottest and coldest server inlet (area mean over each server's inlet face), the largest unit
  coil load against its capacity, setpoint minus return of the worst running unit, and the hottest cold plate
  from the liquid solution;
* ``air_heat_closure_kw``: the units' CFD heat removal minus the declared air heat.

No gradient: the CFD solve is not differentiable. ``liquid`` is the same ``solve_liquid_steady`` result L0
uses for this project (the liquid side does not depend on the air solution).
"""
from __future__ import annotations

import torch

from agent.plant import energy as E

from . import l0
from .spec import CATALOG, THERMAL_CLASSES

HF_VERSION = "dc-design-hf/v1-2026-10-21"


def _unit_by_uid(project: dict) -> dict:
    return {a["uid"]: a["meta"]["name"] for a in project["constructions"]["acus"].values()}


def evaluate_hf(project: dict, liquid: dict, air_metrics: dict, *, thermal_class: str = "recommended",
                primary_inlet_c: float | None = None) -> dict:
    """HF metrics of one compiled design from the scripted API's ``metrics.fields`` (``air_metrics``)."""
    hn = l0.build_network(project)
    c = hn.nominal_controls
    nu = len(hn.unit_names)
    names = _unit_by_uid(project)
    acus = {}
    for row in air_metrics["acus"]:
        uid = str(row["objectPath"]).rsplit(".", 1)[-1]
        acus[names[uid]] = row
    missing = [n for n in hn.unit_names if n not in acus]
    if missing:
        raise ValueError(f"air metrics have no unit row for {missing}")
    ret_c = torch.tensor([float(acus[n]["return"]["fluxMeanK"]) - 273.15 for n in hn.unit_names], dtype=torch.float64)
    coil_kw = torch.tensor([float(acus[n]["heatRemovedW"]) / 1000.0 for n in hn.unit_names], dtype=torch.float64)
    inlet_by_id = {}
    for rack in air_metrics["rackInlet"]["racks"]:
        for s in rack["servers"]:
            inlet_by_id[f'{rack["rackUid"]}/{s["serverUid"]}'] = float(s["areaMeanK"]) - 273.15
    missing = [s for s in hn.server_ids if s not in inlet_by_id]
    if missing:
        raise ValueError(f"air metrics have no inlet row for {len(missing)} servers, e.g. {missing[:3]}")
    inlet = torch.tensor([inlet_by_id[s] for s in hn.server_ids], dtype=torch.float64)

    order = {n: i for i, n in enumerate(hn.unit_names)}
    zero = torch.zeros((), dtype=torch.float64)
    flow_all = torch.stack([c[nu + order[n]] if n in order else zero for n in hn.all_units])
    sup_all = torch.stack([c[order[n]] if n in order else zero for n in hn.all_units])
    ret_all = torch.stack([torch.maximum(ret_c[order[n]], c[order[n]]) if n in order else zero for n in hn.all_units])
    active = [n in order for n in hn.all_units]
    m = liquid["metrics"]
    hx = next(iter(m["primaryReturnK"]))
    primary_in_c = l0._primary_inlet_c(project) if primary_inlet_c is None else primary_inlet_c
    it_kw = float(hn.server_it_kw.sum())
    liquid_kw = hn.liquid_kw
    f64 = lambda v: torch.as_tensor(v, dtype=torch.float64).reshape(1)  # noqa: E731
    pump_kw = f64(liquid["objectives"]["pump_hydraulic_power_W"]) / 1000.0 / l0.PUMP_EFFICIENCY
    plant = E.facility_power(
        torch.tensor(it_kw, dtype=torch.float64), it_kw - liquid_kw, flow_all, sup_all, ret_all,
        [l0.fans() for _ in hn.all_units], acu_active=active,
        liquid_heat_kw=f64(liquid_kw), liquid_t_supply_c=f64(primary_in_c),
        liquid_t_return_c=f64(m["primaryReturnK"][hx]) - 273.15, secondary_pump_kw=pump_kw)
    env = THERMAL_CLASSES[thermal_class]
    constraints = {
        "server_inlet_max": inlet.max() - env["inlet_max_c"],
        "server_inlet_min": env["inlet_min_c"] - inlet.min(),
        "unit_capacity": (coil_kw.max() - CATALOG["acu_capacity_kw"]) / CATALOG["acu_capacity_kw"],
        "unit_heating": (c[:nu] - ret_c).max(),
        "chip_temperature": torch.as_tensor(m["maxChipTemperatureK"], dtype=torch.float64) - 273.15 - l0.CHIP_LIMIT_C,
    }
    return {
        "version": HF_VERSION, "family": hn.family, "pue": plant["pue"], "total_kw": plant["total_kw"],
        "plant": plant, "constraints": constraints, "server_inlet_c": inlet, "unit_return_c": ret_c,
        "unit_coil_kw": coil_kw, "air_heat_closure_kw": coil_kw.sum() - (it_kw - liquid_kw),
        "declared_liquid_kw": liquid_kw, "units": hn.unit_names,
    }
