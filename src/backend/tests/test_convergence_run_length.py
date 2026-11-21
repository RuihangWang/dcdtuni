"""The return-air verdict needs a run long enough to call a plateau (2026-10-22).

Owner rule (2026-10-20): a steady run is CONVERGED once every ACU return-air
temperature is steady within 0.5 K over the last window. The window is 20 % of
the run. The steady-stop watcher would only stop a solve once that window held
_STEADY_STOP_MIN_WINDOW (200) samples, but the final verdict let the window
shrink to 5 samples: a 20-iteration SingleRack solve read CONVERGED at 294.86 K
while the same mesh settles at 288.37 K by iteration 1046, and a 30-iteration
Shinaide hybrid run read CONVERGED while ACU_01 still climbed 0.33 K in its
last 6 iterations (campaign B7). Now the final verdict and the watcher read
one minimum (``convergence.MIN_WINDOW_SAMPLES``); a run below it is
INCONCLUSIVE with a ``too-short`` reason, and the 0.5 K rule is unchanged.

Owner rule 2026-10-27: the window is a fixed 100 iterations (200 until 2026-10-30; no longer 20 % of
the run) and there is no minimum run length beyond it, so a run stops as soon as
its returns are flat within the project's ``stopToleranceK`` (default 0.5 K)
and otherwise runs to the iteration count the user set.

Replays: the latest return-air series of the ten release cases (all CONVERGED,
1002-2327 iterations) and B7's 20-, 30- and 200-iteration runs, extracted with
fixtures/return_air_replay/make_series.py (each monitor's source SHA-256 is in
the fixture).
"""
from __future__ import annotations

import gzip
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.aircooling.runner import convergence as cv
from backend.aircooling.runner.stlgen_runner import STLGenRunner

REPO = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / "fixtures" / "return_air_replay" / "series.json.gz"


def _runs(group):
    doc = json.loads(gzip.decompress(FIXTURE.read_bytes()))
    assert doc["format"] == "dcdtuni.test.return-air-replay/v1"
    return [run for run in doc["runs"] if run["group"] == group]


def _rows(run):
    columns = []
    for monitor in run["monitors"]:
        ticks = [monitor["first"]]
        for delta in monitor["deltas"]:
            ticks.append(ticks[-1] + delta)
        columns.append([t / 10000 for t in ticks])
    assert all(len(c) == run["iterations"] for c in columns)
    return [list(row) for row in zip(*columns)], [m["patch"] for m in run["monitors"]]


RELEASE = _runs("release")
SHORT = _runs("short")


def test_the_fixture_holds_the_ten_release_cases_and_the_three_short_runs():
    assert len(RELEASE) == 10 and all(run["recordedVerdict"] == "CONVERGED" for run in RELEASE)
    assert sorted(run["iterations"] for run in SHORT) == [20, 30, 200]


@pytest.mark.parametrize("run", RELEASE, ids=lambda run: run["run"].split("/")[1])
def test_release_cases_stay_converged(run):
    rows, names = _rows(run)
    sensors = cv.temperature_series_verdict(rows, names=names)
    assert cv.steady_verdict(sensors) == "CONVERGED", sensors["reasons"]
    assert sensors["status"] == "OK"
    # a fixed window of MIN_WINDOW_SAMPLES iterations, whatever the run's length
    assert sensors["window_samples"] == cv.MIN_WINDOW_SAMPLES


@pytest.mark.parametrize("run", RELEASE, ids=lambda run: run["run"].split("/")[1])
def test_the_old_fraction_window_is_still_selectable(run):
    rows, names = _rows(run)
    sensors = cv.temperature_series_verdict(rows, names=names, window_fraction=0.2)
    assert cv.steady_verdict(sensors) == "CONVERGED", sensors["reasons"]
    assert sensors["window_samples"] == int(len(rows) * 0.2)


TOO_SHORT_RUNS = [run for run in SHORT if run["iterations"] < cv.MIN_WINDOW_SAMPLES]


