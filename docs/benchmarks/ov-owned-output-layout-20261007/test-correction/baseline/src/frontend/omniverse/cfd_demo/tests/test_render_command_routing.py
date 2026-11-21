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


def test_air_result_context_is_never_set_by_any_command():
    """Liquid colouring has its own verified context; the air result-layer
    context stays fail-closed (only its None initialisation may assign it)."""
    tree = _tree()
    assigned = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Attribute) and t.attr == "_trusted_result_context" for t in n.targets)]
    assert len(assigned) == 1 and isinstance(assigned[0].value, ast.Constant) and assigned[0].value.value is None
    src = open(EXT).read()
    assert "kind='liquidResult'" in src  # liquid grants are verified as their own kind


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
