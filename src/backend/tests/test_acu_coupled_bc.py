"""Tests for ACU dynamic coupled boundary condition (Set Point control).

Parameter mapping (6Sigma → dcdtuni):
  Set Point               → setPointTemperature (°C)
  Maximum Sensible Cooling → coolingCapacity (kW in schema, W in BCSpec)
  Minimum Supply Temp      → derived from coolantTemperature + 6°C approach
  Maximum Flow Rate        → volumeFlowRate (m³/s)
  Coolant Temperature      → coolantTemperature (°C)
"""
from backend.aircooling.runner.bridge_bc import BCSpec, BridgeBCRegistry
from backend.aircooling.runner.cfdrun_schema import ACUFacePhysics
from backend.aircooling.runner.fields import FieldGenerator

APPROACH_TEMP = 6.0  # °C, heat exchanger approach temperature

_TRANSIENT_CONFIG = {"solverType": "transient"}


class TestBCSpecACUFields:
    def test_bcspec_accepts_all_acu_fields(self):
        spec = BCSpec(
            bc_type="acu_supply",
            temperature=22.0,
            flow_rate=2.69,
            cooling_capacity=35000.0,
            set_point_temp=22.0,
            min_supply_temp=20.3,
            coolant_temp=14.0,
            return_patch_name="ACU_A1_Front_patch",
        )
        assert spec.cooling_capacity == 35000.0
        assert spec.set_point_temp == 22.0
        assert spec.min_supply_temp == 20.3
        assert spec.coolant_temp == 14.0
        assert spec.return_patch_name == "ACU_A1_Front_patch"

    def test_bcspec_acu_fields_default_none(self):
        spec = BCSpec(bc_type="acu_supply", temperature=15.0, flow_rate=4.0)
        assert spec.cooling_capacity is None
        assert spec.coolant_temp is None
        assert spec.return_patch_name is None


class TestACUFacePhysicsSchema:
    def test_accepts_cooling_fields_with_coolant(self):
        phys = ACUFacePhysics(
            temperature=22.0,
            volumeFlowRate=2.69,
            coolingCapacity=35.0,
            coolantTemperature=14.0,
            setPointTemperature=22.0,
        )
        assert phys.coolingCapacity == 35.0
        assert phys.coolantTemperature == 14.0
        assert phys.setPointTemperature == 22.0

    def test_cooling_fields_default_none(self):
        phys = ACUFacePhysics(temperature=15.0, volumeFlowRate=4.0)
        assert phys.coolingCapacity is None
        assert phys.coolantTemperature is None
        assert phys.setPointTemperature is None

    def test_min_supply_temp_removed(self):
        """minSupplyTemperature should no longer be a schema field;
        it is derived at runtime from coolantTemperature + approach."""
        phys = ACUFacePhysics(temperature=22.0, volumeFlowRate=2.69)
        assert not hasattr(phys, "minSupplyTemperature") or phys.minSupplyTemperature is None


class TestMinSupplyTempDerivation:
    """min_supply_temp in BCSpec should be auto-derived from coolant_temp + 6°C
    when not explicitly set."""

    def test_derived_from_coolant_temp(self):
        """When coolant_temp=14, min_supply_temp should be 14+6=20."""
        spec = BCSpec(
            bc_type="acu_supply",
            cooling_capacity=35000.0,
            coolant_temp=14.0,
            return_patch_name="ret",
        )
        # The derivation happens in _build_bridge_bc / fields.py, not in BCSpec itself.
        # BCSpec stores raw values; derivation is tested in the field generation tests below.
        assert spec.coolant_temp == 14.0


class TestBridgeBCACUCoupled:
    def test_supply_gets_return_patch_and_coolant(self):
        registry = BridgeBCRegistry()
        registry.register("ACU_1_Back_patch", BCSpec(
            bc_type="acu_supply",
            temperature=22.0,
            flow_rate=2.69,
            cooling_capacity=35000.0,
            set_point_temp=22.0,
            coolant_temp=14.0,
            return_patch_name="ACU_1_Front_patch",
        ))
        spec = registry.get("ACU_1_Back_patch")
        assert spec.cooling_capacity == 35000.0
        assert spec.coolant_temp == 14.0
        assert spec.return_patch_name == "ACU_1_Front_patch"

    def test_supply_without_cooling_stays_none(self):
        registry = BridgeBCRegistry()
        registry.register("ACU_1_Back_patch", BCSpec(
            bc_type="acu_supply", temperature=15.0, flow_rate=4.0,
        ))
        spec = registry.get("ACU_1_Back_patch")
        assert spec.cooling_capacity is None
        assert spec.return_patch_name is None


def _make_field_generator(tmp_path, bc_data):
    fg = FieldGenerator.__new__(FieldGenerator)
    fg.case_dir = tmp_path
    fg._simulation_config = None
    fg._room_config = None
    fg._patch_geometry = {}
    fg.bridge_registry = None
    fg._imported_bc_data = bc_data
    fg._acu_data = {}
    fg._air_leak_data = {}
    fg._data_collected = True
    fg._room_temperature_c = 24.0
    fg._static_outlet_temperature = False
    return fg


