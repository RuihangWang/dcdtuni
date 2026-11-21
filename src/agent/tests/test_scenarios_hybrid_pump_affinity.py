"""Pump-law gate v2: the product law is the nominal affinity law (retired v1: agent_legacy pump_*_v1)."""
import json
import math
from pathlib import Path

import pytest

from agent.scenarios.hybrid import pump_affinity as gate

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "liquidsingle_control_steady.liquidrun"
CURVE = gate.QuadraticPumpCurve(6.0, 2.0e7)


@pytest.mark.parametrize("flow", [0.0, 1e-4, 4e-4, 8e-4])  # 8e-4 extrapolates to negative head: returned, not clipped
def test_rated_speed_product_law_is_the_reference_curve(flow):
    assert CURVE.product_head_m(flow, 1.0) == pytest.approx(CURVE.reference_head_m(flow), rel=0, abs=1e-12)


@pytest.mark.parametrize("speed", [0.3, 0.6, 0.8, 1.2])
@pytest.mark.parametrize("flow", [0.0, 5e-5, 2e-4])
def test_forward_product_law_is_the_nominal_affinity_transform(speed, flow):
    assert CURVE.product_head_m(flow, speed) == pytest.approx(CURVE.nominal_affinity_head_m(flow, speed), abs=1e-12)


@pytest.mark.parametrize("speed", [0.5, 0.8, 1.5])
def test_corresponding_point_obeys_flow_and_head_affinity_and_v1_did_not(speed):
    point = CURVE.corresponding_point(3e-4, speed)
    assert point["product_head_m"] == pytest.approx(point["affinity_expected_head_m"], abs=1e-12)
    # v1 kept the zero-head flow independent of speed: it misses the point by a Q^2 (1 - N^2).
    gap = CURVE.a * point["target_flow_m3s"] ** 2 * (1 - speed ** 2)
    assert point["legacy_v1_head_m"] - point["product_head_m"] == pytest.approx(gap, abs=1e-12)


@pytest.mark.parametrize("speed", [0.0, 0.5, 1.0])
def test_reverse_flow_is_dissipated_not_fed(speed):
    q = 2e-4
    assert CURVE.product_head_m(-q, speed) == pytest.approx(speed ** 2 * 6.0 + CURVE.a * q * q, abs=1e-12)
    assert CURVE.product_head_m(-q, speed) > CURVE.product_head_m(q, speed)


@pytest.mark.parametrize("head,coef", [(0, 1), (-1, 1), (math.nan, 1), (1, -1), (True, 1), (1, math.inf)])
def test_invalid_curves_fail(head, coef):
    with pytest.raises(ValueError):
        gate.QuadraticPumpCurve(head, coef)


@pytest.mark.parametrize("flow,speed", [(-1e-4, 1.0), (1e-4, 0.0), (math.nan, 1.0), (1e-4, -1.0)])
def test_invalid_nominal_points_fail(flow, speed):
    with pytest.raises(ValueError):
        CURVE.nominal_affinity_head_m(flow, speed)


def test_the_solved_pump_head_is_the_product_law_at_every_speed():
    liquidrun = json.loads(FIXTURE.read_text())
    rows = gate.measure_pump_heads(liquidrun, [1.0, 0.8, 0.6])
    verdict = gate.assess(rows, rtol=1e-9)
    assert verdict["passed"], verdict
    assert [r["speed_ratio"] for r in rows] == [1.0, 0.8, 0.6]
    flows = [r["flow_m3s"] for r in rows]
    assert flows[0] > flows[1] > flows[2] > 0
    # The gate tells the laws apart: off rated speed v1 would predict a different head.
    for row in rows[1:]:
        assert abs(row["legacy_v1_head_m"] - row["measured_head_m"]) > 1e-3


def test_a_liquidrun_without_a_pump_is_refused():
    liquidrun = json.loads(FIXTURE.read_text())
    liquidrun["edges"] = [e for e in liquidrun["edges"] if e.get("type") != "pump"]
    with pytest.raises(ValueError, match="pump"):
        gate.measure_pump_heads(liquidrun, [1.0])
