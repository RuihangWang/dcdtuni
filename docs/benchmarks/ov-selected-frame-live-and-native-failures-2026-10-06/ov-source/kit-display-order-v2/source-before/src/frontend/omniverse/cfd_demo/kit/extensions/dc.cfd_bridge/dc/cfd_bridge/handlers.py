"""Command handlers — call omni.cae viz / USD APIs. Run on the Kit main thread."""
import omni.usd


def _imageable(stage, path):
    from pxr import UsdGeom
    prim = stage.GetPrimAtPath(path)
    return UsdGeom.Imageable(prim) if prim and prim.IsValid() else None


def _prim_visible(stage, path):
    """True if the prim exists AND resolves visible. Used so an ROI clip/move only
    rebuilds the VOLUME when the volume is actually shown — a streamlines-only ROI
    clip must not conjure the volume back on."""
    from pxr import UsdGeom
    img = _imageable(stage, path)
    return bool(img) and img.ComputeVisibility() != UsdGeom.Tokens.invisible


def handle_load_scene(scene, stage, json_path=""):
    """Build (if needed) the data-center scene from a scene-definition JSON, then
    reveal it + frame the camera. The scene is NOT built at startup, so the first
    loadScene runs the full geometry placement (~minutes for GB300 racks). Passing
    a different json_path than the loaded one rebuilds from scratch."""
    import asyncio
    asyncio.ensure_future(_load_scene_async(scene, stage, json_path))