class TestACUExprFixedValueBC:
    def test_acu_supply_with_cooling_generates_expr(self, tmp_path):
        bc_data = {
            "ACU_1_Back_patch": {
                "bc_type": "acu_supply",
                "temperature": 22.0,
                "flow_rate": 2.69,
                "cooling_capacity": 35000.0,
                "set_point_temp": 22.0,
                "coolant_temp": 14.0,
                "return_patch_name": "ACU_1_Front_patch",
            },
        }
        fg = _make_field_generator(tmp_path, bc_data)
        fg._simulation_config = _TRANSIENT_CONFIG
        patches = [("ACU_1_Back_patch", "patch")]
        t_path = tmp_path / "0" / "T"
        (tmp_path / "0").mkdir()
        fg._write_t_field(t_path, patches)
        content = t_path.read_text()
        assert "uniformFixedValue" in content
        assert "type            expression" in content
        assert "weightAverage(T)" in content
        assert "ACU_1_Front_patch" in content
        assert "Qmax" in content
        assert "Tsetpoint" in content
        assert "35000.0" in content

    def test_tmin_derived_from_coolant_temp(self, tmp_path):
        """Tmin in the expression should be coolantTemp + 6°C approach = 20°C = 293.15K."""
        bc_data = {
            "ACU_1_Back_patch": {
                "bc_type": "acu_supply",
                "temperature": 22.0,
                "flow_rate": 2.69,
                "cooling_capacity": 35000.0,
                "set_point_temp": 22.0,
                "coolant_temp": 14.0,
                "return_patch_name": "ACU_1_Front_patch",
            },
        }
        fg = _make_field_generator(tmp_path, bc_data)
        fg._simulation_config = _TRANSIENT_CONFIG
        patches = [("ACU_1_Back_patch", "patch")]
        t_path = tmp_path / "0" / "T"
        (tmp_path / "0").mkdir()
        fg._write_t_field(t_path, patches)
        content = t_path.read_text()
        # coolant=14 + approach=6 = 20°C = 293.15K
        assert "293.15" in content

    def test_tmin_fallback_when_no_coolant(self, tmp_path):
        """Without coolant_temp, Tmin should fall back to setPoint - 5°C."""
        bc_data = {
            "ACU_1_Back_patch": {
                "bc_type": "acu_supply",
                "temperature": 22.0,
                "flow_rate": 2.69,
                "cooling_capacity": 35000.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_Front_patch",
            },
        }
        fg = _make_field_generator(tmp_path, bc_data)
        fg._simulation_config = _TRANSIENT_CONFIG
        patches = [("ACU_1_Back_patch", "patch")]
        t_path = tmp_path / "0" / "T"
        (tmp_path / "0").mkdir()
        fg._write_t_field(t_path, patches)
        content = t_path.read_text()
        # setPoint=22 - 5 = 17°C = 290.15K
        assert "290.15" in content

    def test_acu_supply_without_cooling_stays_fixed_value(self, tmp_path):
        bc_data = {
            "ACU_1_Back_patch": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
            },
        }
        fg = _make_field_generator(tmp_path, bc_data)
        patches = [("ACU_1_Back_patch", "patch")]
        t_path = tmp_path / "0" / "T"
        (tmp_path / "0").mkdir()
        fg._write_t_field(t_path, patches)
        content = t_path.read_text()
        assert "fixedValue" in content
        assert "uniformFixedValue" not in content

    def test_steady_solver_uses_fixed_value_with_setpoint(self, tmp_path):
        """Steady-state solver should use fixedValue at set_point_temp, not expression BC."""
        bc_data = {
            "ACU_1_Back_patch": {
                "bc_type": "acu_supply",
                "temperature": 22.0,
                "flow_rate": 2.69,
                "cooling_capacity": 35000.0,
                "set_point_temp": 20.0,
                "coolant_temp": 14.0,
                "return_patch_name": "ACU_1_Front_patch",
            },
        }
        fg = _make_field_generator(tmp_path, bc_data)
        fg._simulation_config = {"solverType": "steady"}
        patches = [("ACU_1_Back_patch", "patch")]
        t_path = tmp_path / "0" / "T"
        (tmp_path / "0").mkdir()
        fg._write_t_field(t_path, patches)
        content = t_path.read_text()
        assert "fixedValue" in content
        assert "uniformFixedValue" not in content
        assert "293.15" in content  # 20 + 273.15

    def test_setpoint_defaults_to_temperature(self, tmp_path):
        bc_data = {
            "ACU_1_Back_patch": {
                "bc_type": "acu_supply",
                "temperature": 20.0,
                "flow_rate": 2.69,
                "cooling_capacity": 35000.0,
                "coolant_temp": 14.0,
                "return_patch_name": "ACU_1_Front_patch",
            },
        }
        fg = _make_field_generator(tmp_path, bc_data)
        patches = [("ACU_1_Back_patch", "patch")]
        t_path = tmp_path / "0" / "T"
        (tmp_path / "0").mkdir()
        fg._write_t_field(t_path, patches)
        content = t_path.read_text()
        assert "293.15" in content  # 20 + 273.15 as Tsetpoint
