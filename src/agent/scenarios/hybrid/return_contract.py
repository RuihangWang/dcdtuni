"""Air/liquid/common-plant accounting contracts for typed Shinaide-derived tasks.

This is an independent return/heat-partition interface, not a claimed Shinaide
solver acceptance. The canonical engineering file remains read-only. Physical
heat split is supplied by the validated task/compiler/evaluator, never inferred
from a filename or duplicated across air and liquid evaluators.
"""
from __future__ import annotations
import math
from dataclasses import dataclass

@dataclass(frozen=True)
class HeatPartition:
    IT_heat_kW:tuple
    air_heat_kW:tuple
    liquid_heat_kW:tuple
    tolerance_kW:float=1e-8
    def validate(self):
        if not len(self.IT_heat_kW)==len(self.air_heat_kW)==len(self.liquid_heat_kW) or not self.IT_heat_kW:raise ValueError("aligned per-server heat budgets required")
        for total,air,liquid in zip(self.IT_heat_kW,self.air_heat_kW,self.liquid_heat_kW):
            if not all(math.isfinite(x) and x>=0 for x in (total,air,liquid)):raise ValueError("finite nonnegative heat budgets")
            if abs(total-air-liquid)>self.tolerance_kW:raise ValueError("air/liquid thermal partition is not conservative")
        return {"total_IT_heat_kW":sum(self.IT_heat_kW),"air_heat_kW":sum(self.air_heat_kW),"liquid_heat_kW":sum(self.liquid_heat_kW),"per_server_partition_conservative":True}

@dataclass(frozen=True)
class ElectricalBreakdown:
    IT_electrical_kW:float
    air_cooling_kW:dict
    liquid_cooling_kW:dict
    common_plant_kW:dict
    distribution_loss_kW:float=0.
    def validate(self):
        if not math.isfinite(self.IT_electrical_kW) or self.IT_electrical_kW<=0:raise ValueError("fixed positive IT denominator")
        names=list(self.air_cooling_kW)+list(self.liquid_cooling_kW)+list(self.common_plant_kW)
        if len(names)!=len(set(names)):raise ValueError("electrical component duplicated across air/liquid/shared plant")
        values=[*self.air_cooling_kW.values(),*self.liquid_cooling_kW.values(),*self.common_plant_kW.values(),self.distribution_loss_kW]
        if not all(math.isfinite(x) and x>=0 for x in values):raise ValueError("finite nonnegative electrical powers required")
        air=sum(self.air_cooling_kW.values());liquid=sum(self.liquid_cooling_kW.values());shared=sum(self.common_plant_kW.values())
        total=self.IT_electrical_kW+air+liquid+shared+self.distribution_loss_kW
        return {"IT_electrical_kW":self.IT_electrical_kW,"air_cooling_kW":air,"liquid_cooling_kW":liquid,"common_plant_kW":shared,"distribution_loss_kW":self.distribution_loss_kW,"total_electrical_kW":total,"design_point_pPUE":total/self.IT_electrical_kW,"components_counted_once":True}

def validate_hybrid_I1(global_reward,loss_components,objective_weights,*,known_component_names=(),tolerance=1e-10):
    """I1 supplies controls/diagnostics; it cannot silently replace global return."""
    from agent.optim.ispo_controller import ReturnContract
    value=ReturnContract(objective_weights,tuple(known_component_names)).verify(global_reward,loss_components)
    return {"global_reward":value,"I1_global_identity_valid":True,"known_component_names":list(known_component_names),"no_fragment_reward_reassignment":True}
