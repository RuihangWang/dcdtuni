"""Build the GTC scene (geometry + CFD volume + ROI box) — all hidden at startup."""
import os
import math
import json
import time as _time

from . import geom


# Per-field DISPLAY domain = the colormap range pinned to the data's p1..p99 working
# band, NOT the raw min/max. Both fields are heavily right-skewed (computed from
# internal.vtu): T median sits at normalized 0.19 of its [291.7,317.5] K range, |U|
# median 0.305 vs max 27.9. Mapping the colormap over the full min/max therefore
# squashes >99% of voxels into the blue end -> a near-monochrome wash ('can't see the
# temperature'). The percentile band spreads blue->red across where the data actually
# lives (cold supply -> warm-return mode); the thin >p99 tail clamps to opaque red via
# clampToEdge. The Low/High sliders still re-tighten the domain live
# (handle_set_volume_range), and the frontend DEFAULT_RANGE mirrors these values.
#
# The domain must stay PINNED, not autoscaled per load: the whole point of the demo is
# comparing two predictions, and a domain that re-fits each field would renormalise the
# colours so a genuine 1 K room-wide drop rendered identically to the baseline.
#
# Values are p1..p99 over several A3 predictions, which is what the viewport now shows
# (the pre-baked internal.vtu is only reachable via "Load a different result…").
_DISPLAY_DOMAIN = {
    "T": (292.1, 309.0),   # K; A3 predictions run p1 292.9 .. p99 308.9
    # |U|: the A3 field is far slower than the pre-baked internal.vtu this pin was first
    # taken from — p99 1.15 and max 3.83 m/s against that file's p99 4.5 / max 27.9. At
    # the old 4.5 ceiling the entire A3 bulk (median 0.50) sat below 11% of the ramp and
    # the room read as flat blue: the same monochrome wash the T pin exists to avoid.
    "U": (0.0, 1.2),
}


def _apply_thermal_colormap(cm, palette=None):
    """Saturated blue->cyan->green->amber->red ramp with an ALPHA VALLEY — the
    transfer function that makes the IndeX volume read as a VIVID blue-red field
    instead of a pale grey haze.

    Root cause of the old washout (verified against the kit-cae source + the field
    stats): the IndeX volume path (basic.xac) has NO opacity/density multiplier —
    opacity comes ENTIRELY from the colormap alpha. The official 3-stop default put a
    desaturated light grey (0.86,0.86,0.86) at alpha 0.5 right at the colormap mid,
    and the GTC room is mostly cold-supply + warm-return air (58.8% in the cold third,
    40.7% in the middle third of the T range). So a thick column integrated a huge
    number of half-transparent grey samples into a uniform pale-grey cloud that buried
    the blue and red.

    The fix has two halves:
      * SATURATED RGB at every stop (deep blue -> cyan -> green -> amber -> orange ->
        pure red), so whatever IS visible carries strong hue against the dark studio
        background.
      * an ALPHA VALLEY: alpha is near-zero where the voxels are (cold supply 0.04,
        cyan 0.10, green transition 0.16) and rises only on the SPARSE warm/hot tail
        (amber 0.42, orange 0.78, red 1.0). Integrated optical depth ~ alpha x
        voxel-count, so driving alpha toward zero exactly where voxel-count is highest
        keeps the room clear (no fog) while the rare hot-exhaust plumes accumulate into
        discrete, solid-red 'airflow organization'. Stops are biased toward the cold
        end (extra resolution below x=0.40 where the median and the cold/hot-aisle
        boundary live) to match the right-skewed distribution.
    Pair with the PINNED p1..p99 domain (_DISPLAY_DOMAIN) + clampToEdge so the gradient
    spreads across where the data actually lives and the >p99 tail saturates to red."""
    try:
        from pxr import Gf, Sdf
        # Re-assert colormapSource (it can come back empty on a rebuild -> IndeX logs
        # 'Invalid value "" for colormapSource' and renders nothing).
        cm.CreateAttribute("colormapSource", Sdf.ValueTypeNames.String).Set("rgbaPoints")
        #   x=0.00 cold supply  -> deep blue,  nearly transparent (bulk, do not fog)
        #   x=0.18 ~median       -> cyan,        faint
        #   x=0.40 mid           -> green,       low (transition, kept thin)
        #   x=0.62 warm return   -> amber,       rising (the warm-return mode shows up)
        #   x=0.82 hot           -> orange-red,  strong
        #   x=1.00 hot exhaust   -> pure red,    opaque (thin plumes read solid)
        # Stops placed on the field's BIMODAL peaks (cold supply 294-297 K -> x~0.13-
        # 0.32; warm return 302-305 K -> x~0.65-0.85). Alpha is now clearly VISIBLE for
        # both the blue cold aisle and the orange-red hot aisle (the user must SEE the
        # blue-red field) — only the SPARSE green transition (x~0.45) is kept faint to
        # avoid a green haze. Against the dark studio background a translucent saturated
        # volume reads as a coloured GLOW, not fog, so cold blue can carry real alpha.
        cm.GetAttribute("xPoints").Set([0.0, 0.20, 0.45, 0.65, 0.82, 1.0])
        cm.GetAttribute("rgbaPoints").Set([
            Gf.Vec4f(0.10, 0.22, 1.00, 0.18),   # deep blue  -> cold supply, VISIBLE
            Gf.Vec4f(0.00, 0.80, 1.00, 0.22),   # cyan       -> cold peak, clear
            Gf.Vec4f(0.20, 0.90, 0.30, 0.14),   # green      -> sparse transition, faint
            Gf.Vec4f(1.00, 0.75, 0.05, 0.48),   # amber      -> warm-return onset
            Gf.Vec4f(1.00, 0.35, 0.02, 0.82),   # orange-red -> warm peak, strong
            Gf.Vec4f(0.85, 0.00, 0.08, 1.00),   # pure red   -> hot exhaust, opaque
        ])
        if palette in _LUT_STOPS:
            # The editor's palette (its colour bar) with the same alpha valley:
            # only the hue changes, so the volume keeps its clear-room look.
            xs = [0.0, 0.20, 0.45, 0.65, 0.82, 1.0]
            alphas = [0.18, 0.22, 0.14, 0.48, 0.82, 1.00]
            cm.GetAttribute("rgbaPoints").Set([
                Gf.Vec4f(*[c / 255.0 for c in _sample_stops(_LUT_STOPS[palette], x)], a)
                for x, a in zip(xs, alphas)])
    except Exception as e:
        print(f"[dc.cfd_bridge] colormap set failed: {e}")


# Light intensities of the result-volume view (the GTC scene), not of the scene
# surfaces: tuned so a semi-transparent CFD volume reads against a dark backdrop.
_VOLUME_LOOK = {"dome": 140.0, "key": 2500.0, "fill": 450.0}


def _setup_environment(stage):
    """Light the scene so it reads as a clean data hall instead of a black void.

    DCVerse (the reference app) uses a single DistantLight (intensity 3000) +
    PathTracing for bounce. For a real-time streaming demo we keep the RTX
    Real-Time renderer (high FPS) and instead pair a bright white DomeLight
    (soft fill + a light environment background — the "white room" look) with a
    DistantLight key (form + soft shadows). Lights live under /World/Env and are
    always on; geometry stays hidden until loadScene. Authored for a Y-up stage
    in meters."""
    import carb.settings
    from pxr import UsdGeom, UsdLux, Gf
    # A bright "white room" washes out the semi-transparent CFD volume (the core
    # feature) — the proven kit-cae volume recipe uses no dome at all. So we use a
    # darker neutral studio background where the colored volume + streamlines pop,
    # while directional key/fill lights still shape the geometry (not a black void).
    s = carb.settings.get_settings()
    s.set("/rtx/sceneDb/ambientLightIntensity", 0.0)
    # Force the RTX viewport background to a flat constant DARK studio grey. A WHITE
    # background washes out the semi-transparent CFD volume (low contrast -> the
    # temperature cloud "disappears"); a dark neutral backdrop makes the colored
    # volume + the warm/hot plumes POP, which is the whole point of the post-process
    # view. (Earlier this was forced WHITE, contradicting the design note above and
    # making the clipped temperature field nearly invisible.) type enum:
    # 0=None(DomeLight) | 1=Texture | 2=Color. clearColor is a no-op under --no-window.
    _BG = (0.09, 0.10, 0.12)
    s.set("/rtx/background/source/type", 2)
    s.set("/rtx/background/source/color", _BG)
    s.set("/rtx/post/backgroundZeroAlpha/backgroundDefaultColor", _BG)
    s.set("/rtx/post/backgroundZeroAlpha/postTonemapBackgroundComposite", True)
    s.set("/app/window/clearColor", (_BG[0], _BG[1], _BG[2], 1.0))

    UsdGeom.Xform.Define(stage, "/World/Env")

    # The rig's colours, directions and angles are the manifest's Kit rig
    # (textures/materials.json lighting.kit), shared with the editor-scene paths.
    # Only the intensities here are the volume look: a dark studio in which the
    # semi-transparent CFD volume reads (a .cfdproj scene switches to the
    # manifest intensities in _relight_for_cfdproj).
    from . import material_library
    kit = material_library.kit_lighting()

    # Low-intensity constant dome: soft ambient fill only (keeps shadows open)
    # without flooding the scene and drowning the volume.
    dome = UsdLux.DomeLight.Define(stage, "/World/Env/DomeLight")
    dome.CreateIntensityAttr(_VOLUME_LOOK["dome"])
    dome.CreateColorAttr(Gf.Vec3f(*material_library.srgb_to_linear(kit["domeColor"])))
    dome.CreateExposureAttr(0.0)

    for name, prefix in (("KeyLight", "key"), ("FillLight", "fill")):
        # The key shapes the racks; a subtle fill from the opposite side softens the
        # shadow terminator.
        light = UsdLux.DistantLight.Define(stage, f"/World/Env/{name}")
        light.CreateIntensityAttr(_VOLUME_LOOK[prefix])
        light.CreateAngleAttr(float(kit[prefix + "Angle"]))
        light.CreateColorAttr(Gf.Vec3f(*material_library.srgb_to_linear(kit[prefix + "Color"])))
        UsdGeom.Xformable(light).AddRotateXYZOp().Set(
            Gf.Vec3f(*[float(v) for v in kit[prefix + "RotateXYZ"]]))


def _bind_builtin_mdl(stage, prim_path, mtl_name, sub_id, tint=None, opacity=None):
    """Create (once) a builtin-MDL material under /World/Looks and bind it to
    prim_path, overriding any referenced material. OmniPBR.mdl / OmniGlass.mdl
    are Kit builtins (no asset path) so they always resolve offline — unlike the
    remote S3 MDLs some GTC box assets reference."""
    import omni.kit.commands
    from pxr import UsdShade, Sdf, Gf
    mat_path = f"/World/Looks/{mtl_name}"
    if not stage.GetPrimAtPath(mat_path).IsValid():
        omni.kit.commands.execute(
            "CreateMdlMaterialPrimCommand",
            mtl_url=f"{sub_id}.mdl", mtl_name=sub_id, mtl_path=mat_path,
        )
        shader = stage.GetPrimAtPath(f"{mat_path}/Shader")
        if shader and shader.IsValid():
            sh = UsdShade.Shader(shader)
            if tint is not None:
                inp = "diffuse_tint" if sub_id == "OmniPBR" else "glass_color"
                sh.CreateInput(inp, Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*tint))
            if opacity is not None and sub_id == "OmniPBR":
                sh.CreateInput("enable_opacity", Sdf.ValueTypeNames.Bool).Set(True)
                sh.CreateInput("opacity_constant", Sdf.ValueTypeNames.Float).Set(float(opacity))
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    mat = UsdShade.Material(stage.GetPrimAtPath(mat_path))
    api = UsdShade.MaterialBindingAPI.Apply(prim)
    api.Bind(mat, bindingStrength=UsdShade.Tokens.strongerThanDescendants)

# ── Model bbox + files (verbatim from kit-cae load_gtc_final.py) ────────────
MODEL_BBOX = {
    "Rack":      {"min": (-28.290, -15.000, 0.0),       "max": (28.290, 15.000, 79.024)},
    "ACU":       {"min": (-49.7500, -111.5000, 0.4954),  "max": (49.7500, 127.2854, 201.7466)},
    "Box_B":     {"min": (-2.5500, -2.5500, -0.0001),    "max": (2.5500, 2.5500, 5.0500)},
    "Box_Glass": {"min": (-2.5500, -2.5500, -0.0001),    "max": (2.5500, 2.5500, 5.0500)},
    "Box_Duct":  {"min": (-2.5500, -2.5000, -0.0001),    "max": (2.5500, 2.5000, 5.0500)},
    "Room":      {"min": (0.0, 0.0, 0.0),                "max": (2.0, 2.0, 2.0)},
    "Floor":     {"min": (-1.0, 0.0, -1.0),              "max": (1.0, 0.0001, 1.0)},
}
# Class scopes the cfdproj path creates, in tree order (mirrors cfdproj.CLASSES).
_CFDPROJ_GROUPS = ("Floors", "Walls", "Baffles", "Boxes", "Acus", "Racks")

MODEL_FILES = {
    "Rack": "Optimize_Rack.usd", "ACU": "ACU_Offset.usd",
    "Box_duct": "box_Duct.usd", "Box_containment": "box_usd_Glass.usd",
    "Box_other": "box_usd_B.usd", "Room": "Room_Light.usd", "Floor": "Floor.usdz",
}


def _place_model(stage, geo_dir, prim_path, model_file, target_min, target_size,
                 orientation=0, bbox=None, instanceable=False, transpose=False,
                 base_yaw=90.0):
    """Reference `model_file` at `prim_path`, fit to the target world AABB.

    model_file may be a bare name (resolved against geo_dir) or an absolute path
    (e.g. the GB300 NVL72 asset that lives outside geo_dir). `bbox` overrides the
    hardcoded MODEL_BBOX lookup with an explicit ((minx,miny,minz),(maxx,maxy,maxz))
    in the asset's NATIVE units. `instanceable` flags the referenced child as a
    USD instance so 50 copies of a heavy asset share one GPU prototype.
    """
    from pxr import UsdGeom, Gf
    model_path = model_file if os.path.isabs(model_file) else os.path.join(geo_dir, model_file)
    if not os.path.exists(model_path):
        return None
    if bbox is not None:
        m_min, m_max = bbox
    else:
        bbox_key = next((k for k, v in MODEL_FILES.items() if v == model_file), None)
        # All box variants (Box_containment / Box_duct / Box_other) share the one
        # measured box bbox under the "Box_B" key. Without this fallback the lookup
        # misses and uses a (2,2,2) unit cube, scaling every box ~2.55x too large
        # (oversized partitions/ducts that burst out of the room). Mirrors
        # load_gtc_final.py.
        if bbox_key and bbox_key in MODEL_BBOX:
            bb = MODEL_BBOX[bbox_key]
        elif bbox_key and "Box" in bbox_key:
            bb = MODEL_BBOX["Box_B"]
        else:
            bb = {"min": (-1, -1, -1), "max": (1, 1, 1)}
        m_min, m_max = bb["min"], bb["max"]
    m_size = (m_max[0]-m_min[0], m_max[1]-m_min[1], m_max[2]-m_min[2])
    m_center = ((m_min[0]+m_max[0])/2, (m_min[1]+m_max[1])/2, (m_min[2]+m_max[2])/2)
    is_gb300 = os.path.basename(model_path).lower() == "gb300.usd"
    if is_gb300:
        # GB300 base yaw is env-tunable (back-compat); GB300 always transposes.
        try:
            base_yaw = float(os.environ.get("DC_CFD_GB300_BASE_YAW", "90"))
        except ValueError:
            base_yaw = 90.0
        transpose = True
    if transpose:
        # The asset's native footprint is rotated 90deg vs the JSON slot: its NARROW
        # axis (native Y) is the cabinet WIDTH and its LONG axis (native X) is the
        # DEPTH. Fill the slot's X (width) from the asset's Y and the slot's Y (depth)
        # from the asset's X, then rotate +base_yaw about Z (applied OUTSIDE the scale
        # by op order, so it commutes) — together this lands the final footprint at
        # exactly (slotX, slotY) with the rack's real proportions intact. The raw
        # (non-transpose) fit instead crushes width ~0.4x and stretches depth ~1.6x
        # (a squashed, unrecognisable rack). Used for GB300 and Optimize_Rack.usd.
        sx = target_size[0] / max(m_size[1], 0.001)
        sy = target_size[1] / max(m_size[0], 0.001)
        sz = target_size[2] / max(m_size[2], 0.001)
    else:
        sx = target_size[0] / max(m_size[0], 0.001)
        sy = target_size[1] / max(m_size[1], 0.001)
        sz = target_size[2] / max(m_size[2], 0.001)
    tcx = target_min[0] + target_size[0] / 2
    tcy = target_min[1] + target_size[1] / 2
    tcz = target_min[2] + target_size[2] / 2
    if orientation != 0:
        rad = math.radians(orientation)
        dx, dy = tcx - target_min[0], tcy - target_min[1]
        tcx = target_min[0] + dx * math.cos(rad) - dy * math.sin(rad)
        tcy = target_min[1] + dx * math.sin(rad) + dy * math.cos(rad)
    outer = UsdGeom.Xform.Define(stage, prim_path)
    outer.AddTranslateOp().Set(Gf.Vec3d(tcx, tcy, tcz))
    if orientation != 0:
        outer.AddRotateZOp().Set(float(orientation))
    inner_path = prim_path + "/_model"
    inner = UsdGeom.Xform.Define(stage, inner_path)
    inner.AddScaleOp().Set(Gf.Vec3d(sx, sy, sz))
    if transpose:
        # xformOpOrder [scale, rotateZ, translate] => point order centre -> rotate
        # -> scale: the asset is centred, rotated +base_yaw about Z so its front
        # aligns with the slot depth axis, then fit-scaled (transposed above).
        # base_yaw is env-tuned per asset for fine aisle/facing alignment.
        inner.AddRotateZOp().Set(float(base_yaw))
    inner.AddTranslateOp().Set(Gf.Vec3d(-m_center[0], -m_center[1], -m_center[2]))
    if instanceable:
        # Put the reference + instanceable flag on a CLEAN child Xform that carries
        # NO local xformOps, so USD/Hydra collapses the 50 GB300 copies to ONE
        # prototype. When the reference + instanceable sat on the SAME prim as the
        # bbox-fit scale/translate, Hydra did not collapse them and RSS grew per
        # instance (~2 GB each -> ~100 GB at 50). The fit scale/translate stay on
        # the parent _model Xform. We still reference the DEFAULT prim (GB300's
        # /World is identity and wraps the rack as a child whose own xformOps +
        # cm->m unitsResolve must be preserved — referencing /World/GB300 directly
        # would let local xformOps override those and break the geometry).
        ref_prim = UsdGeom.Xform.Define(stage, inner_path + "/_ref").GetPrim()
        ref_prim.GetReferences().AddReference(model_path)
        ref_prim.SetInstanceable(True)
    else:
        stage.GetPrimAtPath(inner_path).GetReferences().AddReference(model_path)
    return outer


