"""Regression: CFDExportMeshConfig must preserve maxGlobalCells/maxLocalCells through
pydantic validation round-trip.

Originally these fields flowed through only because CFDBaseModel sets
extra="allow" — so a future tightening of the extras policy (e.g. to catch
stale fields) would silently drop the user's UI value. This class of bug
previously burned us on `perforated` (declared on the parent but not on the
leaf class) so we declare the cell-limit fields explicitly in CFDExportMeshConfig.
"""
import pytest

from backend.aircooling.runner.cfdrun_schema import CFDExportMeshConfig


def test_rack_region_level_default():
    mc = CFDExportMeshConfig()
    assert mc.rackRegionLevel == 3


def test_rack_region_level_roundtrip():
    mc = CFDExportMeshConfig(rackRegionLevel=5)
    d = mc.model_dump(mode="json")
    assert d["rackRegionLevel"] == 5
    mc2 = CFDExportMeshConfig(**d)
    assert mc2.rackRegionLevel == 5


def test_old_rack_fields_ignored():
    """Old cfdproj files with removed rack fields should parse without error."""
    mc = CFDExportMeshConfig(
        rackRefinementLevel=[2, 3],
        serverFaceRefinementLevel=[2, 3],
        baffleRefinementLevel=[1, 3],
        rackDoorRefinementLevel=[1, 1],
        serverFacePerUnitLevel={"2": [3, 4]},
    )
    # Old fields are preserved as extras (extra="allow")
    assert mc.rackRegionLevel == 3  # default applied


def test_max_global_cells_survives_roundtrip():
    raw = {"baseSize": 0.2, "maxGlobalCells": 800000}
    dumped = CFDExportMeshConfig.model_validate(raw).model_dump(mode="json")
    assert dumped["maxGlobalCells"] == 800000


def test_max_local_cells_survives_roundtrip():
    raw = {"baseSize": 0.2, "maxLocalCells": 600000}
    dumped = CFDExportMeshConfig.model_validate(raw).model_dump(mode="json")
    assert dumped["maxLocalCells"] == 600000


def test_cell_limits_have_sensible_defaults():
    dumped = CFDExportMeshConfig().model_dump(mode="json")
    assert dumped["maxGlobalCells"] == 5000000
    assert dumped["maxLocalCells"] == 5000000


def test_cell_limits_are_ints_not_strings():
    """Frontend sends these as JSON numbers; pydantic must keep them numeric."""
    raw = {"baseSize": 0.2, "maxGlobalCells": 300000, "maxLocalCells": 250000}
    validated = CFDExportMeshConfig.model_validate(raw)
    assert isinstance(validated.maxGlobalCells, int)
    assert isinstance(validated.maxLocalCells, int)
    assert validated.maxGlobalCells == 300000
    assert validated.maxLocalCells == 250000
