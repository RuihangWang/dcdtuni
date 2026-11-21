"""Explicit common-return-setpoint refinement of the air-v1 plant surrogate.

All coils share chilled-water supply and a controlled common water-return
setpoint. Ideal branch balancing supplies the implied nonnegative branch flows.
The reset satisfies the declared hot-end pinch at EVERY coil. It is a
conservative operating-policy assumption, not an equipment UA/capacity model.
Measured room heat closure remains separately reported; the fixed task heat
target prevents solver energy imbalance from reducing the charged plant load.
Every provided positive-airflow unit governs the conservative reset, including
a zero-load unit if supplied. Branch balancing needs independently accepted room
heat closure; capacity, valve authority and water-flow limits remain unverified.
The historical mixed-return v1 implementation remains unchanged.
"""
from __future__ import annotations
from .energy import (design_power, array_namespace, softmin, return_water_c,
                     primary_power_kw, FanParams, PlantParams, CoilParams, AirProps)

VERSION="air-common-return-coilwise-pinch/v2"

def design_power_coilwise(it_kw,acu_flow_m3s,acu_t_supply_c,acu_t_return_c,fans,
                          plant=PlantParams(),coil=CoilParams(),air=AirProps(),
                          other_kw_fraction=0.,tau_k=0.,other_air_heat_kw=0.):
    result=design_power(it_kw,acu_flow_m3s,acu_t_supply_c,acu_t_return_c,fans,
        plant=plant,coil=coil,air=air,other_kw_fraction=other_kw_fraction,
        tau_k=tau_k,other_air_heat_kw=other_air_heat_kw)
    xp=array_namespace(it_kw,acu_flow_m3s,acu_t_supply_c,acu_t_return_c,other_air_heat_kw)
    from .energy import _like,vector
    like=_like((it_kw,acu_flow_m3s,acu_t_supply_c,acu_t_return_c,other_air_heat_kw),xp)
    tra=vector(acu_t_return_c,"per-coil return air",xp,like)
    governing_return=softmin(tra,tau_k,xp)
    ts=result["t_chws_c"]
    twr=return_water_c(ts,governing_return,plant,coil,xp,eps=tau_k)
    # Air temperature mixing remains a diagnostic, never a substitute for
    # the local counterflow hot-end constraint.
    hot_end_gap=tra-twr
    if bool(xp.any(hot_end_gap<coil.pinch_k-1e-10)):
        raise ValueError("Common return setpoint violates a per-coil hot-end pinch")
    q_design=it_kw+sum(f.rated_power_kw for f in fans)+other_air_heat_kw
    primary=primary_power_kw(result["cooling_load_kw"],ts,twr,plant,q_design,xp)
    cooling=result["fan_total_kw"]+primary["primary_kw"]
    total=it_kw+cooling+result["other_kw"]
    result.update(primary)
    result.update(t_chwr_c=twr,cooling_electric_kw=cooling,total_kw=total,
        pue=total/it_kw,total_pue_design=total/it_kw,ppue_cool=1.+cooling/it_kw,
        per_coil_hot_end_gap_k=hot_end_gap,governing_return_air_c=governing_return,
        water_control_policy="common_return_setpoint_with_ideal_branch_balancing",
        energy_model_version=VERSION,
        per_coil_water_heat_capacity_flow_kw_per_k=result["acu_coil_kw"]/(twr-ts),
        target_water_heat_capacity_flow_kw_per_k=result["cooling_load_kw"]/(twr-ts),
        ideal_branch_balancing_requires_room_closure=True,
        equipment_capacity_and_hydraulic_limits_verified=False,
        local_coil_scope="hot/cold-end temperature checks; finite-UA and capacity verification remain separate",
        heat_target_policy="unchanged fixed task heat plus ACU fan heat; measured closure reported separately")
    return result
