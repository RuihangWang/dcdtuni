"""Derived diffusivities must accept turbulence/thermal model boundary updates."""
import re
import pytest
from backend.aircooling.runner.fields import FieldGenerator


def _block(text, name):
    match = re.search(re.escape(name) + r"\s*\{(.*?)\}", text, re.S)
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize("field", ["nut", "alphat"])
@pytest.mark.parametrize("role", ["acu_supply", "acu_return", "fan_outlet", "fan_inlet"])
def test_flow_diffusivity_is_calculated_with_initial_value_preserved(tmp_path, field, role):
    generator = FieldGenerator(case_dir=tmp_path)
    # Registry semantics must take priority even for a mesh patch named "wall".
    generator._imported_bc_data = {"wall_named_flow": {"bc_type": role}}
    path = tmp_path / field
    generator._write_simple_scalar_field(
        path, [("wall_named_flow", "wall")], "0.09",
        "volScalarField", "[0 2 -1 0 0 0 0]",
    )
    text = path.read_text()
    block = _block(text, "wall_named_flow")
    assert re.search(r"type\s+calculated;", block), block
    assert re.search(r"value\s+uniform\s+0.09;", block)
    assert re.search(r"internalField\s+uniform\s+0.09;", text)


@pytest.mark.parametrize("field,wall_bc", [
    ("nut", "nutkWallFunction"),
    ("alphat", "alphatJayatillekeWallFunction"),
])
def test_diffusivity_wall_and_coupled_contracts_are_preserved(tmp_path, field, wall_bc):
    generator = FieldGenerator(case_dir=tmp_path)
    patches = [("wall", "wall"), ("cyc", "cyclic"), ("ami", "cyclicAMI"),
               ("rank", "processor"), ("empty_faces", "empty")]
    # Coupling also takes priority over a flow registry entry.
    generator._imported_bc_data = {name: {"bc_type": "acu_supply"} for name, _ in patches[1:]}
    path = tmp_path / field
    generator._write_simple_scalar_field(
        path, patches, "0.09", "volScalarField", "[0 2 -1 0 0 0 0]",
    )
    text = path.read_text()
    assert re.search(r"type\s+" + wall_bc + ";", _block(text, "wall"))
    for name, patch_type in patches[1:]:
        assert re.search(r"type\s+" + patch_type + ";", _block(text, name))


def test_unrelated_scalar_flow_bc_is_unchanged(tmp_path):
    generator = FieldGenerator(case_dir=tmp_path)
    generator._imported_bc_data = {"inlet": {"bc_type": "acu_supply"}}
    path = tmp_path / "omega"
    generator._write_simple_scalar_field(
        path, [("inlet", "patch")], "1", "volScalarField", "[0 0 -1 0 0 0 0]",
    )
    assert re.search(r"type\s+fixedValue;", _block(path.read_text(), "inlet"))