# Ground grid. Two implementations; the USD one is the default and the NATIVE Kit
# viewport grid is opt-in via DC_CFD_NATIVE_GRID=1.
#
# MEASURED, 2026-08-08, on this app (kit-cae streaming, --no-window, WebRTC), because the
# comment that used to sit here was wrong and worth replacing with numbers:
#
#   * The native grid DOES render in the headless stream. The old claim that it "renders
#     only its axis lines, no cells" is FALSE — at scale 10 / spacing 1 / plane XZ it
#     draws a full major+minor grid, confirmed in a captured frame.
#   * lineWidth works: sweeping 1 -> 3 -> 6 moved the lit-pixel count 193.7k -> 199.5k
#     -> 210.9k, monotonically. Colour and plane work too.
#   * What does NOT work is the CELL SIZE this demo needs. Cell size tracks
#     spacing x scale, and only ~10 m cells draw at all: at scale 10, spacing 1.0 renders
#     (197.7k lit px) while 0.5, 0.3, 0.2, 0.1 and 0.06 all render nothing (177.9k /
#     178.1k / 178.0k / 177.6k / 177.6k, against a 177.3k empty-frame baseline). Varying
#     `scale` instead (1, 20, 40, 60, 100) only ever gives the origin axes.
#
# A 10 m cell is larger than most rooms here — Base_leakage is 4.4 x 6.0 m, so the whole
# room fits inside one cell and the floor reference disappears. That is why the USD grid
# stays the default despite being the heavier implementation. Flip with
# DC_CFD_NATIVE_GRID=1 to get the real OV viewport grid; `setGrid` retunes it live.
NATIVE_GRID = os.environ.get("DC_CFD_NATIVE_GRID", "0") not in ("0", "", "false", "False")
USD_GRID = not NATIVE_GRID
# Only the corner of the parameter space that was measured to render.
NATIVE_GRID_DEFAULTS = {
    "enabled": True,
    "plane": "XZ",          # this stage is Y-UP; the app default XY stands it on edge
    "scale": 10.0,          # smaller values draw the origin axes and nothing else
    "spacing": 1.0,         # x scale = 10 m cells; finer values draw nothing at all
    "majorInterval": 5,
    "lineWidth": 2.0,
    # The app default 0.32 grey is nearly black at this scene's exposure (-1.9).
    "lineColor": [0.62, 0.64, 0.70],
    "fadeStart": 40.0,
    "fadeEnd": 80.0,
    "showOrigin": True,
    # OFF deliberately: trackCamera lets Kit re-pick the grid PLANE from the stage/camera,
    # and this stage is Y-up while the app's own default is Z-up. Pinning plane=XZ and
    # leaving this off is what keeps the grid on the floor instead of on edge.
    "trackCamera": False,
}


def _setup_ground_grid(stage, x0, x1, z0, z1):
    """Turn on the ground grid. Returns a prim path only when the USD fallback is used.

    The return value feeds `mats["grid"]` -> `scene.grid_path`, which the reveal/reset
    paths use to show and hide the grid. The native grid has no prim, so returning None
    is correct and those paths already skip a falsy grid_path.
    """
    if USD_GRID:
        try:
            return _build_floor_grid(stage, x0, x1, z0, z1)
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] USD floor grid ERROR: {e}")
            return None
    try:
        from . import handlers as _h
        _h.handle_set_grid(None, stage, **NATIVE_GRID_DEFAULTS)
    except Exception as e:  # noqa: BLE001
        print(f"[dc.cfd_bridge] native grid ERROR: {e}")
    return None


def _build_floor_grid(stage, xmin, xmax, zmin, zmax, tile=1.0, major=5.0, y=0.005,
                      margin_factor=0.7,
                      minor_color=(0.26, 0.27, 0.31), major_color=(0.44, 0.46, 0.52),
                      minor_w=0.018, major_w=0.035, path="/World/FloorGrid"):
    """Omniverse-style ground grid as a USD BasisCurves overlay.

    Used because the NATIVE viewport grid cannot make cells this small — NOT because it
    cannot render. (An earlier version of this docstring claimed the native grid draws
    only axis lines under --no-window. That was never measured and is false; see
    NATIVE_GRID_DEFAULTS above for the frames that disprove it. What is true is that its
    cells stop drawing below ~10 m, which is bigger than most rooms here.) So we draw the
    grid ourselves to reproduce the OV ground-grid look:
      * EXTENDS well past the room footprint (by margin_factor x its span) so it reads
        as an infinite ground plane, not a box around the racks;
      * TWO-TIER: a faint MINOR grid every `tile` m + brighter MAJOR lines every
        `major` m (the classic OV major/minor look);
      * BRIGHTENED for the dark studio background (the old 0.10 grey was invisible);
      * FADES toward the far edge via per-vertex displayOpacity (no hard cutoff);
      * FIXED cell size (never zoom-adaptive) so a cell is ALWAYS far smaller than the
        room — addressing 'a cell must not be bigger than the room'.
    Bounds come from the JSON room dims (X=width, Z=depth) in STAGE world space, not a
    BBoxCache read (payloads may be unloaded at build time). Authored as a SIBLING
    under /World so it is neither double-transformed by the geo root nor hidden by the
    geometry MakeInvisible()."""
    import math
    from pxr import UsdGeom, Gf, Vt, Sdf
    span = max(xmax - xmin, zmax - zmin)
    mg = span * margin_factor
    x0 = math.floor((xmin - mg) / major) * major
    x1 = math.ceil((xmax + mg) / major) * major
    z0 = math.floor((zmin - mg) / major) * major
    z1 = math.ceil((zmax + mg) / major) * major

    def fade(px, pz):
        # 1.0 across the room footprint, easing to a faint floor (0.12) at the edge so
        # far lines stay subtly visible like the OV grid horizon rather than cutting off.
        dx = max(xmin - px, 0.0, px - xmax)
        dz = max(zmin - pz, 0.0, pz - zmax)
        d = math.hypot(dx, dz)
        if d <= 0.0 or mg <= 0.0:
            return 1.0
        t = max(0.0, 1.0 - d / mg)
        return 0.12 + 0.88 * (t * t)

    def is_major(v):
        return abs(v / major - round(v / major)) < 1e-6

    pts, counts, colors, widths, opac = [], [], [], [], []

    def add(p0, p1, mj):
        # NOTE: .extend()/.append() mutate the enclosing lists in place. Using `+=`
        # here would REBIND the names as locals (augmented assignment) and raise
        # 'cannot access local variable' — the bug that made the grid never build.
        col = major_color if mj else minor_color
        w = major_w if mj else minor_w
        pts.extend([Gf.Vec3f(*p0), Gf.Vec3f(*p1)]); counts.append(2)
        colors.extend([Gf.Vec3f(*col), Gf.Vec3f(*col)])
        widths.extend([w, w])
        opac.extend([fade(p0[0], p0[2]), fade(p1[0], p1[2])])

    nx = int(round((x1 - x0) / tile))
    for i in range(nx + 1):
        x = x0 + i * tile
        add((x, y, z0), (x, y, z1), is_major(x))
    nz = int(round((z1 - z0) / tile))
    for j in range(nz + 1):
        z = z0 + j * tile
        add((x0, y, z), (x1, y, z), is_major(z))

    c = UsdGeom.BasisCurves.Define(stage, path)
    c.CreateTypeAttr("linear")
    c.CreatePointsAttr(Vt.Vec3fArray(pts))
    c.CreateCurveVertexCountsAttr(Vt.IntArray(counts))
    c.CreateWidthsAttr(Vt.FloatArray(widths))
    c.SetWidthsInterpolation(UsdGeom.Tokens.vertex)
    cp = c.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex)
    cp.Set(Vt.Vec3fArray(colors))
    op = c.CreateDisplayOpacityPrimvar(UsdGeom.Tokens.vertex)
    op.Set(Vt.FloatArray(opac))
    prim = c.GetPrim()
    prim.CreateAttribute("primvars:doNotCastShadows", Sdf.ValueTypeNames.Bool).Set(True)
    print(f"[dc.cfd_bridge] floor grid: X[{x0:.1f},{x1:.1f}] Z[{z0:.1f},{z1:.1f}] "
          f"minor {tile}m / major {major}m ({len(counts)} lines)")
    return path


def _place_geometry_from_json(stage, geo_dir, jd):
    from pxr import UsdGeom, Gf
    geo_root = "/World/Geometry"
    models = jd.get("models", {}).get("geometryModels", {})
    rack_models = models.get("racks", {})
    acu_models = models.get("acus", {})
    geometry = jd.get("geometry", {})
    constructions = jd.get("constructions", {})
    plane = geometry.get("plane", [])
    xs = [p["x"] for p in plane]; ys = [p["y"] for p in plane]
    x_min, x_max = min(xs), max(xs); y_min, y_max = min(ys), max(ys)
    room_w, room_d = x_max - x_min, y_max - y_min
    # The geometry below is authored in the GTC layout's native Z-up frame
    # (footprint x,y; height z). The CFD .vtu is Y-up (height y; depth z). Reorient
    # the whole geometry root to Y-up so it registers with the volume/ROI:
    # RotateX(-90) maps +Z(up) -> +Y(up); TranslateZ(+y_max) brings the footprint
    # depth axis back to [0, depth]. (Stage is set Y-up in build().)
    geo = UsdGeom.Xform.Define(stage, geo_root)
    geo.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, y_max))
    geo.AddRotateXOp().Set(-90.0)
    room_h = geometry.get("height", 5.0)
    UsdGeom.Scope.Define(stage, f"{geo_root}/Room")
    _place_model(stage, geo_dir, f"{geo_root}/Room/Shell", "Room_Light.usd",
                 (x_min, y_min, 0), (room_w, room_d, room_h))
    # Floor: Floor.usdz is authored FLAT in the X-Z plane (Y-up normal, ~0 native
    # thickness on Y), unlike the Z-up rack/ACU/room assets. Under the geo-root's
    # RotateX(-90) it would stand on edge as a thin invisible sliver (the reported
    # "floor not visible"). Place it in the Y-up WORLD frame at /World/Floor instead,
    # where its native X-Z-flat orientation is already a proper floor. The geo-root
    # maps geo-local (gx,gy)->world (gx,0,room_d-gy), so world X in [0,room_w], Z in
    # [0,room_d] coincides with the geometry footprint. The 0.05 m thickness is the
    # MIDDLE (Y) target component, so the per-axis fit no longer collapses in-plane Z.
    floor_path = "/World/Floor"
    _place_model(stage, geo_dir, floor_path, "Floor.usdz",
                 (x_min, -0.03, y_min), (room_w, 0.05, room_d))
    # Ground grid — see _setup_ground_grid. USD by default, native on
    # DC_CFD_NATIVE_GRID=1; the measurements behind that choice are recorded there.
    grid_path = _setup_ground_grid(stage, x_min, x_max, 0.0, room_d)
    # Rack asset. DEFAULT = the CANONICAL clean full-height rack Optimize_Rack.usd
    # (native 1.44 x 0.76 x 2.01 m, sits on the floor), placed with transpose-fit
    # (its footprint is rotated 90deg vs the JSON slot). NOT Optimize_Rack_45C, an
    # open-door/exploded variant (10.7 m tall, geometry hanging below the floor) that
    # force-fit into a 2.4 m slot rendered as an unrecognisable blob ("wrong rack").
    # The GB300 NVL72 model (golden inlet panels, from
    # the kit-cae simulation-in-context example) is a 115 MB photoreal asset;
    # referencing it 50x runs the build out of RAM (a confirmed >20 GB runaway, even
    # instanced), so GB300 is OPT-IN and gated to a small "hero" subset:
    #   DC_CFD_RACK_USD=/abs/path/GB300.usd  -> use GB300 for the hero racks
    #   DC_CFD_GB300_COUNT=N                  -> how many front racks get GB300 (default 6)
    # GB300 is Z-up like the GTC racks and force-fit (non-uniform) into each JSON
    # rack footprint, so its wide cabinet reads as a standard rack slot.
    GB300_BBOX = ((-99.4037, -35.44, -13.9436), (62.83, 30.25, 230.6797))
    rack_usd = os.environ.get("DC_CFD_RACK_USD", "").strip()
    gb300_path = rack_usd if os.path.basename(rack_usd).lower() == "gb300.usd" else ""
    if gb300_path and not os.path.exists(gb300_path):
        print(f"[dc.cfd_bridge] GB300 asset missing ({gb300_path}); GTC racks only")
        gb300_path = ""
    try:
        gb300_count = int(os.environ.get("DC_CFD_GB300_COUNT", "6"))
    except ValueError:
        gb300_count = 6
    # GB300 inlet alignment is handled INSIDE _place_model (a +90deg base rotation
    # that maps the asset's +X inlet onto the slot's +Y depth axis, tunable via
    # DC_CFD_GB300_BASE_YAW). So each rack just uses its own JSON orientation here —
    # no per-rack yaw hack. This keeps both rows' inlets facing their cold aisle.
    base_yaw = os.environ.get("DC_CFD_GB300_BASE_YAW", "90")
    try:
        rack_base_yaw = float(os.environ.get("DC_CFD_RACK_BASE_YAW", "90"))
    except ValueError:
        rack_base_yaw = 90.0
    print(f"[dc.cfd_bridge] racks: Optimize_Rack.usd (transpose-fit base_yaw={rack_base_yaw}) "
          f"+ GB300 hero={'yes' if gb300_path else 'no'} count={gb300_count if gb300_path else 0}")
    UsdGeom.Scope.Define(stage, f"{geo_root}/Racks")
    racks = []  # (safe_name, prim_path) in JSON order — drives the per-rack tree
    for i, (rname, rdata) in enumerate(constructions.get("racks", {}).items()):
        rg = rdata.get("geometry", {})
        md = rack_models.get(rg.get("model", "RACK"), {})
        sz = md.get("size", {"x": 0.6, "y": 1.2, "z": 2.4})
        loc = rg.get("location", {})
        use_gb = bool(gb300_path) and i < gb300_count
        ori = rg.get("orientation", 0)
        safe = rname.replace('-', '_')
        rack_path = f"{geo_root}/Racks/{safe}"
        _place_model(stage, geo_dir, rack_path,
                     gb300_path if use_gb else "Optimize_Rack.usd",
                     (loc.get("x", 0), loc.get("y", 0), loc.get("z", 0)),
                     (sz["x"], sz["y"], sz["z"]), ori,
                     bbox=GB300_BBOX if use_gb else None, instanceable=use_gb,
                     transpose=not use_gb, base_yaw=rack_base_yaw)
        racks.append((safe, rack_path))
    UsdGeom.Scope.Define(stage, f"{geo_root}/ACUs")
    for aname, adata in constructions.get("acus", {}).items():
        ag = adata.get("geometry", {})
        md = acu_models.get(ag.get("model", "CWU"), {})
        sz = md.get("size", {"x": 2.8, "y": 1.6, "z": 3.2})
        loc = ag.get("location", {})
        _place_model(stage, geo_dir, f"{geo_root}/ACUs/{aname.replace('-', '_')}",
                     "ACU_Offset.usd",
                     (loc.get("x", 0), loc.get("y", 0), loc.get("z", 0)),
                     (sz["x"], sz["y"], sz["z"]), ag.get("orientation", 0))
    UsdGeom.Scope.Define(stage, f"{geo_root}/Boxes")
    # The GTC "boxes" are a grab-bag: cold-aisle containment (glass), overhead
    # ducts, thin fan-wall partitions, and structural pillars. They all reference
    # box_*.usd assets whose remote-S3 MDL fails offline, so every kind needs a
    # local material override to avoid rendering as a solid white slab.
    boxes = {"containment": [], "duct": [], "partition": [], "other": []}
    for bname, bdata in constructions.get("boxes", {}).items():
        bg = bdata.get("geometry", {}); loc = bg.get("location", {}); bsz = bg.get("size", {})
        if "containment" in bname:
            kind, mf = "containment", "box_usd_Glass.usd"
        elif "duct" in bname:
            kind, mf = "duct", "box_Duct.usd"
        elif "partition" in bname:
            kind, mf = "partition", "box_usd_B.usd"
        else:
            kind, mf = "other", "box_usd_B.usd"
        box_path = f"{geo_root}/Boxes/{bname.replace('-', '_')}"
        tmin = (loc.get("x", 0), loc.get("y", 0), loc.get("z", 0))
        tsize = (max(bsz.get("x", 0.1), 0.02), max(bsz.get("y", 0.1), 0.02),
                 max(bsz.get("z", 0.1), 0.02))
        # NOTE: no per-containment X offset. The GTC JSON already authors the
        # cold-aisle caps airtight against the racks — e.g. rack_1 spans x[6.8,7.4],
        # cap containment_1 spans x[7.4,8.2], rack_2 spans x[8.2,8.8] (caps fill the
        # 0.8 m gaps on the 1.4 m rack pitch). The earlier global +0.8 m shift broke
        # that (it slid BOTH the 0.8 m end-caps AND the 0.1 m aisle side walls), so we
        # trust the authored location like DCVerse placement does.
        if kind == "duct" and os.environ.get("DC_CFD_VERTICAL_BAFFLES", "1") == "1":
            # Re-stand the horizontal aisle-cap duct as a VERTICAL baffle (user
            # request): keep the long run (x) and footprint, make it thin across the
            # aisle and tall up to the ceiling, so the aisle top is no longer capped
            # and hot air can rise/return. First-pass geometry; refine per feedback.
            base_z = loc.get("z", 0.0)
            tsize = (tsize[0], 0.2, max(room_h - base_z, 1.5))
        _place_model(stage, geo_dir, box_path, mf, tmin, tsize)
        boxes[kind].append(box_path)
    return {
        "room": f"{geo_root}/Room/Shell",
        "floor": floor_path,
        "boxes": boxes,
        "racks": racks,
        "grid": grid_path,
    }