async def _load_scene_async(scene, stage, json_path):
    # Build the scene if it has not been built, or if the user picked a different
    # scene JSON than the one currently loaded.
    want = json_path or scene.json_path
    if not getattr(scene, "_built", False) or (want and want != scene.json_path):
        try:
            print(f"[dc.cfd_bridge] loadScene: building scene from {want}")
            await scene.build(json_path=json_path or None)
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] loadScene build ERROR: {e}")
            return

    # build() runs new_stage_async(), which REPLACES the stage — the `stage`
    # captured at command-route time is now an expired handle (calling
    # GetPrimAtPath on it raises a misleading Boost.Python signature error).
    # Re-fetch the live stage so the reveal/frame operate on the freshly built one.
    stage = omni.usd.get_context().get_stage()

    img = _imageable(stage, "/World/Geometry")
    if img:
        img.MakeVisible()
    # Reveal EVERY geo_group, not just the /World/Geometry root. A prior session may
    # have hidden individual groups via setObjectVisible(false); a fresh React page
    # resets every tree checkbox to CHECKED. Without this, the backend keeps those
    # groups invisible while the UI shows them checked -> "all boxes ticked but
    # nothing renders". Force all groups visible so the loaded state always matches
    # the all-checked default (robust to carryover).
    # EXCEPT objects the source project itself marks disabled (.cfdproj meta.enabled
    # == false). The STLGen editor hides those, so revealing them here would make the
    # OV render disagree with the editor on the very same file — in Base_leakage that
    # is the room walls and ceiling, i.e. an opaque white box around everything.
    skip = getattr(scene, "disabled_prims", None) or set()
    n = 0
    for paths in scene.geo_groups.values():
        for p in (paths or []):
            if p in skip:
                continue
            ig = _imageable(stage, p)
            if ig:
                ig.MakeVisible()
                n += 1
    if skip:
        print(f"[dc.cfd_bridge] loadScene: {len(skip)} prim(s) left hidden "
              f"(disabled in the source project)")
        # Machine-readable echo of WHICH groups came up hidden. The command channel is
        # one-way, so without this the tree renders every checkbox ticked while the
        # prims behind some of them are invisible — the box lies, and there is no
        # discoverable way to turn those objects on. GTC_2026 is the case that shows it:
        # all four of its walls are meta.enabled=false, so "Room walls" read as ON while
        # nothing was drawn. The frontend parses this line and unticks them, after which
        # ticking the box reveals them normally.
        _hidden_groups = sorted({
            g for g, paths in scene.geo_groups.items()
            if g and g[0].isupper() and paths and all(p in skip for p in paths)})
        if _hidden_groups:
            print(f"[dc.cfd_bridge] hiddenGroups={','.join(_hidden_groups)}")
    # Re-write the model-tree manifest on EVERY loadScene, not only on a real build.
    # Loading the same project twice in one Kit session takes the reveal-only path, so
    # nothing rewrote the file and the page — which correctly refuses a manifest older
    # than its own load — timed out and showed an empty tree over rendered geometry.
    _mf = getattr(scene, "scene_manifest", None)
    if _mf:
        try:
            from . import scenetree as _tree
            # Fill the ROI here, not at build time: build() creates the box AFTER
            # _build_from_cfdproj writes the manifest, so the value is still None then.
            _roi = scene._roi_bounds_for_manifest()
            if _roi:
                _mf["roi"] = _roi
            print(f"[dc.cfd_bridge] sceneTree written: {_tree.write_manifest(_mf)} "
                  f"({_mf.get('objects', 0)} objects, roi={_mf.get('roi')}, "
                  "refreshed on reveal)")
        except Exception as _e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] scene tree refresh FAILED: {_e}")
    # Re-show the ground grid. Two mechanisms, because there are two grids: the native
    # viewport grid is a SETTING with no prim (reset_scene turns it off), while the USD
    # fallback is a prim sibling of /World/Geometry that the group reveal above misses.
    gp = getattr(scene, "grid_path", None)
    if gp:
        ig = _imageable(stage, gp)
        if ig:
            ig.MakeVisible()
    else:
        from . import scene as _scene_mod
        if not _scene_mod.USD_GRID:
            handle_set_grid(scene, stage, **_scene_mod.NATIVE_GRID_DEFAULTS)
    # What is ACTUALLY visible, computed (not assumed) from the composed stage. The
    # skip-list arithmetic can be self-consistent while a prim still renders, so this
    # walks the result instead of trusting the bookkeeping.
    if skip:
        try:
            from pxr import UsdGeom as _UG
            _bc = _UG.BBoxCache(0, ["default"], useExtentsHint=False)
            _root = stage.GetPrimAtPath("/World/Geometry")
            _vis = {}
            for _p in _root.GetChildren():
                for _o in _p.GetChildren():
                    _im = _UG.Imageable(_o)
                    if not _im:
                        continue
                    if _im.ComputeVisibility() == _UG.Tokens.invisible:
                        continue
                    _r = _bc.ComputeWorldBound(_o).ComputeAlignedRange()
                    _vis[_o.GetPath().pathString] = (
                        tuple(round(v, 1) for v in _r.GetMin()),
                        tuple(round(v, 1) for v in _r.GetMax()))
            print(f"[dc.cfd_bridge] VISIBLE after reveal: {len(_vis)}")
            for _k, _v in sorted(_vis.items()):
                print(f"[dc.cfd_bridge]   VIS {_k.split('/')[-1][:26]:<27} {_v[0]} .. {_v[1]}")
        except Exception as _e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] visibility audit failed: {_e}")
    # The frontend watches this exact line to know the scene is ready (then it
    # populates the geometry list + enables the dataset controls).
    print(f"[dc.cfd_bridge] loadScene: revealed /World/Geometry + {n} group prims")
    await _frame_geometry("/World/Geometry")


