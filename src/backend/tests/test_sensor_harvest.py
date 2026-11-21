"""Runner-side sensor harvest (2026-04-19).

Mirrors the logic in frontend/server/server.py's
`handle_harvest_sensor_results` so the CLI pipeline (run_cfd) produces
the same ``{run_dir}/sensorResults/{name}_sensor_HH-MM-SS.json``
file as the browser-driven flow.

Why in runner, not server: when the user runs
`python -m backend.aircooling.runner.run_cfd --from-cfdrun …` directly (no browser),
the server endpoint is never hit, so the probe JSON was previously missing.
"""
import json
from pathlib import Path

import pytest

from backend.aircooling.runner.sensor_harvest import (
    parse_probes_file,
    find_latest_run_dir,
    read_write_interval,
    parse_probe_locations,
    convert_temp_from_k,
    harvest_sensor_results,
)


SCALAR_PROBE_TEXT = """\
# Probe 0 (2 1 2.5)
# Probe 1 (4 1 2.5)
# Probe 2 (6 1 2.5)
# Time        0             1             2
10            295.15        295.15        295.30
20            295.15        295.25        296.10
30            295.18        295.50        296.80
"""

VECTOR_PROBE_TEXT = """\
# Probe 0 (2 1 2.5)
# Probe 1 (4 1 2.5)
# Time           0                   1
10   (0.1 0.2 0.3)       (0.4 0.5 0.6)
20   (0.11 0.21 0.31)    (0.41 0.51 0.61)
"""

CONTROL_DICT_TEXT = """\
FoamFile
{
    version     2.0;
}
functions
{
    probes
    {
        type            probes;
        libs            ("libsampling.so");
        writeInterval   10;
        fields          (T U p p_rgh);
        probeLocations
        (
            (2.0 1.0 2.5)
            (4.0 1.0 2.5)
            (6.0 1.0 2.5)
        );
    }
}
"""


def test_parse_probes_file_scalar(tmp_path):
    p = tmp_path / "T"
    p.write_text(SCALAR_PROBE_TEXT)
    steps, values = parse_probes_file(p, is_vector=False)
    assert steps == [10, 20, 30]
    assert values[0] == [295.15, 295.15, 295.30]
    assert values[-1] == [295.18, 295.50, 296.80]


def test_parse_probes_file_vector(tmp_path):
    p = tmp_path / "U"
    p.write_text(VECTOR_PROBE_TEXT)
    steps, values = parse_probes_file(p, is_vector=True)
    assert steps == [10, 20]
    assert values[0] == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


def test_parse_probes_file_since_filter(tmp_path):
    """`since=10` must skip row at step 10 and keep later rows."""
    p = tmp_path / "T"
    p.write_text(SCALAR_PROBE_TEXT)
    steps, _ = parse_probes_file(p, is_vector=False, since=10)
    assert steps == [20, 30]


def test_parse_probes_file_missing(tmp_path):
    """Missing file → empty lists, not an exception."""
    steps, values = parse_probes_file(tmp_path / "nope", is_vector=False)
    assert steps == []
    assert values == []


def test_convert_temp_from_k_celsius():
    assert convert_temp_from_k(295.15, "C") == pytest.approx(22.0)


def test_convert_temp_from_k_kelvin():
    assert convert_temp_from_k(295.15, "K") == 295.15


def test_convert_temp_from_k_fahrenheit():
    assert convert_temp_from_k(273.15, "F") == pytest.approx(32.0)


def test_convert_temp_from_k_unknown_unit_defaults_to_c():
    assert convert_temp_from_k(295.15, "x") == pytest.approx(22.0)


def test_parse_probe_locations(tmp_path):
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "controlDict").write_text(CONTROL_DICT_TEXT)
    locs = parse_probe_locations(tmp_path)
    assert locs == [[2.0, 1.0, 2.5], [4.0, 1.0, 2.5], [6.0, 1.0, 2.5]]


def test_parse_probe_locations_missing_dict_returns_empty(tmp_path):
    assert parse_probe_locations(tmp_path) == []


def test_read_write_interval_parses_from_control_dict(tmp_path):
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "controlDict").write_text(CONTROL_DICT_TEXT)
    assert read_write_interval(tmp_path) == 10


def test_read_write_interval_defaults_to_10_when_missing(tmp_path):
    assert read_write_interval(tmp_path) == 10


def test_find_latest_run_dir_picks_newest_with_polymesh(tmp_path):
    """Newest run_dir that has constant/polyMesh wins."""
    log = tmp_path / "log"
    for name in ("2026-04-18-01-00-00_X", "2026-04-19-06-00-00_X"):
        d = log / name
        (d / "constant" / "polyMesh").mkdir(parents=True)
    run_dir = find_latest_run_dir(tmp_path)
    assert run_dir.name == "2026-04-19-06-00-00_X"


