"""Gravity field default direction after Y-up refactor (2026-04-18)."""
from pathlib import Path

from backend.aircooling.runner.openfoam_case import _write_gravity_file


def test_default_gravity_is_y_down(tmp_path: Path):
    """When no gravity dict is supplied, _write_gravity_file must default
    to Y-down: value (0 -9.81 0). Post-refactor, Y is the vertical axis."""
    _write_gravity_file(tmp_path, gravity={})
    g_text = (tmp_path / "g").read_text()
    assert "value           (0 -9.81 0);" in g_text, (
        f"Expected gravity along -Y; got:\n{g_text}"
    )


def test_gravity_dict_overrides_default(tmp_path: Path):
    """Explicit gravity dict wins over default."""
    _write_gravity_file(tmp_path, gravity={"x": 0, "y": 0, "z": -9.81})
    g_text = (tmp_path / "g").read_text()
    assert "value           (0 0 -9.81);" in g_text