async def _frame_geometry(prim_path):
    import omni.kit.app
    app = omni.kit.app.get_app()
    for _ in range(3):
        await app.next_update_async()
    try:
        from omni.kit.viewport.utility import get_active_viewport, frame_viewport_prims
        vp = get_active_viewport()
        if vp:
            ok = frame_viewport_prims(vp, prims=[prim_path])
            print(f"[dc.cfd_bridge] frame {prim_path} -> {ok}")
            # Optional camera preset for full-frame presentation recordings.
            # Normal interactive sessions retain the original framing.
            import json, os
            camera_file = os.environ.get("DC_CFD_PRESENTATION_CAMERA", "")
            if camera_file:
                from pxr import Gf, UsdGeom
                with open(camera_file) as f:
                    cfg = json.load(f)
                eye = Gf.Vec3d(*cfg["eye"])
                target = Gf.Vec3d(*cfg["target"])
                cam = UsdGeom.Camera.Define(omni.usd.get_context().get_stage(),
                                            "/PresentationCamera")
                cam.CreateFocalLengthAttr(float(cfg.get("focalLength", 28.0)))
                cam.CreateHorizontalApertureAttr(36.0)
                cam.CreateVerticalApertureAttr(20.25)
                cam.CreateClippingRangeAttr(Gf.Vec2f(0.1, 10000.0))
                xf = UsdGeom.Xformable(cam)
                xf.ClearXformOpOrder()
                xf.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(
                    eye, target, Gf.Vec3d(0, 1, 0)).GetInverse())
                vp.camera_path = cam.GetPath()
                print(f"[dc.cfd_bridge] presentationCamera eye={tuple(eye)} "
                      f"target={tuple(target)} resolution={vp.resolution}")
        else:
            print("[dc.cfd_bridge] frame: no active viewport")
    except Exception as e:
        print(f"[dc.cfd_bridge] frame error: {e}")


def handle_set_camera(stage, name):
    try:
        from omni.kit.viewport.utility import get_active_viewport
        from pxr import Sdf, UsdGeom
        path = name
        if not name.startswith("/"):
            for prim in stage.Traverse():
                if prim.IsA(UsdGeom.Camera) and prim.GetName() == name:
                    path = str(prim.GetPath()); break
        vp = get_active_viewport()
        if vp:
            vp.camera_path = Sdf.Path(path)
    except Exception as e:
        print(f"[dc.cfd_bridge] setCamera error: {e}")


def handle_set_volume(scene, stage, field, visible):
    """Show the volume coloured by ``field`` (T or U), or hide it.

    Each field has its own voxelised NanoVDB grid, so switching fields rebuilds
    the volume (kit-cae voxelises only one field at a time). Hiding is cheap and
    needs no rebuild.
    """
    import asyncio
    if not visible:
        img = _imageable(stage, scene.volume_path)
        if img:
            img.MakeInvisible()
        # Restore the geometry — the field is no longer shown, so the racks/ACUs come
        # back for the normal scene look.
        _set_geometry_hidden(scene, stage, False)
        return
    scene.active_field = field
    # Hide the Room SHELL (only) so the CFD volume reads as a vivid blue->red field.
    # The shell is a closed translucent box wrapping the whole volume; it depth-occludes
    # and grey-washes the field. GHOSTING geometry does not help — translucent geometry
    # still writes depth + overlays grey (verified). Racks stay visible as context;
    # restored on volume Off.
    _set_geometry_hidden(scene, stage, True)
    asyncio.ensure_future(scene._rebuild_volume(
        stage, field=field, roi_enabled=scene.clip_enabled, make_visible=True))


def handle_set_volume_range(scene, stage, vmin, vmax):
    # Setting the colormap domain live-updates the IndeX render directly (the
    # threshold sliders) — IndeX re-reads the transfer function, no explicit
    # dirty/refresh needed. (An nvindex:composite off->on "refresh" toggle was
    # tried and made the volume vanish + crashed IndeX — do NOT reintroduce it.)
    cm = stage.GetPrimAtPath(scene.colormap_path)
    if cm.IsValid():
        cm.GetAttribute("domain").Set((float(vmin), float(vmax)))


def _group_prims(scene, stage, name):
    """Expand a geo group to the concrete prim paths to act on. Scopes
    (Racks/ACUs) expand to their immediate children; other groups are used
    as-is."""
    from pxr import UsdGeom
    out = []
    for p in scene.geo_groups.get(name, []):
        prim = stage.GetPrimAtPath(p)
        if not prim or not prim.IsValid():
            continue
        if prim.IsA(UsdGeom.Scope):
            out.extend(str(c.GetPath()) for c in prim.GetChildren())
        else:
            out.append(p)
    return out