def test_find_latest_run_dir_none_when_no_polymesh(tmp_path):
    """Dirs without polyMesh are skipped."""
    (tmp_path / "log" / "2026-04-19-06-00-00_X").mkdir(parents=True)
    assert find_latest_run_dir(tmp_path) is None


def test_find_latest_run_dir_handles_missing_log_dir(tmp_path):
    assert find_latest_run_dir(tmp_path) is None


def test_find_latest_run_dir_accepts_a_partitioned_run(tmp_path):
    """A parallel solve leaves processor*/constant/polyMesh and no root mesh
    (2026-09-22: every np8 run logged 'Sensor harvest skipped: no run_dir')."""
    run = tmp_path / "log" / "2026-09-22-11-46-02_Base604"
    for rank in range(2):
        poly = run / f"processor{rank}" / "constant" / "polyMesh"
        poly.mkdir(parents=True)
        for name in ("points", "faces", "owner", "neighbour", "boundary"):
            (poly / name).write_text("FoamFile {}\n0\n(\n)\n")
    assert find_latest_run_dir(tmp_path) == run


def _make_fake_run(case_dir: Path, run_name: str, *, include_U: bool = True):
    """Synthesize a minimal finished run dir for harvest tests."""
    run_dir = case_dir / "log" / run_name
    (run_dir / "constant" / "polyMesh").mkdir(parents=True)
    (run_dir / "system").mkdir(parents=True)
    (run_dir / "system" / "controlDict").write_text(CONTROL_DICT_TEXT)
    probes = run_dir / "postProcessing" / "probes" / "0"
    probes.mkdir(parents=True)
    (probes / "T").write_text(SCALAR_PROBE_TEXT)
    if include_U:
        # 3 columns to match T above
        (probes / "U").write_text(
            "# Probe 0 (2 1 2.5)\n"
            "# Probe 1 (4 1 2.5)\n"
            "# Probe 2 (6 1 2.5)\n"
            "# Time 0 1 2\n"
            "10 (0.1 0.2 0.3) (0.11 0.21 0.31) (0.12 0.22 0.32)\n"
            "20 (0.2 0.3 0.4) (0.21 0.31 0.41) (0.22 0.32 0.42)\n"
            "30 (0.3 0.4 0.5) (0.31 0.41 0.51) (0.32 0.42 0.52)\n"
        )
    return run_dir


def test_harvest_writes_json_to_case_dir(tmp_path):
    run_dir = _make_fake_run(tmp_path, "2026-04-19-06-56-15_Demo")
    out_path = harvest_sensor_results(
        tmp_path,
        case_name="Demo",
        sensor_names=["s1", "s2", "s3"],
        unit_temperature="C",
    )
    assert out_path is not None
    assert out_path.exists()
    # Path shape: {run_dir}/sensorResults/Demo_sensor_HH-MM-SS.json
    rel = out_path.relative_to(run_dir)
    assert rel.parts[0] == "sensorResults"
    assert rel.parts[1].startswith("Demo_sensor_")
    assert rel.parts[1].endswith(".json")
    payload = json.loads(out_path.read_text())
    assert payload["caseName"] == "Demo"
    assert payload["finalStep"] == 30
    assert payload["unitTemperature"] == "C"
    assert len(payload["sensors"]) == 3
    s0 = payload["sensors"][0]
    assert s0["name"] == "s1"
    assert s0["location"] == [2.0, 1.0, 2.5]
    assert s0["T_final"] == pytest.approx(295.18 - 273.15)  # 22.03 °C
    assert s0["U_final"] == [0.3, 0.4, 0.5]


def test_harvest_returns_none_when_no_run_dir(tmp_path):
    """No run_dir yet (e.g. solve never ran) — harvest silently skips."""
    assert harvest_sensor_results(
        tmp_path, case_name="X", sensor_names=[], unit_temperature="C"
    ) is None


def test_harvest_returns_none_when_no_probe_data(tmp_path):
    """Run dir exists but no probes/ subdir — silently skip, don't raise."""
    run_dir = tmp_path / "log" / "2026-04-19-06-56-15_X"
    (run_dir / "constant" / "polyMesh").mkdir(parents=True)
    assert harvest_sensor_results(
        tmp_path, case_name="X", sensor_names=[], unit_temperature="C"
    ) is None


def test_harvest_handles_sensinel_out_of_mesh(tmp_path):
    """OpenFOAM writes -1e+300 for probes outside the mesh. Convert to None."""
    run_dir = _make_fake_run(tmp_path, "2026-04-19-06-00-00_X",
                             include_U=False)
    probes = run_dir / "postProcessing" / "probes" / "0"
    (probes / "T").write_text(
        "# Probe 0 (2 1 2.5)\n"
        "# Probe 1 (999 999 999)\n"
        "# Time 0 1\n"
        "10 295.15 -1e+300\n"
    )
    (probes / "U").write_text(
        "# Probe 0 (2 1 2.5)\n"
        "# Probe 1 (999 999 999)\n"
        "# Time 0 1\n"
        "10 (0.1 0.2 0.3) (-1e+300 -1e+300 -1e+300)\n"
    )
    out = harvest_sensor_results(
        tmp_path, case_name="X", sensor_names=["ok", "out"],
        unit_temperature="C",
    )
    payload = json.loads(out.read_text())
    assert payload["sensors"][0]["T_final"] == pytest.approx(22.0)
    assert payload["sensors"][1]["T_final"] is None
    assert payload["sensors"][1]["U_final"] is None


