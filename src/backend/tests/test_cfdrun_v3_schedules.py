"""cfdrun v3: keyframed air boundaries reach OpenFOAM (transient design §4, plan T4c).

A v3 manifest is v2 plus a `transient` block and per-binding
`parameters.schedules {param: [[t, v], ...]}` (corner rows; a step is two rows
at one t). The reader turns them into the runner's time-varying inputs; the
field writer emits OpenFOAM tables in which a step becomes (t - eps, v_old)
(t, v_new) — OpenFOAM v2412 refuses two rows at one time (probed 09-30) — and
the fan-outlet temperature rise follows a piecewise-LINEAR time() expression.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.aircooling.runner.cfdrun_reader import resolve_run
from backend.aircooling.runner.cfdrun_schema import CfdrunV2
from backend.aircooling.runner.fields import (
    TABLE_STEP_EPSILON_S,
    _build_piecewise_linear_expression,
    _format_time_table,
)
from backend.tests.test_cfdrun_reader import _minimal_manifest, _write_case
from backend.tests.test_template_params import _make_field_gen

EPS = TABLE_STEP_EPSILON_S


# ---------------------------------------------------------------------------
# An independent evaluator for the generated OpenFOAM expression
# ---------------------------------------------------------------------------

def evaluate(expr: str, t: float) -> float:
    """Evaluate `a ? b : c`, comparisons and arithmetic with time() = t."""
    tokens = re.findall(r"time\(\)|\d+\.?\d*(?:[eE][-+]?\d+)?|[-+*/()<?:]", expr)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def ternary():
        cond = comparison()
        if peek() == '?':
            take()
            a = ternary()
            assert take() == ':'
            b = ternary()
            return a if cond else b
        return cond

    def comparison():
        left = additive()
        if peek() == '<':
            take()
            return left < additive()
        return left

    def additive():
        value = term()
        while peek() in ('+', '-'):
            op = take()
            value = value + term() if op == '+' else value - term()
        return value

    def term():
        value = factor()
        while peek() in ('*', '/'):
            op = take()
            value = value * factor() if op == '*' else value / factor()
        return value

    def factor():
        tok = take()
        if tok == '(':
            value = ternary()
            assert take() == ')'
            return value
        if tok == '-':
            return -factor()
        if tok == 'time()':
            return t
        return float(tok)

    result = ternary()
    assert pos == len(tokens), f'unparsed tail {tokens[pos:]}'
    return result


def rows_of(table: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in re.findall(r"\(([-\d.e+]+) ([-\d.e+]+)\)", table)]


# ---------------------------------------------------------------------------
# Tables and expressions
# ---------------------------------------------------------------------------

def test_a_step_becomes_the_epsilon_form_openfoam_accepts():
    table = _format_time_table([{"t": 0, "value": 300}, {"t": 10, "value": 300},
                                {"t": 10, "value": 310}, {"t": 60, "value": 310}])
    rows = rows_of(table)
    times = [t for t, _ in rows]
    assert times == sorted(times) and len(set(times)) == len(times)
    assert rows[1] == (pytest.approx(10 - EPS, abs=1e-12), 300)
    assert rows[2] == (10, 310)


def test_three_rows_at_one_time_are_refused():
    with pytest.raises(ValueError, match="two rows"):
        _format_time_table([{"t": 5, "value": 1}, {"t": 5, "value": 2}, {"t": 5, "value": 3}])


def test_linear_expression_follows_ramps_and_steps():
    rows = [(0, 1000.), (20, 1000.), (40, 2000.), (50, 2000.), (50, 500.), (80, 500.)]
    scale = 1 / 600.
    expr = _build_piecewise_linear_expression(rows, scale)
    assert "time()" in expr
    for t, watts in [(0, 1000), (10, 1000), (20, 1000), (30, 1500), (40, 2000), (49.9, 2000),
                     (50, 500), (65, 500), (200, 500)]:
        assert evaluate(expr, t) == pytest.approx(watts * scale, rel=1e-9), t


# ---------------------------------------------------------------------------
# The v3 manifest
# ---------------------------------------------------------------------------

def v3(bindings, **extra):
    return _minimal_manifest(schemaVersion=3, boundaryBindings=bindings,
                             transient={"durationS": 60.0, "start": {"kind": "uniform",
                                                                     "uniform": {"airT_C": 22.0}}},
                             **extra)


SUPPLY = {"patchName": "ACU_1_supply", "bcType": "acu_supply", "parameters": {
    "temperatureC": 15.0, "flowRateM3s": 4.0,
    "schedules": {"temperatureC": [[0, 15.0], [10, 15.0], [10, 12.0], [60, 12.0]],
                  "flowRateM3s": [[0, 4.0], [20, 4.0], [40, 3.0], [60, 3.0]]}}}


def test_v3_carries_a_transient_block_and_schedules():
    model = CfdrunV2.model_validate(v3({"bb": SUPPLY}))
    assert model.schemaVersion == 3 and model.transient.durationS == 60.0


@pytest.mark.parametrize("mutate,message", [
    (lambda m: m.pop("transient"), "transient"),
    (lambda m: m.update(schemaVersion=2), "schedules"),
    (lambda m: m["boundaryBindings"]["bb"]["parameters"]["schedules"].update(sigma=[[0, 1.0]]), "sigma"),
    (lambda m: m["boundaryBindings"]["bb"]["parameters"]["schedules"]["temperatureC"].reverse(), "order"),
    (lambda m: m["boundaryBindings"]["bb"]["parameters"]["schedules"]["flowRateM3s"].append([90, 3.0]), "duration"),
])
def test_invalid_v3_is_refused_by_name(mutate, message):
    raw = json.loads(json.dumps(v3({"bb": SUPPLY})))
    mutate(raw)
    with pytest.raises(ValueError, match=message):
        CfdrunV2.model_validate(raw)


def test_reader_turns_schedules_into_runner_inputs(tmp_path):
    wall = {"patchName": "ACU_2_front", "bcType": "acu_supply", "parameters": {
        "temperatureC": 18.0, "flowRateM3s": 1.0, "directionWorld": [1.0, 0.0, 0.0], "patchAreaM2": 0.5,
        "schedules": {"flowRateM3s": [[0, 1.0], [30, 1.0], [30, 2.0], [60, 2.0]],
                      "directionWorld": [[0, [1.0, 0.0, 0.0]], [60, [0.0, 0.0, 1.0]]]}}}
    fan = {"patchName": "S1_outlet", "bcType": "fan_outlet", "parameters": {
        "flowRateM3s": 0.5, "powerW": 1000.0, "schedules": {"powerW": [[0, 1000.0], [30, 1000.0], [30, 2000.0], [60, 2000.0]]}}}
    box = {"patchName": "Box_1_top", "bcType": "heat_source_flux", "parameters": {
        "heatFluxWm2": 100.0, "surfaceAreaM2": 2.0, "schedules": {"heatFluxWm2": [[0, 100.0], [60, 300.0]]}}}
    resolved = resolve_run(_write_case(tmp_path, v3({"a": SUPPLY, "w": wall, "f": fan, "b": box})), resolve_stl=False)
    reg = resolved.bridge_registry
    assert resolved.transient["durationS"] == 60.0
    supply = reg.get("ACU_1_supply").timeVarying
    assert supply["supplyTemp"] == [{"t": 0, "value": 15.0}, {"t": 10, "value": 15.0},
                                    {"t": 10, "value": 12.0}, {"t": 60, "value": 12.0}]
    assert supply["airflow"][2] == {"t": 40, "value": 3.0}
    velocity = reg.get("ACU_2_front").timeVarying["velocity"]
    # U = Q/A · dir at the union of both schedules' rows; both jump at 30 s.
    assert velocity[0] == {"t": 0, "value": [2.0, 0.0, 0.0]}
    assert [row["t"] for row in velocity] == [0, 30, 30, 60]
    assert velocity[-1]["value"] == pytest.approx([0.0, 0.0, 4.0])
    assert reg.get("S1_outlet").timeVarying["powerRows"][2] == {"t": 30, "value": 2000.0}
    assert reg.get("Box_1_top").timeVarying["heatOutput"] == [{"t": 0, "value": 200.0}, {"t": 60, "value": 600.0}]


def test_v2_bindings_keep_their_exact_registry_shape(tmp_path):
    resolved = resolve_run(_write_case(tmp_path, _minimal_manifest(boundaryBindings={"bb": {
        "patchName": "P", "bcType": "acu_supply", "parameters": {"temperatureC": 15.0, "timeVarying": {}}}})),
        resolve_stl=False)
    assert resolved.transient is None
    assert resolved.bridge_registry.get("P").timeVarying == {}


# ---------------------------------------------------------------------------
# Field text
# ---------------------------------------------------------------------------

def test_field_text_uses_epsilon_tables_a_vector_table_and_a_linear_rise(tmp_path):
    bc = {
        "ACU_1_supply": {"bc_type": "acu_supply", "flow_rate": 4.0, "temperature": 15.0,
                         "timeVarying": {"airflow": [{"t": 0, "value": 4.0}, {"t": 10, "value": 4.0},
                                                     {"t": 10, "value": 2.0}, {"t": 60, "value": 2.0}],
                                         "supplyTemp": [{"t": 0, "value": 15.0}, {"t": 60, "value": 12.0}]}},
        "ACU_2_front": {"bc_type": "acu_supply", "flow_rate": 1.0, "direction": [1.0, 0.0, 0.0], "patch_area": 0.5,
                        "timeVarying": {"velocity": [{"t": 0, "value": [2.0, 0.0, 0.0]}, {"t": 30, "value": [2.0, 0.0, 0.0]},
                                                     {"t": 30, "value": [4.0, 0.0, 0.0]}]}},
        "S1_outlet": {"bc_type": "fan_outlet", "flow_rate": 0.5, "power": 1000,
                      "timeVarying": {"powerRows": [{"t": 0, "value": 1000.0}, {"t": 30, "value": 1000.0},
                                                    {"t": 50, "value": 2000.0}]}},
        "S1_inlet": {"bc_type": "fan_inlet", "flow_rate": 0.5},
    }
    fg = _make_field_gen(tmp_path, bc)
    (tmp_path / "0").mkdir(parents=True, exist_ok=True)
    patches = [(name, "wall") for name in bc]
    fg._write_u_field(tmp_path / "0" / "U", patches)
    fg._write_t_field(tmp_path / "0" / "T", patches)
    u, t = (tmp_path / "0" / "U").read_text(), (tmp_path / "0" / "T").read_text()
    supply_u = u[u.index("ACU_1_supply"):u.index("ACU_2_front")]
    assert "flowRateInletVelocity" in supply_u and f"({10 - EPS!r} 4.0)" in supply_u.replace(" 4)", " 4.0)")
    front_u = u[u.index("ACU_2_front"):]
    assert "uniformFixedValue" in front_u and "(2.0 0.0 0.0)" in front_u and "(4.0 0.0 0.0)" in front_u
    assert f"({30 - EPS!r} (2.0 0.0 0.0))" in front_u
    outlet = t[t.index("S1_outlet"):]
    expr = re.search(r'expression\s+"([^"]+)"', outlet).group(1)
    rise = re.search(r'\+0\.5\*\((.+)\)$', expr).group(1)
    for time, watts in [(0, 1000.), (40, 1500.), (60, 2000.)]:
        assert evaluate(rise, time) == pytest.approx(watts / (0.5 * 1.19 * 1005.0), rel=1e-3), time


def test_a_steady_stage_never_sees_a_table_or_time(tmp_path):
    from backend.aircooling.runner.fields import strip_time_varying
    bc = {"ACU_1_supply": {"bc_type": "acu_supply", "flow_rate": 4.0, "temperature": 15.0,
                           "timeVarying": {"supplyTemp": [{"t": 0, "value": 15.0}, {"t": 60, "value": 12.0}]}},
          "S1_outlet": {"bc_type": "fan_outlet", "flow_rate": 0.5, "power": 1000,
                        "timeVarying": {"powerRows": [{"t": 0, "value": 1000.0}, {"t": 30, "value": 2000.0}]}},
          "S1_inlet": {"bc_type": "fan_inlet", "flow_rate": 0.5}}
    fg = _make_field_gen(tmp_path, strip_time_varying(bc))
    (tmp_path / "0").mkdir(parents=True, exist_ok=True)
    patches = [(name, "wall") for name in bc]
    fg._write_u_field(tmp_path / "0" / "U", patches)
    fg._write_t_field(tmp_path / "0" / "T", patches)
    text = (tmp_path / "0" / "U").read_text() + (tmp_path / "0" / "T").read_text()
    assert "table" not in text and "time()" not in text
    assert bc["ACU_1_supply"]["timeVarying"]  # the input is not mutated


def test_write_precision_is_raised_when_a_run_carries_schedules():
    from backend.aircooling.runner.fields import ensure_table_write_precision
    assert ensure_table_write_precision({"writePrecision": "single"}, has_schedules=True)["writePrecision"] == "double"
    assert ensure_table_write_precision({}, has_schedules=True)["writePrecision"] == "double"
    assert ensure_table_write_precision({"writePrecision": "single"}, has_schedules=False)["writePrecision"] == "single"
    # 10 digits keep a step's first row distinct even an hour into a run.
    from backend.aircooling.runner.fields import _strict_rows
    rows = _strict_rows([(0, 1.0), (3600, 1.0), (3600, 2.0)])
    assert float(f"{rows[1][0]:.10g}") < 3600


def test_a_uniform_start_begins_at_the_timeline_air_temperature():
    from backend.aircooling.runner.fields import start_simulation_config
    base = {"roomTemperature": 22.0, "solverType": "transient"}
    uniform = {"durationS": 60.0, "start": {"kind": "uniform", "uniform": {"airT_C": 26.5}}}
    assert start_simulation_config(base, uniform)["roomTemperature"] == 26.5
    assert start_simulation_config(base, {"durationS": 60.0, "start": {"kind": "steadyConverged"}}) is base
    assert start_simulation_config(base, None) is base
    assert base["roomTemperature"] == 22.0