class _CfdprojAlreadyBound(Exception):
    """Control-flow sentinel: the cfdproj path bound its own materials.

    The GTC material-override block below is one long try/except that must keep
    catching real errors, so skipping it cleanly from the top is easier to read than
    re-indenting ~30 lines under a condition.
    """


from .slice_render import PALETTE_STOPS as _LUT_STOPS  # noqa: E402  (pure module)


def _sample_stops(stops, t):
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return tuple(a + (b - a) * f for a, b in zip(c0, c1))
    return stops[-1][1]


def _palette_lut(palette):
    """A 256x1 PNG LUT for kit-cae's ScalarColor material, written once to the
    bridge cache: kit-cae ships no cool-to-warm / jet / viridis, the editor's
    palettes, so the OV colours match the Three.js ones."""
    import struct, zlib
    from .owned_paths import bridge_cache_directory
    name = palette if palette in _LUT_STOPS else "coolwarm"
    path = os.path.join(bridge_cache_directory(), f"lut_{name}.png")
    if os.path.isfile(path):
        return path
    stops = _LUT_STOPS[name]
    row = bytearray([0])
    for i in range(256):
        t = i / 255.0
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                row += bytes(int(round(a + (b - a) * f)) for a, b in zip(c0, c1))
                break
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 1, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(row))) + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb") as fh:
        fh.write(png)
    os.replace(path + ".tmp", path)
    return path


