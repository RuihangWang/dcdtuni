"""Server-issued display grants: Kit loads a dataset only on a grant the
server signed, never on a path the browser asserts. The browser relays the
grant verbatim; tampering with any field, replaying after expiry, pointing
outside the configured roots, or running without a key must all fail closed."""
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "kit", "extensions", "dc.cfd_bridge")))
from dc.cfd_bridge import display_grant as dg  # noqa: E402

KEY = "k" * 64


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "case"
    run = root / "Box" / "log" / "run1" / ".ov-display" / "abc" / "EnSight"
    run.mkdir(parents=True)
    f = run / "abc.case"
    f.write_text("FORMAT\ntype: ensight gold\n")
    return root, f


def payload(path, now=1000.0, **kw):
    p = {"kind": "dataset", "path": str(path), "runId": "r" * 64, "run": "run1",
         "time": "2", "issuedAt": now, "expiresAt": now + 600, "nonce": "n1"}
    p.update(kw)
    return p


def test_roundtrip_returns_the_signed_payload(dataset):
    root, f = dataset
    grant = dg.sign(payload(f), KEY)
    out = dg.verify(grant, KEY, now=1001.0, roots=[str(root)])
    assert out["path"] == os.path.realpath(f)
    assert out["schema"] == dg.GRANT_SCHEMA


@pytest.mark.parametrize("field,value", [("path", "/etc/passwd"), ("run", "other"), ("expiresAt", 99999.0)])
def test_any_tampered_field_breaks_the_signature(dataset, field, value):
    root, f = dataset
    grant = dg.sign(payload(f), KEY)
    grant["payload"][field] = value
    with pytest.raises(ValueError, match="signature"):
        dg.verify(grant, KEY, now=1001.0, roots=[str(root)])


def test_wrong_key_missing_key_and_short_key_fail_closed(dataset):
    root, f = dataset
    grant = dg.sign(payload(f), KEY)
    with pytest.raises(ValueError):
        dg.verify(grant, "x" * 64, now=1001.0, roots=[str(root)])
    with pytest.raises(ValueError, match="key"):
        dg.verify(grant, "", now=1001.0, roots=[str(root)])
    with pytest.raises(ValueError, match="key"):
        dg.sign(payload(f), "short")


def test_expired_future_and_overlong_grants_are_rejected(dataset):
    root, f = dataset
    with pytest.raises(ValueError, match="expired"):
        dg.verify(dg.sign(payload(f), KEY), KEY, now=1601.0, roots=[str(root)])
    with pytest.raises(ValueError, match="not yet valid"):
        dg.verify(dg.sign(payload(f, now=5000.0), KEY), KEY, now=1000.0, roots=[str(root)])
    long = payload(f, expiresAt=1000.0 + dg.MAX_TTL_SECONDS + 1)
    with pytest.raises(ValueError, match="lifetime"):
        dg.verify(dg.sign(long, KEY), KEY, now=1001.0, roots=[str(root)])


def test_path_must_sit_inside_a_configured_root(dataset, tmp_path):
    root, f = dataset
    grant = dg.sign(payload(f), KEY)
    with pytest.raises(ValueError, match="root"):
        dg.verify(grant, KEY, now=1001.0, roots=[str(tmp_path / "elsewhere")])
    with pytest.raises(ValueError, match="root"):
        dg.verify(grant, KEY, now=1001.0, roots=[])
    # A symlink that escapes the root is judged by its target.
    outside = tmp_path / "outside.case"
    outside.write_text("x")
    link = root / "escape.case"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="root"):
        dg.verify(dg.sign(payload(link), KEY), KEY, now=1001.0, roots=[str(root)])


def test_shape_kind_and_format_are_enforced(dataset):
    root, f = dataset
    with pytest.raises(ValueError):
        dg.verify({"payload": payload(f)}, KEY, now=1001.0, roots=[str(root)])
    with pytest.raises(ValueError, match="kind"):
        dg.verify(dg.sign(payload(f, kind="resultLayers"), KEY), KEY, now=1001.0, roots=[str(root)])
    bad = f.with_suffix(".sh")
    bad.write_text("x")
    with pytest.raises(ValueError, match="format"):
        dg.verify(dg.sign(payload(bad), KEY), KEY, now=1001.0, roots=[str(root)])
    missing = f.with_name("gone.case")
    with pytest.raises(ValueError, match="exist"):
        dg.verify(dg.sign(payload(missing), KEY), KEY, now=1001.0, roots=[str(root)])


def test_roots_from_env_splits_and_drops_blanks():
    assert dg.roots_from_env("/a:/b::") == ["/a", "/b"]
    assert dg.roots_from_env("") == []
    assert dg.roots_from_env(None) == []


def liquid_payload(path, now=1000.0, **kw):
    p = {"kind": "liquidResult", "path": str(path), "runId": "a" * 32, "resultSha256": "b" * 64,
         "configHash": "c" * 64, "solver": "differentiable_newton_dae", "status": "completed",
         "samples": 61, "projectUid": "proj_Shinaide", "projectContentSha256": "d" * 64,
         "issuedAt": now, "expiresAt": now + 600, "nonce": "n2"}
    p.update(kw)
    return p


@pytest.fixture
def result_json(tmp_path):
    root = tmp_path / "case"
    run = root / "Shinaide_Liquid" / "Liquid" / "log" / ("run-" + "a" * 32)
    run.mkdir(parents=True)
    f = run / "result.json"
    f.write_text("{}")
    return root, f


def test_liquid_result_grant_roundtrips_only_as_its_own_kind(result_json, dataset):
    root, f = result_json
    grant = dg.sign(liquid_payload(f), KEY)
    out = dg.verify(grant, KEY, now=1001.0, roots=[str(root)], kind="liquidResult")
    assert out["projectUid"] == "proj_Shinaide" and out["path"].endswith("result.json")
    # A liquid grant replayed as a dataset load is refused, and vice versa.
    with pytest.raises(ValueError, match="kind"):
        dg.verify(grant, KEY, now=1001.0, roots=[str(root)])
    droot, df = dataset
    with pytest.raises(ValueError, match="kind"):
        dg.verify(dg.sign(payload(df), KEY), KEY, now=1001.0, roots=[str(droot)], kind="liquidResult")


def test_liquid_grant_suffix_and_keys_are_its_own(result_json, tmp_path):
    root, f = result_json
    # A dataset suffix under a liquid grant is refused; .json is never a dataset.
    vtu = f.with_suffix(".vtu")
    vtu.write_text("x")
    with pytest.raises(ValueError, match="format"):
        dg.verify(dg.sign(liquid_payload(vtu), KEY), KEY, now=1001.0, roots=[str(root)], kind="liquidResult")
    with pytest.raises(ValueError, match="format"):
        dg.verify(dg.sign(payload(f, run="r", time="2"), KEY), KEY, now=1001.0, roots=[str(root)])
    extra = liquid_payload(f)
    extra["projectHash"] = "x"
    with pytest.raises(ValueError, match="fields"):
        dg.verify(dg.sign(extra, KEY), KEY, now=1001.0, roots=[str(root)], kind="liquidResult")
    with pytest.raises(ValueError, match="unknown"):
        dg.verify(dg.sign(liquid_payload(f), KEY), KEY, now=1001.0, roots=[str(root)], kind="bogus")
