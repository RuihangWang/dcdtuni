"""Shared air/liquid plant accounting under explicit benchmark assumptions.

Air and liquid branches share ONE chiller/primary pump/rejection plant. Liquid
primary branches can use ideal return-water recirculation when their delivered
supply is warmer than the common plant supply. Air coils share a conservative
common return setpoint constrained by every positive-airflow unit.

Server fan electricity is included in the declared total IT budget. Secondary
liquid-pump electricity is counted separately; dissipative pump heat is outside
the present DAE thermal ledger. Equipment curves/efficiencies require declared
provenance. Algebraic derivatives here do not establish solver derivatives.
"""
from __future__ import annotations
import math
from agent.scenarios.air.energy import (
    array_namespace,as_like,_like,finite,softmin,return_water_c,
    primary_power_kw,PlantParams,CoilParams,AirProps,
)

VERSION="hybrid-shared-plant-energy/v1"

def _vector(value,name,xp,like):
    v=as_like(value,like,xp)
    if v.ndim!=1:raise ValueError(name+" must be a one-dimensional vector")
    return finite(v,name,xp)

def _scalar(value,name,xp,like):
    v=as_like(value,like,xp)
    if v.ndim!=0:raise ValueError(name+" must be scalar")
    return finite(v,name,xp)

def _cat(values,xp):
    return xp.concatenate(values) if xp.__name__=="numpy" else xp.cat(values)

def pump_electric_power_kw(mass_flow_kg_s,head_gain_Pa,efficiency,*,rho_kg_m3,
                           head_convention,efficiency_provenance):
    """Use a declared model head, never infer a fan/pump wattage from flow alone."""
    if not head_convention or not efficiency_provenance:
        raise ValueError("explicit hydraulic-head and efficiency provenance required")
    if not math.isfinite(float(rho_kg_m3)) or rho_kg_m3<=0:raise ValueError("positive fluid density")
    xp=array_namespace(mass_flow_kg_s,head_gain_Pa,efficiency)
    like=_like((mass_flow_kg_s,head_gain_Pa,efficiency),xp)
    m=_vector(mass_flow_kg_s,"pump flow",xp,like)
    head=_vector(head_gain_Pa,"pump head gain",xp,like)
    eta=_vector(efficiency,"pump total efficiency",xp,like)
    if m.shape!=head.shape or m.shape!=eta.shape:raise ValueError("aligned pump vectors")
    if bool(xp.any(m<0)) or bool(xp.any(head<0)) or bool(xp.any(eta<=0)) or bool(xp.any(eta>1)):
        raise ValueError("forward pump flow/head and efficiency in (0,1] required")
    hydraulic=m/rho_kg_m3*head/1000.
    return {"hydraulic_kw":hydraulic,"electrical_kw":hydraulic/eta,
        "head_convention":head_convention,"efficiency_provenance":efficiency_provenance,
        "derivative_scope":"algebraic map of supplied hydraulic state; solver sensitivities separate"}

