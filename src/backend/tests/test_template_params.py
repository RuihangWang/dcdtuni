"""Tests for Jinja2 template parameter completeness.

Verifies that all simulationConfig values from the frontend are correctly
propagated to the OpenFOAM dict templates, and that Python fallback defaults
match the frontend's defaults.
"""
import re
from pathlib import Path

import pytest

from backend.aircooling.runner.openfoam_case import (
    generate_control_dict,
    generate_fv_schemes,
    generate_fv_solution,
    generate_fv_options,
    generate_thermophysical_properties,
    copy_system_files_for_solver,
)

SLAB_SIMULATION_CONFIG = {
    "endTime": 1000,
    "writeInterval": 1000,
    "solverType": "steady",
    "roomTemperature": 22,
    "parallelSolve": True,
    "solveProcess": 8,
    "gravity": {"x": 0, "y": -9.81, "z": 0},
    "residualControl": {"p_rgh": 0.001, "U": 0.001, "T": 0.001, "k_epsilon": 0.001},
    "schemes": {
        "divSchemeU": "bounded Gauss linearUpwind grad(U)",
        "divSchemeT": "bounded Gauss linearUpwind grad(U)",
        "divSchemeK": "bounded Gauss limitedLinear 1",
        "divSchemeEpsilon": "bounded Gauss limitedLinear 1",
        "gradSchemeDefault": "Gauss linear",
        "gradSchemeU": "cellLimited Gauss linear 1",
        "gradSchemeK": "",
        "gradSchemeEpsilon": "",
        "laplacianScheme": "Gauss linear limited corrected 0.5",
        "snGradScheme": "limited corrected 0.5",
    },
    "relaxationFactors": {"p_rgh": 0.3, "U": 0.7, "T": 0.7, "k_epsilon": 0.3},
    "nNonOrthogonalCorrectors": 1,
    "nonOrthoCorrection": True,
}


class TestControlDict:
    def test_endtime_from_config(self, tmp_path):
        generate_control_dict(tmp_path, SLAB_SIMULATION_CONFIG)
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "endTime         1000;" in text

    def test_write_interval_from_config(self, tmp_path):
        generate_control_dict(tmp_path, SLAB_SIMULATION_CONFIG)
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "writeInterval   1000;" in text

    def test_single_precision_by_default(self, tmp_path):
        generate_control_dict(tmp_path, {})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "writePrecision  6;" in text
        assert "timePrecision   6;" in text

    def test_double_precision_via_string(self, tmp_path):
        generate_control_dict(tmp_path, {"writePrecision": "double"})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "writePrecision  10;" in text
        assert "timePrecision   10;" in text

    def test_single_precision_via_string(self, tmp_path):
        generate_control_dict(tmp_path, {"writePrecision": "single"})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "writePrecision  6;" in text
        assert "timePrecision   6;" in text

    def test_legacy_bool_double_precision_still_works(self, tmp_path):
        generate_control_dict(tmp_path, {"doublePrecisionWrite": True})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "writePrecision  10;" in text
        assert "timePrecision   10;" in text

    def test_legacy_bool_single_precision_still_works(self, tmp_path):
        generate_control_dict(tmp_path, {"doublePrecisionWrite": False})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "writePrecision  6;" in text
        assert "timePrecision   6;" in text

    def test_fallback_defaults(self, tmp_path):
        generate_control_dict(tmp_path, {})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "endTime         500;" in text
        assert "writeInterval   100;" in text

    def test_steady_solver_type(self, tmp_path):
        generate_control_dict(tmp_path, {"solverType": "steady"})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "buoyantBoussinesqSimpleFoam" in text

    def test_transient_solver_type(self, tmp_path):
        generate_control_dict(tmp_path, {"solverType": "transient"})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "buoyantBoussinesqPimpleFoam" in text
        assert "adjustTimeStep" in text

    def test_default_solver_is_steady(self, tmp_path):
        generate_control_dict(tmp_path, {})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "buoyantBoussinesqSimpleFoam" in text

    def test_transient_control_dict_seconds(self, tmp_path):
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "endTimeSeconds": 120.0,
            "deltaT": 0.05,
            "writeIntervalSeconds": 5.0,
        })
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "endTime         120" in text
        assert "deltaT          0.05" in text
        assert "writeInterval   5" in text

    def test_transient_max_delta_t(self, tmp_path):
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "maxDeltaT": 2.5,
        })
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "maxDeltaT" in text
        assert "2.5" in text

    def test_transient_max_co(self, tmp_path):
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "maxCo": 0.5,
        })
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "maxCo" in text
        assert "0.5" in text

    def test_transient_no_warning_log(self, tmp_path, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            generate_control_dict(tmp_path, {"solverType": "transient"})
        assert "not yet implemented" not in caplog.text

    def test_transient_adaptive_dt_default(self, tmp_path):
        generate_control_dict(tmp_path, {"solverType": "transient"})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "adjustTimeStep  yes;" in text
        assert "maxCo" in text
        assert "maxDeltaT" in text
        assert "writeControl    adjustableRunTime;" in text

    def test_transient_fixed_dt(self, tmp_path):
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "adjustTimeStep": False,
            "deltaT": 0.001,
        })
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "adjustTimeStep  no;" in text
        assert "deltaT          0.001;" in text
        assert "maxCo" not in text
        assert "maxDeltaT" not in text
        assert "writeControl    timeStep;" in text

    def test_transient_fixed_dt_write_interval_is_steps(self, tmp_path):
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "adjustTimeStep": False,
            "deltaT": 0.001,
            "writeInterval": 10,
        })
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "writeControl    timeStep;" in text
        assert "writeInterval   10;" in text

    def test_transient_fixed_dt_write_interval_not_clamped_to_endtime(self, tmp_path):
        """Fixed-dt: writeInterval=5 steps, endTimeSeconds=0.01s — must NOT clamp
        writeInterval to 0.01 (different units: steps vs seconds)."""
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "adjustTimeStep": False,
            "deltaT": 0.001,
            "endTimeSeconds": 0.01,
            "writeInterval": 5,
        })
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "writeControl    timeStep;" in text
        assert "writeInterval   5;" in text

    def test_transient_start_from_latest_time_default(self, tmp_path):
        generate_control_dict(tmp_path, {"solverType": "transient"})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "startFrom       latestTime;" in text

    def test_transient_start_from_start_time(self, tmp_path):
        generate_control_dict(tmp_path, {"solverType": "transient", "startFrom": "startTime"})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "startFrom       startTime;" in text

    def test_steady_endtime_still_integer(self, tmp_path):
        generate_control_dict(tmp_path, {"endTime": 2000, "writeInterval": 500})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "endTime         2000;" in text
        assert "writeInterval   500;" in text


