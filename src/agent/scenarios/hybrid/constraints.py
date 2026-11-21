"""Evaluate the registered mock task, not unavailable OEM certification."""
import numpy as np
from agent.scenarios.air.objective import margins,violations,ScoreWeights
from agent.scenarios.air.tolerances import (
    temperature_tolerance_policy,ConstraintRule,assess,score,rank_key)
def array(value,name):
    if hasattr(value,"detach"):value=value.detach().cpu().numpy()
    a=np.asarray(value,dtype=float)
    if not np.isfinite(a).all() or not a.size:raise ValueError("required finite observation: "+name)
    return a
def evaluate_registered_constraints(scenario,air,energy,liquid_result,liquid_root,fan_contract):
    limits=scenario["task_thresholds"];active=tuple(scenario["active_acu_uids"])
    tin=array(air["server_inlet_c"],"inlet");tex=array(air["server_exhaust_c"],"exhaust")
    if tin.shape!=(56,) or tex.shape!=(56,):raise ValueError("all 56 server temperatures required")
    ret=np.array([float(air["ACU_return_c_by_uid"][u]) for u in active])
    coil=np.array([float(energy["air_coil_kw_by_UID"][u]) for u in active])
    cap=np.array([scenario["acu_states"][u]["capacity_kw"] for u in active])
    values=margins(tin,tex,ret,coil,cap,energy_closure=float(energy["air_heat_closure_kw"])/max(1,float(energy["air_heat_kw"])),
                   closure_limit=limits["whole_design_energy_closure_fraction_limit"])
    chip=array(liquid_result["chip_temp_K"],"chip temperatures")
    if chip.shape!=(56,1):raise ValueError("native steady chip_temp_K layout must be [56 chips,1 sample]")
    chip=chip[:,0]
    values["chip_hi_k"]=chip-273.15-limits["chip_max_C"]
    flow=sum(float(air["ACU_flow_by_uid"][u]) for u in scenario["all_acu_uids"])
    values["room_flow_shortfall_frac"]=np.asarray([(limits["room_flow_required_m3s"]-flow)/limits["room_flow_required_m3s"]])
    for uid in scenario["all_acu_uids"]:
        expected=scenario["acu_states"][uid];q=float(air["ACU_flow_by_uid"][uid])
        if abs(q-expected["supply_flow_m3s"])>1e-12:raise ValueError("physical command/UID mismatch")
        if expected["state"]=="failed_closed":
            if q!=0 or float(energy["fan_kw_by_UID"][uid])!=0 or float(energy["air_coil_kw_by_UID"][uid])!=0 or air["ACU_return_c_by_uid"][uid] is not None:
                raise ValueError("closed unit supplied physical flow, cooling or thermostat")
    diagnostics=liquid_root["diagnostics"]
    pump_q=array(liquid_root["pump_volume_flow_m3s"],"pump volume flow")
    pump_h=array(liquid_root["used_head_m"],"actual head")
    dp=array(liquid_root["actual_pump_pressure_rise_Pa"],"actual pressure rise")
    expected_dp=array(liquid_root["modeled_pressure_rise_Pa"],"modeled pressure rise")
    values["pump_reverse_flow_m3s"]=np.maximum(0,-pump_q)
    # Strict H>0 is separately kept as an explicit numerical-domain condition;
    # a zero head must not disappear through relu(H_min-H).
    pump_positive_head=bool(np.all(pump_h>0))
    per_uid=max(abs(s["samples"][0]["Q_IT_W"]-s["samples"][0]["Q_air_W"]-
                    s["samples"][0]["actual_Q_liquid_W"]-s["samples"][0]["solid_storage_W"]) for s in fan_contract["servers"])
    storage=max(abs(s["samples"][0]["solid_storage_W"]) for s in fan_contract["servers"])
    fullF=float(air["original_full_residual_inf"])
    actual_air_heat=float(array(air["air_coil_removed_kw"],"air coil heat").sum())
    prescribed_air_heat=sum(s["samples"][0]["Q_air_W"] for s in fan_contract["servers"])/1000
    closure=actual_air_heat-prescribed_air_heat
    gates={
      "air_full_residual":fullF<=limits["air_full_residual_inf_limit"],
      "air_heat_closure":abs(closure)<=limits["air_heat_closure_kw_limit"],
      "liquid_scaled_residual":float(diagnostics["scaled_residual"])<=limits["liquid_scaled_residual_limit"],
      "pump_curve_pressure_match":float(np.max(np.abs(dp-expected_dp)))<=limits["pump_curve_pressure_mismatch_Pa_limit"],
      "pump_positive_head":pump_positive_head,"pump_nonnegative_flow":bool(np.all(pump_q>=0)),
      "per_UID_heat_closure":per_uid<=limits["per_UID_heat_closure_W_limit"],
      "steady_storage":storage<=limits["steady_storage_W_limit"],
      "native_hydraulic_observations_finite":all(np.isfinite(float(diagnostics[k])) for k in ("mass_residual_kg_s","momentum_residual_Pa")),
      "per_UID_one_dimensional_enthalpy":float(np.max(np.abs(array(air["server_1D_reference_exhaust_c"],"1D reference")-tex)))<=1e-7,
    }
    policy=temperature_tolerance_policy(temperature_tolerance_k=limits["temperature_tolerance_K"],
       extra_rules=(ConstraintRule("chip_hi_k","K",1,limits["temperature_tolerance_K"]),
                    ConstraintRule("room_flow_shortfall_frac","1",.1,1e-9/limits["room_flow_required_m3s"]),
                    ConstraintRule("pump_reverse_flow_m3s","m3/s",1e-4,0)),
       extra_required_keys=("chip_hi_k","room_flow_shortfall_frac","pump_reverse_flow_m3s"))
    v=violations(values);a=assess(v,policy=policy);valid=all(gates.values())
    reward=score(float(energy["total_pue_design"]),tex,v,policy=policy,evaluation_valid=valid)
    ranking=rank_key(float(energy["total_pue_design"]),tex,v,policy=policy,evaluation_valid=True) if valid else None
    return {"format":"shinaide-six-ACU-task-assessment/v2-native-chip-layout","full_registered_mock_task":True,
      "valid":valid,"feasible":valid and a.feasible,"numerical_gates":gates,
      "constraint_margins":{k:np.asarray(x).tolist() for k,x in values.items()},
      "violations":{k:np.asarray(x).tolist() for k,x in v.items()},"policy":policy.as_dict(),
      "I0":{"reward":reward,"rank_key":ranking,"PUE":float(energy["total_pue_design"]),
            "definition":"existing bounded feasible scalarization + registered thermal, capacity, chip and room-flow constraints"},
      "hydraulic_observations":diagnostics,
      "physical_closure":{"per_UID_max_W":per_uid,"steady_storage_max_W":storage,"air_kw":closure},
      "equipment_calibration_metadata":limits["future_calibration_metadata"],
      "scope":"feasibility of the preregistered synthetic simulation task; not OEM/field certification"}
