"""Mixed cooling conserves per-server heat and never duplicates electricity."""
import pytest
from agent.scenarios.hybrid.return_contract import HeatPartition,ElectricalBreakdown,validate_hybrid_I1

def test_heat_is_partitioned_per_server_not_full_load_in_both_solvers():
    result=HeatPartition((10.,20.),(2.,5.),(8.,15.)).validate()
    assert result["total_IT_heat_kW"]==result["air_heat_kW"]+result["liquid_heat_kW"]
    with pytest.raises(ValueError):HeatPartition((10.,),(10.,),(10.,)).validate()

def test_air_liquid_shared_electricity_components_are_not_double_counted():
    result=ElectricalBreakdown(100.,{"air_fans":5.},{"liquid_pumps":2.},{"common_chiller":10.},1.).validate()
    assert result["total_electrical_kW"]==118. and result["design_point_pPUE"]==1.18
    with pytest.raises(ValueError):ElectricalBreakdown(100.,{"chiller":5.},{"chiller":2.},{}).validate()

def test_same_global_reward_can_accept_air_liquid_plant_controls():
    components={"air_penalty":.1,"liquid_penalty":.2,"total_power_scaled":.3}
    result=validate_hybrid_I1(-.6,components,{key:1. for key in components},known_component_names=("total_power_scaled",))
    assert result["I1_global_identity_valid"]
    with pytest.raises(ValueError):validate_hybrid_I1(-.3,components,{key:1. for key in components})