class TestFvSchemes:
    def test_frontend_schemes_propagated(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir, SLAB_SIMULATION_CONFIG["schemes"])
        text = (system_dir / "fvSchemes").read_text()
        assert "bounded Gauss linearUpwind grad(U)" in text
        assert "bounded Gauss limitedLinear 1" in text
        assert "Gauss linear limited corrected 0.5" in text
        assert "limited corrected 0.5" in text

    def test_empty_grad_k_epsilon_omitted(self, tmp_path):
        """Empty string for gradSchemeK/Epsilon should omit those entries."""
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir, SLAB_SIMULATION_CONFIG["schemes"])
        text = (system_dir / "fvSchemes").read_text()
        assert "grad(k)" not in text
        assert "grad(epsilon)" not in text

    def test_fallback_defaults_match_frontend(self, tmp_path):
        """When no schemes config provided, defaults should match the
        frontend's default 'fast' preset: first-order upwind throughout."""
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir, {})
        text = (system_dir / "fvSchemes").read_text()
        for field in ("U", "T", "k", "epsilon"):
            assert f"div(phi,{field})" in text
        assert text.count("bounded Gauss upwind") >= 4, "every transport term defaults to upwind"
        assert "linearUpwind" not in text and "limitedLinear" not in text
        assert "Gauss linear limited 0.5" in text, "laplacian defaults to the fast preset's limited 0.5"
        assert "limited 0.5" in text

    def test_transient_fv_schemes_euler(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir, solver_type="transient")
        text = (system_dir / "fvSchemes").read_text()
        assert "Euler" in text
        assert "steadyState" not in text

    def test_transient_fv_schemes_backward(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir, schemes_config={"ddtScheme": "backward"},
                            solver_type="transient")
        text = (system_dir / "fvSchemes").read_text()
        assert "backward" in text
        assert "Euler" not in text
        assert "steadyState" not in text

    def test_transient_fv_schemes_crank_nicolson(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir,
                            schemes_config={"ddtScheme": "CrankNicolson", "crankNicolsonCoeff": 0.9},
                            solver_type="transient")
        text = (system_dir / "fvSchemes").read_text()
        assert "CrankNicolson 0.9" in text

    def test_transient_fv_schemes_cn_custom_coeff(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir,
                            schemes_config={"ddtScheme": "CrankNicolson", "crankNicolsonCoeff": 0.5},
                            solver_type="transient")
        text = (system_dir / "fvSchemes").read_text()
        assert "CrankNicolson 0.5" in text

    def test_steady_fv_schemes_unchanged(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir, SLAB_SIMULATION_CONFIG["schemes"])
        text = (system_dir / "fvSchemes").read_text()
        assert "steadyState" in text

    def test_transient_fv_schemes_spatial_schemes_propagated(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir, SLAB_SIMULATION_CONFIG["schemes"],
                            solver_type="transient")
        text = (system_dir / "fvSchemes").read_text()
        assert "bounded Gauss linearUpwind grad(U)" in text
        assert "steadyState" not in text
        assert "Euler" in text


class TestFvSolution:
    def test_non_ortho_correctors_from_config(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(
            system_dir,
            residual_control=SLAB_SIMULATION_CONFIG["residualControl"],
            relaxation_factors=SLAB_SIMULATION_CONFIG["relaxationFactors"],
            n_non_ortho_correctors=SLAB_SIMULATION_CONFIG["nNonOrthogonalCorrectors"],
        )
        text = (system_dir / "fvSolution").read_text()
        assert "nNonOrthogonalCorrectors 1;" in text

    def test_fallback_residuals_match_schema(self, tmp_path):
        """When no residualControl provided, defaults must match Pydantic schema:
        1e-3 for every field (owner default 2026-10-27)."""
        import re
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir)
        text = (system_dir / "fvSolution").read_text()
        block = text[text.index("residualControl"):]
        block = block[:block.index("}")]
        values = re.findall(r"([0-9.eE+-]+)\s*;", block)
        assert values and all(float(v) == 1e-3 for v in values), block

    def test_residual_values(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(
            system_dir,
            residual_control={"p_rgh": 0.001, "U": 0.001, "T": 0.001, "k_epsilon": 0.001},
        )
        text = (system_dir / "fvSolution").read_text()
        assert "0.001" in text


class TestFvOptions:
    def test_no_dead_perforated_openings_key(self, tmp_path):
        """fvOptions should not contain empty perforated_openings blocks."""
        generate_fv_options(tmp_path, leakage_zones={})
        text = (tmp_path / "system" / "fvOptions").read_text()
        assert "porousBafflePressure" not in text


class TestCopySystemFiles:
    def test_turbulence_on_by_default(self, tmp_path):
        copy_system_files_for_solver(
            tmp_path,
            room_temperature_c=22.0,
            gravity={"x": 0, "y": -9.81, "z": 0},
        )
        text = (tmp_path / "constant" / "turbulenceProperties").read_text()
        assert 'simulationType  RAS;' in text or 'simulationType RAS;' in text


class TestTransientFvSolution:
    def test_transient_fv_solution_uses_pimple(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient")
        text = (system_dir / "fvSolution").read_text()
        assert "PIMPLE" in text
        assert "SIMPLE" not in text

    def test_pimple_default_params(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient")
        text = (system_dir / "fvSolution").read_text()
        assert "nOuterCorrectors    1;" in text
        assert "nCorrectors         2;" in text

    def test_pimple_custom_outer_correctors(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient",
                             n_outer_correctors=5, n_correctors=3)
        text = (system_dir / "fvSolution").read_text()
        assert "nOuterCorrectors    5;" in text
        assert "nCorrectors         3;" in text

    def test_pimple_no_relaxation_piso_mode(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient",
                             n_outer_correctors=1)
        text = (system_dir / "fvSolution").read_text()
        assert "relaxationFactors" not in text

    def test_pimple_relaxation_with_outer(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient",
                             n_outer_correctors=5,
                             relaxation_factors={"p_rgh": 0.7, "U": 0.3, "T": 0.3, "k_epsilon": 0.3})
        text = (system_dir / "fvSolution").read_text()
        assert "relaxationFactors" in text
        assert "0.7" in text

    def test_pimple_has_final_solvers(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient")
        text = (system_dir / "fvSolution").read_text()
        assert "p_rghFinal" in text
        assert "relTol          0;" in text

    def test_steady_fv_solution_unchanged(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir,
                             residual_control={"p_rgh": 0.001, "U": 0.001, "T": 0.001, "k_epsilon": 0.001},
                             relaxation_factors={"p_rgh": 0.3, "U": 0.7, "T": 0.7, "k_epsilon": 0.3})
        text = (system_dir / "fvSolution").read_text()
        assert "SIMPLE" in text
        assert "PIMPLE" not in text


class TestTransientMonitoring:
    """Verify that probes and surfaceFieldValue FOs render in transient controlDict."""

    def test_transient_probes_rendered(self, tmp_path):
        probes = [{"x": 1.0, "y": 2.0, "z": 0.5}]
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "endTimeSeconds": 10,
            "deltaT": 0.01,
        }, probes=probes)
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "probes" in text
        assert "(1.0 2.0 0.5)" in text
        assert "fields" in text

    def test_transient_surface_field_values_rendered(self, tmp_path):
        monitor_patches = ["R1_outlet"]
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "endTimeSeconds": 10,
            "deltaT": 0.01,
        }, monitor_patches=monitor_patches)
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "surfaceFieldValue" in text
        assert "R1_outlet" in text
        assert "areaAverage" in text

    def test_transient_probes_and_sfv_together(self, tmp_path):
        probes = [{"x": 1.0, "y": 2.0, "z": 0.5}]
        monitor_patches = ["R1_outlet", "R2_outlet"]
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "endTimeSeconds": 10,
            "deltaT": 0.01,
        }, probes=probes, monitor_patches=monitor_patches)
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "probes" in text
        assert "avgT_R1_outlet" in text
        assert "avgT_R2_outlet" in text

    def test_transient_fixed_dt_fo_uses_timestep_writecontrol(self, tmp_path):
        """In fixed-dt mode, FO writeControl must be timeStep, not adjustableRunTime."""
        probes = [{"x": 1.0, "y": 2.0, "z": 0.5}]
        monitor_patches = ["R1_outlet"]
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "adjustTimeStep": False,
            "deltaT": 0.001,
            "endTimeSeconds": 0.01,
            "writeInterval": 5,
        }, probes=probes, monitor_patches=monitor_patches)
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "writeControl    timeStep;" in text
        lines = text.split("\n")
        in_functions = False
        for line in lines:
            if "functions" in line:
                in_functions = True
            if in_functions and "adjustableRunTime" in line:
                raise AssertionError("FO still uses adjustableRunTime in fixed-dt mode")

    def test_transient_fo_write_interval_is_1_for_fixed_dt(self, tmp_path):
        """FO writeInterval must be 1 (every step) regardless of main writeInterval."""
        probes = [{"x": 1.0, "y": 2.0, "z": 0.5}]
        monitor_patches = ["R1_outlet"]
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "adjustTimeStep": False,
            "deltaT": 0.001,
            "endTimeSeconds": 0.01,
            "writeInterval": 5000,
        }, probes=probes, monitor_patches=monitor_patches)
        text = (tmp_path / "system" / "controlDict").read_text()
        # Main dict should have writeInterval 5000
        assert "writeInterval   5000;" in text
        # But FO blocks should have writeInterval 1
        lines = text.split("\n")
        in_functions = False
        fo_write_intervals = []
        for line in lines:
            if "functions" in line:
                in_functions = True
            if in_functions and "writeInterval" in line:
                fo_write_intervals.append(line.strip())
        for wi in fo_write_intervals:
            assert "1;" in wi, f"FO writeInterval should be 1, got: {wi}"

    def test_transient_fo_write_interval_is_delta_t_for_adaptive(self, tmp_path):
        """FO writeInterval should be deltaT for adaptive mode (adjustableRunTime)."""
        probes = [{"x": 1.0, "y": 2.0, "z": 0.5}]
        monitor_patches = ["R1_outlet"]
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "adjustTimeStep": True,
            "deltaT": 0.01,
            "endTimeSeconds": 100,
            "writeIntervalSeconds": 50,
        }, probes=probes, monitor_patches=monitor_patches)
        text = (tmp_path / "system" / "controlDict").read_text()
        # Main dict uses writeIntervalSeconds=50
        assert "writeInterval   50" in text
        # FO blocks should use deltaT=0.01 for near-every-step writing
        lines = text.split("\n")
        in_functions = False
        fo_write_intervals = []
        for line in lines:
            if "functions" in line:
                in_functions = True
            if in_functions and "writeInterval" in line:
                fo_write_intervals.append(line.strip())
        for wi in fo_write_intervals:
            assert "0.01;" in wi, f"FO writeInterval should be deltaT=0.01, got: {wi}"

    def test_transient_no_monitoring_no_functions_block(self, tmp_path):
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "endTimeSeconds": 10,
            "deltaT": 0.01,
        })
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "functions" not in text