def _surface_shader(stage, mat):
    """Resolve a material's surface shader. MDL materials connect through the
    "mdl" render context; fall back to the universal context, then to a literal
    <material>/Shader child."""
    from pxr import UsdShade
    for ctx in ("mdl", ""):   # renderContext is a single TfToken in this USD build
        src = mat.ComputeSurfaceSource(ctx)
        if src and src[0]:
            return src[0]
    sh = stage.GetPrimAtPath(mat.GetPath().AppendChild("Shader"))
    return UsdShade.Shader(sh) if sh and sh.IsValid() else None


# Geometry groups hidden while a CFD volume is shown. We hide ONLY the Room shell —
# a CLOSED 5-face box at opacity 0.10 that "seals the hall" (scene.py), so it wraps
# the whole volume, writes depth all around it, and overlays its grey tint, washing
# the field to pale blue-grey. The IndeX volume composites against RTX geometry by
# DEPTH, and even translucent geometry occludes it, so the shell must be hidden (not
# ghosted) to de-wash the field. Racks/ACUs stay VISIBLE as context (they only locally
# occlude the warm air directly behind them). The floor GRID is separate and stays.
_VOLUME_HIDE_GROUPS = ["Room"]


def _set_geometry_hidden(scene, stage, hidden, groups=None):
    """Hide (or restore) the given geometry groups (default: just the Room shell) so
    the CFD volume reads clearly. Called when a volume is enabled (hidden=True) or
    disabled (hidden=False)."""
    names = groups if groups is not None else _VOLUME_HIDE_GROUPS
    n = 0
    for name in names:
        for p in scene.geo_groups.get(name, []):
            img = _imageable(stage, p)
            if img:
                img.MakeInvisible() if hidden else img.MakeVisible()
                n += 1
    print(f"[dc.cfd_bridge] geometry {'hidden' if hidden else 'restored'} for volume "
          f"(groups={names}, {n} prims)")


def _apply_group_opacity(scene, stage, name, opacity):
    """Drive the rendered opacity of one geometry group's bound materials. Walks the
    FULL subtree of each group prim (rack/ACU meshes and their materials live several
    levels down inside the referenced USD) and sets every unique bound material's
    OmniPBR opacity (enable_opacity + opacity_constant) AND the UsdPreviewSurface
    `opacity` float, so a group dims regardless of which surface shader the asset uses.
    Returns the number of materials touched."""
    from pxr import Usd, UsdShade, Sdf
    opacity = max(0.0, min(1.0, float(opacity)))
    seen, n = set(), 0
    # TraverseInstanceProxies is REQUIRED for the .cfdproj path. Its geometry lives in
    # instanceable prototypes under /__Prototypes (that flag is what stops 50 racks from
    # becoming 50 GPU copies), and a default PrimRange stops at the instance boundary —
    # so the walk found no gprim, no bound material, and the receipt read
    # "setObjectOpacity Racks=0.30 (0 materials)". The slider moved and nothing changed.
    #
    # Consequence to know: a prototype's material is shared by every instance of it, and
    # /World/Looks materials are keyed by (preset, colour, opacity) across classes. So
    # this dims by MATERIAL, not by object — fading one rack fades its identical twins.
    # The receipt prints the material count so that is visible rather than surprising.
    _pred = Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)
    for root in scene.geo_groups.get(name, []):
        rp = stage.GetPrimAtPath(root)
        if not rp or not rp.IsValid():
            continue
        for prim in Usd.PrimRange(rp, _pred):
            mat, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
            if not mat:
                continue
            mpath = str(mat.GetPath())
            if mpath in seen:
                continue
            seen.add(mpath)
            sh = _surface_shader(stage, mat)
            if not sh:
                continue
            sh.CreateInput("enable_opacity", Sdf.ValueTypeNames.Bool).Set(True)
            sh.CreateInput("opacity_constant", Sdf.ValueTypeNames.Float).Set(opacity)
            # `opacity` is UsdPreviewSurface's name for it and MDL has no such input, so
            # setting it on an OmniPBR shader makes Hydra log "Parameter 'opacity' ...
            # not available in the MDL representation" once per material per slider
            # move. Only author it where it means something.
            if not sh.GetSourceAsset("mdl"):
                sh.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(opacity)
            n += 1
    return n


