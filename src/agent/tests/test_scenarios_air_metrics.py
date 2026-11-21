import math
import numpy as np
import pytest
from agent.scenarios.air.metrics import (rci_hi,rci_lo,rti_flow,rti_temperature,rti_from_fields,
    shi,rhi,rhi_from_returns,flow_weighted_mean)


def test_rci_facts_match_hand_values_and_extreme_heat_is_not_hidden():
    assert rci_hi([25.,28.,30.],27.,32.)==pytest.approx(100*(1-4/15))
    assert rci_lo([17.,19.],18.,15.)==pytest.approx(100*(1-1/6))
    assert rci_hi([50.],27.,32.)<0
    assert rci_hi([20.,26.],27.,32.)==100.


def test_temperature_and_flow_rti_agree_only_with_the_appropriate_heat_balance():
    assert rti_flow(9.,10.)==pytest.approx(90.)
    assert rti_temperature(34.4,20.,16.)==pytest.approx(90.)
    # nonuniform IT and ACU flows; Q_room = sum(vIT*dTIT) = 40 in normalized units
    d=rti_from_fields([22.,24.],[32.,34.],[1.,3.],[18.,22.],[26.,30.],[2.,3.])
    assert d['rti_flow_percent']==pytest.approx(80.)
    assert d['rti_temperature_percent']==pytest.approx(80.)
    assert d['closure_difference_percentage_points']==pytest.approx(0.)
    bad=rti_from_fields([22.,24.],[32.,34.],[1.,3.],[18.,22.],[27.,31.],[2.,3.])
    assert bad['closure_difference_percentage_points']!=0


def test_shi_handles_nonuniform_rack_flow_and_declared_common_reference():
    assert shi([20.,22.],[36.,38.],18.)==pytest.approx(6/38)
    assert shi([20.,22.],[36.,38.],18.,[1.,3.])==pytest.approx(14/78)
    assert rhi([20.,22.],[36.,38.],18.,[1.,3.])==pytest.approx(64/78)
    # reference shifts with physically shifted supply, not a hidden fixed 14C
    assert shi([22.,24.],[38.,40.],20.,[1.,3.])==pytest.approx(14/78)


def test_measured_rhi_is_independent_and_can_detect_heat_closure_discrepancy():
    # rack heat: 64 normalized units; AHU heat 4*(34-18)=64
    measured=rhi_from_returns([36.,38.],[1.,3.],[34.],[4.],18.)
    assert measured==pytest.approx(64/78)
    bad=rhi_from_returns([36.,38.],[1.,3.],[35.],[4.],18.)
    assert bad+rhi([20.,22.],[36.,38.],18.,[1.,3.])!=pytest.approx(2*64/78)
    assert bad+shi([20.,22.],[36.,38.],18.,[1.,3.])!=pytest.approx(1.)


@pytest.mark.parametrize('call',[lambda:rci_hi([],27.,32.),lambda:rci_hi([math.nan],27.,32.),
    lambda:rci_hi([26.],27.,27.),lambda:rci_lo([18.],18.,18.),lambda:rti_flow(1.,0.),
    lambda:rti_temperature(30.,20.,0.),lambda:shi([20.],[20.],20.),
    lambda:shi([20.,22.],[36.],18.),lambda:shi([20.],[36.],18.,[0.]),
    lambda:flow_weighted_mean([20.,22.],[-1.,2.])])
def test_invalid_or_empty_feedback_is_refused(call):
    with pytest.raises(ValueError):call()


def test_flux_weighting_uses_actual_inputs():
    assert flow_weighted_mean([20.,30.],[1.,3.])==pytest.approx(27.5)
