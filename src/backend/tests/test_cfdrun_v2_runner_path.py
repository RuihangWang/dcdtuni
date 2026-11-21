"""P3 T6 — STLGenRunner consumes the V2 .cfdrun manifest (ResolvedRun) for the
BC registry, instead of re-deriving it with the deleted _build_bridge_bc().

This is the runner-path equivalence proof: a case whose model/<name>.cfdrun is a
REAL V2 manifest (with the transitional meshGeometry block + real boundary
bindings) must, after _load() + _prepare_runtime_metadata(), expose a
self._bridge_registry that equals the frozen v1 derivation baseline
(Base_leakage.registry.json) — under the known-benign normalisation
(timeVarying {} ≡ null, 1e-9 float tolerance).

The fixture src/backend/tests/fixtures/cfdrun_v2_full/Base_leakage.cfdrun was
generated from the live frontend via:
    POST /api/browser/load-proj  case/Base_leakage/Base_leakage.cfdproj
    POST /api/browser/exec-js    (async()=>JSON.stringify(
                                     await window.buildCfdrunJsonV2()))()
so it is the exact V2 manifest the browser now POSTs for mesh/solve.
"""
import json
import shutil
from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = (
    _REPO_ROOT
    / "src/backend/tests/fixtures/cfdrun_v2_full/Base_leakage.cfdrun"
)
_BASELINE = (
    _REPO_ROOT
    / "docs/superpowers/specs/baselines/2026-06-12-v4-refactor"
    / "cfdrun_v1/Base_leakage.registry.json"
)
# Frozen copy of the Base_leakage export's triSurface *.stl (case/*/Mesh is
# gitignored, so the live export is not in a clone).
_REAL_TRISURFACE = (
    _REPO_ROOT / "src/backend/tests/fixtures/cfdrun_cases/Base_leakage/triSurface"
)


def _normalise(dump: dict) -> dict:
    """Apply the known-benign normalisation: timeVarying {} ≡ null."""
    out = {}
    for patch, spec in dump.items():
        s = dict(spec)
        if s.get("timeVarying") == {}:
            s["timeVarying"] = None
        out[patch] = s
    return out


def _assert_close(actual: dict, expected: dict, tol: float = 1e-9) -> None:
    a, e = _normalise(actual), _normalise(expected)
    assert set(a.keys()) == set(e.keys()), (
        f"patch-name set differs: only-in-actual="
        f"{sorted(set(a) - set(e))}, only-in-baseline={sorted(set(e) - set(a))}"
    )
    for patch in e:
        sa, se = a[patch], e[patch]
        assert set(sa.keys()) == set(se.keys()), (
            f"{patch}: field-set differs: {set(sa) ^ set(se)}"
        )
        for field, ve in se.items():
            va = sa[field]
            if (
                isinstance(va, (int, float))
                and isinstance(ve, (int, float))
                and not isinstance(va, bool)
                and not isinstance(ve, bool)
            ):
                assert abs(va - ve) <= tol, (
                    f"{patch}.{field}: {va!r} != {ve!r} (tol={tol})"
                )
            else:
                assert va == ve, f"{patch}.{field}: {va!r} != {ve!r}"


@pytest.fixture
def v2_case(tmp_path: Path) -> Path:
    """A throwaway case dir with the V2 fixture in model/ and a COPY of the frozen
    Base_leakage triSurface STLs (so _load()'s in-place merge/split passes do not
    corrupt the committed fixture)."""
    case_dir = tmp_path / "Base_leakage"
    model_dir = case_dir / "model"
    tri_dir = case_dir / "Mesh" / "constant" / "triSurface"
    model_dir.mkdir(parents=True)
    tri_dir.mkdir(parents=True)
    shutil.copy(_FIXTURE, model_dir / "Base_leakage.cfdrun")
    stls = sorted(_REAL_TRISURFACE.glob("*.stl"))
    # Loud: an empty glob would only surface later as the runner's "no grouped
    # STL files" error, far from the cause.
    assert stls, f"Base_leakage triSurface fixture missing: {_REAL_TRISURFACE}"
    for stl in stls:
        shutil.copy(stl, tri_dir / stl.name)
    return case_dir


def test_fixture_is_v2_with_meshgeometry_and_bindings():
    """Guard: the fixture is a real V2 manifest with meshGeometry + bindings."""
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert raw["schemaVersion"] == 2
    assert raw.get("meshGeometry") is not None
    # 46 boundaryBindings + 6 porousBindings = 52 registry patches.
    assert len(raw["boundaryBindings"]) == 46
    assert len(raw["porousBindings"]) == 6
    # Regression guard (P3 T7): the editor render/export must emit the
    # V4-canonical model.geometry.quaternion, NOT the stale model.transform
    # (identity) mirror. Default_Rack_1 is rotated ~90 deg about Y, so its
    # meshGeometry quaternion must be non-identity; an identity here means the
    # rotation-pipeline regression (un-rotated walls/racks -> wrong mesh) is back.
    rack_q = raw["meshGeometry"]["racks"]["Default_Rack_1"]["geometry"]["quaternion"]
    is_identity = (
        abs(rack_q["x"]) < 1e-9
        and abs(rack_q["y"]) < 1e-9
        and abs(rack_q["z"]) < 1e-9
        and abs(abs(rack_q["w"]) - 1.0) < 1e-9
    )
    assert not is_identity, (
        "Default_Rack_1 meshGeometry quaternion is identity "
        f"({rack_q}); the V4 rotation regression (reading stale model.transform "
        "instead of model.geometry.quaternion) has returned."
    )


def test_runner_path_registry_equals_baseline(v2_case):
    """_load() + _prepare_runtime_metadata() on a V2 manifest produces a
    self._bridge_registry that equals the v1-derivation baseline (52 patches),
    proving the runner now consumes ResolvedRun (not the deleted
    _build_bridge_bc()) for boundary conditions."""
    runner = STLGenRunner(v2_case)
    runner._load()
    runner._prepare_runtime_metadata()

    # The registry comes from the resolved V2 manifest, not an in-runner method.
    assert runner._resolved is not None
    assert runner._bridge_registry is runner._resolved.bridge_registry

    dump = {
        name: spec.model_dump(mode="json")
        for name, spec in runner._bridge_registry.items()
    }
    assert len(dump) == 52, f"expected 52 patches, got {len(dump)}"

    baseline = json.loads(_BASELINE.read_text(encoding="utf-8"))
    _assert_close(dump, baseline)


def test_runner_rejects_non_v2_manifest(tmp_path: Path):
    """A schemaVersion≠2 manifest is rejected at _load() with the A12
    're-export' error (V2 hard-cutover)."""
    case_dir = tmp_path / "legacy"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "legacy.cfdrun").write_text(
        json.dumps({"room": {"height": 3.0}, "meshConfig": {}}),
        encoding="utf-8",
    )
    runner = STLGenRunner(case_dir)
    with pytest.raises(ValueError, match=r"re-export"):
        runner._load()