def _server_parse_probes_file():
    """server.py's parser, loaded by path under its own name: the bare name
    ``server`` may already be the src/server package in this session."""
    import importlib.util
    import sys
    name = "template_params_server_under_test"
    if name not in sys.modules:
        server_dir = Path(__file__).resolve().parents[2] / "server"
        if str(server_dir) not in sys.path:
            sys.path.insert(0, str(server_dir))
        spec = importlib.util.spec_from_file_location(name, server_dir / "server.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].parse_probes_file


class TestProbeParserFloatTime:
    """Verify server probe file parser handles transient float time steps."""

    def test_float_time_scalar(self, tmp_path):
        parse_probes_file = _server_parse_probes_file()

        probe_file = tmp_path / "T"
        probe_file.write_text(
            "# Probe 0 (1.0 2.0 0.5)\n"
            "# Time          probe0\n"
            "0.001        299.5\n"
            "0.002        300.1\n"
            "0.003        300.8\n"
        )
        steps, values = parse_probes_file(probe_file, is_vector=False)
        assert len(steps) == 3
        assert steps[0] == 0.001
        assert steps[1] == 0.002
        assert values[0] == [299.5]

    def test_float_time_since_filter(self, tmp_path):
        parse_probes_file = _server_parse_probes_file()

        probe_file = tmp_path / "T"
        probe_file.write_text(
            "# Probe 0 (1.0 2.0 0.5)\n"
            "# Time          probe0\n"
            "0.001        299.5\n"
            "0.002        300.1\n"
            "0.003        300.8\n"
        )
        steps, values = parse_probes_file(probe_file, is_vector=False, since=0.001)
        assert len(steps) == 2
        assert steps[0] == 0.002

    def test_float_time_vector(self, tmp_path):
        parse_probes_file = _server_parse_probes_file()

        probe_file = tmp_path / "U"
        probe_file.write_text(
            "# Probe 0 (1.0 2.0 0.5)\n"
            "# Time          probe0\n"
            "0.001        (0.1 0.2 0.3)\n"
            "0.002        (0.4 0.5 0.6)\n"
        )
        steps, values = parse_probes_file(probe_file, is_vector=True)
        assert len(steps) == 2
        assert steps[0] == 0.001
        assert values[0] == [[0.1, 0.2, 0.3]]


# ---------------------------------------------------------------------------
# Time-varying BC tests (Task 2 & 3)
# ---------------------------------------------------------------------------

from backend.aircooling.runner.fields import (
    _format_time_table, FieldGenerator, FEEDBACK_DAMPING_LAMBDA,
)


def _make_field_gen(tmp_path, imported_bc_data, simulation_config=None):
    """Create a minimal FieldGenerator with pre-populated _imported_bc_data."""
    fg = FieldGenerator(
        case_dir=tmp_path,
        simulation_config=simulation_config or {},
    )
    fg._imported_bc_data = imported_bc_data
    fg._data_collected = True
    return fg


_FAN_OUTLET_DAMPED_EXPR = re.compile(
    r'expression\s+"(?P<a>[-\d.eE+]+)\*tSink_(?P<sid>\w+)'
    r'\+(?P<b>[-\d.eE+]+)\*tOutPrev_(?P=sid)\+(?P<c>[-\d.eE+]+)"'
)


def _assert_fan_outlet_dt(text, expected_dt,
                          outlet="S1_outlet_u1", inlet="S1_inlet_u1"):
    """Assert a steady fan_outlet T BC converges to T_in + expected_dt.

    The BC is the damped feedback  T_out = a*T_in + b*T_out_prev + c
    (see FEEDBACK_DAMPING_LAMBDA): a = lambda, a + b = 1 so the fixed point
    is T_out = T_in + c/a, and c is lambda*dT. tSink must read the paired
    inlet (flux-weighted) and tOutPrev the outlet itself.
    """
    block = text.split(f"    {outlet}\n", 1)[1].split("\n    }\n", 1)[0]
    m = _FAN_OUTLET_DAMPED_EXPR.search(block)
    assert m, f"{outlet}: no damped temperature-rise expression in\n{block}"
    a, b, c = (float(m[k]) for k in "abc")
    assert a == pytest.approx(FEEDBACK_DAMPING_LAMBDA)
    assert a + b == pytest.approx(1.0)
    assert abs(c / a - expected_dt) <= 5e-5 / a + 1e-12
    sid = m["sid"]
    # flux-weighted: the transported inlet enthalpy, reduced across ranks
    assert f'"tSink_{sid}{{{inlet}}} = sum(phi*T)/sum(phi)"' in block
    assert f'"tOutPrev_{sid}{{{outlet}}} = weightAverage(T)"' in block


class TestTimeVaryingBC:
    # -- _format_time_table unit tests --

    def test_format_time_table_scalar(self):
        entries = [{"t": 0, "value": 1.4}, {"t": 60, "value": 1.4}, {"t": 60.1, "value": 0}]
        result = _format_time_table(entries)
        assert "(0 1.4)" in result
        assert "(60 1.4)" in result
        assert "(60.1 0)" in result

    def test_format_time_table_with_transform(self):
        entries = [{"t": 0, "value": 15}, {"t": 60, "value": 25}]
        result = _format_time_table(entries, transform_fn=lambda v: v + 273.15)
        assert "288.15" in result
        assert "298.15" in result

    # -- ACU supply velocity (U field) --

    def test_acu_supply_time_varying_velocity(self, tmp_path):
        """When timeVarying airflow data exists, ACU supply should use flowRateInletVelocity + table."""
        tv_data = [{"t": 0, "value": 1.4}, {"t": 60, "value": 1.4}, {"t": 60.1, "value": 0}]
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "flow_rate": 1.4,
                "timeVarying": {"airflow": tv_data},
            }
        }
        fg = _make_field_gen(tmp_path, bc_data)
        u_path = tmp_path / "0" / "U"
        u_path.parent.mkdir(parents=True, exist_ok=True)
        patches = [("ACU_1_supply", "wall")]
        fg._write_u_field(u_path, patches)
        text = u_path.read_text()
        assert "flowRateInletVelocity" in text
        assert "volumetricFlowRate" in text
        assert "table" in text
        assert "(0 1.4)" in text
        assert "(60.1 0)" in text

    def test_acu_return_velocity_uses_pressure_inlet_outlet(self, tmp_path):
        # ACU return is a backflow-stable pressure outlet (pairs with
        # fixedValue p_rgh=0). Plain zeroGradient U diverges on flow reversal.
        tv_data = [{"t": 0, "value": 1.4}, {"t": 60, "value": 0}]
        bc_data = {
            "ACU_1_return": {
                "bc_type": "acu_return",
                "flow_rate": 1.4,
                "timeVarying": {"airflow": tv_data},
            }
        }
        fg = _make_field_gen(tmp_path, bc_data)
        u_path = tmp_path / "0" / "U"
        u_path.parent.mkdir(parents=True, exist_ok=True)
        patches = [("ACU_1_return", "wall")]
        fg._write_u_field(u_path, patches)
        text = u_path.read_text()
        assert "pressureInletOutletVelocity" in text
        assert "flowRateOutletVelocity" not in text
        assert "volumetricFlowRate" not in text
        assert "table" not in text

    def test_acu_supply_no_time_varying_unchanged(self, tmp_path):
        """Without timeVarying, ACU supply should use original flowRateInletVelocity."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "flow_rate": 1.4,
            }
        }
        fg = _make_field_gen(tmp_path, bc_data)
        u_path = tmp_path / "0" / "U"
        u_path.parent.mkdir(parents=True, exist_ok=True)
        patches = [("ACU_1_supply", "wall")]
        fg._write_u_field(u_path, patches)
        text = u_path.read_text()
        assert "flowRateInletVelocity" in text
        assert "uniformFixedValue" not in text

    # -- ACU supply temperature (T field) --

    def test_acu_supply_time_varying_temperature(self, tmp_path):
        tv_data = [{"t": 0, "value": 15}, {"t": 60, "value": 15}, {"t": 60.1, "value": 25}]
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15,
                "timeVarying": {"supplyTemp": tv_data},
            }
        }
        fg = _make_field_gen(tmp_path, bc_data)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        patches = [("ACU_1_supply", "wall")]
        fg._write_t_field(t_path, patches)
        text = t_path.read_text()
        assert "uniformFixedValue" in text
        assert "table" in text
        assert "288.15" in text     # 15 + 273.15
        assert "298.15" in text     # 25 + 273.15

    def test_acu_supply_no_time_varying_temp_unchanged(self, tmp_path):
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15,
            }
        }
        fg = _make_field_gen(tmp_path, bc_data)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        patches = [("ACU_1_supply", "wall")]
        fg._write_t_field(t_path, patches)
        text = t_path.read_text()
        assert "fixedValue" in text
        assert "288.15" in text
        assert "table" not in text

    def test_acu_supply_time_varying_temp_overrides_expression(self, tmp_path):
        """timeVarying supplyTemp should override the transient expression path too."""
        tv_data = [{"t": 0, "value": 18}, {"t": 120, "value": 22}]
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 18,
                "cooling_capacity": 30000,
                "return_patch_name": "ACU_1_return",
                "timeVarying": {"supplyTemp": tv_data},
            }
        }
        fg = _make_field_gen(tmp_path, bc_data, simulation_config={"solverType": "transient"})
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        patches = [("ACU_1_supply", "wall")]
        fg._write_t_field(t_path, patches)
        text = t_path.read_text()
        assert "uniformFixedValue" in text
        assert "table" in text
        assert "291.15" in text     # 18 + 273.15
        assert "295.15" in text     # 22 + 273.15
        assert "expression" not in text

    # -- Heat source flux time-varying (T field) --

    def test_heat_source_time_varying_flux(self, tmp_path):
        """Heat source with timeVarying heatOutput should use q as table."""
        tv_data = [{"t": 0, "value": 5000}, {"t": 120, "value": 5000}, {"t": 150, "value": 10000}]
        surface_area = 2.0
        bc_data = {
            "R3_wall": {
                "bc_type": "heat_source_flux",
                "heat_flux": 2500,
                "surface_area": surface_area,
                "timeVarying": {"heatOutput": tv_data},
            }
        }
        fg = _make_field_gen(tmp_path, bc_data)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        patches = [("R3_wall", "wall")]
        fg._write_t_field(t_path, patches)
        text = t_path.read_text()
        assert "externalWallHeatFluxTemperature" in text
        assert "table" in text
        assert "2500" in text     # 5000W / 2m²
        assert "5000" in text     # 10000W / 2m²

    def test_heat_source_no_time_varying_unchanged(self, tmp_path):
        """Without timeVarying, heat source uses constant q."""
        bc_data = {
            "R3_wall": {
                "bc_type": "heat_source_flux",
                "heat_flux": 2500,
            }
        }
        fg = _make_field_gen(tmp_path, bc_data)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        patches = [("R3_wall", "wall")]
        fg._write_t_field(t_path, patches)
        text = t_path.read_text()
        assert "externalWallHeatFluxTemperature" in text
        assert "q               uniform 2500" in text
        assert "table" not in text


    # -- Fan-server time-varying power (T field) --

    def test_fan_outlet_time_varying_power(self, tmp_path):
        """Fan-server with timeVarying power should produce time-dependent ΔT."""
        tv_data = [
            {"t": 0, "value": 1000},
            {"t": 60, "value": 1000},
            {"t": 61, "value": 2000},
        ]
        flow_rate = 0.5
        rho = 1.19
        cp = 1005.0
        mass_flow = flow_rate * rho
        # ΔT at 1000W = 1000 / (0.595 * 1005) = 1.6722...
        # ΔT at 2000W = 2000 / (0.595 * 1005) = 3.3445...
        dt_1000 = 1000 / (mass_flow * cp)
        dt_2000 = 2000 / (mass_flow * cp)

        bc_data = {
            "S1_outlet_u1": {
                "bc_type": "fan_outlet",
                "flow_rate": flow_rate,
                "power": 1000,
                "timeVarying": {"power": tv_data},
            },
            "S1_inlet_u1": {
                "bc_type": "fan_inlet",
                "flow_rate": flow_rate,
            },
        }
        fg = _make_field_gen(tmp_path, bc_data)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        patches = [("S1_outlet_u1", "wall"), ("S1_inlet_u1", "wall")]
        fg._write_t_field(t_path, patches)
        text = t_path.read_text()

        assert "S1_outlet_u1" in text
        assert "expression" in text
        assert "tSink" in text
        assert f"{round(dt_1000, 4)}" in text
        assert f"{round(dt_2000, 4)}" in text
        assert "time()" in text

    def test_fan_outlet_no_time_varying_unchanged(self, tmp_path):
        """Without timeVarying, fan-server keeps the damped constant-ΔT
        expression (no table) whose fixed point is T_in + ΔT."""
        bc_data = {
            "S1_outlet_u1": {
                "bc_type": "fan_outlet",
                "flow_rate": 0.5,
                "power": 1000,
            },
            "S1_inlet_u1": {
                "bc_type": "fan_inlet",
                "flow_rate": 0.5,
            },
        }
        fg = _make_field_gen(tmp_path, bc_data)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        patches = [("S1_outlet_u1", "wall"), ("S1_inlet_u1", "wall")]
        fg._write_t_field(t_path, patches)
        text = t_path.read_text()

        assert "expression" in text
        assert "tSink" in text
        # Constant ΔT, no table
        dt = 1000 / (0.5 * 1.19 * 1005.0)
        _assert_fan_outlet_dt(text, dt)
        assert "table" not in text.split("S1_outlet_u1")[1].split("}")[0] if "S1_outlet_u1" in text else True

    def test_fan_server_time_varying_airflow_u_field(self, tmp_path):
        """Fan-server with timeVarying airflow should use volumetricFlowRate table in U."""
        tv_data = [{"t": 0, "value": 0.5}, {"t": 1, "value": 0.5}, {"t": 1.1, "value": 0}]
        bc_data = {
            "S1_outlet_u1": {
                "bc_type": "fan_outlet",
                "flow_rate": 0.5,
                "power": 1000,
                "timeVarying": {"airflow": tv_data},
            },
            "S1_inlet_u1": {
                "bc_type": "fan_inlet",
                "flow_rate": 0.5,
                "timeVarying": {"airflow": tv_data},
            },
        }
        fg = _make_field_gen(tmp_path, bc_data)
        u_path = tmp_path / "0" / "U"
        u_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_u_field(u_path, [("S1_outlet_u1", "wall"), ("S1_inlet_u1", "wall")])
        u_text = u_path.read_text()

        # fan_inlet should have time-varying volumetricFlowRate
        u_inlet = u_text.split("S1_inlet_u1")[1]
        assert "flowRateOutletVelocity" in u_inlet
        assert "table" in u_inlet
        assert "(1.1 0)" in u_inlet

        # fan_outlet should also have time-varying volumetricFlowRate
        u_outlet = u_text.split("S1_outlet_u1")[1].split("S1_inlet_u1")[0]
        assert "flowRateInletVelocity" in u_outlet
        assert "table" in u_outlet
        assert "(1.1 0)" in u_outlet

    def test_acu_supply_fixed_temp_mode(self, tmp_path):
        """fixedTemp mode should generate fixedValue regardless of solver type."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "cooling_capacity": 35000,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
                "cooling_model_mode": "fixedTemp",
            }
        }
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "fixedValue" in t_text
        assert "uniform 288.15" in t_text
        assert "expression" not in t_text

    def test_acu_supply_legacy_no_model_transient(self, tmp_path):
        """Old cfdrun (cooling_capacity but no coolingModel) in transient → Level 0 expression."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "cooling_capacity": 35000,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
            }
        }
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "expression" in t_text
        assert "Qmax_ACU_1_supply = 35000" in t_text
        assert "weightAverage(T)" in t_text

    def test_acu_supply_legacy_no_model_steady(self, tmp_path):
        """Old cfdrun steady-state → fixedValue even with cooling_capacity."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "cooling_capacity": 35000,
                "set_point_temp": 22.0,
            }
        }
        fg = _make_field_gen(tmp_path, bc_data, simulation_config={"solverType": "steady"})
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "fixedValue" in t_text
        assert "uniform 295.15" in t_text
        assert "expression" not in t_text

    def test_acu_supply_linear_capacity_expression(self, tmp_path):
        """linearCapacity mode generates expression with Q_max = f(T_return)."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 1.7,
                "cooling_capacity": 35000,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
                "cooling_model_mode": "linearCapacity",
                "rated_return_temp": 35.0,
            }
        }
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "uniformFixedValue" in t_text
        assert "expression" in t_text
        assert "Qrated_ACU_1_supply = 35000" in t_text
        assert "Trated_ACU_1_supply = 308.15" in t_text  # 35 + 273.15
        assert "Tcool_ACU_1_supply = 287.15" in t_text   # 14 + 273.15
        assert "weightAverage(T)" in t_text

    def test_acu_supply_linear_capacity_cold_return(self, tmp_path):
        """When T_return < T_coolant, Q_max should floor to 0 via max(..., 0)."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "cooling_capacity": 35000,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
                "cooling_model_mode": "linearCapacity",
                "rated_return_temp": 35.0,
            }
        }
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "max(Qrated_ACU_1_supply" in t_text
        assert ",0)" in t_text.replace(" ", "")

    def test_acu_supply_table_capacity_expression(self, tmp_path):
        """tableCapacity generates piecewise ternary interpolation for Q_max = f(T_return)."""
        perf_table = [
            {"returnTemp": 25, "capacity": 20},
            {"returnTemp": 30, "capacity": 30},
            {"returnTemp": 35, "capacity": 35},
        ]
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
                "cooling_model_mode": "tableCapacity",
                "cooling_performance_table": perf_table,
            }
        }
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "uniformFixedValue" in t_text
        assert "expression" in t_text
        assert "298.15" in t_text  # 25 + 273.15
        assert "303.15" in t_text  # 30 + 273.15
        assert "308.15" in t_text  # 35 + 273.15
        assert "20000" in t_text   # 20 kW → 20000 W
        assert "30000" in t_text   # 30 kW → 30000 W
        assert "35000" in t_text   # 35 kW → 35000 W
        assert "weightAverage(T)" in t_text

    def test_acu_supply_table_capacity_extrapolation(self, tmp_path):
        """Table endpoints use constant extrapolation."""
        perf_table = [
            {"returnTemp": 28, "capacity": 25},
            {"returnTemp": 33, "capacity": 32},
        ]
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
                "cooling_model_mode": "tableCapacity",
                "cooling_performance_table": perf_table,
            }
        }
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "301.15" in t_text  # 28 + 273.15
        assert "25000" in t_text   # 25 kW
        assert "306.15" in t_text  # 33 + 273.15
        assert "32000" in t_text   # 32 kW


