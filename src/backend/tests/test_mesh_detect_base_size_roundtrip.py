"""CFDExportMeshConfig.detectBaseSize — schema round-trip (2026-04-19).

Per spec 2026-04-19-mesh-size-quality-universal-design.md §Change 3:
- Defaults to None (backward compatible — existing cfdproj/cfdrun lack
  this field, auto-detect falls back to baseSize).
- When None, JSON dump must omit the field entirely (keeps existing
  files diff-clean on re-save).
- When set, round-trips as a plain float.

The runner itself does not consume this value (auto-detect is
client-side) but schema validation must accept + preserve it.
"""
import pytest

from backend.aircooling.runner.cfdrun_schema import CFDExportMeshConfig


def test_detect_base_size_defaults_to_none():
    """Brand-new CFDExportMeshConfig has detectBaseSize == None."""
    mc = CFDExportMeshConfig()
    assert mc.detectBaseSize is None


def test_detect_base_size_none_omitted_from_dump():
    """None → JSON dump omits the key (use exclude_none=True)."""
    mc = CFDExportMeshConfig(baseSize=0.35)
    dumped = mc.model_dump(mode="json", exclude_none=True)
    assert "detectBaseSize" not in dumped
    # Sanity — the explicit field we set IS present
    assert dumped["baseSize"] == 0.35


def test_detect_base_size_set_roundtrips():
    """Non-None survives validate → dump cycle as a float, including the
    canonical exclude_none=True dump mode that Task 6 persistence relies on."""
    raw = {"baseSize": 0.35, "detectBaseSize": 0.178}
    mc = CFDExportMeshConfig.model_validate(raw)
    assert mc.detectBaseSize == pytest.approx(0.178)
    dumped = mc.model_dump(mode="json")
    assert dumped["detectBaseSize"] == pytest.approx(0.178)
    # Non-None must survive exclude_none=True (the frontend's persistence
    # mode — omit-when-null, preserve-when-set is the whole contract).
    dumped_excl = mc.model_dump(mode="json", exclude_none=True)
    assert dumped_excl["detectBaseSize"] == pytest.approx(0.178)


def test_detect_base_size_accepts_float_strings_like_other_floats():
    """Pydantic coerces numeric strings → float (match baseSize behaviour)."""
    raw = {"baseSize": 0.35, "detectBaseSize": "0.178"}
    mc = CFDExportMeshConfig.model_validate(raw)
    assert mc.detectBaseSize == pytest.approx(0.178)


def test_detect_base_size_explicit_null_stays_none():
    """Explicit JSON `null` → None (not a validation error)."""
    raw = {"baseSize": 0.35, "detectBaseSize": None}
    mc = CFDExportMeshConfig.model_validate(raw)
    assert mc.detectBaseSize is None