def handle_set_object_opacity(scene, stage, name, opacity):
    """Set the rendered opacity of one geometry group (the Inspector opacity slider)."""
    n = _apply_group_opacity(scene, stage, name, opacity)
    print(f"[dc.cfd_bridge] setObjectOpacity {name}={max(0.0, min(1.0, float(opacity))):.2f} ({n} materials)")


def handle_select_object(scene, stage, name):
    """Highlight a geometry group in the viewport by selecting its prims (OV draws
    the selection outline). Empty name clears the selection."""
    ctx = omni.usd.get_context()
    sel = ctx.get_selection()
    if not name:
        sel.clear_selected_prim_paths()
        return
    paths = _group_prims(scene, stage, name)
    if paths:
        sel.set_selected_prim_paths(paths, True)
        print(f"[dc.cfd_bridge] selectObject {name} ({len(paths)} prims)")


# The Kit viewport's OWN ground grid, drawn by omni.kit.viewport.legacy_gizmos. The
# setting names are the ones its plugin binary actually reads:
#
#   /app/viewport/grid/enabled  majorInterval  plane  showOrigin  spacing  trackCamera
#   /persistent/app/viewport/grid/lineColor  lineWidth  offset  scale
#                                            lineFadeOutStartDistance  lineFadeOutEndDistance
#
# `plane` matters here: this stage is Y-up (UsdGeom.SetStageUpAxis(stage, y) — a .cfdproj
# is authored Y-up), so the ground plane is XZ. A Z-up default would stand the grid on
# edge like a wall.
GRID_SETTINGS = {
    "enabled": "/app/viewport/grid/enabled",
    "spacing": "/app/viewport/grid/spacing",
    "majorInterval": "/app/viewport/grid/majorInterval",
    "plane": "/app/viewport/grid/plane",
    "showOrigin": "/app/viewport/grid/showOrigin",
    "trackCamera": "/app/viewport/grid/trackCamera",
    "lineWidth": "/persistent/app/viewport/grid/lineWidth",
    "lineColor": "/persistent/app/viewport/grid/lineColor",
    "scale": "/persistent/app/viewport/grid/scale",
    "offset": "/persistent/app/viewport/grid/offset",
    "fadeStart": "/persistent/app/viewport/grid/lineFadeOutStartDistance",
    "fadeEnd": "/persistent/app/viewport/grid/lineFadeOutEndDistance",
}


def read_grid_settings():
    """Current value of every grid setting — the receipt that says what took effect."""
    import carb
    s = carb.settings.get_settings()
    return {k: s.get(p) for k, p in GRID_SETTINGS.items()}


def handle_set_grid(scene, stage, **opts):
    """Drive the NATIVE viewport grid through carb settings.

    This replaces a hand-built USD BasisCurves grid. That grid was thousands of line
    segments authored into the stage, which made it a pickable scene object, put it in
    the geometry hierarchy, and pinned its extent at build time so it could not follow
    the camera. The native grid is a renderer feature: no prims, no extent, and it is
    what the OV viewport draws everywhere else.
    """
    import carb
    s = carb.settings.get_settings()
    applied = {}
    for key, value in opts.items():
        path = GRID_SETTINGS.get(key)
        if path is None or value is None:
            continue
        s.set(path, value)
        applied[key] = value
    print(f"[dc.cfd_bridge] setGrid {applied} -> {read_grid_settings()}")


