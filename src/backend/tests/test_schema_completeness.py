"""Tests for Pydantic schema completeness.

Every field the frontend sends and the runner reads must be declared
in the schema so Pydantic validates types and provides defaults.
"""
import pytest

from backend.aircooling.runner.cfdrun_schema import CFDExportSimulationConfig, ResidualControl


FULL_FRONTEND_SIMULATION_CONFIG = {
    "endTime": 1000,
    "writeInterval": 1000,
    "doublePrecisionWrite": True,
    "writePrecision": "double",
    "endTimeSeconds": 300.0,
    "deltaT": 0.1,
    "maxDeltaT": 1.0,
    "maxCo": 1.0,
    "adjustTimeStep": True,
    "writeIntervalSeconds": 10.0,
    "nOuterCorrectors": 1,
    "nCorrectors": 2,
    "ddtScheme": "Euler",
    "crankNicolsonCoeff": 0.9,
    "solverType": "steady",
    "roomTemperature": 22,
    "parallelSolve": True,
    "solveProcess": 8,
    "gravity": {"x": 0, "y": -9.81, "z": 0},
    "monitoring": {
        "residuals": True,
        "serverTemperature": [{"patch": "R1_outlet", "shortId": "R1", "type": "outlet"}],
    },
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
    "solverPreset": "balanced",
}


class TestSimulationConfigSchema:
    def test_parses_full_frontend_config(self):
        """CFDExportSimulationConfig must accept ALL fields the frontend sends."""
        sc = CFDExportSimulationConfig(**FULL_FRONTEND_SIMULATION_CONFIG)
        assert sc.endTime == 1000
        assert sc.parallelSolve is True
        assert sc.solveProcess == 8

    def test_gravity_field_exists(self):
        sc = CFDExportSimulationConfig(**FULL_FRONTEND_SIMULATION_CONFIG)
        assert sc.gravity == {"x": 0, "y": -9.81, "z": 0}

    def test_schemes_field_exists(self):
        sc = CFDExportSimulationConfig(**FULL_FRONTEND_SIMULATION_CONFIG)
        assert sc.schemes["divSchemeU"] == "bounded Gauss linearUpwind grad(U)"

    def test_relaxation_factors_field_exists(self):
        sc = CFDExportSimulationConfig(**FULL_FRONTEND_SIMULATION_CONFIG)
        assert sc.relaxationFactors["p_rgh"] == 0.3

    def test_non_ortho_correctors_field_exists(self):
        sc = CFDExportSimulationConfig(**FULL_FRONTEND_SIMULATION_CONFIG)
        assert sc.nNonOrthogonalCorrectors == 1

    def test_monitoring_field_exists(self):
        sc = CFDExportSimulationConfig(**FULL_FRONTEND_SIMULATION_CONFIG)
        assert sc.monitoring["residuals"] is True

    def test_non_ortho_correction_field_exists(self):
        sc = CFDExportSimulationConfig(**FULL_FRONTEND_SIMULATION_CONFIG)
        assert sc.nonOrthoCorrection is True

    def test_solver_preset_field_exists(self):
        sc = CFDExportSimulationConfig(**FULL_FRONTEND_SIMULATION_CONFIG)
        assert sc.solverPreset == "balanced"

    def test_defaults_when_empty(self):
        """Default CFDExportSimulationConfig should have sane defaults for all fields."""
        sc = CFDExportSimulationConfig()
        assert sc.endTime == 500
        assert sc.writeInterval == 100
        assert sc.doublePrecisionWrite is False
        assert sc.writePrecision == "single"
        assert sc.endTimeSeconds == 300.0
        assert sc.deltaT == 0.1
        assert sc.maxDeltaT == 1.0
        assert sc.maxCo == 1.0
        assert sc.adjustTimeStep is True
        assert sc.writeIntervalSeconds == 10.0
        assert sc.nOuterCorrectors == 1
        assert sc.nCorrectors == 2
        assert sc.ddtScheme == "Euler"
        assert sc.crankNicolsonCoeff == 0.9
        assert sc.parallelSolve is False
        assert sc.solveProcess == 1
        assert sc.gravity == {"x": 0, "y": -9.81, "z": 0}
        assert sc.nNonOrthogonalCorrectors == 0
        assert sc.nonOrthoCorrection is False
        assert sc.solverPreset == "fast"
        assert sc.monitoring == {}
        assert sc.schemes == {}
        assert sc.relaxationFactors == {}


    def test_physical_properties_field_exists(self):
        """Runner reads physical_properties for thermophysicalProperties generation."""
        sc = CFDExportSimulationConfig()
        assert hasattr(sc, "physical_properties")
        assert sc.physical_properties == {}

    def test_unit_temperature_field_exists(self):
        """Runner reads unitTemperature for sensor harvest."""
        sc = CFDExportSimulationConfig()
        assert hasattr(sc, "unitTemperature")
        assert sc.unitTemperature == "C"

    def test_physical_properties_roundtrip(self):
        sc = CFDExportSimulationConfig(physical_properties={"cp": 1010, "mu": "1.9e-05"})
        assert sc.physical_properties["cp"] == 1010