def hybrid_design_power(it_kw,air_heat_kw,acu_flow_m3s,acu_t_supply_c,acu_t_return_c,
                        fans,liquid_load_kw,liquid_t_supply_c,liquid_t_return_c,
                        secondary_liquid_pump_kw,*,plant=PlantParams(),
                        coil=CoilParams(),air=AirProps(),tau_k=0.,
                        other_kw_fraction=0.,plant_design_load_kw=None):
    """Steady heat budget; all electricity is assembled once with one IT denominator.

    All thermal branches use a common primary water medium. Heat-capacity flow
    in kW/K avoids inventing fluid density/cp. Measured air closure is returned
    separately and must pass the task's acceptance gate. Plant load uses the
    fixed declared heat partition, preventing solver heat loss from improving PUE.
    """
    xs=(it_kw,air_heat_kw,acu_flow_m3s,acu_t_supply_c,acu_t_return_c,
        liquid_load_kw,liquid_t_supply_c,liquid_t_return_c,secondary_liquid_pump_kw)
    xp=array_namespace(*xs);like=_like(xs,xp)
    it=_scalar(it_kw,"IT heat/electricity",xp,like);qa=_scalar(air_heat_kw,"air heat",xp,like)
    flow=_vector(acu_flow_m3s,"ACU flow",xp,like)
    tsa=_vector(acu_t_supply_c,"delivered post-fan supply",xp,like)
    tra=_vector(acu_t_return_c,"return air",xp,like)
    ql=_vector(liquid_load_kw,"liquid heat",xp,like)
    tls=_vector(liquid_t_supply_c,"delivered liquid-primary supply",xp,like)
    tlr=_vector(liquid_t_return_c,"liquid-primary return",xp,like)
    secpump=_vector(secondary_liquid_pump_kw,"secondary pump electricity",xp,like)
    if it<=0 or qa<0 or bool(xp.any(ql<0)) or bool(xp.any(secpump<0)):
        raise ValueError("positive IT and nonnegative loads/pump electricity required")
    if ql.shape!=tls.shape or ql.shape!=tlr.shape:raise ValueError("aligned liquid branches")
    if flow.shape!=tsa.shape or flow.shape!=tra.shape or len(fans)!=len(flow):
        raise ValueError("aligned air branches and fan catalog")
    if not math.isfinite(other_kw_fraction) or other_kw_fraction<0:raise ValueError("nonnegative other electricity")
    if not math.isfinite(tau_k) or tau_k<0:raise ValueError("nonnegative smoothing")
    if bool(xp.abs(qa+xp.sum(ql)-it)>1e-8+1e-7*it):
        raise ValueError("steady IT=air+liquid heat partition required; storage/transient needs another model")
    active=ql>0
    if bool(xp.any(tlr[active]<=tls[active])):raise ValueError("positive liquid heat needs return warmer than supply")
    reset_candidates=[]
    zero=it*0
    if len(flow):
        if bool(xp.any(flow<=0)) or bool(xp.any(tra<tsa)):
            raise ValueError("positive airflow and return>=supply required")
        rated=as_like([f.rated_flow_m3s for f in fans],like,xp)
        reference_power=as_like([f.rated_power_kw for f in fans],like,xp)
        exponent=as_like([f.exponent for f in fans],like,xp)
        fraction=flow/rated
        low=as_like([f.min_fraction for f in fans],like,xp);high=as_like([f.max_fraction for f in fans],like,xp)
        if bool(xp.any(fraction<low)) or bool(xp.any(fraction>high)):
            raise ValueError("ACU flow outside declared catalog; never silently clip or change rating")
        fan_kw=reference_power*fraction**exponent
        measured_room=air.rho*air.cp*flow*(tra-tsa)/1000.
        coil_leaving=tsa-fan_kw*1000./(air.rho*air.cp*flow)
        reset_candidates.append(tsa-coil.approach_k)
    else:
        if qa>1e-8:raise ValueError("positive room heat needs an air branch")
        fan_kw=flow;measured_room=flow;coil_leaving=flow
    if bool(xp.any(active)):reset_candidates.append(tls[active])
    if not reset_candidates:raise ValueError("at least one cooling branch required")
    ts=softmin(_cat(reset_candidates,xp),tau_k,xp)
    if len(flow) and bool(xp.any(coil_leaving<=ts)):raise ValueError("coil cold-end crossover")
    q_air_coil=qa+xp.sum(fan_kw)
    if len(flow):
        tr_air=return_water_c(ts,softmin(tra,tau_k,xp),plant,coil,xp,eps=tau_k)
        gap=tra-tr_air
        if bool(xp.any(gap<coil.pinch_k-1e-10)):raise ValueError("individual coil pinch")
        air_capacity_flow=q_air_coil/(tr_air-ts)
        measured_branch_capacity=(measured_room+fan_kw)/(tr_air-ts)
    else:
        tr_air=ts;gap=flow;air_capacity_flow=zero;measured_branch_capacity=flow
    # With ideal warm-return mixing, only a fraction of each liquid primary
    # loop flow comes from the plant. Both flow and heat identities are visible.
    liquid_capacity=ql[active]/(tlr[active]-ts)
    liquid_local_capacity=ql[active]/(tlr[active]-tls[active])
    mixing_fraction=liquid_capacity/liquid_local_capacity
    if bool(xp.any(mixing_fraction<0)) or bool(xp.any(mixing_fraction>1+1e-10)):
        raise ValueError("liquid branch requires unmodelled heating or invalid return mixing")
    q_total=q_air_coil+xp.sum(ql)
    capacity=air_capacity_flow+xp.sum(liquid_capacity)
    tr=ts+q_total/capacity
    q_design=(it+sum(f.rated_power_kw for f in fans)) if plant_design_load_kw is None else plant_design_load_kw
    primary=primary_power_kw(q_total,ts,tr,plant,q_design,xp)
    secondary=xp.sum(secpump);fan_total=xp.sum(fan_kw);other=other_kw_fraction*it
    total=it+fan_total+secondary+primary["primary_kw"]+other
    result=dict(primary)
    result.update(other_heat_location="outside the currently modeled thermal domain",
        energy_model_version=VERSION,it_kw=it,air_heat_kw=qa,liquid_heat_kw=xp.sum(ql),
        air_fan_kw=fan_kw,air_fan_total_kw=fan_total,secondary_liquid_pump_kw=secpump,
        secondary_liquid_pump_total_kw=secondary,total_kw=total,total_pue_design=total/it,
        cooling_electric_kw=fan_total+secondary+primary["primary_kw"],other_kw=other,
        plant_cooling_load_kw=q_total,t_chws_c=ts,t_chwr_c=tr,
        air_group_water_return_c=tr_air,per_coil_hot_end_gap_k=gap,
        actual_air_heat_kw=measured_room,air_heat_closure_kw=xp.sum(measured_room)-qa,
        air_branch_heat_capacity_flow_kw_per_K=measured_branch_capacity,
        target_air_heat_capacity_flow_kw_per_K=air_capacity_flow,
        liquid_fresh_capacity_flow_kw_per_K=liquid_capacity,
        liquid_local_capacity_flow_kw_per_K=liquid_local_capacity,
        liquid_fresh_flow_fraction=mixing_fraction,
        total_primary_capacity_flow_kw_per_K=capacity,
        server_fan_electricity="included in the declared IT budget; no separate invented fan watts",
        secondary_pump_thermal_scope="electricity counted once; pump dissipation outside current DAE heat ledger",
        plant_topology="one common plant, common air return setpoint, ideal liquid return recirculation",
        equipment_capacity_and_hydraulic_limits_verified=False,
        derivative_scope="algebraic energy inputs only; no PDE sensitivity inferred",
        reset_mode="hard" if tau_k==0 else "explicit_smooth_proxy")
    for name in ["total_kw","total_pue_design","t_chws_c","t_chwr_c"]:finite(result[name],name,xp)
    return result