def handle_focus_object(scene, stage, name):
    """Frame the viewport on one geometry group — the editor's double-click-to-focus.

    Also the only way to INSPECT a rack's internals: the recess, the server duct walls
    and the U-division seams are millimetres on a 1 m box, invisible in a whole-room
    shot. Selection first, then frame the selection, because the viewport utility frames
    what is selected rather than an arbitrary prim list.
    """
    paths = _group_prims(scene, stage, name)
    if not paths:
        print(f"[dc.cfd_bridge] focusObject: unknown group {name!r}")
        return
    try:
        from omni.kit.viewport.utility import frame_viewport_selection, get_active_viewport
        omni.usd.get_context().get_selection().set_selected_prim_paths(paths, True)
        vp = get_active_viewport()
        if vp:
            # No force_legacy_api: this Kit rejects the kwarg with an ERROR line
            # ('no longer supported on frame_viewport_selection').
            frame_viewport_selection(viewport_api=vp)
        print(f"[dc.cfd_bridge] focusObject {name} ({len(paths)} prims)")
    except Exception as e:  # noqa: BLE001
        print(f"[dc.cfd_bridge] focusObject error: {e}")


def handle_load_dataset(scene, stage, path):
    """Load a new CFD dataset file (server-side path on the Kit host) at runtime."""
    import asyncio
    asyncio.ensure_future(scene.load_dataset(stage, path))


def handle_extract_roi(scene, stage, fmt="vtu"):
    """Cut the ROI out of the loaded result and make THAT the dataset.

    Distinct from setRoiClip: the clip keeps the whole field loaded and limits what the
    voxeliser reads, so hiding the ROI box brings everything back. Extraction replaces
    the dataset, so the field stays confined — that difference is the acceptance test.
    """
    import asyncio
    asyncio.ensure_future(scene.extract_roi(stage, fmt=fmt))


def handle_reset_scene(scene, stage):
    """Reset the viewport to a clean slate (fresh-Kit look) — fired by the frontend once
    per page load so a browser refresh is a from-scratch demo without a real Kit restart."""
    import asyncio
    asyncio.ensure_future(scene.reset_scene(stage))


def handle_set_object_visible(scene, stage, name, visible):
    """Show/hide a named geometry group (a USD model kind) by toggling the
    visibility of every prim in scene.geo_groups[name]. Toggling a Scope prim
    (Racks/ACUs) hides all instances under it."""
    paths = scene.geo_groups.get(name)
    if not paths:
        print(f"[dc.cfd_bridge] setObjectVisible: unknown group '{name}'")
        return
    n = 0
    for p in paths:
        img = _imageable(stage, p)
        if img:
            img.MakeVisible() if visible else img.MakeInvisible()
            n += 1
    print(f"[dc.cfd_bridge] setObjectVisible {name}={visible} ({n} prims)")


def handle_set_roi_visible(scene, stage, visible):
    img = _imageable(stage, scene.roi_path)
    if not img:
        return
    ctx = omni.usd.get_context()
    if visible:
        img.MakeVisible()
        ctx.set_pickable(scene.roi_path, True)          # let the gizmo grab it
        ctx.get_selection().set_selected_prim_paths([scene.roi_path], True)
    else:
        img.MakeInvisible()
        ctx.get_selection().clear_selected_prim_paths()


