"""Design-condition facility power of an air-cooled or hybrid air–liquid data hall.

One plant (chiller, primary chilled-water pumps, heat rejection) serves every
air coil (ACU) and every liquid primary branch (CDU). Given a solved operating
point -- per-ACU delivered supply temperature, return temperature and airflow,
per-CDU primary supply/return temperature and heat, and the declared IT heat
partition -- this module returns the electricity of each component, the plant
load, the chilled-water supply and return temperatures and the design PUE.

Conventions
-----------
* Units: degC, m3/s, kW, kg/m3, J/(kg K).
* ACU supply temperature is the DELIVERED room-inlet temperature, after the
  unit fan. The coil leaving air is colder by the fan temperature rise.
* Each kW of electricity is counted once: IT (including server fans), ACU fans,
  secondary liquid pumps, primary plant, and declared "other" electricity.
  Each kW of heat is charged to the plant once: the declared air heat plus the
  ACU fan heat, plus the liquid heat.
* The plant load uses the DECLARED heat partition (IT = air + liquid), not the
  heat the solver happens to deliver to the coils, so a solver energy imbalance
  cannot lower the computed PUE. The measured air-side closure is returned
  separately and is checked by the caller's acceptance gate.
* Chilled-water reset: the plant supply temperature is the lowest of (delivered
  air supply minus the coil approach) over the active ACUs and the delivered
  liquid primary supply over the active CDU branches. All coils share one
  water-return setpoint that satisfies the hot-end pinch at every coil; liquid
  branches warmer than the plant supply use ideal return-water recirculation.
* Closed (failed) ACUs keep their identity in the inputs with zero airflow; they
  carry no fan power and no coil. The plant design load (pump reference) is the
  installed one and does not change with the operating context.

Parameters are declared research assumptions, not vendor curves
(``PARAMETER_STATUS``). The DOE-2 mode follows the EnergyPlus Electric EIR
temperature factor at the design part-load ratio; the exponential and Carnot
modes are transparent benchmark forms:
https://energyplus.readthedocs.io/en/latest/guides/engineering-reference/14.3-chillers.html

All functions accept NumPy arrays or torch tensors (floating point); with
tensors the result is differentiable by autograd. Every physical-validity
check raises ``ValueError``: an infeasible operating point is reported, never
clipped.

Ported and condensed from the frozen research branch
``research/ispo-validation-20261012`` (commit 7065ce31):
``src/agent/scenario_air/energy.py``, ``energy_coilwise.py`` and
``src/agent/scenario_hybrid/energy.py``, ``energy_inactive_acu_v1.py``.
The air-only case is the hybrid model with no liquid branch.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

VERSION = "plant-energy/v1-2026-10-21"
PARAMETER_STATUS = "declared research assumptions, uncalibrated"
KELVIN = 273.15


# ---------------------------------------------------------------- array helpers

def _is_torch(x: Any) -> bool:
    return type(x).__module__.split(".")[0] == "torch"


def array_namespace(*xs: Any):
    """``torch`` if any input is a tensor, else ``numpy``."""
    for x in xs:
        if _is_torch(x):
            import torch
            return torch
    return np


def _like(xs, xp):
    return next((x for x in xs if _is_torch(x)), None)


def as_like(value, like, xp):
    if xp is np:
        return np.asarray(value, dtype=float)
    if like is None or not like.is_floating_point():
        raise ValueError("torch inputs must have a floating-point dtype")
    return xp.as_tensor(value, dtype=like.dtype, device=like.device)


def _finite(value, name, xp):
    if not bool(xp.all(xp.isfinite(value))):
        raise ValueError(f"{name} must be finite")
    return value


def _vector(value, name, xp, like):
    v = as_like(value, like, xp)
    if v.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional vector")
    return _finite(v, name, xp)


def _scalar(value, name, xp, like):
    v = as_like(value, like, xp)
    if v.ndim != 0:
        raise ValueError(f"{name} must be a scalar")
    return _finite(v, name, xp)


def _cat(values, xp):
    return np.concatenate(values) if xp is np else xp.cat(values)


def _check(condition: bool, message: str):
    if not condition:
        raise ValueError(message)


def _positive(value, name):
    _check(math.isfinite(float(value)) and float(value) > 0, f"{name} must be finite and positive")


def _nonnegative(value, name):
    _check(math.isfinite(float(value)) and float(value) >= 0, f"{name} must be finite and nonnegative")


def softmin(v, tau: float, xp):
    """Minimum of a vector; ``tau > 0`` selects the log-sum-exp smooth proxy.

    The proxy lies below the hard minimum by at most ``tau * log(n)``, so it is
    conservative for a chilled-water reset. ``tau = 0`` is the hard minimum.
    """
    _nonnegative(tau, "tau")
    m = v.min()
    if tau == 0:
        return m
    return m - tau * xp.log(xp.sum(xp.exp(-(v - m) / tau)))


def smooth_min2(a, b, eps: float, xp):
    _nonnegative(eps, "eps")
    if eps == 0:
        return 0.5 * (a + b - xp.abs(a - b))
    return 0.5 * (a + b - xp.sqrt((a - b) ** 2 + eps ** 2))


# ---------------------------------------------------------------- parameters

@dataclass(frozen=True)
class FanParams:
    """ACU fan: P = rated_power * (Q / rated_flow) ** exponent within [min, max] fraction."""
    rated_flow_m3s: float
    rated_power_kw: float
    exponent: float = 2.8
    min_fraction: float = 0.22
    max_fraction: float = 1.0
    provenance: str = PARAMETER_STATUS

    def __post_init__(self):
        _positive(self.rated_flow_m3s, "rated fan flow")
        _nonnegative(self.rated_power_kw, "rated fan power")
        _positive(self.exponent, "fan exponent")
        _check(math.isfinite(self.min_fraction) and math.isfinite(self.max_fraction)
               and 0 <= self.min_fraction < self.max_fraction,
               "fan flow-fraction domain must be ordered and nonnegative")


@dataclass(frozen=True)
class CoilParams:
    approach_k: float = 4.0  # delivered supply air minus chilled-water supply
    pinch_k: float = 2.0     # return air minus water return, at every coil
    provenance: str = PARAMETER_STATUS

    def __post_init__(self):
        _positive(self.approach_k, "delivered-air/water approach")
        _nonnegative(self.pinch_k, "return-air/water pinch")


@dataclass(frozen=True)
class AirProps:
    rho: float = 1.18
    cp: float = 1006.0
    provenance: str = "reference values; the caller must match the CFD properties"

    def __post_init__(self):
        _positive(self.rho, "air density")
        _positive(self.cp, "air cp")


@dataclass(frozen=True)
class PlantParams:
    chiller_model: str = "exp"   # exp | carnot | doe2
    cop_ref: float = 6.0
    t_ref_c: float = 7.0
    k_per_k: float = 0.032       # exp: EIR = exp(-k (Ts - Tref)) / COPref
    eta_carnot: float | None = None
    t_cond_c: float = 35.0
    evap_dt_k: float = 2.0
    eirft: tuple = ()            # doe2: six EIR-f(T) coefficients, normalized at the reference
    t_cwe_c: float = 30.0
    curve_provenance: str = ""
    curve_t_chws_range_c: tuple = ()
    curve_t_cwe_range_c: tuple = ()
    curve_reference_tolerance: float = 0.01
    dt_w_mode: str = "coil_limited"  # fixed | coil_limited
    dt_w_design_k: float = 6.0
    dt_w_max_k: float = 10.0
    dt_w_min_k: float = 1.0
    pump_kw_per_kw: float = 0.02     # primary pump electricity per kW of design load at design flow
    pump_exponent: float = 2.0
    rejection_kw_per_kw: float = 0.03
    provenance: str = PARAMETER_STATUS

    def __post_init__(self):
        for key in ("eirft", "curve_t_chws_range_c", "curve_t_cwe_range_c"):
            object.__setattr__(self, key, tuple(getattr(self, key)))
        _positive(self.curve_reference_tolerance, "curve normalization tolerance")
        _check(self.chiller_model in ("exp", "carnot", "doe2"), "unknown chiller model")
        _check(self.dt_w_mode in ("fixed", "coil_limited"), "unknown water return mode")
        for name in ("cop_ref", "dt_w_design_k", "dt_w_max_k", "dt_w_min_k", "pump_exponent"):
            _positive(getattr(self, name), name)
        for name in ("k_per_k", "evap_dt_k", "pump_kw_per_kw", "rejection_kw_per_kw"):
            _nonnegative(getattr(self, name), name)
        for name in ("t_ref_c", "t_cond_c", "t_cwe_c"):
            _check(math.isfinite(getattr(self, name)), f"{name} must be finite")
        _check(self.dt_w_min_k <= self.dt_w_design_k <= self.dt_w_max_k,
               "water dT bounds must enclose the design dT")
        _check(self.eta_carnot is None or 0 < self.eta_carnot <= 1,
               "Carnot efficiency fraction must be in (0, 1]")


# ---------------------------------------------------------------- plant components

def chiller_eir(t_chws_c, plant: PlantParams, xp=None):
    """Chiller electric input ratio (kW electricity per kW cooling) at supply temperature Ts."""
    xp = xp or array_namespace(t_chws_c)
    t = _finite(as_like(t_chws_c, _like((t_chws_c,), xp), xp), "chilled-water supply temperature", xp)
    if plant.chiller_model in ("exp", "carnot"):
        te = t - plant.evap_dt_k + KELVIN
        tc = plant.t_cond_c + KELVIN
        _check(not bool(xp.any(te <= 0)) and not bool(xp.any(te >= tc)),
               "evaporator temperature must be positive in kelvin and below the condenser")
    if plant.chiller_model == "exp":
        eir = xp.exp(-plant.k_per_k * (t - plant.t_ref_c)) / plant.cop_ref
    elif plant.chiller_model == "carnot":
        eta = plant.eta_carnot
        if eta is None:
            te_ref = plant.t_ref_c - plant.evap_dt_k + KELVIN
            eta = plant.cop_ref * (tc - te_ref) / te_ref
            _check(0 < eta <= 1, "reference COP cannot calibrate a physical Carnot fraction")
        eir = (tc - te) / (eta * te)
    else:
        _check(len(plant.eirft) == 6 and plant.curve_provenance.strip() != "",
               "doe2 needs six source-identified EIR-f(T) coefficients")
        _check(len(plant.curve_t_chws_range_c) == 2 and len(plant.curve_t_cwe_range_c) == 2,
               "doe2 needs declared chilled- and condenser-water temperature domains")
        lo, hi = plant.curve_t_chws_range_c
        clo, chi = plant.curve_t_cwe_range_c
        _check(all(math.isfinite(float(c)) for c in (*plant.eirft, lo, hi, clo, chi)) and lo < hi and clo < chi,
               "doe2 coefficients and domains must be finite and ordered")
        _check(not bool(xp.any(t < lo)) and not bool(xp.any(t > hi)) and clo <= plant.t_cwe_c <= chi,
               "doe2 evaluation outside the source curve domain")
        a, b, c, d, e, f = plant.eirft
        y, x0 = plant.t_cwe_c, plant.t_ref_c
        ref = a + b * x0 + c * x0 * x0 + d * y + e * y * y + f * x0 * y
        _check(lo <= x0 <= hi and abs(ref - 1) <= plant.curve_reference_tolerance,
               "the EIR-f(T) curve must be normalized at its declared reference point")
        eir = (a + b * t + c * t * t + d * y + e * y * y + f * t * y) / plant.cop_ref
    _finite(eir, "chiller EIR", xp)
    _check(not bool(xp.any(eir <= 0)), "chiller EIR must stay positive in its declared domain")
    return eir


def return_water_c(t_chws_c, t_air_return_c, plant: PlantParams, coil: CoilParams, xp=None, eps=0.0):
    """Common water-return temperature that keeps the hot-end pinch below the coldest return air."""
    xp = xp or array_namespace(t_chws_c, t_air_return_c)
    like = _like((t_chws_c, t_air_return_c), xp)
    ts = _finite(as_like(t_chws_c, like, xp), "water supply", xp)
    tra = _finite(as_like(t_air_return_c, like, xp), "governing return air", xp)
    cap = tra - coil.pinch_k - ts
    if plant.dt_w_mode == "fixed":
        dt = plant.dt_w_design_k
        _check(not bool(xp.any(cap < dt)), "fixed water dT violates the coil pinch")
    else:
        _check(not bool(xp.any(cap < plant.dt_w_min_k)), "return-air pinch cannot support the minimum water dT")
        dt = smooth_min2(cap, plant.dt_w_max_k, eps, xp)
        _check(not bool(xp.any(dt <= 0)), "smooth water-return proxy has a nonpositive dT")
    return ts + dt


def primary_power_kw(q_kw, t_chws_c, t_chwr_c, plant: PlantParams, q_design_kw, xp=None):
    """Chiller + primary pump + heat rejection electricity for load Q at (Ts, Tr), kW."""
    xp = xp or array_namespace(q_kw, t_chws_c, t_chwr_c)
    like = _like((q_kw, t_chws_c, t_chwr_c), xp)
    q, ts, tr, qdes = (as_like(v, like, xp) for v in (q_kw, t_chws_c, t_chwr_c, q_design_kw))
    for v, name in ((q, "cooling load"), (ts, "water supply"), (tr, "water return"), (qdes, "design load")):
        _finite(v, name, xp)
    _check(not bool(xp.any(q < 0)) and not bool(xp.any(qdes <= 0)) and not bool(xp.any(tr <= ts)),
           "primary model needs nonnegative load, positive design load and Tr > Ts")
    chiller = q * chiller_eir(ts, plant, xp)
    ratio = (q / qdes) * plant.dt_w_design_k / (tr - ts)   # water flow relative to design
    pump = plant.pump_kw_per_kw * qdes * ratio ** plant.pump_exponent
    rejection = plant.rejection_kw_per_kw * (q + chiller)
    return dict(chiller_kw=chiller, chw_pump_kw=pump, rejection_kw=rejection,
                primary_kw=chiller + pump + rejection, water_flow_ratio=ratio,
                heat_rejected_kw=q + chiller)


def pump_electric_power_kw(mass_flow_kg_s, head_gain_pa, efficiency, *, rho_kg_m3: float,
                           head_convention: str, efficiency_provenance: str):
    """Pump electricity from a solved flow and head: m/rho * dp / eta. Never inferred from flow alone."""
    _check(bool(head_convention) and bool(efficiency_provenance),
           "declare the head convention and the efficiency provenance")
    _positive(rho_kg_m3, "fluid density")
    xp = array_namespace(mass_flow_kg_s, head_gain_pa, efficiency)
    like = _like((mass_flow_kg_s, head_gain_pa, efficiency), xp)
    m = _vector(mass_flow_kg_s, "pump flow", xp, like)
    dp = _vector(head_gain_pa, "pump head gain", xp, like)
    eta = _vector(efficiency, "pump efficiency", xp, like)
    _check(m.shape == dp.shape == eta.shape, "aligned pump vectors")
    _check(not bool(xp.any(m < 0)) and not bool(xp.any(dp < 0))
           and not bool(xp.any(eta <= 0)) and not bool(xp.any(eta > 1)),
           "forward flow, nonnegative head and efficiency in (0, 1] required")
    hydraulic = m / rho_kg_m3 * dp / 1000.0
    return {"hydraulic_kw": hydraulic, "electrical_kw": hydraulic / eta,
            "head_convention": head_convention, "efficiency_provenance": efficiency_provenance}


# ---------------------------------------------------------------- facility power

def facility_power(it_kw, air_heat_kw, acu_flow_m3s, acu_t_supply_c, acu_t_return_c,
                   fans: Sequence[FanParams], *, acu_active=None,
                   liquid_heat_kw=(), liquid_t_supply_c=(), liquid_t_return_c=(),
                   secondary_pump_kw=(), plant: PlantParams = PlantParams(),
                   coil: CoilParams = CoilParams(), air: AirProps = AirProps(),
                   other_kw_fraction: float = 0.0, tau_k: float = 0.0,
                   plant_design_load_kw=None) -> dict:
    """Facility electricity and design PUE of one steady operating point.

    ``it_kw`` is the IT electricity (= IT heat, server fans included);
    ``air_heat_kw`` the declared part rejected to room air, the rest to liquid:
    ``it_kw == air_heat_kw + sum(liquid_heat_kw)``. ACU vectors are aligned with
    ``fans``; ``acu_active`` (bool per ACU, default all) marks closed units,
    which must have zero flow and whose temperatures are ignored. Liquid vectors
    are aligned per CDU primary branch. ``plant_design_load_kw`` defaults to the
    installed load ``it_kw + sum(rated fan power of ALL ACUs)``.
    """
    _nonnegative(other_kw_fraction, "other electricity fraction")
    _nonnegative(tau_k, "tau")
    inputs = (it_kw, air_heat_kw, acu_flow_m3s, acu_t_supply_c, acu_t_return_c,
              liquid_heat_kw, liquid_t_supply_c, liquid_t_return_c, secondary_pump_kw)
    xp = array_namespace(*inputs)
    like = _like(inputs, xp)
    it = _scalar(it_kw, "IT power", xp, like)
    qa = _scalar(air_heat_kw, "air heat", xp, like)
    flow_all = _vector(acu_flow_m3s, "ACU flow", xp, like)
    n_acu = flow_all.shape[0]
    _check(len(fans) == n_acu, "one fan record per ACU")
    active = np.ones(n_acu, dtype=bool) if acu_active is None else np.asarray(acu_active, dtype=bool)
    _check(active.shape == (n_acu,), "one active flag per ACU")
    mask = active if xp is np else xp.as_tensor(active, device=flow_all.device)
    _check(not bool(xp.any(flow_all[~mask] != 0)), "a closed ACU has exactly zero flow")

    def sel(v):
        return v[mask]
    flow = sel(flow_all)
    tsa_all, tra_all = as_like(acu_t_supply_c, like, xp), as_like(acu_t_return_c, like, xp)
    _check(tuple(tsa_all.shape) == (n_acu,) and tuple(tra_all.shape) == (n_acu,),
           "one supply and one return temperature per ACU")
    tsa = _vector(sel(tsa_all), "ACU delivered supply", xp, like)
    tra = _vector(sel(tra_all), "ACU return", xp, like)
    fans_on = [f for f, a in zip(fans, active) if a]
    ql = _vector(liquid_heat_kw, "liquid heat", xp, like)
    tls = _vector(liquid_t_supply_c, "liquid primary supply", xp, like)
    tlr = _vector(liquid_t_return_c, "liquid primary return", xp, like)
    sec = _vector(secondary_pump_kw, "secondary pump electricity", xp, like)
    _check(ql.shape == tls.shape == tlr.shape, "aligned liquid branches")
    _check(bool(it > 0) and bool(qa >= 0) and not bool(xp.any(ql < 0)) and not bool(xp.any(sec < 0)),
           "positive IT and nonnegative heat and pump electricity required")
    _check(bool(xp.abs(qa + xp.sum(ql) - it) <= 1e-8 + 1e-7 * it),
           "the declared steady heat partition IT = air + liquid must hold")
    on = ql > 0
    _check(not bool(xp.any(tlr[on] <= tls[on])), "a heated liquid branch returns warmer than it is supplied")

    candidates = []
    if flow.shape[0]:
        _check(not bool(xp.any(flow <= 0)) and not bool(xp.any(tra < tsa)),
               "an active ACU has positive flow and return >= delivered supply")
        rated = as_like([f.rated_flow_m3s for f in fans_on], like, xp)
        fraction = flow / rated
        low = as_like([f.min_fraction for f in fans_on], like, xp)
        high = as_like([f.max_fraction for f in fans_on], like, xp)
        _check(not bool(xp.any(fraction < low)) and not bool(xp.any(fraction > high)),
               "ACU flow outside its declared fan domain")
        fan_kw = as_like([f.rated_power_kw for f in fans_on], like, xp) * fraction ** \
            as_like([f.exponent for f in fans_on], like, xp)
        measured_air = air.rho * air.cp * flow * (tra - tsa) / 1000.0
        coil_leaving = tsa - fan_kw * 1000.0 / (air.rho * air.cp * flow)
        candidates.append(tsa - coil.approach_k)
    else:
        _check(bool(qa <= 1e-8), "positive room heat needs an active ACU")
        fan_kw = measured_air = coil_leaving = flow
    if bool(xp.any(on)):
        candidates.append(tls[on])
    _check(bool(candidates), "at least one cooling branch required")
    ts = softmin(_cat(candidates, xp), tau_k, xp)
    _check(not flow.shape[0] or not bool(xp.any(coil_leaving <= ts)),
           "fan heat makes the coil leaving air colder than the chilled water")

    fan_total = xp.sum(fan_kw)
    q_air_coil = qa + fan_total
    if flow.shape[0]:
        tr_air = return_water_c(ts, softmin(tra, tau_k, xp), plant, coil, xp, eps=tau_k)
        gap = tra - tr_air
        _check(not bool(xp.any(gap < coil.pinch_k - 1e-10)), "the common water return violates a coil pinch")
        air_capacity = q_air_coil / (tr_air - ts)
    else:
        tr_air, gap, air_capacity = ts, flow, it * 0
    liquid_capacity = ql[on] / (tlr[on] - ts)           # fresh plant water per liquid branch, kW/K
    local_capacity = ql[on] / (tlr[on] - tls[on])       # loop water per liquid branch, kW/K
    mixing = liquid_capacity / local_capacity
    _check(not bool(xp.any(mixing < 0)) and not bool(xp.any(mixing > 1 + 1e-10)),
           "a liquid branch colder than the plant supply needs unmodelled heating")
    q_total = q_air_coil + xp.sum(ql)
    capacity = air_capacity + xp.sum(liquid_capacity)
    tr = ts + q_total / capacity
    q_design = (it + sum(f.rated_power_kw for f in fans)) if plant_design_load_kw is None \
        else as_like(plant_design_load_kw, like, xp)
    primary = primary_power_kw(q_total, ts, tr, plant, q_design, xp)
    sec_total = xp.sum(sec)
    other = other_kw_fraction * it
    cooling = fan_total + sec_total + primary["primary_kw"]
    total = it + cooling + other
    out = dict(primary)
    out.update(
        version=VERSION, parameter_status=PARAMETER_STATUS,
        it_kw=it, air_heat_kw=qa, liquid_heat_kw=xp.sum(ql),
        acu_active=tuple(bool(a) for a in active),
        fan_kw=fan_kw, fan_total_kw=fan_total,
        secondary_pump_total_kw=sec_total, other_kw=other,
        cooling_electric_kw=cooling, total_kw=total, pue=total / it,
        plant_load_kw=q_total, plant_design_load_kw=q_design,
        t_chws_c=ts, t_chwr_c=tr, t_chwr_air_c=tr_air,
        coil_hot_end_gap_k=gap, coil_leaving_air_c=coil_leaving,
        measured_air_heat_kw=measured_air, air_heat_closure_kw=xp.sum(measured_air) - qa,
        liquid_fresh_flow_fraction=mixing,
        reset_mode="hard" if tau_k == 0 else "smooth proxy",
        chiller_scope="design part-load temperature factor" if plant.chiller_model == "doe2"
        else "benchmark surrogate",
    )
    for key in ("total_kw", "pue", "t_chws_c", "t_chwr_c"):
        _finite(out[key], key, xp)
    return out
