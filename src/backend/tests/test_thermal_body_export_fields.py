"""Actual React STL exports must retain their thermal BC through field generation."""
import json
import math
import re
from pathlib import Path

import pytest

from backend.aircooling.runner.cfdrun_reader import resolve_run
from backend.aircooling.runner.fields import FieldGenerator

FIXTURES = Path(__file__).parent / "fixtures" / "thermal_body_export"


def _areas(folder):
    result = {}
    for path in folder.glob("*.stl"):
        for name, content in re.findall(r"^solid\s+(\S+)\s*\n([\s\S]*?)^endsolid\b", path.read_text(), re.M):
            vertices = [tuple(map(float, xyz)) for xyz in re.findall(
                r"vertex\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", content)]
            assert len(vertices) and len(vertices) % 3 == 0
            area = 0
            for i in range(0, len(vertices), 3):
                a, b, c = vertices[i:i+3]
                u = [b[j] - a[j] for j in range(3)]
                v = [c[j] - a[j] for j in range(3)]
                cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
                area += math.sqrt(sum(n*n for n in cross))/2
            result[name] = area
    return result


@pytest.mark.parametrize("mode", ["temperature", "power", "scaled"])
def test_actual_export_reader_to_generated_thermal_fields(tmp_path, mode):
    folder = FIXTURES / mode
    raw = json.loads((folder / "thermal.cfdrun").read_text())
    resolved = resolve_run(tmp_path, cfdrun_path=folder / "thermal.cfdrun",
                           tri_surface_dir=folder, resolve_stl=True)
    area = _areas(folder)
    assert area, "the oracle must be a real rendered STL, not predicted names"
    expected = {b["patchName"]: b for b in raw["boundaryBindings"].values()
                if b["bcType"] in ("fixedTemperature", "heat_source_flux")}
    assert set(expected) <= area.keys()
    generator = FieldGenerator(case_dir=tmp_path, simulation_config=resolved.simulation_config,
                               room_config={}, patch_geometry={}, bridge_registry=resolved.bridge_registry)
    generator.generate_fields(tmp_path, [(name, "wall") for name in area])
    temperature = (tmp_path / "0" / "T").read_text()
    velocity = (tmp_path / "0" / "U").read_text()
    total_watts = 0
    for name, binding in expected.items():
        t_block = re.search(r"\b" + re.escape(name) + r"\s*\{([^}]+)\}", temperature)
        u_block = re.search(r"\b" + re.escape(name) + r"\s*\{([^}]+)\}", velocity)
        assert t_block and u_block, name
        assert "noSlip" in u_block[1]
        if mode == "temperature":
            assert "fixedValue" in t_block[1]
            match = re.search(r"value\s+uniform\s+([\d.]+)", t_block[1])
            assert float(match[1]) == pytest.approx(305.15)
        else:
            assert "externalWallHeatFluxTemperature" in t_block[1]
            match = re.search(r"q\s+uniform\s+([\d.eE+-]+)", t_block[1])
            total_watts += float(match[1]) * area[name]
    if mode == "temperature":
        assert len(expected) == 6
    else:
        assert total_watts == pytest.approx(120, rel=1e-8)
        if mode == "power":
            assert not any("Back" in name for name in expected)
            assert sum("Front" in name for name in expected) == 2