def handle_set_roi_box(scene, stage, mn, mx):
    """Resize/move the ROI box so its world AABB equals [mn, mx], by RE-AUTHORING
    the box's local geometry (``points`` + ``extent``) — NOT via an xform.

    The voxeliser reads the box's LOCAL bound (``ComputeLocalBound`` over the
    authored curve points) and ignores the prim's xform stack, so a
    ``TransformPrimSRT`` scale/translate never reached the clip (it left the
    voxelisation full-domain, or empty if the box translated out of overlap).
    Instead we keep the xform identity and linearly remap the box's current
    corner points into the target AABB — self-correcting from any current state.
    """
    from pxr import UsdGeom, Vt, Gf
    from . import geom
    import asyncio
    prim = stage.GetPrimAtPath(scene.roi_path)
    if not prim or not prim.IsValid():
        return
    # keep local == world so the authored points ARE the clip region the voxeliser sees
    xf = UsdGeom.Xformable(prim)
    if xf and xf.GetOrderedXformOps():
        xf.ClearXformOpOrder()
    pts_attr = prim.GetAttribute("points")
    pts = pts_attr.Get() if pts_attr and pts_attr.IsValid() else None
    if not pts:
        print("[dc.cfd_bridge] ROI box: no points to re-author")
        return
    cmin, cmax = geom.aabb_of(pts)
    new_pts = Vt.Vec3fArray([Gf.Vec3f(*p)
                             for p in geom.remap_points_to_aabb(pts, mn, mx)])
    pts_attr.Set(new_pts)
    ext = prim.GetAttribute("extent")
    if ext and ext.IsValid():
        ext.Set(Vt.Vec3fArray([Gf.Vec3f(*mn), Gf.Vec3f(*mx)]))
    print(f"[dc.cfd_bridge] ROI box re-authored -> {tuple(mn)}..{tuple(mx)} "
          f"(from {tuple(cmin)}..{tuple(cmax)})")

    async def _apply():
        from omni.cae.testing import wait_for_update
        for _ in range(2):
            await wait_for_update()
        # re-voxelise within the new box if clipping is active
        if scene.clip_enabled:
            if _prim_visible(stage, scene.volume_path):
                await scene._rebuild_volume(stage, field=scene.active_field,
                                            roi_enabled=True, make_visible=True)
            # ROI-IS-GOD: a moved/resized box re-confines the streamlines too.
            if scene.streamlines_enabled:
                await scene.generate_streamlines(stage)
    asyncio.ensure_future(_apply())


def handle_set_roi_clip(scene, stage, enabled):
    """Clip the volume to the ROI box (or release it to the full domain).

    The clip is the voxelisation ROI, which only takes effect when the volume is
    built, so we rebuild voxelised within the box's CURRENT bounds (enabled) or
    over the full domain (disabled). ROI-IS-GOD: the same clip also re-confines any
    active streamlines (seeds + integrated lines).
    """
    import asyncio
    scene.clip_enabled = bool(enabled)
    vol_shown = _prim_visible(stage, scene.volume_path)

    async def _apply():
        # Only re-voxelise+show the VOLUME if it is currently shown (else a
        # streamlines-only clip would force the volume on). Streamlines always
        # re-confine to the ROI when active.
        if vol_shown:
            await scene._rebuild_volume(stage, field=scene.active_field,
                                        roi_enabled=scene.clip_enabled, make_visible=True)
        if scene.streamlines_enabled:
            await scene.generate_streamlines(stage)
    asyncio.ensure_future(_apply())


def _resolve_seed_center(scene, stage, center):
    """Decide whose seed centre wins: the frontend's numbers or a viewport drag.

    The command channel is one-way, so the frontend never learns that the user dragged
    the sphere — it keeps sending its own (now stale) centre, which used to overwrite the
    drag and snap the ball back to its default. Arbitrate instead:

    * the UI value CHANGED since its last command -> the user typed new numbers, UI wins;
    * otherwise, if the prim drifted from where we placed it -> the user dragged it, the
      viewport wins.
    """
    from . import geom
    ui = [float(x) for x in center]
    ui_changed = geom.moved(scene._seed_ui_center, ui)
    scene._seed_ui_center = ui
    if ui_changed:
        scene.seed_center = ui
    else:
        scene.adopt_dragged_seed(stage)


