"""Explicit closed ACU branches around the frozen shared-plant energy model."""
import numpy as np
import torch
from .energy import hybrid_design_power
def shared_power_with_closed_acus(*,acu_uids,active_uids,flow_by_uid,supply_by_uid,return_by_uid,
    fans_by_uid,it_kw,air_heat_kw,liquid_load_kw,liquid_supply_c,liquid_return_c,secondary_pump_kw,
    plant_design_load_kw,**kwargs):
    all_uids=tuple(acu_uids);active=tuple(u for u in all_uids if u in active_uids)
    if len(set(all_uids))!=len(all_uids) or set(active)!=set(active_uids) or not active:
        raise ValueError("explicit all/active ACU identity required")
    if any(set(x)!=set(all_uids) for x in (flow_by_uid,supply_by_uid,return_by_uid,fans_by_uid)):
        raise ValueError("complete six-unit ledger required, including failed body")
    for uid in all_uids:
        q=flow_by_uid[uid]
        if uid not in active:
            if float(q)!=0 or return_by_uid[uid] is not None:
                raise ValueError("closed unit has exactly zero flow and unavailable return temperature")
        elif float(q)<=0 or return_by_uid[uid] is None:
            raise ValueError("active unit requires positive flow and a return state")
    values=[flow_by_uid[u] for u in active]+[supply_by_uid[u] for u in active]+[return_by_uid[u] for u in active]
    sample=next((v for v in values if isinstance(v,torch.Tensor)),None)
    def stack(vals):
        return torch.stack([torch.as_tensor(v,dtype=sample.dtype,device=sample.device) for v in vals]) if sample is not None else np.asarray(vals,dtype=float)
    if plant_design_load_kw is None:raise ValueError("full installed plant design load must be fixed across faults")
    result=hybrid_design_power(it_kw,air_heat_kw,stack([flow_by_uid[u] for u in active]),
        stack([supply_by_uid[u] for u in active]),stack([return_by_uid[u] for u in active]),
        [fans_by_uid[u] for u in active],liquid_load_kw,liquid_supply_c,liquid_return_c,secondary_pump_kw,
        plant_design_load_kw=plant_design_load_kw,**kwargs)
    result["active_ACU_UID_order"]=active;result["all_ACU_UID_order"]=all_uids
    result["fan_kw_by_UID"]={u:(result["air_fan_kw"][active.index(u)] if u in active else result["it_kw"]*0) for u in all_uids}
    result["air_coil_kw_by_UID"]={u:((result["actual_air_heat_kw"]+result["air_fan_kw"])[active.index(u)] if u in active else result["it_kw"]*0) for u in all_uids}
    result["installed_plant_design_load_kw"]=plant_design_load_kw
    result["closed_ACU_UIDs"]=tuple(u for u in all_uids if u not in active)
    result["inactive_ports_semantics"]="physical body retained; supply/return closed; no thermostat or heat extraction"
    return result