class TestTransientTimeVaryingIntegration:
    """Integration tests: full transient scenarios with time-varying BCs."""

    def test_acu_return_velocity_is_pressure_outlet_even_when_supply_has_airflow_table(self, tmp_path):
        """Return face should not prescribe a flow rate; it is a backflow-stable
        pressure outlet (pressureInletOutletVelocity) anchored by p_rgh=0."""
        tv_supply = {
            "airflow": [{"t": 0, "value": 4}, {"t": 2, "value": 4}, {"t": 2.1, "value": 0}],
            "supplyTemp": [{"t": 0, "value": 15}, {"t": 1, "value": 15}, {"t": 3, "value": 25}],
        }
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "flow_rate": 4,
                "temperature": 15,
                "timeVarying": tv_supply,
                "return_patch_name": "ACU_1_return",
            },
            "ACU_1_return": {
                "bc_type": "acu_return",
                "flow_rate": 4,
                # NO timeVarying here — return velocity is a pressure outlet.
            },
        }
        fg = _make_field_gen(tmp_path, bc_data)
        u_path = tmp_path / "0" / "U"
        u_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_u_field(u_path, [("ACU_1_supply", "wall"), ("ACU_1_return", "wall")])
        u_text = u_path.read_text()

        # Supply should have time-varying airflow
        u_supply = u_text.split("ACU_1_supply")[1].split("ACU_1_return")[0]
        assert "flowRateInletVelocity" in u_supply
        assert "table" in u_supply
        assert "(2.1 0)" in u_supply

        # Return should be a backflow-stable pressure outlet.
        u_return = u_text.split("ACU_1_return")[1]
        assert "pressureInletOutletVelocity" in u_return
        assert "flowRateOutletVelocity" not in u_return
        assert "table" not in u_return

    def test_acu_shutdown_scenario(self, tmp_path):
        """Simulate ACU shutdown: airflow 1.4->0 at t=60s, supplyTemp 15->15 (constant)."""
        tv = {
            "airflow": [{"t": 0, "value": 1.4}, {"t": 60, "value": 1.4}, {"t": 60.1, "value": 0}],
            "supplyTemp": [{"t": 0, "value": 15}, {"t": 60, "value": 15}, {"t": 60.1, "value": 15}],
        }
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "flow_rate": 1.4,
                "temperature": 15,
                "timeVarying": tv,
            },
            "ACU_1_return": {
                "bc_type": "acu_return",
                "flow_rate": 1.4,
                "timeVarying": tv,
            },
        }
        fg = _make_field_gen(tmp_path, bc_data)

        u_path = tmp_path / "0" / "U"
        t_path = tmp_path / "0" / "T"
        u_path.parent.mkdir(parents=True, exist_ok=True)

        patches_u = [("ACU_1_supply", "wall"), ("ACU_1_return", "wall")]
        patches_t = [("ACU_1_supply", "wall")]

        fg._write_u_field(u_path, patches_u)
        fg._write_t_field(t_path, patches_t)

        u_text = u_path.read_text()
        t_text = t_path.read_text()

        # U field: flowRateInletVelocity + table for airflow
        assert "flowRateInletVelocity" in u_text
        assert "volumetricFlowRate" in u_text
        assert "table" in u_text
        assert "(60.1 0)" in u_text

        # T field: uniformFixedValue + table for temperature (in K)
        assert "uniformFixedValue" in t_text
        assert "table" in t_text
        assert "288.15" in t_text  # 15 deg C in K

    def test_heat_source_power_ramp(self, tmp_path):
        """Rack power ramp: 5kW -> 10kW over 30s starting at t=120s."""
        tv_data = [
            {"t": 0, "value": 5000},
            {"t": 120, "value": 5000},
            {"t": 150, "value": 10000},
        ]
        bc_data = {
            "R3_wall": {
                "bc_type": "heat_source_flux",
                "heat_flux": 2500,
                "surface_area": 2.0,
                "timeVarying": {"heatOutput": tv_data},
            }
        }
        fg = _make_field_gen(tmp_path, bc_data)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        patches = [("R3_wall", "wall")]
        fg._write_t_field(t_path, patches)
        text = t_path.read_text()

        assert "externalWallHeatFluxTemperature" in text
        assert "table" in text
        # 5000W / 2m^2 = 2500, 10000W / 2m^2 = 5000
        assert "2500" in text
        assert "5000" in text

    def test_mixed_steady_and_time_varying(self, tmp_path):
        """Mix of time-varying ACU and constant heat source."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "flow_rate": 1.4,
                "temperature": 15,
                "timeVarying": {
                    "airflow": [{"t": 0, "value": 1.4}, {"t": 60, "value": 0}],
                },
            },
            "R1_wall": {
                "bc_type": "heat_source_flux",
                "heat_flux": 1000,
                # NO timeVarying -- should remain constant
            },
        }
        fg = _make_field_gen(tmp_path, bc_data)

        u_path = tmp_path / "0" / "U"
        t_path = tmp_path / "0" / "T"
        u_path.parent.mkdir(parents=True, exist_ok=True)

        fg._write_u_field(u_path, [("ACU_1_supply", "wall")])
        fg._write_t_field(t_path, [("ACU_1_supply", "wall"), ("R1_wall", "wall")])

        u_text = u_path.read_text()
        t_text = t_path.read_text()

        # ACU supply should be time-varying (flowRateInletVelocity + table)
        assert "flowRateInletVelocity" in u_text
        assert "table" in u_text
        # Heat source should be constant
        assert "q               uniform 1000" in t_text

    def test_full_transient_acu_shutdown_plus_server_power_ramp(self, tmp_path):
        """Full scenario: ACU shutdown + ACU temp ramp + fan-server power ramp."""
        bc_data = {
            # ACU1 supply: temp ramp 15→25°C + airflow reduction 4→2
            "ACU1_supply": {
                "bc_type": "acu_supply",
                "flow_rate": 4,
                "temperature": 15,
                "timeVarying": {
                    "airflow": [{"t": 0, "value": 4}, {"t": 3, "value": 4}, {"t": 3.1, "value": 2}],
                    "supplyTemp": [{"t": 0, "value": 15}, {"t": 1, "value": 15}, {"t": 3, "value": 25}],
                },
            },
            "ACU1_return": {
                "bc_type": "acu_return",
                "flow_rate": 4,
                "timeVarying": {
                    "airflow": [{"t": 0, "value": 4}, {"t": 3, "value": 4}, {"t": 3.1, "value": 2}],
                },
            },
            # ACU2 supply: full shutdown at t=2.1s
            "ACU2_supply": {
                "bc_type": "acu_supply",
                "flow_rate": 4,
                "temperature": 15,
                "timeVarying": {
                    "airflow": [{"t": 0, "value": 4}, {"t": 2, "value": 4}, {"t": 2.1, "value": 0}],
                },
            },
            "ACU2_return": {
                "bc_type": "acu_return",
                "flow_rate": 4,
                "timeVarying": {
                    "airflow": [{"t": 0, "value": 4}, {"t": 2, "value": 4}, {"t": 2.1, "value": 0}],
                },
            },
            # Fan-server: power ramp 150→2000W
            "S1_outlet": {
                "bc_type": "fan_outlet",
                "flow_rate": 0.5,
                "power": 150,
                "timeVarying": {
                    "power": [{"t": 0, "value": 150}, {"t": 3, "value": 150}, {"t": 3.1, "value": 2000}],
                },
            },
            "S1_inlet": {
                "bc_type": "fan_inlet",
                "flow_rate": 0.5,
            },
        }
        fg = _make_field_gen(tmp_path, bc_data)
        u_path = tmp_path / "0" / "U"
        t_path = tmp_path / "0" / "T"
        u_path.parent.mkdir(parents=True, exist_ok=True)

        all_patches = [(p, "wall") for p in bc_data]
        fg._write_u_field(u_path, all_patches)
        fg._write_t_field(t_path, all_patches)

        u_text = u_path.read_text()
        t_text = t_path.read_text()

        # --- U field checks ---
        # ACU1 supply: flowRateInletVelocity + table with airflow reduction
        assert "ACU1_supply" in u_text
        u_acu1 = u_text.split("ACU1_supply")[1].split("}")[0]
        assert "flowRateInletVelocity" in u_acu1
        assert "table" in u_acu1
        assert "(3.1 2)" in u_acu1

        # ACU2 supply: shutdown — airflow→0
        assert "ACU2_supply" in u_text
        u_acu2 = u_text.split("ACU2_supply")[1].split("}")[0]
        assert "flowRateInletVelocity" in u_acu2
        assert "(2.1 0)" in u_acu2

        # ACU2 return: pressure outlet, velocity is not prescribed.
        assert "ACU2_return" in u_text
        u_acu2r = u_text.split("ACU2_return")[1].split("}")[0]
        assert "pressureInletOutletVelocity" in u_acu2r
        assert "flowRateOutletVelocity" not in u_acu2r

        # --- T field checks ---
        # ACU1 supply: temp ramp 15→25°C (288.15K → 298.15K)
        t_acu1 = t_text.split("ACU1_supply")[1].split("}")[0]
        assert "uniformFixedValue" in t_acu1
        assert "288.15" in t_acu1
        assert "298.15" in t_acu1

        # Fan-server: expression with time-dependent ΔT
        assert "S1_outlet" in t_text
        assert "tSink_S1_outlet" in t_text
        assert "time()" in t_text
        rho, cp = 1.19, 1005.0
        mass_flow = 0.5 * rho
        dt_150 = round(150 / (mass_flow * cp), 4)
        dt_2000 = round(2000 / (mass_flow * cp), 4)
        assert str(dt_150) in t_text
        assert str(dt_2000) in t_text

    def test_slab_scenario_server_shutdown_power_double_acu_off(self, tmp_path):
        """Slab-like scenario: R2 shutdown@0.5s, R5 power double@0.75s, PCU_2 off@2s."""
        rho, cp = 1.19, 1005.0
        bc_data = {
            # R2 fan-server: shutdown at t=0.5s (power=0, flow=0)
            "R2_outlet": {
                "bc_type": "fan_outlet", "flow_rate": 0.4485, "power": 3600,
                "timeVarying": {"power": [
                    {"t": 0, "value": 3600}, {"t": 0.5, "value": 3600}, {"t": 0.51, "value": 0},
                ]},
            },
            "R2_inlet": {"bc_type": "fan_inlet", "flow_rate": 0.4485},
            # R5 fan-server: power double at t=0.75s (1000→2000W)
            "R5_outlet": {
                "bc_type": "fan_outlet", "flow_rate": 0.065, "power": 1000,
                "timeVarying": {"power": [
                    {"t": 0, "value": 1000}, {"t": 0.75, "value": 1000}, {"t": 0.76, "value": 2000},
                ]},
            },
            "R5_inlet": {"bc_type": "fan_inlet", "flow_rate": 0.065},
            # PCU_2: shutdown at t=2s
            "PCU_2_supply": {
                "bc_type": "acu_supply", "flow_rate": 4, "temperature": 15,
                "return_patch_name": "PCU_2_return",
                "timeVarying": {"airflow": [
                    {"t": 0, "value": 4}, {"t": 2, "value": 4}, {"t": 2.01, "value": 0},
                ]},
            },
            "PCU_2_return": {"bc_type": "acu_return", "flow_rate": 4},
        }
        fg = _make_field_gen(tmp_path, bc_data)
        u_path = tmp_path / "0" / "U"
        t_path = tmp_path / "0" / "T"
        u_path.parent.mkdir(parents=True, exist_ok=True)
        all_patches = [(p, "wall") for p in bc_data]
        fg._write_u_field(u_path, all_patches)
        fg._write_t_field(t_path, all_patches)
        u_text = u_path.read_text()
        t_text = t_path.read_text()

        # --- U: PCU_2 supply shutdown at t=2s ---
        assert "PCU_2_supply" in u_text
        assert "(2.01 0)" in u_text
        # Return is a backflow-stable pressure outlet for PCU/ACU return patches.
        assert "PCU_2_return" in u_text
        u_ret = u_text.split("PCU_2_return")[1]
        assert "pressureInletOutletVelocity" in u_ret
        assert "flowRateOutletVelocity" not in u_ret

        # --- T: R2 shutdown → ΔT drops to 0 ---
        assert "R2_outlet" in t_text
        assert "time()" in t_text
        m2 = 0.4485 * rho
        dt_3600 = round(3600 / (m2 * cp), 4)
        assert str(dt_3600) in t_text

        # --- T: R5 power double → ΔT doubles ---
        assert "R5_outlet" in t_text
        m5 = 0.065 * rho
        dt_1000 = round(1000 / (m5 * cp), 4)
        dt_2000 = round(2000 / (m5 * cp), 4)
        assert str(dt_1000) in t_text
        assert str(dt_2000) in t_text


class TestServerBCModes:
    """Tests for 3 server BC modes: powerAndFlow, tempRiseAndFlow, powerAndTempRise.

    The relationship is Q_heat = rho * Cp * V_dot * DeltaT.
    Given any 2, the 3rd is derived.  All modes produce the same OpenFOAM BC
    (flowRateInletVelocity for U, damped temperature-rise expression for T
    whose fixed point is T_in + ΔT).
    """

    RHO = 1.19
    CP = 1005.0

    def test_server_bc_mode_power_and_flow_default(self, tmp_path):
        """Default mode (powerAndFlow): P=1000W, Q=0.5 → ΔT = P/(ρ·Cp·Q)."""
        P, Q = 1000.0, 0.5
        expected_dt = P / (Q * self.RHO * self.CP)

        bc_data = {
            "S1_outlet_u1": {
                "bc_type": "fan_outlet",
                "flow_rate": Q,
                "power": P,
            },
            "S1_inlet_u1": {
                "bc_type": "fan_inlet",
                "flow_rate": Q,
            },
        }
        fg = _make_field_gen(tmp_path, bc_data)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("S1_outlet_u1", "wall"), ("S1_inlet_u1", "wall")])
        text = t_path.read_text()

        _assert_fan_outlet_dt(text, expected_dt)
        assert "expression" in text

    def test_server_bc_mode_temp_rise_and_flow(self, tmp_path):
        """tempRiseAndFlow: ΔT=10, Q=0.5 → P = ρ·Cp·Q·ΔT = 603.6W.

        stlgen_runner computes P upstream; fields.py sees power+flow_rate.
        """
        DT, Q = 10.0, 0.5
        derived_P = self.RHO * self.CP * Q * DT  # 603.6 W

        bc_data = {
            "S1_outlet_u1": {
                "bc_type": "fan_outlet",
                "flow_rate": Q,
                "power": derived_P,
            },
            "S1_inlet_u1": {
                "bc_type": "fan_inlet",
                "flow_rate": Q,
            },
        }
        fg = _make_field_gen(tmp_path, bc_data)

        # T field: verify ΔT matches the original user-specified value
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("S1_outlet_u1", "wall"), ("S1_inlet_u1", "wall")])
        text = t_path.read_text()
        recomputed_dt = derived_P / (Q * self.RHO * self.CP)
        assert abs(recomputed_dt - DT) < 1e-6
        _assert_fan_outlet_dt(text, DT)

        # U field: flowRate must be the user-specified Q
        u_path = tmp_path / "0" / "U"
        fg._write_u_field(u_path, [("S1_outlet_u1", "wall"), ("S1_inlet_u1", "wall")])
        u_text = u_path.read_text()
        assert "flowRateInletVelocity" in u_text
        assert str(Q) in u_text

    def test_server_bc_mode_power_and_temp_rise(self, tmp_path):
        """powerAndTempRise: P=5000W, ΔT=8 → Q = P/(ρ·Cp·ΔT).

        stlgen_runner computes Q upstream; fields.py sees power+flow_rate.
        """
        P, DT = 5000.0, 8.0
        derived_Q = P / (self.RHO * self.CP * DT)

        bc_data = {
            "S1_outlet_u1": {
                "bc_type": "fan_outlet",
                "flow_rate": derived_Q,
                "power": P,
            },
            "S1_inlet_u1": {
                "bc_type": "fan_inlet",
                "flow_rate": derived_Q,
            },
        }
        fg = _make_field_gen(tmp_path, bc_data)

        # T field: the BC's fixed point must carry ΔT = 8.0
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("S1_outlet_u1", "wall"), ("S1_inlet_u1", "wall")])
        text = t_path.read_text()
        recomputed_dt = P / (derived_Q * self.RHO * self.CP)
        assert abs(recomputed_dt - DT) < 1e-6
        _assert_fan_outlet_dt(text, DT)

        # U field: flowRate must be the derived Q
        u_path = tmp_path / "0" / "U"
        fg._write_u_field(u_path, [("S1_outlet_u1", "wall"), ("S1_inlet_u1", "wall")])
        u_text = u_path.read_text()
        assert "flowRateInletVelocity" in u_text
        assert f"{derived_Q}" in u_text

    @pytest.mark.skip(
        reason="_build_bridge_bc deleted in P3 T6; server BC-mode dispatch now "
        "lives in cfdrun_export.js (translateRackServers) + cfdrun_reader. See "
        "src/frontend/test/test_cfdrun_export.mjs + test_cfdrun_v2_runner_path.py."
    )
    def test_runner_server_bc_mode_dispatch(self, tmp_path):
        """Verify _build_bridge_bc computes derived values for each mode."""
        from backend.aircooling.runner.stlgen_runner import STLGenRunner

        RHO, CP = 1.19, 1005.0
        rack_name = "Rack_1"

        def _make_cfdrun(mode, power=None, flow=None, temp_rise=None):
            phys = {}
            if power is not None:
                phys["power"] = power
            if flow is not None:
                phys["volumeFlowRate"] = flow
            if temp_rise is not None:
                phys["temperatureRise"] = temp_rise
            if mode:
                phys["serverBCMode"] = mode
            return {
                "projectName": "test",
                "room": {"height": 3.0, "plane": [{"x": 0, "y": 0, "z": 0}],
                         "locationInMesh": {"x": 1, "y": 1, "z": 1}},
                "racks": {rack_name: {
                    "geometry": {"size": {"W": 0.6}, "slotWidthRatio": 0.8,
                                 "unitHeight": 0.04445},
                    "servers": [{
                        "id": "srv1", "type": "fanServer", "spanSlots": 2,
                        "physics": phys,
                        "faces": {
                            "inlet": {"patches": [{"solid": "srv1_inlet"}]},
                            "outlet": {"patches": [{"solid": "srv1_outlet"}]},
                        },
                    }],
                }},
                "meshConfig": {"baseSize": 0.1},
                "solverConfig": {},
            }

        def _run_and_get_outlet(cfdrun):
            runner = STLGenRunner.__new__(STLGenRunner)
            runner._data = cfdrun
            runner._case_dir = tmp_path
            runner.model_dir = tmp_path / "model"
            runner.model_dir.mkdir(parents=True, exist_ok=True)
            runner._bridge_registry = None
            runner._stl_solids = {}
            runner._build_bridge_bc()
            return runner._bridge_registry.get("srv1_outlet")

        # Mode 1: powerAndFlow (default) — P=1000, Q=0.5
        bc1 = _run_and_get_outlet(
            _make_cfdrun("powerAndFlow", power=1000, flow=0.5))
        assert bc1 is not None
        assert abs(bc1.power - 1000) < 1e-6
        assert abs(bc1.flow_rate - 0.5) < 1e-6

        # Mode 2: tempRiseAndFlow — DT=10, Q=0.5 → P = 1.19*1005*0.5*10 = 5979.75
        bc2 = _run_and_get_outlet(
            _make_cfdrun("tempRiseAndFlow", flow=0.5, temp_rise=10.0))
        expected_P = RHO * CP * 0.5 * 10.0
        assert abs(bc2.power - expected_P) < 1e-3
        assert abs(bc2.flow_rate - 0.5) < 1e-6

        # Mode 3: powerAndTempRise — P=5000, DT=8 → Q = 5000/(1.19*1005*8)
        bc3 = _run_and_get_outlet(
            _make_cfdrun("powerAndTempRise", power=5000, temp_rise=8.0))
        expected_Q = 5000 / (RHO * CP * 8.0)
        assert abs(bc3.power - 5000) < 1e-6
        assert abs(bc3.flow_rate - expected_Q) < 1e-6


@pytest.mark.parametrize("scheduled", [False, True])
def test_volume_flow_initialization_evaluates_real_mesh_area(tmp_path, scheduled):
    # A supplied zero `value` suppresses Function1/mesh-area evaluation in the
    # OpenFOAM constructor, hiding all boundary flux from initial CourantNo.
    bc = {
        "ACU_1_supply": {"bc_type": "acu_supply", "flow_rate": 1.4},
        "S1_inlet": {"bc_type": "fan_inlet", "flow_rate": 0.5},
        "S1_outlet": {"bc_type": "fan_outlet", "flow_rate": 0.5},
    }
    if scheduled:
        for data in bc.values():
            data["timeVarying"] = {"airflow": [{"t": 0, "value": data["flow_rate"]},
                                               {"t": 2, "value": data["flow_rate"] * 0.5}]}
    fg = _make_field_gen(tmp_path, bc)
    target = tmp_path / "0" / "U"
    target.parent.mkdir()
    fg._write_u_field(target, [(name, "wall") for name in bc])
    text = target.read_text()
    for name in bc:
        block = text.split("    " + name + "\n", 1)[1].split("    }", 1)[0]
        assert "flowRate" in block and "volumetricFlowRate" in block
        assert not re.search(r"\bvalue\s+uniform", block), name
        assert ("table" in block) == scheduled