def handle_set_streamlines(scene, stage, enabled, field, seed_center, seed_radius,
                           n_seeds, max_steps, direction):
    """Generate (enabled) or remove (disabled) the streamlines. Integration ALWAYS uses
    the vec3 U field; ``field`` (T|U) is only the COLOUR field. Seeds + lines are
    confined to the ROI box when clipping is active (enforced in generate_streamlines)."""
    import asyncio
    scene.streamline_field = field if field in ("T", "U") else "T"
    _resolve_seed_center(scene, stage, seed_center)
    scene.seed_radius = float(seed_radius)
    scene.n_seeds = int(n_seeds)
    scene.sl_max_steps = int(max_steps)
    if direction in ("forward", "backward", "both"):
        scene.sl_direction = direction
    if enabled:
        asyncio.ensure_future(scene.generate_streamlines(stage))
    else:
        asyncio.ensure_future(scene.remove_streamlines(stage))


def handle_set_seed_sphere(scene, stage, visible, center, radius, n_seeds):
    """Create / move / show / hide the draggable seed-sphere preview (no re-trace).
    Mirrors the ROI-box show pattern: showing it selects the prim so the transform
    gizmo attaches for dragging."""
    import asyncio
    _resolve_seed_center(scene, stage, center)
    scene.seed_radius = float(radius)
    scene.n_seeds = int(n_seeds)
    asyncio.ensure_future(scene.update_seed_sphere(stage, bool(visible)))


def handle_set_slice(scene, stage, visible, origin, normal, radius, field, domain):
    """Adopt the panel's slice parameters (display-only surface slice).

    The plane parameters land FIRST; only then does the origin go through the
    seed-sphere style arbitration (changed panel numbers win, otherwise a
    dragged preview disc wins). The caller awaits ``scene.update_slice`` after
    this and acks with the resulting state — re-authoring the disc before the
    arbitration would let a stale panel origin overwrite the user's drag."""
    scene.slice_visible = bool(visible)
    scene.slice_normal = [float(v) for v in normal]
    scene.slice_radius = float(radius)
    scene.slice_field = field
    scene.slice_domain = None if domain is None else [float(v) for v in domain]
    scene.resolve_slice_origin(stage, origin)


def handle_sync_from_viewport(scene, stage):
    """Re-render whatever the user DRAGGED in the viewport.

    Kit emits no drag events over the one-way command channel, so a gizmo drag cannot
    reach the render by itself — this is the button that pulls it through. Bake a dragged
    ROI box into its points, adopt a dragged seed sphere, then re-voxelise the volume
    inside the new region and re-trace the streamlines from the new seeds.

    The volume is only rebuilt when clipping is ON and the volume is actually shown:
    with the clip off the ROI does not bound the volume, so re-voxelising would be an
    expensive no-op, and a streamlines-only sync must not conjure the volume back on.
    """
    import asyncio
    aabb = scene.bake_roi_xform(stage)
    scene.adopt_dragged_seed(stage)
    slice_moved = (getattr(scene, 'adopt_dragged_slice', None) is not None
                   and scene.adopt_dragged_slice(stage) is not None)
    vol_shown = _prim_visible(stage, scene.volume_path)
    rebuild_vol = bool(scene.clip_enabled and vol_shown)
    print(f"[dc.cfd_bridge] syncFromViewport clip={scene.clip_enabled} "
          f"volume={vol_shown} streamlines={scene.streamlines_enabled} "
          f"roi={'none' if not aabb else 'ok'}")

    async def _apply():
        from omni.cae.testing import wait_for_update
        for _ in range(2):
            await wait_for_update()
        if rebuild_vol:
            await scene._rebuild_volume(stage, field=scene.active_field,
                                        roi_enabled=True, make_visible=True)
        if scene.streamlines_enabled:
            await scene.generate_streamlines(stage)
        if slice_moved and scene.slice_visible:
            await scene.update_slice(stage)
        if not rebuild_vol and not scene.streamlines_enabled and not slice_moved:
            # Nothing to re-render, but the ROI numbers still moved — say so, otherwise
            # the button looks dead when the user drags before showing a field.
            print("[dc.cfd_bridge] syncFromViewport: ROI updated (nothing rendered yet)")
    asyncio.ensure_future(_apply())
