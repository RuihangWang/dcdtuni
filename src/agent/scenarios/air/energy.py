"""Shared NumPy/PyTorch design-condition air-v1 energy model.

Units: degC, m3/s, kW, kg/m3, J/(kg K). Default parameters are explicitly
uncalibrated mock research assumptions, not equipment/vendor curves.
Supply air is the delivered room-inlet temperature after the ACU fan.
The declared approach is delivered-air minus chilled-water supply; actual
pre-fan coil leaving temperature is also returned and checked for crossover.
Q_air removes IT/declared room heat; Q_evap = Q_air_target + ACU fan heat.
Fan electricity enters facility power once and evaporator heat once.

The optional DOE-2 temperature-factor mode is the fixed-design-PLR=1
specialization. A complete EnergyPlus Electric:EIR model also needs matched
capacity and part-load curves. Coefficients and domain/provenance are required;
no vendor coefficients are fabricated. The exp/Carnot modes are transparent
benchmark surrogates. Authoritative framework:
https://energyplus.readthedocs.io/en/latest/guides/engineering-reference/14.3-chillers.html
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Sequence
import math
import numpy as np

KELVIN = 273.15
PARAMETER_STATUS = "mock_unvalidated_air_v1"


def array_namespace(*xs: Any):
    for x in xs:
        if type(x).__module__.split('.')[0] == 'torch':
            import torch
            return torch
    return np


def _like(xs, xp):
    return next((x for x in xs if type(x).__module__.split('.')[0] == 'torch'), None)


def as_like(value, like, xp):
    if xp is np:
        return np.asarray(value, dtype=float)
    if like is None or not like.is_floating_point():
        raise ValueError("torch inputs must have a floating-point dtype")
    return xp.as_tensor(value, dtype=like.dtype, device=like.device)


def finite(value, name, xp):
    if not bool(xp.all(xp.isfinite(value))):
        raise ValueError(f"{name} must be finite")
    return value


def vector(value, name, xp, like=None):
    value = as_like(value, like, xp)
    if value.ndim != 1 or value.shape[0] == 0:
        raise ValueError(f"{name} must be a nonempty one-dimensional vector")
    return finite(value, name, xp)


def _positive(value, name):
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"{name} must be finite and positive")


def _nonnegative(value, name):
    if not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


def softmin(v, tau: float, xp):
    """Conservative unnormalized log-sum-exp minimum; tau=0 is the hard min.

    tau>0 is an explicit smooth proxy and has a size-dependent tau*log(n)
    offset at ties; it is not silently used for the official hard reset.
    """
    _nonnegative(tau, 'tau')
    m = v.min()
    if tau == 0:
        return m
    return m - tau * xp.log(xp.sum(xp.exp(-(v-m)/tau)))


def smooth_min2(a, b, eps: float, xp):
    _nonnegative(eps, 'eps')
    if eps == 0:
        return 0.5*(a+b-xp.abs(a-b))
    return 0.5*(a+b-xp.sqrt((a-b)**2+eps**2))


def smooth_max2(a, b, eps: float, xp):
    _nonnegative(eps, 'eps')
    if eps == 0:
        return 0.5*(a+b+xp.abs(a-b))
    return 0.5*(a+b+xp.sqrt((a-b)**2+eps**2))


@dataclass(frozen=True)
class FanParams:
    rated_flow_m3s: float
    rated_power_kw: float
    exponent: float = 2.8
    min_fraction: float = 0.22
    max_fraction: float = 1.0
    provenance: str = PARAMETER_STATUS

    def __post_init__(self):
        _positive(self.rated_flow_m3s, 'rated fan flow')
        _nonnegative(self.rated_power_kw, 'rated fan power')
        _positive(self.exponent, 'fan exponent')
        if not math.isfinite(self.min_fraction) or not math.isfinite(self.max_fraction) or not 0 <= self.min_fraction < self.max_fraction:
            raise ValueError('fan flow-fraction domain must be ordered and nonnegative')


@dataclass(frozen=True)
class CoilParams:
    approach_k: float = 4.0
    pinch_k: float = 2.0
    provenance: str = PARAMETER_STATUS

    def __post_init__(self):
        _positive(self.approach_k, 'delivered-air/water approach')
        _nonnegative(self.pinch_k, 'return-water/air pinch')


@dataclass(frozen=True)
class PlantParams:
    chiller_model: str = 'exp'
    cop_ref: float = 6.0
    t_ref_c: float = 7.0
    k_per_k: float = 0.032
    eta_carnot: float | None = None
    t_cond_c: float = 35.0
    evap_dt_k: float = 2.0
    eirft: tuple = ()
    t_cwe_c: float = 30.0
    curve_provenance: str = ''
    curve_t_chws_range_c: tuple = ()
    curve_t_cwe_range_c: tuple = ()
    curve_reference_tolerance: float = 0.01
    dt_w_mode: str = 'coil_limited'
    dt_w_design_k: float = 6.0
    dt_w_max_k: float = 10.0
    dt_w_min_k: float = 1.0
    pump_kw_per_kw: float = 0.02
    pump_exponent: float = 2.0
    rejection_kw_per_kw: float = 0.03
    provenance: str = PARAMETER_STATUS

    def __post_init__(self):
        for key in ('eirft','curve_t_chws_range_c','curve_t_cwe_range_c'):
            object.__setattr__(self,key,tuple(getattr(self,key)))
        _positive(self.curve_reference_tolerance,'curve normalization tolerance')
        if self.chiller_model not in ('exp', 'carnot', 'doe2'):
            raise ValueError('unknown chiller model')
        if self.dt_w_mode not in ('fixed', 'coil_limited'):
            raise ValueError('unknown water return mode')
        for name in ('cop_ref','dt_w_design_k','dt_w_max_k','dt_w_min_k','pump_exponent'):
            _positive(getattr(self,name),name)
        for name in ('k_per_k','evap_dt_k','pump_kw_per_kw','rejection_kw_per_kw'):
            _nonnegative(getattr(self,name),name)
        for name in ('t_ref_c','t_cond_c','t_cwe_c'):
            if not math.isfinite(getattr(self,name)):
                raise ValueError(f'{name} must be finite')
        if not self.dt_w_min_k <= self.dt_w_design_k <= self.dt_w_max_k:
            raise ValueError('water dT bounds must enclose the design dT')
        if self.eta_carnot is not None and not 0 < self.eta_carnot <= 1:
            raise ValueError('Carnot efficiency fraction must be in (0,1]')


@dataclass(frozen=True)
class AirProps:
    rho: float = 1.18
    cp: float = 1006.0
    provenance: str = 'mock reference values; caller must match CFD properties'

    def __post_init__(self):
        _positive(self.rho,'air density')
        _positive(self.cp,'air cp')


def chiller_eir(t_chws_c, plant: PlantParams, xp=None):
    xp = xp or array_namespace(t_chws_c)
    like = _like((t_chws_c,),xp)
    t = as_like(t_chws_c,like,xp)
    finite(t,'chilled-water supply temperature',xp)
    if plant.chiller_model == 'exp':
        te=t-plant.evap_dt_k+KELVIN
        if bool(xp.any(te<=0)) or bool(xp.any(te>=plant.t_cond_c+KELVIN)):
            raise ValueError('exp model requires a positive Kelvin evaporator below condenser; inputs are degC')
        result = xp.exp(-plant.k_per_k*(t-plant.t_ref_c))/plant.cop_ref
    elif plant.chiller_model == 'carnot':
        te = t-plant.evap_dt_k+KELVIN
        tc = plant.t_cond_c+KELVIN
        if bool(xp.any(te <= 0)) or bool(xp.any(te >= tc)):
            raise ValueError('Carnot mode requires positive Kelvin evaporator temperature below condenser')
        eta = plant.eta_carnot
        if eta is None:
            te_ref = plant.t_ref_c-plant.evap_dt_k+KELVIN
            eta = plant.cop_ref*(tc-te_ref)/te_ref
            if not 0 < eta <= 1:
                raise ValueError('reference COP cannot calibrate a physical Carnot fraction')
        result = (tc-te)/(eta*te)
    else:
        if len(plant.eirft) != 6 or not isinstance(plant.curve_provenance,str) or not plant.curve_provenance.strip():
            raise ValueError('doe2 needs six source-identified EIRFT coefficients')
        if len(plant.curve_t_chws_range_c) != 2 or len(plant.curve_t_cwe_range_c) != 2:
            raise ValueError('doe2 requires declared chilled/condenser temperature domains')
        lo,hi = plant.curve_t_chws_range_c
        clo,chi = plant.curve_t_cwe_range_c
        if not all(math.isfinite(float(c)) for c in (*plant.eirft,lo,hi,clo,chi)) or not lo < hi or not clo < chi:
            raise ValueError('doe2 coefficients/domains must be finite and ordered')
        if bool(xp.any(t < lo)) or bool(xp.any(t > hi)) or not clo <= plant.t_cwe_c <= chi:
            raise ValueError('doe2 evaluation outside the source curve domain')
        a,b,c,d,e,f = plant.eirft; y=plant.t_cwe_c
        xref=plant.t_ref_c
        reference_factor=a+b*xref+c*xref*xref+d*y+e*y*y+f*xref*y
        if reference_factor<=0:
            raise ValueError('chiller EIRFT must remain positive at the reference')
        if not lo<=xref<=hi or abs(reference_factor-1)>plant.curve_reference_tolerance:
            raise ValueError('source EIRFT must be normalized at its declared reference point')
        result = (a+b*t+c*t*t+d*y+e*y*y+f*t*y)/plant.cop_ref
    finite(result,'chiller EIR',xp)
    if bool(xp.any(result <= 0)):
        raise ValueError('chiller EIR must remain positive in its declared domain')
    return result


def return_water_c(t_chws_c,t_ra_mix_c,plant: PlantParams,coil: CoilParams,xp=None,eps=0.0):
    """Lumped return-water rule; reject pinch/minimum-dT conflicts, never hide them."""
    xp=xp or array_namespace(t_chws_c,t_ra_mix_c)
    like=_like((t_chws_c,t_ra_mix_c),xp)
    ts=as_like(t_chws_c,like,xp); tr_air=as_like(t_ra_mix_c,like,xp)
    finite(ts,'water supply',xp); finite(tr_air,'mixed return air',xp)
    cap=tr_air-coil.pinch_k-ts
    if plant.dt_w_mode == 'fixed':
        dt=plant.dt_w_design_k
        if bool(xp.any(cap < dt)):
            raise ValueError('fixed return-water dT violates the declared coil pinch')
    else:
        if bool(xp.any(cap < plant.dt_w_min_k)):
            raise ValueError('return-air pinch cannot support the minimum water dT')
        dt=smooth_min2(cap,plant.dt_w_max_k,eps,xp)
        if bool(xp.any(dt <= 0)):
            raise ValueError('smooth water-return proxy has nonpositive dT')
    return ts+dt


def primary_power_kw(q_kw,t_chws_c,t_chwr_c,plant: PlantParams,q_design_kw,xp=None):
    """Lumped primary electric power as a function of (Q, Ts, Tr), in kW."""
    xp=xp or array_namespace(q_kw,t_chws_c,t_chwr_c)
    like=_like((q_kw,t_chws_c,t_chwr_c),xp)
    q=as_like(q_kw,like,xp); ts=as_like(t_chws_c,like,xp); tr=as_like(t_chwr_c,like,xp)
    qdes=as_like(q_design_kw,like,xp)
    for val,name in ((q,'cooling load'),(ts,'water supply'),(tr,'water return'),(qdes,'design cooling load')):
        finite(val,name,xp)
    if bool(xp.any(q < 0)) or bool(xp.any(qdes <= 0)) or bool(xp.any(tr <= ts)):
        raise ValueError('primary model needs nonnegative Q, positive design Q and Tr>Ts')
    ch=q*chiller_eir(ts,plant,xp)
    ratio=(q/qdes)*plant.dt_w_design_k/(tr-ts)
    pump=plant.pump_kw_per_kw*qdes*ratio**plant.pump_exponent
    rejection=plant.rejection_kw_per_kw*(q+ch)
    return dict(chiller_kw=ch,chw_pump_kw=pump,rejection_kw=rejection,
                primary_kw=ch+pump+rejection,water_flow_ratio=ratio,
                heat_rejected_kw=q+ch)


def design_power(it_kw,acu_flow_m3s,acu_t_supply_c,acu_t_return_c,fans: Sequence[FanParams],
                 plant=PlantParams(),coil=CoilParams(),air=AirProps(),
                 other_kw_fraction=0.0,tau_k=0.0,other_air_heat_kw=0.0):
    """Official hard reset uses tau=0; tau>0 explicitly selects a smooth proxy.

    P_other is outside the modeled air-room heat load unless other_air_heat_kw
    is specified. Positive out-of-room auxiliary pump heat is not added to
    evaporator load in this declared v1 lumped approximation.
    """
    _nonnegative(other_kw_fraction,'other power fraction'); _nonnegative(tau_k,'tau')
    xp=array_namespace(it_kw,acu_flow_m3s,acu_t_supply_c,acu_t_return_c,other_air_heat_kw)
    like=_like((it_kw,acu_flow_m3s,acu_t_supply_c,acu_t_return_c,other_air_heat_kw),xp)
    flow=vector(acu_flow_m3s,'ACU flow',xp,like)
    tsa=vector(acu_t_supply_c,'delivered supply air',xp,like)
    tra=vector(acu_t_return_c,'return air',xp,like)
    if flow.shape != tsa.shape or flow.shape != tra.shape or len(fans) != flow.shape[0]:
        raise ValueError('one flow, supply/return temperature and fan record per ACU are required')
    it=as_like(it_kw,like,xp); other_heat=as_like(other_air_heat_kw,like,xp)
    if it.ndim != 0 or other_heat.ndim != 0:
        raise ValueError('IT/other room heat must be scalars for this single-design API')
    finite(it,'IT power',xp); finite(other_heat,'other room heat',xp)
    if bool(it <= 0) or bool(other_heat < 0) or bool(other_heat > other_kw_fraction*it):
        raise ValueError('positive IT and 0<=other room heat<=declared other electricity are required')
    if bool(xp.any(flow <= 0)) or bool(xp.any(tra < tsa)):
        raise ValueError('positive ACU flow and return>=delivered supply are required')
    rated=as_like([f.rated_flow_m3s for f in fans],like,xp)
    power=as_like([f.rated_power_kw for f in fans],like,xp)
    exponent=as_like([f.exponent for f in fans],like,xp)
    fractions=flow/rated
    low=as_like([f.min_fraction for f in fans],like,xp); high=as_like([f.max_fraction for f in fans],like,xp)
    if bool(xp.any(fractions < low)) or bool(xp.any(fractions > high)):
        raise ValueError('ACU flow is outside the declared task fan domain')
    fan_kw=power*fractions**exponent
    fan_total=xp.sum(fan_kw)
    air_room_kw=air.rho*air.cp*flow*(tra-tsa)/1000.0
    fan_rise=fan_kw*1000.0/(air.rho*air.cp*flow)
    coil_leaving_air=tsa-fan_rise
    ts=softmin(tsa,tau_k,xp)-coil.approach_k
    if bool(xp.any(coil_leaving_air <= ts)):
        raise ValueError('fan heat creates coil air/water temperature crossover')
    mix=xp.sum(flow*tra)/xp.sum(flow)
    twr=return_water_c(ts,mix,plant,coil,xp,eps=tau_k)
    room_target=it+other_heat
    q_evap=room_target+fan_total
    # The design pump reference is fixed by the task, not a policy-controlled denominator.
    q_design=it+xp.sum(power)+other_heat
    primary=primary_power_kw(q_evap,ts,twr,plant,q_design,xp)
    other=other_kw_fraction*it
    cooling_electric=fan_total+primary['primary_kw']
    total=it+cooling_electric+other
    result=dict(primary)
    result.update(fan_kw=fan_kw,fan_total_kw=fan_total,cooling_load_kw=q_evap,
                  acu_room_heat_kw=air_room_kw,acu_coil_kw=air_room_kw+fan_kw,
                  t_chws_c=ts,t_chwr_c=twr,t_return_mix_c=mix,
                  coil_leaving_air_c=coil_leaving_air,fan_air_rise_k=fan_rise,
                  coil_cold_end_approach_k=coil_leaving_air-ts,
                  other_kw=other,other_air_heat_kw=other_heat,total_kw=total,
                  cooling_electric_kw=cooling_electric,pue=total/it,
                  total_pue_design=total/it,ppue_cool=1.0+cooling_electric/it,
                  energy_closure=(xp.sum(air_room_kw)-room_target)/room_target,
                  energy_closure_kw=xp.sum(air_room_kw)-room_target,
                  parameter_status=PARAMETER_STATUS,
                  supply_temperature_location='delivered_room_inlet_after_fan',
                  approach_reference='delivered_supply_minus_chilled_water',
                  reset_mode='hard' if tau_k==0 else 'smooth_proxy',
                  chiller_scope='fixed_design_PLR_temperature_factor' if plant.chiller_model=='doe2' else 'lumped_benchmark_surrogate')
    for key in ('total_kw','pue','cooling_load_kw','t_chwr_c'):
        finite(result[key],key,xp)
    return result