def test_harvest_mismatched_sensor_names_raises(tmp_path):
    """Name-count mismatch is a programmer error, not data error."""
    _make_fake_run(tmp_path, "2026-04-19-06-00-00_X")
    with pytest.raises(ValueError, match="sensor count mismatch"):
        harvest_sensor_results(
            tmp_path, case_name="X", sensor_names=["only_one"],
            unit_temperature="C",
        )


# Saved probe enabled metadata controls both native columns and their labels.
def test_runner_probe_enabled_names_and_positions():
    from backend.aircooling.runner.stlgen_runner import STLGenRunner

    runner = object.__new__(STLGenRunner)
    sensors = [
        {"id": "sensor_1", "name": "enabled", "x": 2, "y": 1, "z": 2.5, "enabled": True},
        {"id": "sensor_2", "name": "disabled", "x": 3, "y": 1, "z": 2.5, "enabled": False},
        {"id": "sensor_3", "name": "legacy", "x": 4, "y": 1, "z": 2.5},
        {"id": "sensor_4", "x": 6, "y": 1, "z": 2.5, "enabled": True},
    ]
    runner._data = {"sensorPoints": sensors}
    before = json.loads(json.dumps(sensors))
    assert runner._extract_sensor_points() == [
        {"x": 2.0, "y": 1.0, "z": 2.5},
        {"x": 4.0, "y": 1.0, "z": 2.5},
        {"x": 6.0, "y": 1.0, "z": 2.5},
    ]
    assert runner._sensor_names() == ["enabled", "legacy", "sensor_4"]
    assert runner._data["sensorPoints"] == before


def test_runner_probe_enabled_harvest_column_alignment(tmp_path):
    from backend.aircooling.runner.stlgen_runner import STLGenRunner

    run = _make_fake_run(tmp_path, "2026-10-11-00-00-00_ProbeEnabled")
    runner = object.__new__(STLGenRunner)
    runner.case_dir = tmp_path
    runner.cfdrun_path = tmp_path / "ProbeEnabled.cfdrun"
    runner._data = {
        "sensorPoints": [
            {"id": "sensor_1", "name": "enabled", "x": 2, "y": 1, "z": 2.5, "enabled": True},
            {"id": "sensor_2", "name": "disabled", "x": 3, "y": 1, "z": 2.5, "enabled": False},
            {"id": "sensor_3", "name": "legacy", "x": 4, "y": 1, "z": 2.5},
            {"id": "sensor_4", "x": 6, "y": 1, "z": 2.5, "enabled": True},
        ],
        "simulationConfig": {"unitTemperature": "C"},
    }
    runner._auto_harvest_sensors()
    files = list((run / "sensorResults").glob("*.json"))
    assert len(files) == 1
    rows = json.loads(files[0].read_text())["sensors"]
    assert [row["name"] for row in rows] == ["enabled", "legacy", "sensor_4"]
    assert [row["location"] for row in rows] == [
        [2.0, 1.0, 2.5], [4.0, 1.0, 2.5], [6.0, 1.0, 2.5]
    ]
    assert [row["T_final"] for row in rows] == pytest.approx(
        [295.18 - 273.15, 295.50 - 273.15, 296.80 - 273.15]
    )


def test_runner_disabled_probes_are_not_written_or_harvested(monkeypatch):
    from backend.aircooling.runner.stlgen_runner import STLGenRunner
    from backend.aircooling.runner import sensor_harvest

    runner = object.__new__(STLGenRunner)
    runner._data = {"sensorPoints": [
        {"id": "sensor_1", "name": "sensor_1", "x": 3.25, "y": 1.13, "z": 3.46, "enabled": False},
        {"id": "sensor_2", "name": "sensor_2", "x": 4.37, "y": 0.96, "z": 0.65, "enabled": False},
        {"id": "sensor_3", "name": "sensor_3", "x": 4.15, "y": 0.65, "z": 5.48, "enabled": False},
    ]}
    assert runner._extract_sensor_points() == []
    assert runner._sensor_names() == []

    def forbidden_harvest(*args, **kwargs):
        pytest.fail("Disabled probes must not be harvested or used as column names")

    monkeypatch.setattr(sensor_harvest, "harvest_sensor_results", forbidden_harvest)
    assert runner._auto_harvest_sensors() is None
