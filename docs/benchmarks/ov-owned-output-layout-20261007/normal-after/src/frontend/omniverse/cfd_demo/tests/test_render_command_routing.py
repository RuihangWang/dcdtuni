"""Every command the render path validates must also be ROUTED to it.

Stage 3 shipped setSlice/getSliceState in `_route_render_command`'s `allowed`
key sets but not in `_route_command`'s render-path set, so both fell through
to the legacy fire-and-forget router as "unknown command" and never acked.
extension.py imports Kit-only modules, so this reads its source with ast."""
import ast
import os

EXT = os.path.join(os.path.dirname(__file__), "..", "kit", "extensions", "dc.cfd_bridge",
                   "dc", "cfd_bridge", "extension.py")


def _tree():
    with open(EXT) as f:
        return ast.parse(f.read())


def _allowed_commands(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "allowed" for t in node.targets):
            return {k.value for k in node.value.keys}
    raise AssertionError("allowed dict not found")


def _routed_commands(tree):
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    display = set()
    for node in cls.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "OV_DISPLAY_COMMANDS":
            display = {e.value for e in node.value.args[0].elts}
    route = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_route_command")
    for node in ast.walk(route):
        if isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.In):
            comp = node.comparators[0]
            base = comp.left if isinstance(comp, ast.BinOp) else comp
            if isinstance(base, ast.Set):
                return {e.value for e in base.elts} | display
    raise AssertionError("render-path routing set not found")


def test_every_validated_render_command_is_routed_to_the_render_path():
    tree = _tree()
    missing = _allowed_commands(tree) - _routed_commands(tree)
    assert not missing, f"validated but never routed (would never ack): {sorted(missing)}"


def test_acks_are_deferred_past_the_webrtc_drop_window():
    src = open(EXT).read()
    assert "ACK_QUIET_FRAMES" in src and "_flush_acks" in src
    # _render_ack must enqueue, not push directly on the bus.
    tree = _tree()
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    ack = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_render_ack")
    assert "push" not in ast.unparse(ack)


def _signed_route_harness(monkeypatch, tmp_path, *, blocked=False):
    """Real HMAC verifier, artifact read and controller, controlled USD writer.

    Hashes/receipts here are synthetic test identities, never native acceptance.
    This tests receiver lifecycle, not the numerical packet's full USD schema.
    """
    import asyncio
    import hashlib
    import importlib.util
    import json
    import sys
    import time
    from test_dataset_scene_race import BRIDGE, _harness
    ext, scene, stage, _, _, _ = _harness(monkeypatch, tmp_path, None)
    spec = importlib.util.spec_from_file_location("race_bridge.display_grant", BRIDGE / "display_grant.py")
    grants = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, grants)
    spec.loader.exec_module(grants)
    verifier = sys.modules["race_bridge.verified_result_grant"]
    # A public controlled test key, scoped to this test, never a service key.
    key = "controlled-router-test-key-" * 2
    monkeypatch.setenv("DCDTUNI_OV_GRANT_KEY", key)
    monkeypatch.setenv("DC_CFD_DATASET_ROOTS", str(tmp_path))
    source = {"projectUid": "old-project", "projectContentSha256": "a" * 64,
              "cfdrunSha256": "b" * 64, "meshReceiptSha256": "c" * 64,
              "meshQuality": "accepted", "run": {"uid": "controlled-run", "receiptSha256": "d" * 64}}
    convergence = {"verdict": "NOT_CONVERGED", "receiptSha256": "e" * 64}
    writes, tickets = [], []
    entered, release = asyncio.Event(), asyncio.Event()
    async def writer(target, ticket):
        tickets.append(ticket)
        packet = verifier.read_granted_packet(ticket.granted())
        entered.set()
        if blocked:
            await release.wait()
        # Same actual final gate used by production's USD transaction.
        with ext._verified_result_controller.commit(ticket, packet,
                lambda: ext._result_scene_identity(target), lambda: ext._display_generation):
            writes.append((packet["source"], packet["timeSeconds"]))
        return {"controlledWriter": True}
    ext._load_verified_result = writer
    def grant(*, frame=0.02, common_generation=0):
        packet = {"schema": "dcdtuni.render-result-layers/v3", "source": source,
                  "timeSeconds": frame, "runConvergence": convergence}
        raw = json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        artifact = tmp_path / ("controlled-" + str(frame) + ".json")
        artifact.write_bytes(raw)
        now = time.time()
        payload = {"kind": "rendererResult", "path": str(artifact), "runId": "f" * 64,
                   "issuedAt": now, "expiresAt": now + 60, "nonce": "controlled-nonce",
                   "packetSha256": hashlib.sha256(raw).hexdigest(), "nativeInputSha256": "1" * 64,
                   "postprocessRevision": "controlled-pp", "timeSeconds": frame,
                   "sceneRevision": ext._scene_revision, "displayGeneration": common_generation,
                   "source": source, "runConvergence": convergence}
        return grants.sign(payload, key)
    return ext, stage, grants, key, grant, writes, tickets, entered, release