class TestTimeVaryingSchema:
    def test_time_varying_entry_roundtrip(self):
        from backend.aircooling.runner.cfdrun_schema import TimeVaryingEntry
        e = TimeVaryingEntry(t=60.0, value=1.4)
        assert e.t == 60.0
        assert e.value == 1.4

    def test_time_varying_entry_from_dict(self):
        from backend.aircooling.runner.cfdrun_schema import TimeVaryingEntry
        e = TimeVaryingEntry(**{"t": 0, "value": 5000})
        assert e.t == 0.0
        assert e.value == 5000.0

    def test_acu_face_physics_has_time_varying(self):
        from backend.aircooling.runner.cfdrun_schema import ACUFacePhysics
        p = ACUFacePhysics(volumeFlowRate=1.4)
        assert hasattr(p, "timeVarying")
        assert p.timeVarying == {}

    def test_acu_face_physics_time_varying_roundtrip(self):
        from backend.aircooling.runner.cfdrun_schema import ACUFacePhysics
        p = ACUFacePhysics(
            volumeFlowRate=1.4,
            timeVarying={
                "airflow": [{"t": 0, "value": 1.4}, {"t": 60, "value": 0}],
                "supplyTemp": [{"t": 0, "value": 15}, {"t": 60, "value": 25}]
            }
        )
        assert len(p.timeVarying["airflow"]) == 2
        assert p.timeVarying["airflow"][0]["value"] == 1.4

    def test_heat_source_has_time_varying(self):
        from backend.aircooling.runner.cfdrun_schema import CFDExportHeatSource
        hs = CFDExportHeatSource(totalPower=5000, stlFile="dummy.stl")
        assert hasattr(hs, "timeVarying")
        assert hs.timeVarying == {}

    def test_heat_source_time_varying_roundtrip(self):
        from backend.aircooling.runner.cfdrun_schema import CFDExportHeatSource
        hs = CFDExportHeatSource(
            totalPower=5000,
            stlFile="dummy.stl",
            timeVarying={"heatOutput": [{"t": 0, "value": 5000}, {"t": 120, "value": 10000}]}
        )
        assert len(hs.timeVarying["heatOutput"]) == 2


class TestResidualControlSchema:
    def test_defaults_match_frontend(self):
        # owner default 2026-10-27: 1e-3 for every field (cfdproj_state_v4.js)
        rc = ResidualControl()
        assert float(rc.p_rgh) == 1e-3
        assert float(rc.U) == 1e-3
        assert float(rc.T) == 1e-3
        assert float(rc.k_epsilon) == 1e-3


class TestRackServerSchema:
    """RackServer absolute depth offsets (stacked-server WP4, 2026-09-17).

    The V2 export emits `inletOffsetM` / `outletOffsetM` on both the fanServer
    and nonFan branches of the meshGeometry server objects (the old
    extraInletOffset/extraOutletOffset keys are gone). Legacy manifests lack
    the new keys entirely, so they must stay optional.
    """

    def test_fan_server_with_absolute_offsets(self):
        from backend.aircooling.runner.cfdrun_schema import RackServer
        srv = RackServer(
            id="srv1",
            type="fanServer",
            startSlot=16,
            spanSlots=5,
            inletSolidName="R1_fan_inlet_patch",
            outletSolidName="R1_fan_outlet_patch",
            inletOffsetM=0.12,
            outletOffsetM=0.08,
        )
        assert srv.inletOffsetM == pytest.approx(0.12)
        assert srv.outletOffsetM == pytest.approx(0.08)

    def test_nonfan_server_with_absolute_offsets(self):
        from backend.aircooling.runner.cfdrun_schema import RackServer
        srv = RackServer(
            id="sw1",
            type="nonFan",
            startSlot=16,
            spanSlots=1,
            inletOffsetM=0.0,
            outletOffsetM=0.3,
        )
        assert srv.inletOffsetM == pytest.approx(0.0)
        assert srv.outletOffsetM == pytest.approx(0.3)

    def test_offsets_optional_default_none(self):
        """Legacy manifests without the keys must still validate."""
        from backend.aircooling.runner.cfdrun_schema import RackServer
        srv = RackServer(id="srv1")
        assert srv.inletOffsetM is None
        assert srv.outletOffsetM is None

    def test_offsets_explicit_none(self):
        """The frontend may emit null for a server without declared depths."""
        from backend.aircooling.runner.cfdrun_schema import RackServer
        srv = RackServer(id="srv1", inletOffsetM=None, outletOffsetM=None)
        assert srv.inletOffsetM is None
        assert srv.outletOffsetM is None

    def test_offsets_invalid_type_raises(self):
        """Declared (not extra="allow") fields must reject non-numeric input."""
        from pydantic import ValidationError
        from backend.aircooling.runner.cfdrun_schema import RackServer
        with pytest.raises(ValidationError):
            RackServer(id="srv1", inletOffsetM="not-a-number")

    def test_rack_with_servers_validates_nested(self):
        """The runner path: servers arrive nested in a CFDExportRack dict."""
        from backend.aircooling.runner.cfdrun_schema import CFDExportRack
        rack = CFDExportRack.model_validate({
            "stlFile": "merged_server_5U.stl",
            "servers": [
                {
                    "id": "srv1",
                    "type": "fanServer",
                    "startSlot": 16,
                    "spanSlots": 5,
                    "inletOffsetM": 0.12,
                    "outletOffsetM": 0.08,
                },
                # stacked sibling in the same U range, nonFan branch
                {
                    "id": "sw1",
                    "type": "nonFan",
                    "startSlot": 16,
                    "spanSlots": 1,
                    "inletOffsetM": 0.0,
                    "outletOffsetM": 0.3,
                },
                # legacy server object without the new keys
                {"id": "srv_old", "type": "fanServer"},
            ],
        })
        assert len(rack.servers) == 3
        assert rack.servers[0].inletOffsetM == pytest.approx(0.12)
        assert rack.servers[1].outletOffsetM == pytest.approx(0.3)
        assert rack.servers[2].inletOffsetM is None
        assert rack.servers[2].outletOffsetM is None