def test_a_run_of_one_window_is_judged():
    run = next(run for run in SHORT if run["iterations"] == 200)
    rows, names = _rows(run)
    sensors = cv.temperature_series_verdict(rows, names=names)
    assert sensors["status"] == "OK" and sensors["window_samples"] == cv.MIN_WINDOW_SAMPLES
    assert cv.steady_verdict(sensors) == ("CONVERGED" if sensors["passed"] else "NOT_CONVERGED")


@pytest.mark.parametrize("run", TOO_SHORT_RUNS, ids=lambda run: f"{run['iterations']}-iterations")
def test_short_runs_are_inconclusive_too_short(run):
    rows, names = _rows(run)
    sensors = cv.temperature_series_verdict(rows, names=names)
    assert cv.steady_verdict(sensors) == "INCONCLUSIVE"
    assert sensors["status"] == "TOO_SHORT" and not sensors["passed"]
    minimum = cv.minimum_run_iterations()
    assert minimum == 100
    assert sensors["reasons"][0].startswith(f"too-short: {run['iterations']} iterations < minimum {minimum}")
    assert sensors["minimumIterations"] == minimum and sensors["iterations"] == run["iterations"]
    # the per-monitor read-out is kept as a diagnostic
    assert set(sensors["probes"]) == set(names)


def test_the_minimum_is_the_window_itself():
    assert cv.MIN_WINDOW_SAMPLES == 100 and cv.WINDOW_FRACTION is None
    assert cv.run_length_shortfall(99).startswith("too-short: 99 iterations < minimum 100")
    assert cv.run_length_shortfall(100) is None
    # the old fraction-of-run window, when a caller asks for it
    assert cv.run_length_shortfall(499, window_fraction=0.2).startswith("too-short: 499 iterations < minimum 500")
    # point probes write every 10th iteration: the solver iterations count
    rows = [[293.15 + 5 * (1 - 2.718281828 ** (-i / 10))] for i in range(15)]
    assert cv.temperature_series_verdict(rows)["status"] == "TOO_SHORT"
    rows = [[293.15 + 5 * (1 - 2.718281828 ** (-i / 10))] for i in range(100)]
    assert cv.temperature_series_verdict(rows, iterations=1000)["passed"]


def test_the_stop_tolerance_is_the_projects():
    # a return still drifting 0.3 K over the last 100 iterations: flat at 0.5 K, not at 0.2 K
    rows = [[300.0 + 3.0 * min(i, 300) / 300 + 0.3 * max(0, i - 300) / 100] for i in range(500)]
    assert cv.temperature_series_verdict(rows)["passed"]
    assert not cv.temperature_series_verdict(rows, plateau_tol=0.2)["passed"]


def test_the_plateau_rule_itself_is_unchanged():
    sig = cv.temperature_series_verdict.__kwdefaults__
    assert sig["plateau_tol"] == 0.5 and sig["plateau_fraction"] == 0.01 and sig["change_min"] == 0.05


def _watch(tmp_path, monkeypatch, verdict, iterations):
    tmp_path.mkdir()
    runner = object.__new__(STLGenRunner)
    runner._steady_stop_thresholds = {"p_rgh": 1e-3, "U": 1e-3, "T": 1e-3, "k_epsilon": 1e-3}
    runner._sensor_names = lambda: []
    runner._data = {"simulationConfig": {}}
    runner._STEADY_STOP_POLL_SECONDS = 0.02
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "controlDict").write_text("application x;\nstopAt          endTime;\nendTime 5000;\n")
    log = tmp_path / "run.log"
    log.write_text("")
    monkeypatch.setattr(cv, "assess_run", lambda *a, **k: {
        "verdict": verdict, "residuals": {"iterations": iterations}, "sensors": {}})
    monkeypatch.setattr(cv, "parse_solver_log", lambda _p: SimpleNamespace(residuals={}))
    with runner._watch_steady_stop(tmp_path, log):
        time.sleep(0.3)
    return "stopAt          writeNow;" in (tmp_path / "system" / "controlDict").read_text()


