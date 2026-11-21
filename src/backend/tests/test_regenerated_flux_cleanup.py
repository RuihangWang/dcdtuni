"""A regenerated initial U must not inherit a pressure flux from an older solve."""
from pathlib import Path

import pytest

from backend.aircooling.runner.fields import FieldGenerator


@pytest.mark.parametrize("relative", [".", "processor0"])
def test_regenerated_zero_discards_only_its_old_flux(tmp_path, relative):
    case = tmp_path / relative
    zero = case / "0"
    zero.mkdir(parents=True)
    for name in ("phi", "phi.gz", "keepUserField"):
        (zero / name).write_text("old")
    history = case / "1" / "phi"
    history.parent.mkdir()
    history.write_text("historical pressure-corrected flux")
    generator = FieldGenerator(case_dir=case, simulation_config={}, room_config={},
                               patch_geometry={}, bridge_registry=None)

    generator.generate_fields(case, [("wall", "wall")])

    assert not (zero / "phi").exists()
    assert not (zero / "phi.gz").exists()
    assert "internalField" in (zero / "U").read_text()
    assert (zero / "keepUserField").read_text() == "old"
    assert history.read_text() == "historical pressure-corrected flux"