class CfdScene:
    # kit-cae Faces operator: the coloured boundary surface (setSurfaceDisplay)
    surface_path = "/World/CAE/SurfaceField"
    surface_field = "off"
    # boundary patches of the loaded result (.vtp), split off in _convert_case
    surface_vtp_path = None
    streamline_domain_override = None
    streamline_palette = None
    volume_palette = None
    slice_palette = None
    slice_show_disc = True
    _surface_hidden = ()

    def __init__(self, vtu_path, json_path, geo_dir, ranges_path=""):
        self.vtu_path = vtu_path
        self.json_path = json_path
        self.geo_dir = geo_dir
        self.ranges_path = ranges_path
        self.dataset_path = None
        self.t_field = None
        self.u_field = None
        # Pre-computed SCALAR velocity magnitude field. The CaeViz volume's
        # vector_magnitude voxelisation of the raw vec3 U produces a UNIFORM constant
        # block (broken — the scalar path used for T works, the vector path does not),
        # so the data file carries a real scalar 'Umag' array we voxelise as a plain
        # scalar (mode=unchanged), exactly like T. Falls back to vector_magnitude on U
        # if a dataset lacks Umag.
        self.umag_field = None
        self.volume_path = "/World/CAE/InternalVolume"
        # Each field/ROI rebuild creates the volume on a FRESH, never-used prim path
        # (InternalVolume_<seq>) so its first exec is a genuine INITIAL rebuild; the old
        # volume is retired afterwards. self.volume_path always points at the LIVE one.
        self._vol_seq = 0
        self.roi_path = "/World/CAE/BoundingBox"
        self.colormap_path = self.volume_path + "/Material/Colormap"
        # Default physical ranges for the GTC internal.vti. T over VALID (unmasked)
        # points spans ~268.6..310.9 K; the raw array min reads 0.0 only from masked
        # ghost points (vtkValidPointMask), so we pin the valid physical band rather
        # than the raw min. |U| spans 0..8.1 m/s. Overridden per-dataset by an
        # external ranges JSON when present.
        self.ranges = {"T": (268.6, 310.9), "Umag": (0.0, 8.1)}
        # Natural (identity-transform) world bounds of the ROI box, captured at
        # build time so handle_set_roi_box can resize it deterministically
        # (absolute target AABB, no compounding scale across repeated calls).
        self.roi_base_min = None
        self.roi_base_max = None
        # Volume state. kit-cae voxelises once at creation, so switching the field
        # or toggling the ROI clip requires a fresh volume; we track the desired
        # state here and rebuild on demand (see _rebuild_volume). _volume_busy
        # serialises rebuilds so rapid clicks cannot race.
        self.active_field = "T"
        self.clip_enabled = False
        self._volume_busy = False
        self._pending = None  # latest (field, roi_enabled, make_visible) request
        self._loading = False  # serialises runtime dataset swaps (load_dataset)
        # Named geometry groups (USD model kind -> list of prim paths), populated
        # at build time. Drives the web object list + per-group visibility toggle.
        self.geo_groups = {}
        self.rack_names = []
        self.scene_manifest = None    # model-tree manifest for the web UI
        self.disabled_prims = set()   # prims the source project marks disabled  # individual rack prim names (per-rack tree order)
        self._built = False   # True once build() has placed the scene geometry
        # ── Streamlines (native kit-cae CreateCaeVizStreamlines, GPU Warp RK4(5)) ──
        # A BasisCurves viz INTEGRATED on the vec3 U field and COLOURED by a scalar
        # field (T by default, |U| via the toggle — the colours rel just retargets;
        # the ScalarColor MDL material auto-scales via RescaleRangeAPI). Seeds are the
        # vertices of a draggable UnitSphere preview, so seed COUNT maps to the sphere
        # tessellation resolution (~resolution^2 vertices). ROI-IS-GOD: when the clip
        # is on, BOTH the seeds and the integrated lines stay inside the ROI box — the
        # nanovdb voxelisation clips the input grid to the box (lines cannot leave it),
        # and the seed centre/radius are clamped into the box so every seed starts
        # inside. Unlike the IndeX volume, BasisCurves are not IndeX prims, so the
        # streamlines are simply removed and recreated on each Apply (no rotating-path
        # / composite-off retire dance is needed).
        # The seed sphere is SPLIT in two so the visible ball is always smooth while the
        # seed COUNT (= tessellation) can stay low: (1) SeedSphere = the draggable, smooth,
        # translucent VISUAL preview (fixed high resolution — a real-looking ball); (2)
        # SeedPoints = an INVISIBLE UnitSphere whose ~res² surface vertices ARE the actual
        # streamline seeds (resolution derived from n_seeds). Both are co-located.
        self.seed_sphere_path = "/World/CAE/SeedSphere"   # smooth visual preview
        self.seed_source_path = "/World/CAE/SeedPoints"   # invisible seed vertices
        self.streamlines_path = "/World/CAE/Streamlines"
        self.streamlines_enabled = False
        self.streamline_field = "T"      # colour field; integration ALWAYS uses vec3 U
        self.seed_center = [0.0, 0.0, 0.0]   # set from the dataset AABB centre at ingest
        self.seed_radius = 1.0
        self.n_seeds = 27
        self.sl_max_steps = 500
        self.sl_direction = "forward"    # "forward" | "backward" | "both"
        self.sl_width = 0.04
        self.seed_visible = False        # frontend-driven visibility of the visual preview
        self._seed_res = 0               # current SEED-SOURCE tessellation (recreate on change)
        # Drag arbitration for the seed sphere (see adopt_dragged_seed). _commanded is
        # where WE last placed the prim; _ui is the last centre the frontend sent. If the
        # prim's live translation has drifted from _commanded, the user dragged it in the
        # viewport and that position wins over the frontend's (stale) numbers.
        self._seed_commanded_center = None
        self._seed_ui_center = None
        self._sl_busy = False            # serialises streamline (re)generation
        self._sl_pending = False         # a regen was requested while one was running
        # Fixed tessellation of the VISUAL preview sphere — high enough to read as a smooth
        # ball regardless of the seed count (which only drives the invisible SeedPoints).
        self._visual_seed_res = 32
        # Surface slice (display-only, Stage 3): a plane cut of the loaded
        # dataset, coloured by a scalar field and clipped to the preview disc.
        # The disc gizmo is draggable; adoption mirrors the seed sphere's
        # "viewport wins over stale UI numbers" arbitration.
        self.slice_visible = False
        self.slice_origin = [0.0, 0.0, 0.0]
        self.slice_normal = [0.0, 1.0, 0.0]
        self.slice_radius = 1.0
        self.slice_field = "T"
        self.slice_domain = None
        self._slice_cache_key = None
        self._slice_summary = None
        self._slice_grid = None  # (dataset path, raw vtk grid) — re-cuts skip the read
        # OV-display volume: per-field colour domain chosen from the loaded
        # dataset (acked setVolumeDisplay). Empty = the pinned reference domain.
        self._volume_domain_override = {}
        self._slice_commanded_origin = None
        self._slice_ui_origin = None

    def _load_ranges(self):
        if self.ranges_path and os.path.exists(self.ranges_path):
            with open(self.ranges_path) as f:
                r = json.load(f)
                self.ranges = {k: tuple(v) for k, v in r.items()}

    async def build(self, json_path=None):
        """Place the data-center geometry from a scene-definition JSON. Called by
        loadScene (NOT at startup). ``json_path`` overrides the configured default;
        new_stage_async() resets the stage so re-building with a different JSON is a
        clean rebuild. Geometry is left HIDDEN; handle_load_scene reveals it."""
        import omni.usd
        from omni.cae.testing import wait_for_update
        from pxr import UsdGeom

        if json_path:
            self.json_path = json_path
        # A rebuild starts from a fresh stage, so any previously loaded dataset is
        # gone — reset the dataset-derived state so the gated UI requires a reload.
        self._built = False
        self.dataset_path = None
        self.t_field = self.u_field = self.umag_field = None
        self.roi_base_min = self.roi_base_max = None
        self.streamlines_enabled = False
        self.seed_visible = False
        self._seed_res = 0
        self._load_ranges()
        ctx = omni.usd.get_context()
        await ctx.new_stage_async()
        stage = ctx.get_stage()
        # Y-up stage: matches the CFD .vtu's native frame (height along Y). The GTC
        # geometry, authored Z-up, is reoriented to Y-up at its /World/Geometry root.
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        UsdGeom.Xform.Define(stage, "/World")
        stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
        # Light the hall (dome + key/fill) so it is not a black void. The new
        # stage also carries Kit's template sun (/Environment/defaultLight), which
        # would light the hall a second time from the side; only /World/Env lights it.
        _setup_environment(stage)
        from .render_scene import retire_foreign_lights
        retired = retire_foreign_lights(stage, "/World/Env")
        if retired:
            print(f"[dc.cfd_bridge] retired stage-template lights: {retired}")
        for _ in range(5):
            await wait_for_update()

        # Second geometry source: a .cfdproj straight out of the STLGen editor, with
        # geometry AUTHORED at true size instead of a fixed asset scaled per-axis into
        # an AABB. Chosen the SAME way as the GTC scene — the user picks a file in the
        # "Load CFD Geometry" dialog — so the source is a runtime choice, not a startup
        # env var. (An earlier revision gated it on DC_CFD_CFDPROJ, which never fired:
        # the dialog always overwrites self.json_path with whatever was picked.)
        # Everything after this block (grid, hide-until-loadScene, the ROI box,
        # self._built) is SHARED — an early return would skip the ROI and leave the
        # scene marked unbuilt.
        mats = None
        if str(self.json_path).lower().endswith(".cfdproj"):
            mats = await self._build_from_cfdproj(stage, self.json_path)
            if mats is None:
                print("[dc.cfd_bridge] cfdproj build failed; falling back to the GTC JSON")
                self.json_path = os.environ.get("DC_CFD_JSON", self.json_path)

        if mats is None:
            with open(self.json_path) as f:
                jd = json.load(f)
            mats = _place_geometry_from_json(stage, self.geo_dir, jd)
        # Bind offline-safe builtin materials (all GTC box/room assets reference
        # unreachable remote MDLs or are plain grey). Thin fan-wall partitions are
        # made semi-transparent so they read as boundaries instead of dominating
        # the view as solid white slabs.
        # The cfdproj path already authored per-object materials from the shared material
        # manifest and filled geo_groups, so it skips all of this: the remote-MDL breakage
        # the block below works around is a property of the stock GTC assets, not of
        # generated geometry.
        try:
            if mats.get("source") == "cfdproj":
                raise _CfdprojAlreadyBound
            b = mats["boxes"]
            # Room shell is a closed 5-face box; opaque it seals the hall and
            # hides every rack + the CFD volume. Render it as a faint white glass
            # envelope (low opacity) so the "white room" context stays but the
            # camera sees straight into the hall. Floor stays opaque (the ground).
            _bind_builtin_mdl(stage, mats["room"], "RoomShell", "OmniPBR", tint=(0.88, 0.89, 0.92), opacity=0.10)
            _bind_builtin_mdl(stage, mats["floor"], "FloorConcrete", "OmniPBR", tint=(0.30, 0.31, 0.34))
            for p in b["containment"]:
                _bind_builtin_mdl(stage, p, "ContainmentGlass", "OmniGlass", tint=(0.62, 0.80, 0.92))
            for p in b["partition"]:
                _bind_builtin_mdl(stage, p, "PartitionPanel", "OmniPBR", tint=(0.80, 0.84, 0.88), opacity=0.22)
            for p in b["duct"]:
                _bind_builtin_mdl(stage, p, "DuctMetal", "OmniPBR", tint=(0.52, 0.55, 0.60))
            for p in b["other"]:
                _bind_builtin_mdl(stage, p, "BoxConcrete", "OmniPBR", tint=(0.60, 0.60, 0.62))
            print(f"[dc.cfd_bridge] materials bound (room/floor + boxes: "
                  f"{len(b['containment'])}glass {len(b['partition'])}part "
                  f"{len(b['duct'])}duct {len(b['other'])}other)")
            # Named toggleable groups for the web object list. Racks/ACUs are whole
            # Scopes (toggling the Scope hides all instances); the rest are explicit
            # prim lists. Each maps to one distinct GTC USD asset.
            self.geo_groups = {
                "Room":        [mats["room"]],
                "Floor":       [mats["floor"]],
                "Racks":       ["/World/Geometry/Racks"],
                "ACUs":        ["/World/Geometry/ACUs"],
                "Containment": list(b["containment"]),
                "Ducts":       list(b["duct"]),
                "Partitions":  list(b["partition"]) + list(b["other"]),
            }
            # Per-rack entries (rack prim name -> its prim path) so the web tree can
            # show + toggle/select each rack individually under "Racks". The whole
            # "Racks" Scope still toggles every rack at once.
            self.rack_names = [safe for safe, _ in mats.get("racks", [])]
            for safe, path in mats.get("racks", []):
                self.geo_groups[safe] = [path]
            # Same manifest contract as the cfdproj path, so the web tree has exactly
            # one source of truth and never renders a row for a scene it did not load.
            try:
                from . import scenetree as _tree
                _mf = _tree.gtc_manifest(self.geo_groups, self.rack_names)
                _mf["roi"] = self._roi_bounds_for_manifest()
                self.scene_manifest = _mf
                print(f"[dc.cfd_bridge] sceneTree written: "
                      f"{_tree.write_manifest(_mf)} ({_mf['objects']} racks)")
            except Exception as _e:  # noqa: BLE001
                print(f"[dc.cfd_bridge] scene tree manifest FAILED: {_e}")
        except _CfdprojAlreadyBound:
            pass
        except Exception as e:
            print(f"[dc.cfd_bridge] material bind ERROR: {e}")
        # The 0.6 m floor grid is a NON-SELECTABLE overlay: keep its path for cleanup
        # but do NOT add it to geo_groups (so it never appears in the model tree as a
        # pickable/toggleable object — the user's "not a selectable USD object"
        # request) and mark it non-pickable in the viewport.
        if mats.get("grid"):
            self.grid_path = mats["grid"]
            try:
                omni.usd.get_context().set_pickable(mats["grid"], False)
            except Exception as e:
                print(f"[dc.cfd_bridge] grid set_pickable failed: {e}")
        # geometry hidden until loadScene reveals it
        UsdGeom.Imageable(stage.GetPrimAtPath("/World/Geometry")).MakeInvisible()
        for _ in range(10):
            await wait_for_update()

        # Create the ROI bounding box NOW — sized to the geometry's world AABB and
        # INDEPENDENT of any CFD dataset. This is what lets the user set the region of
        # interest + turn Clip on as soon as the geometry loads, BEFORE running the
        # dataset ("simulate"). The frontend re-authors the box on edit; the voxeliser
        # reads its local bound as the clip region when a field is later shown.
        try:
            _bc = UsdGeom.BBoxCache(0, ["default"], useExtentsHint=False)
            _rng = _bc.ComputeWorldBound(
                stage.GetPrimAtPath("/World/Geometry")).ComputeAlignedRange()
            if _rng.IsEmpty():
                raise ValueError("empty geometry bound")
            _mn, _mx = tuple(_rng.GetMin()), tuple(_rng.GetMax())
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] ROI geometry-bound failed ({e}); using room default")
            _mn, _mx = (0.0, 0.0, 0.0), (31.0, 5.0, 19.2)
        self._create_roi_box(stage, _mn, _mx)
        print(f"[dc.cfd_bridge] ROI box created (pre-dataset) "
              f"{self.roi_base_min}..{self.roi_base_max}")

        # NOTE: the CFD dataset is NOT auto-ingested here. Post-processing (volume,
        # thresholds) is gated in the UI until the user explicitly loads a result
        # file via the "simulate" dialog, which drives load_dataset() -> the
        # _ingest_dataset() pipeline. The ROI box (above) is the exception: it is
        # available immediately so the region can be set before simulate. This keeps
        # geometry and CFD-result loading separate and builds the scene fast without
        # paying the voxelisation cost up front.
        self._built = True
        print("[dc.cfd_bridge] scene built (geometry only; load a dataset for post-processing)")

    def _relight_for_cfdproj(self, stage):
        """Re-tune the lights when the scene came from a .cfdproj.

        The default rig (dome 140 / key 2500 / fill 450) was tuned so a semi-transparent
        CFD volume reads against a dark backdrop, not so geometry matches the editors.
        A .cfdproj scene switches to the manifest rig (textures/materials.json
        lighting.kit), the one the React display bridge authors.

        Only the cfdproj path calls this; the GTC + volume look is untouched.
        """
        import carb.settings
        from pxr import UsdLux

        # Tone mapping, exposure and shadows are the manifest's (textures/materials.json
        # lighting.kit), applied by the same function the React display bridge uses, so
        # both Kit paths expose a scene identically. The rig is a fixed RTX camera
        # (histogram off): the auto-exposure this path used to keep ON re-metered every
        # frame, which is why a negative compensation "saturated" in the old
        # measurements and a dark surface came out several times too bright. Those
        # measurements also predated the shared manifest (Phong editor, hex/255 colours
        # without sRGB decode) and ran with Kit's template sun still on the stage (a
        # side light of 3000 at exposure 1.5, retired in build() now), which is what
        # "blew the frame out" with a fixed camera. The 2026-10-22 calibration against
        # the editors is in docs/benchmarks/materials-shared-pool-2026-10-21/.
        from . import material_library
        from . import render_scene
        kit = material_library.kit_lighting()
        st = carb.settings.get_settings()
        render_scene.apply_kit_exposure(st)
        # Experiment levers, unset in production.
        if "DC_CFD_CFDPROJ_EXPOSURE" in os.environ:
            st.set("/rtx/post/tonemap/exposureCompensation", float(os.environ["DC_CFD_CFDPROJ_EXPOSURE"]))
        shadows = os.environ.get("DC_CFD_CFDPROJ_SHADOWS", "1" if kit["shadows"] else "0") != "0"
        st.set("/rtx/shadows/enabled", shadows)

        dome = os.environ.get("DC_CFD_CFDPROJ_DOME", kit["domeIntensity"])
        keyi = os.environ.get("DC_CFD_CFDPROJ_KEY", kit["keyIntensity"])
        fill = os.environ.get("DC_CFD_CFDPROJ_FILL", kit["fillIntensity"])
        for p, val in (("/World/Env/DomeLight", dome),
                       ("/World/Env/KeyLight", keyi),
                       ("/World/Env/FillLight", fill)):
            prim = stage.GetPrimAtPath(p)
            if prim and prim.IsValid():
                UsdLux.LightAPI(prim).GetIntensityAttr().Set(float(val))
        print(f"[dc.cfd_bridge] cfdproj light rig: dome/key/fill = {dome}/{keyi}/{fill}, "
              f"autoExposure={bool(kit['autoExposure'])}, shadows={'ON' if shadows else 'OFF'}")

    async def _build_from_cfdproj(self, stage, path):
        """Author the scene from a .cfdproj. Returns a mats-shaped dict, or None.

        Returning None (rather than raising) lets build() fall back to the GTC JSON, so
        a bad path or a schema surprise degrades to the old scene instead of an empty
        viewport with a traceback in the log.
        """
        # wait_for_update is imported LOCALLY in build(); it is not a module global, so
        # relying on it here raised NameError after the geometry was already authored —
        # a build that logged every success line and then failed.
        from omni.cae.testing import wait_for_update
        try:
            from . import cfdproj as _cfdproj
            from . import emitters as _emitters
            from . import usdgen as _usdgen
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] cfdproj modules unavailable: {e}")
            return None
        try:
            scene = _cfdproj.read_cfdproj(path)
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] cannot read {path}: {e}")
            return None

        print(f"[dc.cfd_bridge] cfdproj source: {os.path.basename(path)} "
              f"({len(scene.placements)} placements, room {scene.room_height} m)")
        self._relight_for_cfdproj(stage)
        try:
            groups = _usdgen.build_scene(stage, scene, _emitters.emit)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"[dc.cfd_bridge] cfdproj authoring failed: {e}")
            return None
        for _ in range(5):
            await wait_for_update()

        x0, x1, z0, z1 = _cfdproj.room_bounds(scene)
        grid = _setup_ground_grid(stage, x0, x1, z0, z1)

        # Class scopes first (the coarse toggles), then per-object entries, matching
        # the GTC path's tree shape so the web model tree needs no special case.
        self.geo_groups = groups
        self.rack_names = [p.name for p in scene.placements if p.cls == "racks"]
        # Prims the project says are off. handle_load_scene consults this so its
        # blanket reveal does not resurrect them.
        self.disabled_prims = set(_usdgen.disabled_prim_paths(scene, groups))
        # Per-object world AABB. Without it a misplaced object can only be found by
        # eyeballing the stream, and "which prim is that panel?" is unanswerable.
        try:
            from pxr import UsdGeom as _UG
            _bc = _UG.BBoxCache(0, ["default"], useExtentsHint=False)
            for _p in scene.placements:
                _safe = _usdgen._safe_name(_p.name) or _p.uid
                for _path in groups.get(_safe, []):
                    _r = _bc.ComputeWorldBound(
                        stage.GetPrimAtPath(_path)).ComputeAlignedRange()
                    _mn = tuple(round(v, 2) for v in _r.GetMin())
                    _mx = tuple(round(v, 2) for v in _r.GetMax())
                    print(f"[dc.cfd_bridge]   {'ON ' if _p.enabled else 'off'} "
                          f"{_p.cls[:6]:<6} {_p.name[:22]:<23} {_mn} .. {_mx}")
        except Exception as _e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] per-object bounds unavailable: {_e}")
        print(f"[dc.cfd_bridge] cfdproj groups: "
              f"{', '.join(k for k in groups if k[0].isupper() and k in _CFDPROJ_GROUPS)}")
        # Hand the web UI the real tree. Without it the page falls back to a hardcoded
        # list built for the GTC scene, whose 'Floor' group a .cfdproj build never
        # creates — so that row's checkbox toggled nothing and looked broken.
        try:
            from . import scenetree as _tree
            mf = _tree.build_manifest(
                scene, groups, os.path.splitext(os.path.basename(path))[0])
            # Kept on the Scene so handle_load_scene can re-stamp the file when a
            # second load of the SAME project takes the reveal-only path.
            self.scene_manifest = mf
            mf["roi"] = self._roi_bounds_for_manifest()
            print(f"[dc.cfd_bridge] sceneTree written: {_tree.write_manifest(mf)} "
                  f"({mf['objects']} objects in {len(mf['categories'])} categories, "
                  f"roi={mf['roi']})")
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] scene tree manifest FAILED: {e}")
        return {"source": "cfdproj", "grid": grid,
                "room": None, "floor": None, "boxes": None, "racks": []}

    def _create_roi_box(self, stage, mn, mx):
        """Author the ROI as a 12-edge BasisCurves wireframe box spanning the world
        AABB [mn, mx], with an IDENTITY transform so the authored points ARE the clip
        region the voxeliser reads. Mirrors kit-cae's CreateCaeVizBoundingBox but is
        DATASET-INDEPENDENT — it runs at geometry-build time so the ROI exists before
        any CFD result is loaded. handle_set_roi_box re-authors these points to resize.
        Also records the box's base AABB + seeds the streamline sphere centre."""
        from pxr import UsdGeom, Vt, Gf
        mn = [float(v) for v in mn]
        mx = [float(v) for v in mx]
        c = [
            (mn[0], mn[1], mn[2]), (mx[0], mn[1], mn[2]),
            (mx[0], mx[1], mn[2]), (mn[0], mx[1], mn[2]),
            (mn[0], mn[1], mx[2]), (mx[0], mn[1], mx[2]),
            (mx[0], mx[1], mx[2]), (mn[0], mx[1], mx[2]),
        ]
        edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6),
                 (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
        pts = []
        for a, b in edges:
            pts.append(Gf.Vec3f(*c[a]))
            pts.append(Gf.Vec3f(*c[b]))
        if stage.GetPrimAtPath(self.roi_path).IsValid():
            stage.RemovePrim(self.roi_path)
        crv = UsdGeom.BasisCurves.Define(stage, self.roi_path)
        crv.CreatePointsAttr().Set(Vt.Vec3fArray(pts))
        crv.CreateCurveVertexCountsAttr().Set([2] * len(edges))
        crv.CreateExtentAttr().Set([Gf.Vec3f(*mn), Gf.Vec3f(*mx)])
        crv.CreateTypeAttr().Set(UsdGeom.Tokens.linear)
        diag = sum((mx[i] - mn[i]) ** 2 for i in range(3)) ** 0.5
        crv.CreateWidthsAttr().Set([max(diag * 0.005, 1e-3)])
        crv.SetWidthsInterpolation(UsdGeom.Tokens.constant)
        UsdGeom.Imageable(crv.GetPrim()).MakeInvisible()
        self.roi_base_min = tuple(mn)
        self.roi_base_max = tuple(mx)
        # Default the streamline seed sphere to the box centre (mid-height); the
        # frontend sends an explicit centre on every command, so this is a fallback.
        self.seed_center = [(mn[i] + mx[i]) / 2.0 for i in range(3)]

    async def _ingest_dataset(self, stage):
        """Import ``self.vtu_path`` and discover the CaeDataSet + T/U/Umag fields.

        The ROI box already exists (created at geometry-build time) and NO volume is
        built here: the volume is voxelised on demand when the user first shows a field
        (Temp/Velocity), clipped to the ROI when Clip is active. So loading a result
        ("simulate") leaves the flow field OFF until the user picks a field. Returns
        True on success. Assumes the prior dataset/volume prims were torn down."""
        from omni.cae.importer.vtk import import_to_stage
        from omni.cae.testing import wait_for_update
        await import_to_stage(path=self.vtu_path, prim_path="/World/internal")
        for _ in range(10):
            await wait_for_update()
        for p in stage.Traverse():
            ps = str(p.GetPath())
            if ps.startswith("/World/internal"):
                if p.GetTypeName() == "CaeDataSet" and not self.dataset_path:
                    self.dataset_path = ps
                if p.GetName() == "T":
                    self.t_field = ps
                if p.GetName() == "U":
                    self.u_field = ps
                if p.GetName() == "Umag":
                    self.umag_field = ps
        if not self.umag_field and self.u_field:
            self.umag_field = self._derive_umag(stage)
        print(f"[dc.cfd_bridge] dataset={self.dataset_path} "
              f"T={self.t_field} U={self.u_field} Umag={self.umag_field}")
        if not self.dataset_path:
            print("[dc.cfd_bridge] _ingest_dataset: no CaeDataSet found")
            return False
        return True

    @staticmethod
    def _range_stats_impl(np, a):
        """min/max plus the p1..p99 working band, as plain floats."""
        return {"min": float(a.min()), "max": float(a.max()),
                "p1": float(np.percentile(a, 1)), "p99": float(np.percentile(a, 99))}

    def write_dataset_info(self, path):
        """Publish this dataset's field ranges for the web sliders.

        Same side-channel shape as the scene manifest: a JSON file the dev server hands
        back, because the WebRTC command channel has no return path. Without it the
        Low/High sliders keep the GTC/A3 constants — a room whose |U| peaks at 8 m/s
        opened on a 0..1.2 domain, which clamps almost every voxel to the top colour and
        reads as a solid red block rather than a flow field.
        """
        info = {"path": path, "fields": getattr(self, "field_stats", None) or {}}
        try:
            from .owned_paths import metadata_path, write_owned_json
            out = metadata_path("DC_CFD_DATASET_INFO", "dataset-info.json")
            write_owned_json(out, info, "DC_CFD_DATASET_INFO")
            print(f"[dc.cfd_bridge] datasetInfo written: {out} {info['fields']}")
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] datasetInfo FAILED: {e}")

    # ── ROI extraction (Phase 1 of the drag-&-sim design) ────────────────────
    # The difference from the existing clip: the clip loads the WHOLE result and then
    # limits what the voxeliser looks at, so the 128 MB read, the DAV conversion and the
    # full-domain cell set are all still paid. Extraction cuts the cells out FIRST and
    # hands Kit a small dataset, which is what "only load/read the ROI" actually means.
    #
    # Visible consequence, and the acceptance test: with the clip, hiding the ROI box
    # brings the whole field back, because the full field was always there. With
    # extraction, the field stays confined — there is nothing else loaded.

    async def extract_roi(self, stage, mn=None, mx=None, fmt="vtu"):
        """Cut the ROI's cells out of the loaded result and load THAT as the dataset.

        Returns the written path, or None. `mn`/`mx` default to the live ROI box, baked
        so a viewport drag is honoured (the same source the voxeliser reads).
        """
        src = getattr(self, "source_vtu_path", None) or self.vtu_path
        if not src or not os.path.exists(src):
            print(f"[dc.cfd_bridge] extractRoi: no source result loaded ({src})")
            return None
        if mn is None or mx is None:
            mn, mx = self._current_roi_aabb(stage)
        out = self._extract_roi_file(src, mn, mx, fmt)
        if not out:
            return None
        # Remember the ORIGINAL so a second, different ROI cuts from the full result
        # again rather than from the previous cut — otherwise ROIs would only ever
        # shrink and moving the box outside the last one would return nothing.
        self.source_vtu_path = src
        # load_dataset deliberately leaves the flow field OFF (a fresh result should not
        # assume which field you want). For an ROI cut that is wrong: the user's action
        # was "cut this region", not "load a file", and blanking the viewport reads as
        # the extraction having failed. Restore whatever was on screen.
        # active_field alone cannot tell "showing T" from "off" — handle_set_volume sets
        # it either way — so ask the stage whether a volume is actually on screen.
        field = getattr(self, "active_field", None)
        showing = False
        try:
            from pxr import UsdGeom as _UG
            _v = stage.GetPrimAtPath(self.volume_path)
            showing = bool(_v and _v.IsValid()
                           and _UG.Imageable(_v).ComputeVisibility() != _UG.Tokens.invisible)
        except Exception:
            showing = False
        await self.load_dataset(stage, out)
        if field and showing:
            await self._rebuild_volume(stage, field=field,
                                       roi_enabled=False, make_visible=True)
            print(f"[dc.cfd_bridge] extractRoi: restored field={field} on the ROI dataset")
        return out

    def _extract_roi_file(self, src, mn, mx, fmt="vtu"):
        """Write an ROI-only dataset next to the cache. Cached by (source, box, fmt)."""
        import hashlib
        try:
            import numpy as np
            from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
            from vtkmodules.vtkCommonCore import vtkIdTypeArray
            from vtkmodules.vtkCommonDataModel import vtkSelection, vtkSelectionNode
            from vtkmodules.vtkFiltersCore import vtkCellCenters
            from vtkmodules.vtkFiltersExtraction import vtkExtractSelection
            from vtkmodules.vtkFiltersGeometry import vtkDataSetSurfaceFilter  # noqa: F401
            from vtkmodules.vtkIOXML import (vtkXMLUnstructuredGridReader,
                                             vtkXMLUnstructuredGridWriter)
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] extractRoi: VTK unavailable ({e})")
            return None

        st = os.stat(src)
        box = tuple(round(float(v), 4) for v in (*mn, *mx))
        key = hashlib.sha1(
            f"{src}|{st.st_mtime_ns}|{st.st_size}|{box}|{fmt}".encode()).hexdigest()[:16]
        from .owned_paths import bridge_cache_directory, owned_path
        cache_dir = bridge_cache_directory()
        out = owned_path(os.path.join(cache_dir, f"roi_{key}.{fmt}"), "ROI cache")
        if os.path.exists(out):
            print(f"[dc.cfd_bridge] extractRoi: cache hit, 0 cells computed -> "
                  f"{os.path.basename(out)}")
            return out

        t0 = _time.time()
        # Keep the source grid AND its cell centroids resident, keyed by the file's
        # identity. Re-reading a 122 MB .vtu and recomputing 609,990 cell centres on every
        # ROI is most of the 6.81 s the first implementation cost, and the user's workflow
        # is to DRAG the box — i.e. many ROIs against one result.
        # Requirement 2, backend half: answer "which cells are in this box?" from the
        # LAYOUT, without touching the result. The layout is built once per mesh from the
        # cell centres and is 12 MB against the 122 MB result; a second ROI on the same
        # mesh — the drag case — never reads the .vtu to decide what to select.
        #
        # The values still come from the grid, so the grid is still read ONCE per session
        # and kept resident. Removing that last read means serving values out of the
        # cache too, which is the AI path's job (the OpenFOAM result is already complete,
        # so there is nothing to accumulate).
        _sig = (src, st.st_mtime_ns, st.st_size)
        _res = getattr(self, "_roi_src_cache", None)
        _lay = getattr(self, "_cpd_cache", None)
        _ids_fast = None
        if _lay is not None and _lay[0] == _sig:
            _t_q = _time.time()
            _ids_fast = _lay[1].layout.query(mn, mx)
            print(f"[dc.cfd_bridge] extractRoi: layout query {_ids_fast.size} cells in "
                  f"{1000 * (_time.time() - _t_q):.1f} ms (no result read)")
        if _res and _res[0] == _sig:
            grid, ctr = _res[1], _res[2]
            _reused = True
        else:
            r = vtkXMLUnstructuredGridReader()
            r.SetFileName(src)
            r.Update()
            grid = r.GetOutput()
            cc = vtkCellCenters()
            cc.SetInputData(grid)
            cc.Update()
            ctr = vtk_to_numpy(cc.GetOutput().GetPoints().GetData())
            self._roi_src_cache = (_sig, grid, ctr)
            _reused = False
            try:
                from . import cpd_cache as _cc
                _root = owned_path(os.path.join(cache_dir, "cpd"), "CPD cache")
                self._cpd_cache = (
                    _sig, _cc.CpdCache.open(_root, src, lambda: ctr, n_cells=len(ctr)))
                print(f"[dc.cfd_bridge] cpd layout ready: mesh_id="
                      f"{self._cpd_cache[1].mid} n={len(ctr)}")
            except Exception as e:  # noqa: BLE001
                print(f"[dc.cfd_bridge] cpd layout unavailable: {e}")
                self._cpd_cache = None
        n_all = grid.GetNumberOfCells()
        lo = np.asarray(mn, dtype=float)
        hi = np.asarray(mx, dtype=float)
        # Centroid-in-box. A cell straddling the face is included or excluded by its
        # centre, which is right for rendering; a conservative variant would dilate by
        # one cell size.
        #
        # USE the layout's answer when there is one. It was previously computed, printed
        # and then thrown away while this line recomputed the same selection by brute
        # force — which made the "no result read" receipt true of a number nobody
        # consumed. Both are exact centroid-in-box tests over the same array; the layout
        # only prunes with bins first (proven over 200 random boxes in
        # tests/test_cpd_cache.py). The one difference is that the layout compares in
        # float32, so a cell whose centre sits within float32 epsilon of a box face can
        # fall the other way — irrelevant for a rendering selection, and stated here so
        # nobody re-derives it from a one-cell count difference.
        if _ids_fast is not None:
            ids = _ids_fast.astype(np.int64, copy=False)
        else:
            ids = np.nonzero(((ctr >= lo) & (ctr <= hi)).all(axis=1))[0].astype(np.int64)
        if ids.size == 0:
            print(f"[dc.cfd_bridge] extractRoi: ROI {box} contains 0 of {n_all} cells")
            return None

        sel_ids = numpy_to_vtk(ids, deep=1, array_type=vtkIdTypeArray().GetDataType())
        node = vtkSelectionNode()
        node.SetFieldType(vtkSelectionNode.CELL)
        node.SetContentType(vtkSelectionNode.INDICES)
        node.SetSelectionList(sel_ids)
        sel = vtkSelection()
        sel.AddNode(node)
        ex = vtkExtractSelection()
        ex.SetInputData(0, grid)
        ex.SetInputData(1, sel)
        ex.Update()
        sub = ex.GetOutput()

        w = vtkXMLUnstructuredGridWriter()
        w.SetFileName(out)
        w.SetInputData(sub)
        w.SetDataModeToBinary()
        w.Write()
        print(f"[dc.cfd_bridge] extractRoi: src={'resident' if _reused else 'read'} "
              f"{sub.GetNumberOfCells()} of {n_all} cells "
              f"({100.0 * sub.GetNumberOfCells() / max(1, n_all):.1f}%) "
              f"box={box} {_time.time() - t0:.2f}s -> {os.path.basename(out)} "
              f"({os.path.getsize(out) // (1024 * 1024)} MB vs {st.st_size // (1024 * 1024)} MB)")
        return out

    def _roi_bounds_for_manifest(self):
        """The scene's own ROI AABB, for the web UI's Box Bounds inputs.

        Those inputs shipped hardcoded to 31 / 5 / 19.2 m — the GTC room. Loading a
        4.4 x 3.6 x 6.0 m project left them reading the GTC numbers while the backend
        had correctly built the box around the actual room, so the panel disagreed with
        the viewport and typing into a field snapped the box to a room that was not
        there.
        """
        mn, mx = self.roi_base_min, self.roi_base_max
        if not mn or not mx:
            return None
        return {"min": [round(float(v), 4) for v in mn],
                "max": [round(float(v), 4) for v in mx]}

    def _derive_umag(self, stage):
        """Synthesise a scalar |U| field when the dataset only carries the vec3 U.

        The GTC demo file ships a pre-computed `Umag` array, so Velocity colouring
        always worked there. Nothing else does: `foamToVTK` writes U and no magnitude,
        so a result solved by this repo's own pipeline loaded with Umag=None and the
        Velocity button fell back to CaeViz `vector_magnitude` — which voxelises a raw
        vec3 into a UNIFORM constant block, i.e. a solid-coloured volume that looks like
        a render bug rather than a flow field. Half the post-processing was unusable on
        our own data.

        So compute it here and hand it to the npz data delegate as a real scalar field
        prim, which the volume can target exactly like T. Cached next to nothing the user
        owns: the .npz goes in kit/cache/, keyed by the source file's path+mtime+size, so
        re-loading the same result is instant and re-solving invalidates it.
        """
        import hashlib
        import numpy as np
        from pxr import Sdf
        from omni.cae.schema import cae

        try:
            st = os.stat(self.vtu_path)
        except OSError as e:
            print(f"[dc.cfd_bridge] Umag: cannot stat {self.vtu_path}: {e}")
            return None
        key = hashlib.sha1(
            f"{self.vtu_path}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:16]
        from .owned_paths import bridge_cache_directory, owned_path
        cache_dir = bridge_cache_directory()
        npz = owned_path(os.path.join(cache_dir, f"umag_{key}.npz"), "Umag cache")

        if not os.path.exists(npz):
            try:
                import time as _t
                t0 = _t.time()
                from vtkmodules.util.numpy_support import vtk_to_numpy
                from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridReader
                r = vtkXMLUnstructuredGridReader()
                r.SetFileName(self.vtu_path)
                r.Update()
                out = r.GetOutput()
                # U is CellData in a foamToVTK internal.vtu; accept PointData too so an
                # arbitrary result file still works.
                arr = out.GetCellData().GetArray("U") or out.GetPointData().GetArray("U")
                if arr is None:
                    print("[dc.cfd_bridge] Umag: no U array to derive from")
                    return None
                u = vtk_to_numpy(arr)
                mag = np.linalg.norm(u, axis=1).astype(np.float32)
                # Field ranges come from the SAME read, so the sliders can open on this
                # dataset instead of on the GTC file's pinned numbers. p1..p99 as well as
                # min..max: both fields are heavily right-skewed, and mapping colour over
                # the raw extent washes the volume to a single hue.
                stats = {"Umag": self._range_stats_impl(np, mag)}
                tarr = (out.GetCellData().GetArray("T")
                        or out.GetPointData().GetArray("T"))
                if tarr is not None:
                    stats["T"] = self._range_stats_impl(
                        np, vtk_to_numpy(tarr).astype(np.float32))
                np.savez(npz, Umag=mag, stats=np.array(json.dumps(stats)))
                # Open the sliders on THIS dataset's range, not the GTC file's pinned
                # 0..8.1 — a room whose peak is 2 m/s would otherwise render in the
                # bottom quarter of the colormap and read as uniformly still air.
                self.ranges["Umag"] = (float(mag.min()), float(mag.max()))
                print(f"[dc.cfd_bridge] Umag derived: {mag.size} cells, "
                      f"range {mag.min():.3f}..{mag.max():.3f} m/s, "
                      f"{_t.time() - t0:.1f}s -> {os.path.basename(npz)}")
            except Exception as e:  # noqa: BLE001
                print(f"[dc.cfd_bridge] Umag derive FAILED: {e}")
                return None
            self.field_stats = stats
        else:
            print(f"[dc.cfd_bridge] Umag: cached {os.path.basename(npz)}")
            try:
                with np.load(npz, allow_pickle=False) as z:
                    self.field_stats = json.loads(str(z["stats"]))
            except Exception as e:  # noqa: BLE001
                print(f"[dc.cfd_bridge] Umag: cached stats unreadable ({e})")

        # The volume targets a field PRIM, so the derived array has to become one. It is
        # authored as a sibling of T/U under the same container, which is also where the
        # discovery loop above would have found a real Umag.
        parent = self.t_field or self.u_field
        path = parent.rsplit("/", 1)[0] + "/Umag"
        try:
            fa = cae.NumPyFieldArray.Define(stage, path)
            fa.CreateFileNamesAttr(Sdf.AssetPathArray([npz]))
            fa.CreateArrayNameAttr("Umag")
            # |U| lives where U lives. Without an association IndeX refuses the
            # field ("Only vertex and cell associations are supported ... Got
            # none") and the |U| volume never executes.
            src = stage.GetPrimAtPath(self.u_field) if self.u_field else None
            assoc = cae.FieldArray(src).GetFieldAssociationAttr().Get() if src else None
            fa.CreateFieldAssociationAttr().Set(assoc or cae.Tokens.cell)
            return path
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] Umag: cannot author field prim: {e}")
            return None

    async def load_dataset(self, stage, path):
        """Runtime: swap the CFD dataset for a new file on the Kit host. Native
        VTK formats (.vtu/.vti/.vts/.vtp/.vtk) load directly; .vtm (multiblock)
        is converted to .vtu first. Tears down the old volume + streamlines + dataset
        and re-imports, but KEEPS the ROI box + clip state (set before/after simulate).
        NO volume is built here — the flow field stays off until the user shows a field.
        Returns True when the dataset was ingested (the acked OV-display load
        reports this; the reference demo's fire-and-forget path ignores it)."""
        import os
        from omni.cae.testing import wait_for_update
        if self._loading:
            print("[dc.cfd_bridge] load_dataset: already loading, ignored")
            return False
        path = os.path.abspath(os.path.expanduser(str(path).strip()))
        if not os.path.exists(path):
            print(f"[dc.cfd_bridge] load_dataset: file not found: {path}")
            return False
        ext = os.path.splitext(path)[1].lower()
        if ext == ".vtm":
            conv = await self._convert_vtm(path)
            if not conv:
                print("[dc.cfd_bridge] load_dataset: .vtm conversion failed")
                return False
            path, ext = conv, ".vtu"
        elif ext == ".case":
            # EnSight Gold (.case) — e.g. an OpenFOAM foamToEnsight export. The VTK
            # importer only reads native XML VTK, so convert to .vtu first (VTK's
            # EnSight reader is bundled with omni.cae.vtk).
            self.surface_vtp_path = None
            conv = await self._convert_case(path)
            if not conv:
                print("[dc.cfd_bridge] load_dataset: .case conversion failed")
                return False
            path, ext = conv, ".vtu"
        if ext not in (".vtu", ".vti", ".vts", ".vtp", ".vtk"):
            print(f"[dc.cfd_bridge] load_dataset: unsupported format {ext}")
            return False
        self._loading = True
        ok = False
        try:
            # A new dataset starts with no volume: undo only our temporary shell mask.
            from .handlers import _set_geometry_hidden
            _set_geometry_hidden(self, stage, False)
            # Tear down the old viz + dataset, but KEEP the ROI box and clip state: the
            # user may have set the region of interest (and turned Clip on) BEFORE
            # loading a result, and that intent must survive the swap ("ROI is god").
            if stage.GetPrimAtPath(self.volume_path).IsValid():
                try:
                    from omni.cae.data import cache as _cache
                    _cache.remove(f"[viz:index_volume]::{self.volume_path}")
                except Exception:
                    pass
                stage.RemovePrim(self.volume_path)
            for pth in (self.streamlines_path, self.surface_path, self.seed_sphere_path,
                        self.seed_source_path, "/World/internal"):
                if stage.GetPrimAtPath(pth).IsValid():
                    stage.RemovePrim(pth)
            for _ in range(6):
                await wait_for_update()
            # Reset only the dataset-derived state, then re-ingest. clip_enabled and the
            # ROI box (bounds + visibility) are intentionally preserved.
            self.vtu_path = path
            self.dataset_path = None
            self.t_field = self.u_field = self.umag_field = None
            self.active_field = "T"
            self.streamlines_enabled = False
            self.seed_visible = False
            self._seed_res = 0
            # Stats and slice caches belong to the OLD dataset; a T-only file
            # must not report the previous file's |U| range.
            self.field_stats = None
            self._volume_domain_override = {}
            self._slice_grid = None
            self._slice_cache_key = None
            self._slice_summary = None
            ok = await self._ingest_dataset(stage)
        finally:
            self._loading = False
        if ok:
            # Only a USER-picked result becomes the new extraction source; an ROI cut we
            # wrote ourselves must not, or the next ROI would cut from the last cut and
            # could only ever shrink.
            if not os.path.basename(path).startswith("roi_"):
                self.source_vtu_path = path
            self.write_dataset_info(path)
        print(f"[dc.cfd_bridge] load_dataset {'OK' if ok else 'FAILED'}: {path}")
        return ok

    async def reset_scene(self, stage):
        """Return the viewport to a clean slate (as if Kit just restarted) WITHOUT paying
        the real-restart cost: tear down the dataset/volume/streamlines post-processing,
        hide the ROI box + geometry + floor grid, and reset all post-processing state. The
        geometry stays BUILT (cached), so the next 'Load CFD Geometry' reveals it fast.

        The frontend fires this ONCE per page load, so a browser refresh is a genuine
        from-scratch demo: both the React UI (fresh mount) AND the Kit viewport reset."""
        from omni.cae.testing import wait_for_update
        from pxr import UsdGeom
        import omni.usd
        from .handlers import _set_geometry_hidden
        _set_geometry_hidden(self, stage, False)
        # 1) Retire the IndeX volume SAFELY: composite-off -> hide -> settle -> drop the
        #    [viz:index_volume] cache -> remove (removing while IndeX holds GPU refs crashes).
        vp = stage.GetPrimAtPath(self.volume_path)
        if vp and vp.IsValid():
            try:
                vp.GetAttribute("nvindex:composite").Set(False)
            except Exception:
                pass
            try:
                UsdGeom.Imageable(vp).MakeInvisible()
            except Exception:
                pass
            for _ in range(6):
                await wait_for_update()
            try:
                from omni.cae.data import cache as _cache
                _cache.remove(f"[viz:index_volume]::{self.volume_path}")
            except Exception:
                pass
            stage.RemovePrim(self.volume_path)
        # 2) Remove streamlines + both seed prims + the imported dataset.
        for pth in (self.streamlines_path, self.surface_path, self.seed_sphere_path, self.seed_source_path,
                    "/World/internal"):
            if stage.GetPrimAtPath(pth).IsValid():
                stage.RemovePrim(pth)
        # 3) Hide EVERYTHING geometry so the viewport goes truly empty: the ROI box, the
        #    /World/Geometry root (Racks/ACUs live under it), the floor grid, AND every
        #    named group prim — Floor/Room/Containment/… are authored as SIBLINGS of
        #    /World/Geometry, so hiding the root alone leaves the floor slab behind. All
        #    prims stay on the stage for a fast re-reveal (handle_load_scene re-shows them).
        # The NATIVE grid has no prim, so hiding prims cannot switch it off. Turn the
        # setting off here and let handle_load_scene turn it back on, mirroring exactly
        # what the USD grid's hide/re-show did.
        if not USD_GRID:
            try:
                from . import handlers as _h
                _h.handle_set_grid(None, stage, enabled=False)
            except Exception as _e:  # noqa: BLE001
                print(f"[dc.cfd_bridge] native grid off failed: {_e}")
        hide_paths = [self.roi_path, "/World/Geometry", getattr(self, "grid_path", None)]
        for paths in self.geo_groups.values():
            hide_paths.extend(paths or [])
        for pth in hide_paths:
            if not pth:
                continue
            prim = stage.GetPrimAtPath(pth)
            if prim and prim.IsValid():
                UsdGeom.Imageable(prim).MakeInvisible()
        # 4) Reset post-processing state (dataset/field/clip/streamlines).
        self.dataset_path = None
        self.t_field = self.u_field = self.umag_field = None
        self.active_field = "T"
        self.clip_enabled = False
        self.streamlines_enabled = False
        self.seed_visible = False
        self._seed_res = 0
        # 5) Clear the viewport selection (Inspector).
        try:
            omni.usd.get_context().get_selection().clear_selected_prim_paths()
        except Exception:
            pass
        for _ in range(4):
            await wait_for_update()
        print("[dc.cfd_bridge] scene reset to clean slate "
              "(geometry + grid hidden; post-processing cleared)")

    async def _convert_vtm(self, vtm_path):
        """Convert a .vtm (vtkMultiBlockDataSet) to a sibling .vtu by appending
        every non-empty leaf block into one unstructured grid. Uses VTK from the
        Kit runtime (omni.cae.vtk bundles vtkmodules). Runs in a thread so it does
        not stall the render. Returns the .vtu path or None."""
        import os, asyncio
        from .owned_paths import owned_path
        out = owned_path(os.path.splitext(vtm_path)[0] + ".dcconv.vtu", "dataset conversion")

        def _do():
            try:
                from vtkmodules.vtkIOXML import (
                    vtkXMLMultiBlockDataReader, vtkXMLUnstructuredGridWriter)
                from vtkmodules.vtkFiltersCore import vtkAppendFilter
            except Exception:
                from vtk import (vtkXMLMultiBlockDataReader,  # type: ignore
                                 vtkXMLUnstructuredGridWriter, vtkAppendFilter)
            r = vtkXMLMultiBlockDataReader()
            r.SetFileName(vtm_path)
            r.Update()
            app = vtkAppendFilter()
            app.MergePointsOn()
            it = r.GetOutput().NewIterator()
            it.InitTraversal()
            n = 0
            while not it.IsDoneWithTraversal():
                blk = it.GetCurrentDataObject()
                if blk is not None and blk.GetNumberOfCells() > 0:
                    app.AddInputData(blk)
                    n += 1
                it.GoToNextItem()
            if n == 0:
                return None
            app.Update()
            w = vtkXMLUnstructuredGridWriter()
            w.SetFileName(out)
            w.SetInputData(app.GetOutput())
            w.Write()
            return out

        try:
            result = await asyncio.get_event_loop().run_in_executor(None, _do)
            print(f"[dc.cfd_bridge] _convert_vtm: {vtm_path} -> {result}")
            return result
        except Exception as e:
            print(f"[dc.cfd_bridge] _convert_vtm error: {e}")
            return None

    async def _convert_case(self, case_path):
        """Convert an EnSight Gold ``.case`` to a sibling ``.vtu`` by reading every
        block with VTK's generic EnSight reader and appending the non-empty leaves
        into one unstructured grid. Mirrors _convert_vtm; runs off-thread. Returns
        the .vtu path or None."""
        import os, asyncio
        from .owned_paths import owned_path
        out = owned_path(os.path.splitext(case_path)[0] + ".dcconv.vtu", "dataset conversion")

        def _do():
            try:
                from vtkmodules.vtkIOEnSight import vtkGenericEnSightReader
                from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridWriter
                from vtkmodules.vtkFiltersCore import vtkAppendFilter
                from vtkmodules.vtkCommonDataModel import vtkCompositeDataSet
            except Exception:
                from vtk import (vtkGenericEnSightReader,  # type: ignore
                                 vtkXMLUnstructuredGridWriter, vtkAppendFilter,
                                 vtkCompositeDataSet)
            r = vtkGenericEnSightReader()
            r.SetCaseFileName(case_path)
            r.ReadAllVariablesOn()
            r.Update()
            out_data = r.GetOutput()  # vtkMultiBlockDataSet
            # The internal mesh (3-D cells) feeds the volume, slice and
            # streamlines; the boundary patches (2-D faces, exported with the
            # case since the surface field) become one surface dataset.
            app = vtkAppendFilter()
            app.MergePointsOn()
            surf = vtkAppendFilter()
            surf.MergePointsOn()
            n = ns = 0
            blocks = []
            if out_data.IsA("vtkCompositeDataSet"):
                it = out_data.NewIterator()
                it.InitTraversal()
                while not it.IsDoneWithTraversal():
                    name = None
                    try:
                        if it.HasCurrentMetaData():
                            name = it.GetCurrentMetaData().Get(vtkCompositeDataSet.NAME())
                    except Exception:
                        name = None
                    blocks.append((it.GetCurrentDataObject(), name))
                    it.GoToNextItem()
            elif out_data is not None:
                blocks.append((out_data, None))
            from vtkmodules.vtkCommonCore import vtkIntArray
            patch_names = []
            for blk, name in blocks:
                if blk is None or blk.GetNumberOfCells() == 0:
                    continue
                if blk.GetCell(0).GetCellDimension() >= 3:
                    app.AddInputData(blk)
                    n += 1
                else:
                    # tag every face with its patch, so the surface field can
                    # leave out the patches the panel hides (the room shell)
                    ids = vtkIntArray()
                    ids.SetName("patchId")
                    ids.SetNumberOfTuples(blk.GetNumberOfCells())
                    ids.Fill(len(patch_names))
                    blk.GetCellData().AddArray(ids)
                    patch_names.append(str(name or f"patch{len(patch_names)}"))
                    surf.AddInputData(blk)
                    ns += 1
            if n == 0:
                return None
            app.Update()
            w = vtkXMLUnstructuredGridWriter()
            w.SetFileName(out)
            w.SetInputData(app.GetOutput())
            w.Write()
            surface_out = None
            if ns:
                surf.Update()
                surface_out = owned_path(os.path.splitext(case_path)[0] + ".dcconv.boundary.vtu",
                                         "dataset conversion")
                ws = vtkXMLUnstructuredGridWriter()
                ws.SetFileName(surface_out)
                ws.SetInputData(surf.GetOutput())
                ws.Write()
                with open(surface_out + ".patches.json", "w") as fh:
                    json.dump(patch_names, fh)
            return out, surface_out

        try:
            result = await asyncio.get_event_loop().run_in_executor(None, _do)
            if result is None:
                print(f"[dc.cfd_bridge] _convert_case: {case_path} -> None")
                return None
            result, self.surface_vtp_path = result
            print(f"[dc.cfd_bridge] _convert_case: {case_path} -> {result} (boundary {self.surface_vtp_path})")
            return result
        except Exception as e:
            print(f"[dc.cfd_bridge] _convert_case error: {e}")
            return None

    async def _rebuild_volume(self, stage, field="T", roi_enabled=False,
                              make_visible=False):
        """(Re)create the IndeX volume voxelised for ``field``, optionally clipped
        to the ROI box.

        kit-cae voxelises a NanoVDB grid ONCE when the volume is created and only
        rebuilds it on a structural (INITIAL) exec — a later field-selection change
        or ROI-box move does not re-voxelise. So to colour by a different field or
        to clip to a moved/resized box we tear the volume down and recreate it.

        Requests COALESCE: if a rebuild is already running, the latest request is
        stored and run when the current one finishes, so fast field/clip switches
        converge on the LAST requested state instead of dropping intermediate
        clicks. ~1-2s per rebuild.
        """
        from omni.cae.testing import wait_for_update  # noqa: F401 (parity import)
        self._pending = (field, roi_enabled, make_visible)
        if self._volume_busy:
            print(f"[dc.cfd_bridge] rebuild busy; queued {self._pending}")
            return
        self._volume_busy = True
        try:
            while self._pending is not None:
                f, roi, vis = self._pending
                self._pending = None
                await self._do_rebuild_volume(stage, f, roi, vis)
        finally:
            self._volume_busy = False

    async def _do_rebuild_volume(self, stage, field, roi_enabled, make_visible):
        """Build the volume on a FRESH, ROTATING prim path on every field/ROI change,
        then retire the previous one. We never edit the live prim in place and never
        recreate on the same path.

        Why (root-caused against the kit-cae source + proven empirically):

        (1) IndeX composites the grid held in [viz:index_volume]::<vol> (index_volume.py).
        That cache is refilled — and setup_importer re-pushes the grid to IndeX — ONLY
        when IndeXVolume.exec() runs with context.is_full_rebuild_needed()==True, i.e. on
        an INITIAL or STRUCTURAL_CHANGE exec (execution_context.py). An in-place edit of
        the colors rel is supposed to yield STRUCTURAL_CHANGE, but that signal is NOT
        reliably delivered in the live streaming app (the controller consumes+clears the
        change set each tick), so the operator re-runs as a TEMPORAL_TICK and SKIPS
        setup_importer — leaving the FIRST-build grid (T, 292-317 K) bound in IndeX. Under
        the velocity domain (0,4.5) that clamps to colormap max == a SOLID RED block.
        Proven: a clean 0->4.5 Umag X-gradient still rendered solid red in place.

        (2) Recreating on the SAME path does force INITIAL — but the controller keeps a
        per-PATH last_execution entry that is NOT cleared on RemovePrim, so the recreated
        prim is not always run as a clean INITIAL: the IndeX compute task then finds no
        grid ('No source dataset found') and renders an empty/uniform block. Proven: the
        same-path recreate turned the red block CYAN (empty grid), not the gradient.

        (3) RemovePrim / cache.remove while IndeX still holds GPU refs to the NanoVDB grid
        is a use-after-free == HARD CRASH (observed). So any teardown must first set
        nvindex:composite=False + hide the prim and settle, releasing those refs.

        The FIX uses a brand-new prim path each time (InternalVolume_<seq>): a path the
        controller has never executed, so its first exec is an unambiguous INITIAL that
        voxelises THIS field from scratch and refills [viz:index_volume] — exactly the
        path that already works for the very first field (T). The OLD volume is retired
        only AFTER the new one is live (no visual gap), composite-off first (no crash).

        The ROI rel is set on the fresh prim before its first settle, so the initial exec
        voxelises against the correct bounds."""
        from omni.cae.data.commands import execute_command
        from omni.cae.data import cache as _cache
        from omni.cae.schema import viz as cae_viz
        from omni.cae.testing import wait_for_update
        from pxr import UsdGeom
        # Velocity colours by the pre-computed SCALAR 'Umag' field (voxelised as a plain
        # scalar like T). Fall back to the raw vec3 U (vector_magnitude) only if Umag is
        # absent from the dataset.
        u_scalar = field == "U" and self.umag_field is not None
        if field == "T":
            fld = self.t_field
        elif u_scalar:
            fld = self.umag_field
        else:
            fld = self.u_field

        # ROTATING PATHS (see docstring): build the new volume on a FRESH, never-executed
        # prim path so its first exec is an unambiguous INITIAL rebuild. The OLD volume
        # (if any) is retired at the END, after the new one is live.
        old_path = (self.volume_path
                    if self.volume_path and stage.GetPrimAtPath(self.volume_path).IsValid()
                    else None)
        # kit-cae's CreateCaeVizVolume SELECTS the prim it creates, which silently steals
        # the transform gizmo from whatever the user was about to drag. That breaks the
        # core workflow: clip to the ROI, show a field, then drag the box — the gizmo is
        # no longer on the box, so the drag grabs nothing. Remember the selection here and
        # restore it once the rebuild settles.
        try:
            import omni.usd
            prev_selection = list(
                omni.usd.get_context().get_selection().get_selected_prim_paths() or [])
        except Exception:
            prev_selection = []
        self._vol_seq += 1
        new_path = f"/World/CAE/InternalVolume_{self._vol_seq}"
        # Create the volume fresh. Keep it HIDDEN until the colormap is applied so IndeX
        # never composites the default opaque colormap (the 'solid red block' flash).
        await execute_command("CreateCaeVizVolume", dataset_path=self.dataset_path,
                              prim_path=new_path, type="vdb")
        vpr = stage.GetPrimAtPath(new_path)
        if not (vpr and vpr.IsValid()):
            print("[dc.cfd_bridge] rebuild: volume prim missing after create")
            return
        UsdGeom.Imageable(vpr).MakeInvisible()
        # Publish the new path NOW so the colormap block + the range/Off handlers target
        # the live volume.
        self.volume_path = new_path
        self.colormap_path = new_path + "/Material/Colormap"

        # === Select the field to voxelise on the FRESH prim ===
        if fld:
            fs = cae_viz.FieldSelectionAPI(vpr, "colors")
            fs.CreateTargetRel().SetTargets([fld])
            # Umag and T are scalars -> unchanged; the raw vec3 U fallback -> magnitude.
            fs.CreateModeAttr().Set(
                cae_viz.Tokens.vector_magnitude
                if (field == "U" and not u_scalar) else cae_viz.Tokens.unchanged)
        # Set the ROI directly on the FRESH prim: bound to the box when clipping, cleared
        # otherwise. The initial exec voxelises against these bounds — no clear+rebind
        # dance and no manual get_input_dataset cache drop is needed (the prim is new, so
        # its first exec is always a full rebuild that voxelises the current ROI).
        roi_rel = cae_viz.DatasetVoxelizationAPI(vpr, "source").GetRoiRel()
        roi_rel.SetTargets([self.roi_path] if roi_enabled else [])
        # CreateCaeVizVolume applies CaeVizRescaleRangeAPI("colors"), which rewrites
        # the colormap domain to the field's raw min/max on every full exec. That
        # exec can land AFTER the pinned domain below (first voxelise-kernel compile,
        # slow voxelisation), so the pin silently loses; on a uniform field the raw
        # range is zero-width and the whole volume maps to the opaque end colour
        # (measured 2026-09-30: SyntheticAirDisplay T rendered solid red, the
        # read-back domain was (303, 303)). The domain is always pinned here, so
        # auto-rescale is switched off before the first exec.
        try:
            cae_viz.RescaleRangeAPI(vpr, "colors").CreateRescaleModeAttr().Set(
                cae_viz.Tokens.disable)
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] rebuild: could not disable colormap auto-rescale: {e}")

        # Let the re-exec voxelise within the ROI box (heavy for the ~1M-cell .vtu) and
        # refill index_volume. The completion signal is the IndeX grid landing in the DAV
        # cache under this volume's own key — the prim path is always fresh, so the entry
        # can only be this exec's.
        #
        # NOT `extent`: it is written for some rebuilds and left None for others (both
        # appear in the same session's log), so polling on it silently burned the full
        # 300-cycle ceiling on the very first measured run.
        # CONSERVATIVE, pending a verified predicate. Two candidate completion signals
        # were tried and BOTH burned the full ceiling on a live run: `extent` (written for
        # some rebuilds, left None for others — both appear in one session's log) and the
        # DAV cache entry under this volume's key. Rather than guess a third time, take
        # the safe win and record what actually flips.
        #
        # The safe win is real: wait_for_update() is TEN cycles, so the old
        # `for _ in range(30)` was 300 cycles and >=3 s of pure asyncio.sleep. Asking for
        # 30 directly is the same intent at a tenth the cost, against 83-114 ms of
        # measured voxelisation work.
        _vol_key = f"[viz:index_volume]::{new_path}"
        _t0 = _time.time()
        await self._settle(30)
        _vox_cycles = 30
        # Diagnostic for the next iteration: which signal, if either, is set by now.
        print(f"[dc.cfd_bridge] voxelise settle {_time.time() - _t0:.2f}s "
              f"extent={vpr.GetAttribute('extent').Get() is not None} "
              f"cache={_cache.get(_vol_key) is not None}")
        # Wait for the Material/Colormap child (authored by the command) before
        # configuring it; configuring a missing colormap leaves IndeX on its red
        # default ('Cannot access colormap prim').
        cm = None
        for _ in range(15):
            cm = stage.GetPrimAtPath(self.colormap_path)
            if cm and cm.IsValid():
                break
            await wait_for_update()
        # PINNED domain to the field's p1..p99 working band (_DISPLAY_DOMAIN), NOT AUTO
        # (0,-1). AUTO maps the colormap over the field's raw min/max; both fields are
        # heavily right-skewed, so >99% of voxels fall in the bottom ~60% of that bar
        # and the red half is an invisible sliver (the reported near-monochrome wash).
        # The percentile band spreads blue->amber->red across where the data lives. The
        # >p99 exhaust tail saturates to opaque red via clampToEdge (below). Trade-off
        # vs AUTO: a clipped sub-region now shows ABSOLUTE colours (same colour == same
        # temperature everywhere) instead of auto-rescaling to its local range — which
        # is MORE correct for a temperature demo. The Low/High sliders re-tighten live.
        dom = self._volume_domain_override.get(field) or _DISPLAY_DOMAIN.get(field, (0.0, 1.0))
        if cm and cm.IsValid():
            cm.GetAttribute("domain").Set((float(dom[0]), float(dom[1])))
            # clampToEdge: values outside [lo,hi] clamp to the nearest edge colour and
            # stay visible — a continuous gradient, nothing vanishes; the thin hot tail
            # past hi saturates to the opaque red end (solid plumes, not a void).
            cm.GetAttribute("domainBoundaryMode").Set("clampToEdge")
            _apply_thermal_colormap(cm, getattr(self, "volume_palette", None))
        else:
            print("[dc.cfd_bridge] rebuild: colormap prim never appeared")
        # Wait for the domain we just wrote to read back, rather than for 200 frames.
        if cm and cm.IsValid():
            await self._poll(
                lambda: cm.GetAttribute("domain").Get() is not None,
                "colormap-domain", max_cycles=200)
        actual_dom = None
        if cm and cm.IsValid():
            actual_dom = cm.GetAttribute("domain").Get()
        img = UsdGeom.Imageable(vpr)
        if img:
            img.MakeVisible() if make_visible else img.MakeInvisible()
        # Retire the OLD volume now that the new one is live (no visual gap). Composite-off
        # FIRST to release IndeX's GPU refs (else RemovePrim is a use-after-free crash),
        # then hide, settle, drop its [viz:index_volume] cache, and remove the prim.
        if old_path and old_path != new_path:
            old = stage.GetPrimAtPath(old_path)
            if old and old.IsValid():
                try:
                    old.GetAttribute("nvindex:composite").Set(False)
                except Exception:
                    pass
                try:
                    UsdGeom.Imageable(old).MakeInvisible()
                except Exception:
                    pass
                await self._settle(6)
                try:
                    _cache.remove(f"[viz:index_volume]::{old_path}")
                except Exception:
                    pass
                stage.RemovePrim(old_path)
                await self._settle(4)
        # Receipt the UI/driver waits on. `extent` is the voxelised grid bounds the
        # operator wrote from the (ROI-intersected) dataset — full domain when not
        # clipping, the sub-box once clipped.
        # Give the gizmo back to whatever the user had selected before this rebuild (see
        # prev_selection above). The retired volume path is dropped — selecting a prim we
        # just removed would leave the gizmo on nothing.
        try:
            import omni.usd
            keep = [p for p in prev_selection
                    if p != old_path and stage.GetPrimAtPath(p).IsValid()]
            sel = omni.usd.get_context().get_selection()
            if keep and list(sel.get_selected_prim_paths() or []) != keep:
                sel.set_selected_prim_paths(keep, True)
                print(f"[dc.cfd_bridge] selection restored after rebuild -> {keep}")
        except Exception as e:
            print(f"[dc.cfd_bridge] selection restore skipped ({e})")
        ext = vpr.GetAttribute("extent").Get()
        print(f"[dc.cfd_bridge] volume rebuilt cycles={_vox_cycles} field={field} roi={roi_enabled} "
              f"visible={make_visible} path={new_path} domain_set={tuple(dom)} "
              f"domain_actual={tuple(actual_dom) if actual_dom else None} extent={ext}")
        # DEBUG (velocity diagnosis): dump the colormap + field-selection state IndeX
        # actually renders, so a solid-colour volume can be traced to domain vs field.
        try:
            _rgba = cm.GetAttribute("rgbaPoints").Get() if (cm and cm.IsValid()) else None
            _alphas = [round(float(v[3]), 2) for v in _rgba] if _rgba else None
            _src = cm.GetAttribute("colormapSource").Get() if (cm and cm.IsValid()) else None
            _fs = cae_viz.FieldSelectionAPI(vpr, "colors")
            print(f"[dc.cfd_bridge] DBG field={field} cmSrc={_src} alphas={_alphas} "
                  f"mode={_fs.GetModeAttr().Get()} colorTarget={[str(t) for t in _fs.GetTargetRel().GetTargets()]}")
        except Exception as _e:
            print(f"[dc.cfd_bridge] DBG read err: {_e}")

    # ── Frame waiting ────────────────────────────────────────────────────────
    # kit-cae's wait_for_update() defaults to TEN app cycles, each followed by a 10 ms
    # asyncio.sleep (omni.cae.testing/__init__.py:80-99). Every `for _ in range(N):
    # await wait_for_update()` in this file therefore costs 10*N cycles and >=N*100 ms of
    # pure sleeping — the volume rebuild's 30+20+6+4 is 600 cycles and >=6 s of sleep
    # against 83-114 ms of real voxelisation work. The nesting is almost certainly a
    # misreading of the default; nothing here wants 300 frames.

    @staticmethod
    async def _settle(cycles=3):
        """Yield `cycles` app cycles. Use where there is nothing to poll on."""
        from omni.cae.testing import wait_for_update
        await wait_for_update(cycles=cycles)

    @staticmethod
    async def _poll(check, label, max_cycles=300, quiet=False):
        """Return as soon as `check()` is true, instead of burning a fixed budget.

        Returns (ok, cycles). The ceiling is the old fixed count, so the worst case is
        no slower than before while the common case returns in a few frames.
        """
        from omni.cae.testing import wait_for_update
        for i in range(max_cycles):
            try:
                if check():
                    return True, i
            except Exception:
                pass
            await wait_for_update(cycles=1)
        if not quiet:
            print(f"[dc.cfd_bridge] wait({label}) hit its {max_cycles}-cycle ceiling")
        return False, max_cycles

    # ── Streamlines ──────────────────────────────────────────────────────────
    @staticmethod
    def _unique_seeds(res):
        """Distinct surface vertices of CreateCaeVizMeshPrim's res x res UV sphere.

        NOT res**2. The poles collapse to one vertex each and the phi=0 seam repeats the
        phi=2*pi column, leaving (res-2)*(res-1)+2 unique points: res 5 gives 14, not 25.
        Asking for 27 seeds therefore produced 14 — a 45% shortfall that made every trace
        sparser than requested.
        """
        r = max(3, int(res))
        return (r - 2) * (r - 1) + 2

    @classmethod
    def _seed_resolution(cls, n_seeds):
        """Smallest UV-sphere resolution whose UNIQUE vertex count reaches `n_seeds`."""
        want = max(1, int(n_seeds))
        for r in range(3, 41):
            if cls._unique_seeds(r) >= want:
                return r
        return 40

    def bake_roi_xform(self, stage):
        """Bake a VIEWPORT DRAG of the ROI box into the box's authored points.

        Omniverse's transform gizmo moves a prim by authoring an XFORM; it never
        rewrites the prim's points. But the voxeliser reads the box's LOCAL bound (the
        authored points) and ignores the xform stack, so a dragged box moved on screen
        while the clip region stayed behind — the user saw the wireframe in one place and
        the clipped volume in another. Re-author the points at their WORLD positions and
        clear the xform, so local == world again and the voxeliser sees the drag.

        Idempotent: with an identity xform (nothing dragged) it is a pure read.
        Returns the resulting world AABB as ``(mn, mx)``, or ``None`` with no ROI prim.
        """
        from pxr import UsdGeom, Vt, Gf
        prim = stage.GetPrimAtPath(self.roi_path)
        if not (prim and prim.IsValid()):
            return None
        pts_attr = prim.GetAttribute("points")
        pts = pts_attr.Get() if (pts_attr and pts_attr.IsValid()) else None
        if not pts:
            return None
        xf = UsdGeom.Xformable(prim)
        m = None
        if xf:
            try:
                m = xf.GetLocalTransformation()
            except Exception:
                m = None
        if geom.is_identity(m):
            return geom.aabb_of(pts)
        world = geom.transform_points(pts, m)
        pts_attr.Set(Vt.Vec3fArray([Gf.Vec3f(*p) for p in world]))
        try:
            xf.ClearXformOpOrder()
        except Exception:
            pass
        mn, mx = geom.aabb_of(world)
        ext = prim.GetAttribute("extent")
        if ext and ext.IsValid():
            ext.Set(Vt.Vec3fArray([Gf.Vec3f(*mn), Gf.Vec3f(*mx)]))
        print(f"[dc.cfd_bridge] ROI from viewport -> "
              f"min=({mn[0]:.3f}, {mn[1]:.3f}, {mn[2]:.3f}) "
              f"max=({mx[0]:.3f}, {mx[1]:.3f}, {mx[2]:.3f})")
        return mn, mx

    def _live_seed_center(self, stage):
        """The seed sphere's CURRENT translation — i.e. where the user dragged it.
        ``None`` when the prim does not exist yet."""
        from pxr import UsdGeom
        prim = stage.GetPrimAtPath(self.seed_sphere_path)
        if not (prim and prim.IsValid()):
            return None
        xf = UsdGeom.Xformable(prim)
        if not xf:
            return None
        try:
            m = xf.GetLocalTransformation()
        except Exception:
            return None
        return [float(m[3][0]), float(m[3][1]), float(m[3][2])]

    def adopt_dragged_seed(self, stage):
        """THE VIEWPORT WINS: if the sphere's live translation has drifted from the last
        centre we placed it at, the user dragged it — take that as the new seed centre.

        Without this, every Apply re-placed the sphere at the frontend's centre, so a
        dragged ball visibly snapped back to its default and the seeds were emitted at
        the old spot. Returns the adopted centre, or ``None`` if it was not dragged."""
        live = self._live_seed_center(stage)
        if live is None:
            return None
        if not geom.moved(self._seed_commanded_center, live):
            return None
        self.seed_center = live
        print(f"[dc.cfd_bridge] seed from viewport -> "
              f"center=({live[0]:.3f}, {live[1]:.3f}, {live[2]:.3f})")
        return live

    def adopt_dragged_slice(self, stage):
        """THE VIEWPORT WINS for the slice disc, mirroring adopt_dragged_seed."""
        from . import slice_render
        live = slice_render.disc_origin_from_xform(stage)
        if live is None or self._slice_commanded_origin is None:
            return None
        if not geom.moved(self._slice_commanded_origin, live):
            return None
        self.slice_origin = [float(v) for v in live]
        print(f"[dc.cfd_bridge] slice from viewport -> "
              f"origin=({live[0]:.3f}, {live[1]:.3f}, {live[2]:.3f})")
        return self.slice_origin

    def resolve_slice_origin(self, stage, origin):
        """Arbitrate frontend numbers vs a viewport drag, seed-sphere style:
        changed UI numbers win; otherwise a drifted disc wins."""
        ui = [float(x) for x in origin]
        ui_changed = geom.moved(self._slice_ui_origin, ui)
        self._slice_ui_origin = ui
        if ui_changed:
            self.slice_origin = ui
        else:
            self.adopt_dragged_slice(stage)
        self._slice_commanded_origin = list(self.slice_origin)

    async def update_slice(self, stage):
        """(Re)author the preview disc and, when a dataset is loaded and the
        cache key moved, re-cut the plane and recolour the slice mesh.

        Returns the summary the setSlice/getSliceState ack carries:
        ``{tris, field, found, min, max, cached, reason}``. The VTK read and cut
        run in an executor (a large result would otherwise stall the stream);
        only the USD authoring happens on Kit's loop."""
        import asyncio
        from pxr import UsdGeom
        from . import slice_render
        disc = slice_render.author_slice_disc(
            stage, self.slice_origin, self.slice_normal, self.slice_radius)
        self._slice_commanded_origin = list(self.slice_origin)
        imageable = UsdGeom.Imageable(disc.GetPrim())
        if self.slice_visible and getattr(self, "slice_show_disc", True):
            imageable.MakeVisible()
            try:
                import omni.usd
                omni.usd.get_context().get_selection().set_selected_prim_paths(
                    [slice_render.SLICE_DISC_PATH], False)
            except Exception as exc:  # selection is a drag affordance, not correctness
                print(f"[dc.cfd_bridge] slice disc select skipped: {exc}")
        else:
            imageable.MakeInvisible()
        mesh_prim = stage.GetPrimAtPath(slice_render.SLICE_MESH_PATH)
        dataset = getattr(self, "source_vtu_path", None) or getattr(self, "vtu_path", None)
        if not self.slice_visible or not dataset:
            if mesh_prim:
                UsdGeom.Imageable(mesh_prim).MakeInvisible()
            return {'tris': 0, 'cached': False,
                    'reason': 'hidden' if not self.slice_visible else 'no dataset loaded'}
        palette = getattr(self, "slice_palette", None)
        key = slice_render.cache_key(dataset, self.slice_origin, self.slice_normal,
                                     self.slice_radius, self.slice_field, self.slice_domain) + f"|{palette}"
        if key == self._slice_cache_key and mesh_prim and self._slice_summary:
            UsdGeom.Imageable(mesh_prim).MakeVisible()
            return {**self._slice_summary, 'cached': True}
        cached = getattr(self, '_slice_grid', None)
        origin, normal = list(self.slice_origin), list(self.slice_normal)
        radius, field, domain = self.slice_radius, self.slice_field, self.slice_domain

        def _work():
            grid = cached[1] if cached and cached[0] == dataset else slice_render.read_grid(dataset)
            return grid, slice_render.slice_geometry(grid, origin, normal, radius, field, domain, palette)

        grid, out = await asyncio.get_event_loop().run_in_executor(None, _work)
        self._slice_grid = (dataset, grid)
        summary = out['summary']
        if not out['tris']:
            print("[dc.cfd_bridge] slice cut produced no triangles (plane misses the dataset)")
            if mesh_prim:
                UsdGeom.Imageable(mesh_prim).MakeInvisible()
            self._slice_cache_key = key
            self._slice_summary = {**summary, 'reason': 'plane misses the dataset'}
            return {**self._slice_summary, 'cached': False}
        slice_render.author_slice_mesh(stage, out['points'], out['tris'], out['colors'])
        UsdGeom.Imageable(stage.GetPrimAtPath(slice_render.SLICE_MESH_PATH)).MakeVisible()
        self._slice_cache_key = key
        self._slice_summary = {**summary, 'reason': None}
        print(f"[dc.cfd_bridge] slice rebuilt field={field} tris={summary['tris']} "
              f"found={summary['found']} radius={radius:.3f} min={summary.get('min')} "
              f"max={summary.get('max')} domain={domain} palette={palette}")
        return {**self._slice_summary, 'cached': False}

    async def show_volume_display(self, stage, field, domain=None, palette=None):
        """Acked OV-display volume: 'off' hides; 'T'/'U' rebuild the IndeX
        volume on this dataset's own colour domain and return what IndeX was
        actually given, so the browser's colour bar reads the real numbers."""
        from pxr import UsdGeom
        from . import handlers, slice_render
        if field == 'off':
            handlers.handle_set_volume(self, stage, 'T', False)
            return {'field': 'off', 'visible': False}
        if not self.dataset_path:
            raise ValueError('no dataset loaded — load a result into OV first')
        self.volume_palette = palette
        chosen = slice_render.volume_display_domain(self.field_stats, field, domain)
        if chosen is not None:
            self._volume_domain_override[field] = (chosen[0], chosen[1])
            source, degenerate = chosen[2], chosen[3]
        else:
            self._volume_domain_override.pop(field, None)
            source, degenerate = 'pinned default', False
        # handle_set_volume's show path, awaited instead of scheduled: a queued
        # rebuild would let the ack race ahead of the volume it describes.
        self.active_field = field
        handlers._set_geometry_hidden(self, stage, True)
        await self._rebuild_volume(stage, field=field, roi_enabled=self.clip_enabled,
                                   make_visible=True)
        # A rebuild already in flight only queues ours; wait for the drain.
        await self._poll(lambda: not self._volume_busy and self._pending is None,
                         'volume-display', max_cycles=3000)
        # Let IndeX's own exec land, then read back what it is really using: the
        # ack reports the domain on the prim, not the one we asked for.
        from omni.cae.testing import wait_for_update
        for _ in range(3):
            await wait_for_update()
        cm = stage.GetPrimAtPath(self.colormap_path)
        # IndeX reads the transfer function when the colormap CHANGES. With
        # auto-rescale off nothing writes it during/after the volume's exec, so
        # IndeX kept the colormap state it captured before the exec (measured:
        # volume composited but nothing drawn). The reference range slider works
        # by writing the domain; do the same once the volume is live.
        pinned = self._volume_domain_override.get(field) or _DISPLAY_DOMAIN.get(field)
        if cm and cm.IsValid() and pinned:
            lo, hi = float(pinned[0]), float(pinned[1])
            cm.GetAttribute("domain").Set((lo, hi + max(1e-6, abs(hi) * 1e-6)))
            await wait_for_update()
            cm.GetAttribute("domain").Set((lo, hi))
            await wait_for_update()
        dom = cm.GetAttribute('domain').Get() if cm and cm.IsValid() else None
        wanted = self._volume_domain_override.get(field)
        if wanted and dom is not None and (abs(dom[0] - wanted[0]) > 1e-3 * max(1.0, abs(wanted[0]))
                                           or abs(dom[1] - wanted[1]) > 1e-3 * max(1.0, abs(wanted[1]))):
            source = f'{source}; overridden to {tuple(round(float(v), 4) for v in dom)}'
        vol = stage.GetPrimAtPath(self.volume_path)
        visible = bool(vol and vol.IsValid()
                       and UsdGeom.Imageable(vol).ComputeVisibility() != UsdGeom.Tokens.invisible)
        # IndeX's operator switches nvindex:composite on as the LAST step of its
        # exec, which lands several seconds after the rebuild returns (measured
        # ~10 s on a 10x6x2.8 m room). Ack once the volume is really composited,
        # or say it is not yet.
        composited, _ = await self._poll(
            lambda: bool(vol and vol.IsValid() and vol.GetAttribute('nvindex:composite').Get()),
            'volume-composite', max_cycles=1500)
        return {'field': field, 'visible': visible, 'composited': bool(composited),
                'source': source, 'degenerate': degenerate,
                'domain': [float(dom[0]), float(dom[1])] if dom is not None else None}

    async def remove_slice(self, stage):
        from pxr import UsdGeom
        from . import slice_render
        for path in (slice_render.SLICE_MESH_PATH, slice_render.SLICE_DISC_PATH):
            prim = stage.GetPrimAtPath(path)
            if prim:
                UsdGeom.Imageable(prim).MakeInvisible()
        self._slice_cache_key = None
        self._slice_summary = None

    def _current_roi_aabb(self, stage):
        """The ROI box's CURRENT world AABB. Bakes any viewport drag into the points
        first, so callers (the seed clamp, the voxeliser) see where the box actually is,
        falling back to the base AABB when the prim has no points."""
        baked = self.bake_roi_xform(stage)
        if baked:
            return list(baked[0]), list(baked[1])
        mn = list(self.roi_base_min) if self.roi_base_min else [0.0, 0.0, 0.0]
        mx = list(self.roi_base_max) if self.roi_base_max else [1.0, 1.0, 1.0]
        return mn, mx

    async def _place_seed_prim(self, stage, path):
        """Position + scale a seed prim (the visual sphere OR the seed-point source) to the
        current centre/radius. ROI-IS-GOD: when clipping is active, the radius is capped and
        the centre clamped so the WHOLE sphere stays inside the ROI box (every seed starts
        in-bounds; lines are additionally clipped by the nanovdb grid)."""
        from omni.cae.data.commands import execute_command
        from pxr import UsdGeom, Gf
        c = [float(x) for x in self.seed_center]
        r = float(self.seed_radius)
        rv = [r, r, r]
        if self.clip_enabled:
            mn, mx = self._current_roi_aabb(stage)
            # PER AXIS, not one isotropic cap. `min()` over all three axes meant the
            # THINNEST axis shrank the ball in every direction: a 4.4 x 1.0 x 2.0 m ROI
            # slab collapsed a 2.0 m sphere to 0.50 m, a 64x loss of seeded volume, and
            # the traces came out sparse and short. Scaling per axis fills the slab with
            # an ellipsoid, which is what asking for a big seed ball in a flat region
            # means. TransformPrimSRT already takes a 3-vector scale.
            for i in range(3):
                half = max((mx[i] - mn[i]) / 2.0, 1e-3)
                rv[i] = min(r, half)
                # Pull in by a hair so the extreme vertices do not land exactly ON the
                # nanovdb faces, where they sample out of bounds and yield no curve.
                pad = min(rv[i] * 1e-3, (mx[i] - mn[i]) * 1e-3)
                c[i] = min(max(c[i], mn[i] + rv[i] + pad), mx[i] - rv[i] - pad)
        self._seed_applied_radius = list(rv)
        try:
            await execute_command("TransformPrimSRT", path=path,
                                  new_translation=c, new_scale=rv)
        except Exception as e:
            print(f"[dc.cfd_bridge] TransformPrimSRT failed ({e}); XformCommonAPI fallback")
            sp = stage.GetPrimAtPath(path)
            if sp and sp.IsValid():
                xf = UsdGeom.XformCommonAPI(sp)
                xf.SetTranslate(Gf.Vec3d(*c))
                xf.SetScale(Gf.Vec3f(*rv))
        # Remember where WE put it (post-clamp — that IS the prim's translation), so a
        # later drift can only mean the user dragged the gizmo. See adopt_dragged_seed.
        self._seed_commanded_center = list(c)
        return c, rv

    async def update_seed_sphere(self, stage, visible):
        """Create / position / show the draggable VISUAL seed-sphere preview — a SMOOTH,
        translucent ball at a FIXED high tessellation (``_visual_seed_res``) so it always
        reads as a real sphere no matter how few seeds are requested. Centre + radius drive
        the xform. When shown, the sphere is selected so Omniverse's transform gizmo attaches
        (drag to reposition), the same mechanism as the ROI box. The actual seed POINTS live
        on the separate invisible SeedPoints prim (see ``_ensure_seed_source``)."""
        from omni.cae.data.commands import execute_command
        from omni.cae.testing import wait_for_update
        from pxr import UsdGeom, Gf, Vt
        import omni.usd
        self.seed_visible = bool(visible)
        sp = stage.GetPrimAtPath(self.seed_sphere_path)
        if not (sp and sp.IsValid()):
            try:
                await execute_command("CreateCaeVizMeshPrim", prim_type="UnitSphere",
                                      prim_path=self.seed_sphere_path,
                                      resolution=self._visual_seed_res)
            except Exception as e:
                print(f"[dc.cfd_bridge] CreateCaeVizMeshPrim failed ({e}); Sphere fallback")
                UsdGeom.Sphere.Define(stage, self.seed_sphere_path)
            for _ in range(4):
                await wait_for_update()
            # translucent blue preview bubble (like the HTML seed sphere)
            try:
                g = UsdGeom.Gprim(stage.GetPrimAtPath(self.seed_sphere_path))
                g.CreateDisplayColorPrimvar().Set(Vt.Vec3fArray([Gf.Vec3f(0.27, 0.53, 1.0)]))
                g.CreateDisplayOpacityPrimvar().Set(Vt.FloatArray([0.28]))
            except Exception:
                pass
        await self._place_seed_prim(stage, self.seed_sphere_path)
        img = UsdGeom.Imageable(stage.GetPrimAtPath(self.seed_sphere_path))
        ctx = omni.usd.get_context()
        if visible:
            if img:
                img.MakeVisible()
            try:
                ctx.set_pickable(self.seed_sphere_path, True)     # let the gizmo grab it
                ctx.get_selection().set_selected_prim_paths([self.seed_sphere_path], True)
            except Exception:
                pass
        else:
            if img:
                img.MakeInvisible()
            try:
                ctx.get_selection().clear_selected_prim_paths()
            except Exception:
                pass
        # Print the APPLIED centre/radius. Printing the requested 2.0 while the clamp had
        # actually used 0.50 is what made this failure take three investigations to read.
        _ap = getattr(self, "_seed_applied_radius", None) or [self.seed_radius] * 3
        print(f"[dc.cfd_bridge] seed sphere visible={visible} visual_res={self._visual_seed_res} "
              f"center={tuple(round(float(x), 3) for x in self._seed_commanded_center)} "
              f"radius_req={self.seed_radius} radius_applied="
              f"{tuple(round(float(v), 3) for v in _ap)} clip={self.clip_enabled}")

    async def _ensure_seed_source(self, stage):
        """Create / refresh the INVISIBLE seed-point source: a UnitSphere MESH whose ~res²
        surface vertices ARE the streamline seeds (res from the requested seed COUNT). Kept
        separate from the smooth visual preview so the count never makes the ball blocky.
        Recreated only when the resolution changes; always re-placed to the current xform."""
        from omni.cae.data.commands import execute_command
        from omni.cae.testing import wait_for_update
        from pxr import UsdGeom
        sp = stage.GetPrimAtPath(self.seed_source_path)
        res = self._seed_resolution(self.n_seeds)
        if (not (sp and sp.IsValid())) or res != self._seed_res:
            if sp and sp.IsValid():
                stage.RemovePrim(self.seed_source_path)
                for _ in range(2):
                    await wait_for_update()
            try:
                await execute_command("CreateCaeVizMeshPrim", prim_type="UnitSphere",
                                      prim_path=self.seed_source_path, resolution=res)
            except Exception as e:
                print(f"[dc.cfd_bridge] seed source create failed ({e}); Sphere fallback")
                UsdGeom.Sphere.Define(stage, self.seed_source_path)
            self._seed_res = res
            for _ in range(4):
                await wait_for_update()
        await self._place_seed_prim(stage, self.seed_source_path)
        img = UsdGeom.Imageable(stage.GetPrimAtPath(self.seed_source_path))
        if img:
            img.MakeInvisible()

    def _pin_streamline_colours(self, stage):
        """Give the streamline ScalarColor/AnimatedStreaks shaders a real colour
        domain. basic.mdl only colours when domain.x < domain.y (and
        enable_coloring); otherwise it samples the LUT at 1.0, which for
        gist_rainbow is magenta — every OV streamline rendered solid magenta
        because the auto-rescale never wrote the creation-time (0, -1). Uses
        the same dataset band as the slice/volume colour bars. Returns the
        domain applied, or None."""
        from pxr import Sdf, Usd, UsdShade
        from . import slice_render
        field = "T" if self.streamline_field == "T" else "U"
        chosen = slice_render.streamline_display_domain(getattr(self, "field_stats", None), field)
        override = getattr(self, "streamline_domain_override", None)
        if override:
            chosen = (float(override[0]), float(override[1]))
        if chosen is None:
            lo, hi = (_DISPLAY_DOMAIN.get(field) or (0.0, 1.0))
        else:
            lo, hi = chosen[0], chosen[1]
        applied = None
        root = stage.GetPrimAtPath(self.streamlines_path + "/Materials")
        if not root:
            return None
        for prim in Usd.PrimRange(root):
            if not prim.IsA(UsdShade.Shader):
                continue
            shader = UsdShade.Shader(prim)
            if shader.GetInput("domain"):
                shader.GetInput("domain").Set((float(lo), float(hi)))
                applied = [float(lo), float(hi)]
            en = shader.GetInput("enable_coloring") or shader.CreateInput("enable_coloring", Sdf.ValueTypeNames.Bool)
            en.Set(True)
            palette = getattr(self, "streamline_palette", None)
            if palette and shader.GetInput("lut"):
                shader.GetInput("lut").Set(_palette_lut(palette))
        try:
            from omni.cae.schema import viz as cae_viz
            cae_viz.RescaleRangeAPI(stage.GetPrimAtPath(self.streamlines_path), "colors") \
                .CreateRescaleModeAttr().Set(cae_viz.Tokens.disable)
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] streamlines: could not disable colour auto-rescale: {e}")
        print(f"[dc.cfd_bridge] streamline colours pinned field={field} domain={applied} "
              f"resolved={self._streamline_domain_readback(stage)}")
        return applied

    def _streamline_domain_readback(self, stage):
        """What the renderer will read: the USD-resolved and the Fabric (usdrt)
        value of every streamline shader's domain/enable_coloring."""
        from pxr import Usd, UsdShade
        out = []
        root = stage.GetPrimAtPath(self.streamlines_path + "/Materials")
        if not root:
            return out
        for prim in Usd.PrimRange(root):
            if not prim.IsA(UsdShade.Shader):
                continue
            shader = UsdShade.Shader(prim)
            dom = shader.GetInput("domain")
            en = shader.GetInput("enable_coloring")
            entry = {"shader": prim.GetParent().GetName(),
                     "usd": [tuple(dom.Get()) if dom and dom.Get() is not None else None,
                             en.Get() if en else None]}
            try:
                from omni.cae.data import usd_utils
                rt = usd_utils.get_prim_rt(prim)
                rd = rt.GetAttribute("inputs:domain").Get()
                re_ = rt.GetAttribute("inputs:enable_coloring").Get()
                entry["fabric"] = [tuple(rd) if rd is not None else None, re_]
            except Exception as e:  # noqa: BLE001
                entry["fabric"] = f"unavailable: {e}"
            out.append(entry)
        return out

    async def generate_streamlines(self, stage):
        """(Re)generate the streamlines from the current seed sphere + params.
        Coalesces like the volume rebuild so rapid Apply / ROI-drag requests converge
        on the last state instead of racing."""
        if not self.dataset_path:
            print("[dc.cfd_bridge] streamlines: no dataset loaded")
            return
        self._sl_pending = True
        if self._sl_busy:
            return
        self._sl_busy = True
        try:
            while self._sl_pending:
                self._sl_pending = False
                await self._do_generate_streamlines(stage)
        finally:
            self._sl_busy = False

    async def _do_generate_streamlines(self, stage):
        """Build the streamlines with native kit-cae (GPU Warp RK4(5)): integrate on the
        vec3 U field, colour by a SCALAR field (T default; |U|/Umag via the toggle) with
        the auto-scaling ScalarColor MDL material, seeded from the sphere. type="nanovdb"
        so the lines are confined to the ROI box when clipping is active."""
        from omni.cae.data.commands import execute_command
        from omni.cae.schema import viz as cae_viz
        from omni.cae.testing import wait_for_update
        if not self.u_field:
            print("[dc.cfd_bridge] streamlines: no vec3 U field for integration")
            return
        # Colour field: T (scalar) default; |U| colours by the scalar Umag like the
        # volume. Integration ALWAYS uses the vec3 U field regardless.
        color_fld = (self.umag_field or self.u_field) if self.streamline_field == "U" \
            else self.t_field

        # Keep the VISUAL preview placed (clamped into the ROI when clipping) at whatever
        # visibility the user chose, and (re)build the INVISIBLE seed-point source that the
        # integrator actually seeds from.
        await self.update_seed_sphere(stage, visible=self.seed_visible)
        await self._ensure_seed_source(stage)

        # BasisCurves are not IndeX prims: just remove + recreate on each Apply.
        if stage.GetPrimAtPath(self.streamlines_path).IsValid():
            stage.RemovePrim(self.streamlines_path)
            for _ in range(3):
                await wait_for_update()

        await execute_command("CreateCaeVizStreamlines", dataset_path=self.dataset_path,
                              prim_path=self.streamlines_path, type="nanovdb")
        viz = stage.GetPrimAtPath(self.streamlines_path)
        if not (viz and viz.IsValid()):
            print("[dc.cfd_bridge] streamlines: prim missing after create")
            return

        # NO update pump between create and configure. There used to be ten
        # wait_for_update() calls here, which let the CaeViz controller run a sync
        # against a prim whose `seeds` relationship had no targets yet — that is exactly
        # the "[omni.cae.viz.controller] Streamlines: Missing targets on
        # .../dataset_selection:seeds:target" warning. kit-cae's own examples and tests
        # (example_streamlines.py, test_streamlines.py, load_gtc_final.py) all wire the
        # relationships in the same block as the create, and never emit it.
        #
        # It is not cosmetic. That first exec fails, and its `finally` still records
        # last_execution[operator_class]/[device]; if the sync's trailing
        # clear_changes(prim) lands after our relationship edits, prim_changed is False
        # and Streamlines has supports_temporal=False, so nothing ever re-triggers it on
        # a static .vtu. The prim would then sit there forever with a warning and no
        # curves — "it failed, but only a warning", which is the report we got. Today it
        # worked only because the ordering happened to fall the other way.
        #
        # Belt and braces: keep the operator DISABLED until every input is set, so no
        # sync can observe a half-configured prim at all.
        try:
            cae_viz.OperatorAPI(viz).CreateEnabledAttr().Set(False)
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] streamlines: cannot gate operator ({e})")

        # seeds -> the invisible seed-point sphere; velocities -> vec3 U (integration);
        # colors -> T or Umag.
        seeds_api = cae_viz.DatasetSelectionAPI(viz, "seeds")
        (seeds_api.GetTargetRel() or seeds_api.CreateTargetRel()).SetTargets(
            [self.seed_source_path])
        vel_api = cae_viz.FieldSelectionAPI(viz, "velocities")
        (vel_api.GetTargetRel() or vel_api.CreateTargetRel()).SetTargets([self.u_field])
        vel_api.CreateModeAttr().Set(cae_viz.Tokens.unchanged)   # keep vector components
        if color_fld:
            col_api = cae_viz.FieldSelectionAPI(viz, "colors")
            (col_api.GetTargetRel() or col_api.CreateTargetRel()).SetTargets([color_fld])

        sl = cae_viz.StreamlinesAPI(viz)
        dtok = getattr(cae_viz.Tokens, self.sl_direction, None)
        if dtok:
            sl.GetDirectionAttr().Set(dtok)
        sl.GetMaxStepsAttr().Set(int(self.sl_max_steps))
        sl.GetWidthAttr().Set(float(self.sl_width))

        # ROI-IS-GOD: clip the traced lines to the ROI box (nanovdb grid == ROI bounds)
        # when clipping is active; otherwise trace the full domain.
        roi_rel = cae_viz.DatasetVoxelizationAPI(viz, "source").GetRoiRel()
        roi_rel.SetTargets([self.roi_path] if self.clip_enabled else [])

        # Colour domain before the FIRST execution: the ScalarColor material is
        # compiled when the curves first appear, and basic.mdl turns colouring
        # off for the creation-time domain (0, -1). Pinning only after the trace
        # (as before) left every streamline at the LUT's 1.0 end (magenta).
        self.streamline_colour_domain = self._pin_streamline_colours(stage)

        # Everything is wired: let it run.
        try:
            cae_viz.OperatorAPI(viz).CreateEnabledAttr().Set(True)
        except Exception:
            pass

        # Poll on the curves actually existing. 4 points is the placeholder the create
        # command authors, so "more than 4" means the integrator wrote real geometry.
        _ok, _n = await self._poll(
            lambda: (self._streamline_readback(viz)[0] or 0) > 4,
            "streamline-trace", max_cycles=300)

        self.streamlines_enabled = True
        # Read the REAL outcome instead of announcing success unconditionally. The
        # operator writes its points through usdrt/Fabric and never updates the USD
        # `extent`, so the extent stays at the 4-point placeholder the create command
        # authored — printing it implied a degenerate 0.3 x 0.05 x 0.05 m result when the
        # curves were metres long. Count the runtime points the way kit-cae's own test
        # does, and say FAILED when there are none.
        npts, vis = self._streamline_readback(viz)
        print(f"[dc.cfd_bridge] streamline colour state after trace: {self._streamline_domain_readback(stage)}")
        _ap = getattr(self, "_seed_applied_radius", None) or [self.seed_radius] * 3
        ok = npts is None or npts > 4
        print(f"[dc.cfd_bridge] streamlines {'generated' if ok else 'FAILED (no curves)'} "
              f"cycles={_n} "
              f"field={self.streamline_field} roi={self.clip_enabled} "
              f"res={self._seed_res} seeds={self._unique_seeds(self._seed_res)} "
              f"points={npts if npts is not None else 'n/a'} visibility={vis} "
              f"center={tuple(round(float(x), 3) for x in self._seed_commanded_center)} "
              f"radius_applied={tuple(round(float(v), 3) for v in _ap)} "
              f"dir={self.sl_direction} maxSteps={self.sl_max_steps} "
              f"path={self.streamlines_path}")

    @staticmethod
    def _streamline_readback(viz):
        """(point count, visibility) of the generated curves, from the RUNTIME stage.

        The curves live in Fabric, not in USD, so a USD read reports the placeholder.
        Returns (None, None) when the runtime stage is unavailable rather than guessing.
        """
        try:
            from omni.cae.data import usd_utils
            from usdrt import UsdGeom as UsdGeomRT
            rt = usd_utils.get_prim_rt(viz)
            pts = UsdGeomRT.BasisCurves(rt).GetPointsAttr().Get()
            img = UsdGeomRT.Imageable(rt).GetVisibilityAttr().Get()
            return (len(pts) if pts is not None else 0), img
        except Exception as e:  # noqa: BLE001
            print(f"[dc.cfd_bridge] streamlines readback unavailable: {e}")
            return None, None

    async def show_surface_display(self, stage, field, domain=None, palette="coolwarm", hidden=()):
        """Surface field on the result's own boundary patches, coloured by T or
        |U| with kit-cae's ScalarColor MDL (the material the Faces operator
        binds). kit-cae's Faces operator cannot take the external faces of the
        polyhedral cut-cell mesh, so the patches foamToEnsight exported -- the
        exact wall values, not cell averages -- are authored as one mesh with
        the `colors` primvar ScalarColor reads. The engineering geometry is
        hidden while the field shows (the patches cover the same surfaces).
        'off' removes it and restores the geometry."""
        import asyncio
        from omni.cae.testing import wait_for_update
        from pxr import Sdf, Usd, UsdGeom, UsdShade, Vt
        from . import slice_render
        if field == 'off':
            if stage.GetPrimAtPath(self.surface_path).IsValid():
                stage.RemovePrim(self.surface_path)
            self._restore_surface_hidden(stage)
            for _ in range(2):
                await wait_for_update()
            self.surface_field = 'off'
            return {'field': 'off', 'visible': False}
        if not self.dataset_path:
            raise ValueError('no dataset loaded — load a result into OV first')
        src = self.surface_vtp_path
        if not src or not os.path.isfile(src):
            raise ValueError('the loaded result has no boundary patches (reload it to convert them)')
        if domain is None:
            chosen = slice_render.volume_display_domain(self.field_stats, field, None)
            domain = [chosen[0], chosen[1]] if chosen is not None else list(_DISPLAY_DOMAIN.get(field) or (0.0, 1.0))
        lo, hi = float(domain[0]), float(domain[1])

        def _read():
            import numpy as np
            from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridReader
            from vtkmodules.util.numpy_support import vtk_to_numpy
            rd = vtkXMLUnstructuredGridReader()
            rd.SetFileName(src)
            rd.Update()
            g = rd.GetOutput()
            pts = vtk_to_numpy(g.GetPoints().GetData()).astype(np.float32)
            offs = vtk_to_numpy(g.GetCells().GetOffsetsArray()).astype(np.int64)
            conn = vtk_to_numpy(g.GetCells().GetConnectivityArray()).astype(np.int32)
            counts = np.diff(offs).astype(np.int32)
            cd = g.GetCellData()
            keep = None
            hidden_set = set(hidden or ())
            pid = cd.GetArray('patchId')
            names_path = src + ".patches.json"
            if hidden_set and pid is not None and os.path.isfile(names_path):
                with open(names_path) as fh:
                    names = json.load(fh)
                drop = np.array([nm in hidden_set for nm in names] + [False], dtype=bool)
                ids = vtk_to_numpy(pid).astype(np.int64)
                keep = ~drop[np.clip(ids, 0, len(names))]
            if field == 'T':
                arr = cd.GetArray('T')
                vals = vtk_to_numpy(arr).astype(np.float32) if arr is not None else None
            else:
                arr = cd.GetArray('U')
                vals = (np.linalg.norm(vtk_to_numpy(arr), axis=1).astype(np.float32)
                        if arr is not None else None)
            if keep is not None and vals is not None:
                conn = conn[np.repeat(keep, counts)]
                counts = counts[keep]
                vals = vals[keep]
            return pts, counts, conn, vals
        pts, counts, conn, vals = await asyncio.get_event_loop().run_in_executor(None, _read)
        if vals is None:
            raise ValueError(f'the boundary patches carry no {field} values')

        mesh = UsdGeom.Mesh.Define(stage, self.surface_path)
        mesh.CreatePointsAttr().Set(Vt.Vec3fArray.FromNumpy(pts))
        mesh.CreateFaceVertexCountsAttr().Set(Vt.IntArray.FromNumpy(counts))
        mesh.CreateFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(conn))
        mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        mesh.CreateDoubleSidedAttr().Set(True)
        pv = UsdGeom.PrimvarsAPI(mesh.GetPrim()).CreatePrimvar(
            "colors", Sdf.ValueTypeNames.FloatArray, UsdGeom.Tokens.uniform)
        pv.Set(Vt.FloatArray.FromNumpy(vals))
        mat_path = self.surface_path + "/Materials/ScalarColor"
        if not stage.GetPrimAtPath(mat_path).IsValid():
            import omni.kit.commands
            omni.kit.commands.execute("CreateMdlMaterialPrim", mtl_name="ScalarColor",
                                      mtl_url="cae/mdl/basic.mdl", mtl_path=mat_path, stage=stage)
        material = UsdShade.Material(stage.GetPrimAtPath(mat_path))
        shader = material.ComputeSurfaceSource("mdl")[0]
        if not shader:
            raise RuntimeError('ScalarColor material has no MDL surface shader')
        shader.CreateInput("lut", Sdf.ValueTypeNames.Asset).Set(_palette_lut(palette))
        shader.CreateInput("domain", Sdf.ValueTypeNames.Float2).Set((lo, hi))
        shader.CreateInput("enable_coloring", Sdf.ValueTypeNames.Bool).Set(True)
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
        UsdGeom.Imageable(mesh.GetPrim()).MakeVisible()
        self._hide_engineering_geometry(stage)
        for _ in range(3):
            await wait_for_update()
        self.surface_field = field
        print(f"[dc.cfd_bridge] surface field={field} faces={len(counts)} domain={[lo, hi]} palette={palette}")
        return {'field': field, 'visible': True, 'domain': [lo, hi], 'faces': int(len(counts))}

    def _hide_engineering_geometry(self, stage):
        """Hide the engineering scene's models (not its decorations) while the
        boundary field, which covers the same surfaces, is shown."""
        from pxr import UsdGeom
        if self._surface_hidden:
            return
        root = stage.GetPrimAtPath('/World/RenderScene')
        hidden = []
        if root and root.IsValid():
            for child in root.GetChildren():
                if child.GetName() == 'Decorations':
                    continue
                img = UsdGeom.Imageable(child)
                if img and img.ComputeVisibility() != UsdGeom.Tokens.invisible:
                    img.MakeInvisible()
                    hidden.append(str(child.GetPath()))
        self._surface_hidden = tuple(hidden)

    def _restore_surface_hidden(self, stage):
        from pxr import UsdGeom
        for path in self._surface_hidden:
            prim = stage.GetPrimAtPath(path)
            if prim and prim.IsValid():
                UsdGeom.Imageable(prim).MakeVisible()
        self._surface_hidden = ()

    async def remove_streamlines(self, stage):
        """Remove the streamlines + both seed prims (visual + source) and clear selection."""
        from omni.cae.testing import wait_for_update
        import omni.usd
        self.streamlines_enabled = False
        self.seed_visible = False
        for pth in (self.streamlines_path, self.seed_sphere_path, self.seed_source_path):
            if stage.GetPrimAtPath(pth).IsValid():
                stage.RemovePrim(pth)
        self._seed_res = 0
        try:
            omni.usd.get_context().get_selection().clear_selected_prim_paths()
        except Exception:
            pass
        for _ in range(3):
            await wait_for_update()
        print("[dc.cfd_bridge] streamlines removed")