def test_the_watcher_reads_the_same_minimum(tmp_path, monkeypatch):
    assert not _watch(tmp_path / "a", monkeypatch, "CONVERGED", 99)
    assert _watch(tmp_path / "b", monkeypatch, "CONVERGED", 100)
    # change the one constant: both the final verdict and the watcher follow
    monkeypatch.setattr(cv, "MIN_WINDOW_SAMPLES", 4)
    assert cv.minimum_run_iterations() == 4
    assert _watch(tmp_path / "c", monkeypatch, "CONVERGED", 20)
    rows, names = _rows(next(run for run in SHORT if run["iterations"] == 20))
    assert cv.temperature_series_verdict(rows, names=names)["status"] == "OK"
    assert not hasattr(STLGenRunner, "_STEADY_STOP_MIN_WINDOW")


# -- the same through assess_run on the real run directories, where they exist ----------------------
def _run_dir(run):
    path = REPO / run["run"]
    return path if (path / "convergence.json").is_file() else None


@pytest.mark.parametrize("run", RELEASE + SHORT, ids=lambda run: run["run"].split("/")[-1])
def test_assess_run_replays_the_real_run_directory(run):
    path = _run_dir(run)
    if path is None:
        pytest.skip(f"{run['run']} is not on this machine (case/*/log and _tmp are git-ignored)")
    report = json.loads((path / "convergence.json").read_text())
    out = cv.assess_run(path, log_path=path / Path(report["log"]).name, write=False)
    returns = out["sensors"]["streams"]["acuReturnPatches"]["verdict"]
    assert [m["sha256"] for m in returns["monitors"]] == [m["sha256"] for m in run["monitors"]]
    recorded = report["sensors"].get("streams", {}).get("acuReturnPatches", {}).get("verdict") or report["sensors"]
    if run["group"] == "release":
        assert out["verdict"] == "CONVERGED" and out["verdictReason"] is None
        # the fixed window; the same verdict as the recorded report
        assert returns["window_samples"] == cv.MIN_WINDOW_SAMPLES
        assert set(returns["probes"]) == set(recorded["probes"])
    elif run["iterations"] < cv.MIN_WINDOW_SAMPLES:
        assert out["verdict"] == "INCONCLUSIVE"
        assert out["verdictReason"].startswith(f"too-short: {run['iterations']} iterations < minimum 100")
        assert out["sensors"]["status"] == "TOO_SHORT"


# -- a run too short to judge passes no return on its own read-out (2026-10-22) ----------------------
@pytest.mark.parametrize("run", TOO_SHORT_RUNS, ids=lambda run: f"{run['iterations']}-iterations")
def test_a_too_short_run_passes_no_single_return(run):
    """The 20-iteration SingleRack run read every return 'passed' on its 5-sample window while the
    run itself was INCONCLUSIVE; a return is judged only on a window the verdict accepts."""
    rows, names = _rows(run)
    sensors = cv.temperature_series_verdict(rows, names=names)
    assert sensors["status"] == "TOO_SHORT"
    shortfall = sensors["reasons"][0]
    for name in names:
        probe = sensors["probes"][name]
        assert probe["passed"] is None, (name, probe)
        assert probe["reasons"][0] == shortfall
        # the window read-out stays as a diagnostic
        assert {"first", "last", "range", "window_peak_to_peak", "window_drift"} <= set(probe)


def test_a_flat_short_return_is_not_passed_and_a_long_one_is():
    def rows(n):
        return [[300.0 + 2.0 * min(i, 50) / 50] for i in range(n)]
    short = cv.temperature_series_verdict(rows(80), names=["ACU_1_Top_patch"])
    probe = short["probes"]["ACU_1_Top_patch"]
    assert short["status"] == "TOO_SHORT" and probe["passed"] is None
    # a run shorter than the window still covers the rise, so its read-out also says why it is not flat
    assert probe["reasons"][0] == short["reasons"][0] and probe["reasons"][0].startswith("too-short: 80 iterations")
    judged = cv.temperature_series_verdict(rows(400), names=["ACU_1_Top_patch"])
    assert judged["status"] == "OK" and judged["probes"]["ACU_1_Top_patch"] == {
        **judged["probes"]["ACU_1_Top_patch"], "passed": True, "reasons": []}
    # the merged stream keys keep the same per-return read-out
    merged = cv._temperature_streams_verdict(short, short)
    assert merged["probes"]["return:ACU_1_Top_patch"]["passed"] is None