def test_signed_diagnostic_context_is_installed_only_after_actual_verification(monkeypatch, tmp_path):
    import asyncio
    import json
    from test_dataset_scene_race import _drain_until
    async def scenario():
        ext, stage, _, _, grant, writes, tickets, _, _ = _signed_route_harness(monkeypatch, tmp_path)
        assert ext._trusted_result_context is None
        signed = grant()
        ext._route_render_command("loadVerifiedRenderResultLayers", json.dumps({"requestId": "verified", "grant": signed}))
        await _drain_until(lambda: len(ext.acks) == 1)
        assert ext.acks[0]["ok"] is True
        assert ext._trusted_result_context == {"source": signed["payload"]["source"], "timeSeconds": 0.02}
        assert writes == [(signed["payload"]["source"], 0.02)] and len(tickets) == 1
        assert tickets[0].granted()["runConvergence"]["verdict"] == "NOT_CONVERGED"
    asyncio.run(scenario())


import pytest

@pytest.mark.parametrize("command, fields", [
    ("beginRenderResultLayers", {"chunks": 1}),
    ("renderResultLayersChunk", {"index": 0, "data": "controlled"}),
    ("renderResultLayersProgress", {"resultRequestId": "raw", "received": 1}),
    ("loadRenderResultLayers", {}),
])
def test_unsigned_commands_cannot_create_or_replace_a_verified_context(monkeypatch, tmp_path, command, fields):
    import asyncio
    import json
    from test_dataset_scene_race import _drain_until
    async def scenario():
        ext, _, _, _, grant, writes, _, _, _ = _signed_route_harness(monkeypatch, tmp_path)
        ext._route_render_command(command, json.dumps({"requestId": "raw-first", **fields}))
        assert ext.acks[-1]["ok"] is False and "verified server canonical grant" in ext.acks[-1]["error"]
        assert ext._trusted_result_context is None and writes == []
        ext._route_render_command("loadVerifiedRenderResultLayers", json.dumps({"requestId": "verified", "grant": grant()}))
        await _drain_until(lambda: len(ext.acks) == 2)
        context = ext._trusted_result_context.copy()
        ext._route_render_command(command, json.dumps({"requestId": "raw-after", **fields}))
        assert ext.acks[-1]["ok"] is False
        assert ext._trusted_result_context == context and len(writes) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["signature", "source", "revision", "expired", "wrong-kind", "artifact-bytes"])
def test_bad_signed_inputs_cannot_author_or_replace_entry_context(monkeypatch, tmp_path, fault):
    import asyncio
    import copy
    import json
    from pathlib import Path
    from test_dataset_scene_race import _drain_until
    async def scenario():
        ext, _, grants, key, grant, writes, _, _, _ = _signed_route_harness(monkeypatch, tmp_path)
        valid = grant()
        ext._route_render_command("loadVerifiedRenderResultLayers", json.dumps({"requestId": "good", "grant": valid}))
        await _drain_until(lambda: len(ext.acks) == 1)
        context = copy.deepcopy(ext._trusted_result_context)
        bad = copy.deepcopy(valid)
        if fault == "signature":
            bad["signature"] = "0" * 64
        elif fault == "artifact-bytes":
            Path(bad["payload"]["path"]).write_text("changed-controlled-packet")
        else:
            payload = bad["payload"]
            if fault == "source": payload["source"]["projectUid"] = "another-project"
            elif fault == "revision": payload["sceneRevision"] += 1
            elif fault == "expired": payload["issuedAt"] -= 100; payload["expiresAt"] -= 100
            elif fault == "wrong-kind": payload["kind"] = "liquidResult"
            bad = grants.sign(payload, key)
        ext._route_render_command("loadVerifiedRenderResultLayers", json.dumps({"requestId": "bad", "grant": bad}))
        await _drain_until(lambda: len(ext.acks) == 2)
        assert ext.acks[-1]["ok"] is False and writes == [(valid["payload"]["source"], 0.02)]
        # Artifact bytes are checked after a verified context is installed;
        # its failed load cannot author data. Entry failures leave prior context.
        if fault != "artifact-bytes": assert ext._trusted_result_context == context
    asyncio.run(scenario())


@pytest.mark.parametrize("retire", ["clear", "scene", "newer-signed"])
def test_late_verified_writer_cannot_commit_after_retirement(monkeypatch, tmp_path, retire):
    import asyncio
    import json
    from test_dataset_scene_race import _drain_until
    async def scenario():
        ext, stage, _, _, grant, writes, tickets, entered, release = _signed_route_harness(monkeypatch, tmp_path, blocked=True)
        ext._route_render_command("loadVerifiedRenderResultLayers", json.dumps({"requestId": "old", "grant": grant()}))
        await _drain_until(entered.is_set)
        if retire == "clear":
            ext._route_render_command("clearRenderResultLayers", json.dumps({"requestId": "clear", "displayGeneration": 1}))
            assert ext._trusted_result_context is None
        elif retire == "scene":
            ext._route_render_command("loadRenderScene", json.dumps({"requestId": "scene"}))
            assert ext._trusted_result_context is None
        else:
            ext._route_render_command("loadVerifiedRenderResultLayers", json.dumps({"requestId": "new", "grant": grant(frame=0.04, common_generation=1)}))
            assert ext._trusted_result_context["timeSeconds"] == 0.04
        release.set()
        await _drain_until(lambda: len(ext.acks) == 2)
        old = next(a for a in ext.acks if a["requestId"] == "old")
        assert old["ok"] is False and "context changed" in old["error"]
        newer = next(a for a in ext.acks if a["requestId"] != "old")
        assert newer["ok"] is True
        if retire == "newer-signed":
            assert [frame for _, frame in writes] == [0.04] and len(tickets) == 2
        else:
            assert writes == []
            assert ext._trusted_result_context is None
            if retire == "scene": assert stage.project == "new-project"
        # No production assignment count is assumed: lifecycle protection is actual.
    asyncio.run(scenario())


def test_view_state_reports_the_scene_identity_for_ack_recovery():
    """A scene ack the livestream plugin drops (NVST_R_BUSY) is recovered by
    the browser from getViewState: that reply must carry the request id of
    the last authored scene plus every identity field a scene ack carries, and
    the id must be recorded only after authoring succeeded."""
    tree = _tree()
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    route = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_route_render_command")
    acks = [n for n in ast.walk(route) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", "") == "_render_ack"
            and any(k.arg == "sceneRequestId" for k in n.keywords)]
    assert len(acks) == 1, "exactly the view-state reply reports sceneRequestId"
    keys = {k.arg for k in acks[0].keywords}
    assert {"view", "primitives", "sceneRevision", "projectHash", "sceneRequestId"} <= keys
    author = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_author_render_scene")
    src = ast.unparse(author)
    assert src.index("author_scene(") < src.index("self._last_scene_request_id = request_id")
    assert "_author_render_scene(" in ast.unparse(route)



def test_a_scene_for_another_project_clears_the_air_result_display():
    """Measured 2026-09-30: after Base_coldAisleIso's streamlines, loading the
    Shinaide scene left them drawn over the liquid racks. The dataset load
    records the scene's project; a scene for another project schedules the
    clear, which removes streamlines and slice, hides the volume and drops the
    dataset references later slice/volume commands would use."""
    tree = _tree()
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    fn = {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    route = ast.unparse(fn["_route_render_command"])
    author = ast.unparse(fn["_author_render_scene"])
    assert "packet['project']['uid'] != self._display_project_uid" in author
    assert "self._clear_display(stage)" in author
    assert "_author_render_scene(" in route
    assert "self._display_project_uid = self._scene_project_uid" in ast.unparse(fn["_load_granted_dataset"])
    clear = ast.unparse(fn["_clear_display"])
    for call in ("remove_streamlines", "remove_slice", "show_volume_display(stage, 'off')",
                 "dataset_path = None", "source_vtu_path = None", "RemovePrim('/World/internal')"):
        assert call in clear, call
