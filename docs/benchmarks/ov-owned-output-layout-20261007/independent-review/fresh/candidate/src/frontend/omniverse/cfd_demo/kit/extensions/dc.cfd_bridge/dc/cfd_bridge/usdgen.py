"""Author quads as USD geometry, with a content-addressed prototype cache.

Two halves:

  * `ensure_materials(stage)` builds /World/Looks/<typeKey> from the presets that
    `scripts/gen_material_library.py` extracts out of the frontend, using the Kit
    BUILT-IN OmniPBR / OmniGlass MDLs. Builtin matters: several stock GTC box assets
    reference a remote S3 MDL that does not resolve offline, which is the whole reason
    scene.py carries `_bind_builtin_mdl`.

  * `build_placement(...)` authors one placement. Geometry goes into a cached prototype
    keyed by CONTENT (class + size + slot layout + servers), never by object uid — four
    identical racks would otherwise write four identical files. The placement itself is
    just an Xform (translate + orient) referencing that prototype, flagged instanceable
    so Hydra collapses the copies to one GPU prototype. That flag is not decoration:
    scene.py:278 records a >20 GB RSS runaway from referencing a heavy asset 50x without
    it.
"""

from __future__ import annotations

import hashlib
import json
import os

# Bump when the authored geometry changes shape. It is part of the cache key, so a bump
# invalidates every cached prototype without anyone having to remember to clear a dir.
GEN_VERSION = "2"   # v2: rack internals (server 6-face boxes, cavity walls, recess)

# One texture repeat per this many metres. Authoring UVs from real dimensions is the
# reason to generate geometry instead of scaling a fixed asset: a 5 m wall and a 1 m
# wall then show the SAME texture density, which a non-uniform scale cannot do.
UV_METRES_PER_REPEAT = 0.5

_DEMO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
PRESETS_JSON = os.path.join(_DEMO, "USDresource/material_presets.json")
TEXTURE_ROOT = os.path.join(_DEMO, "USDresource/textures")
BAKED_ROOT = os.path.join(_DEMO, "USDresource/baked")
CACHE_ROOT = os.path.join(_DEMO, "kit/cache")

# Presets rendered as glass rather than PBR. Containment panels read as glazing in the
# editor; OmniGlass takes glass_color, not diffuse_tint, so they cannot share the path.
_GLASSY = frozenset()

_presets_cache = None


def presets() -> dict:
    global _presets_cache
    if _presets_cache is None:
        try:
            with open(PRESETS_JSON) as f:
                _presets_cache = json.load(f)
        except Exception as e:
            print(f"[dc.cfd_bridge] material presets unreadable ({e}); using flat greys")
            _presets_cache = {}
    return _presets_cache


def _rgb(hexval):
    if hexval is None:
        return (0.55, 0.57, 0.60)      # 'tintColor: null' = use the model colour
    return (((hexval >> 16) & 255) / 255.0, ((hexval >> 8) & 255) / 255.0,
            (hexval & 255) / 255.0)


def resolve_tint(key: str, model_color: int) -> int:
    """The frontend's tint precedence, verbatim (createMaterialForType, ~:2405).

        if a diffuse texture exists AND preset.tintColor is not null:
            colour = preset.tintColor      # preset wins
        else:
            colour = the MODEL's own colour

    The `else` branch is the one that matters and the one the first pass missed: a
    preset with NO texture does not tint anything, so an ACU shell must render in its
    per-face body colour, not in the preset's. Painting every untextured surface with
    the preset turned the ACUs white against the editor's dark blue-grey.
    """
    d = presets().get(key) or {}
    tint = d.get("tintColor")
    # `hasDiffuse` is the frontend's own notion — a procedural canvas generator OR a
    # textures/MISC photo. Deciding it from files on disk gets 'rack-bottom' wrong: it
    # has a generator but no jpg directory, so a disk check says "untextured" and keeps
    # the model colour where the editor uses the preset's.
    if d.get("hasDiffuse") and tint is not None:
        return int(tint)
    return int(model_color)


def material_for(stage, key: str, model_color: int, opacity: float = 1.0) -> str:
    """Material prim for one (typeKey, colour, opacity) triple, created on first use.

    Keyed by all three: the same typeKey drawn in two model colours is two materials
    (an untextured preset carries no colour of its own), and the same colour at two
    opacities is two more. Opacity being absent from this key is how GTC_2026 rendered
    fully OPAQUE in OV while the editor drew it fully transparent — every object in that
    project is meta.display.opacity 0, and nothing here read it.
    """
    rgb = resolve_tint(key, model_color)
    a = max(0.0, min(1.0, float(opacity)))
    suffix = "" if a >= 0.999 else f"__a{int(round(a * 100)):03d}"
    path = f"/World/Looks/{key.replace('-', '_')}__{rgb:06x}{suffix}"
    if not stage.GetPrimAtPath(path).IsValid():
        _author_material(stage, path, key, rgb, a)
    return path


def ensure_materials(stage) -> dict:
    """Create /World/Looks/<key> once per stage. Returns {typeKey: material path}.

    Authored DIRECTLY, not through CreateMdlMaterialPrimCommand. That command builds the
    shader asynchronously, so reading back `<mtl>/Shader` on the next line found nothing
    and the guard skipped every input — materials came out as untinted default OmniPBR,
    which is why the OV racks rendered mid-grey against the editor's near-black while
    every geometric check passed. Direct authoring is synchronous and inspectable.

    The tint maps Three.js's `MeshPhongMaterial.color`, which MULTIPLIES its `map`, onto
    OmniPBR's `diffuse_tint`, which multiplies `diffuse_texture` the same way. Untextured
    presets additionally need `diffuse_color_constant`, since there is nothing for the
    tint to multiply.
    """
    out = {}
    for key in presets():
        out[key] = f"/World/Looks/{key.replace('-', '_')}"
    print(f"[dc.cfd_bridge] material presets loaded: {len(out)} "
          "(instances authored on first use, per (preset, model colour))")
    return out


def _author_material(stage, path: str, key: str, rgb: int, opacity: float = 1.0):
    """Author one OmniPBR/OmniGlass material at `path`, tinted `rgb`."""
    from pxr import Gf, Sdf, UsdShade

    if True:
        d = presets().get(key) or {}
        photo = d.get("photo") or {}
        glassy = key in _GLASSY
        mdl = "OmniGlass.mdl" if glassy else "OmniPBR.mdl"
        ident = "OmniGlass" if glassy else "OmniPBR"

        mat = UsdShade.Material.Define(stage, path)
        sh = UsdShade.Shader.Define(stage, path + "/Shader")
        sh.CreateImplementationSourceAttr(UsdShade.Tokens.sourceAsset)
        sh.SetSourceAsset(Sdf.AssetPath(mdl), "mdl")
        sh.SetSourceAssetSubIdentifier(ident, "mdl")
        for out_name in ("surface", "displacement", "volume"):
            mat.CreateOutput(out_name, Sdf.ValueTypeNames.Token).ConnectToSource(
                sh.ConnectableAPI(), "out")

        col = Gf.Vec3f(*_rgb(rgb))
        if glassy:
            sh.CreateInput("glass_color", Sdf.ValueTypeNames.Color3f).Set(col)
            return

        sh.CreateInput("diffuse_tint", Sdf.ValueTypeNames.Color3f).Set(col)
        baked = d.get("baked") or {}
        # A procedural key with no baked PNG has no image at all here, so its colour must
        # also land on the constant or OmniPBR falls back to its own default.
        if not photo and not baked.get("diffuse"):
            sh.CreateInput("diffuse_color_constant", Sdf.ValueTypeNames.Color3f).Set(col)
        sh.CreateInput("reflection_roughness_constant", Sdf.ValueTypeNames.Float).Set(
            float(d.get("roughness", 0.7)))
        sh.CreateInput("metallic_constant", Sdf.ValueTypeNames.Float).Set(
            float(d.get("metalness", 0.1)))
        if photo.get("file"):
            # Resolve through the symlink now: USD stores the path we give it, and a
            # relative one would resolve against the STAGE, not this module.
            ap = os.path.realpath(os.path.join(TEXTURE_ROOT, photo["file"]))
            if os.path.exists(ap):
                sh.CreateInput("diffuse_texture", Sdf.ValueTypeNames.Asset).Set(
                    Sdf.AssetPath(ap))
            else:
                print(f"[dc.cfd_bridge] material {key}: photo missing at {ap}")
        for map_name, input_name in (("diffuse", "diffuse_texture"),
                                     ("normal", "normalmap_texture")):
            rel = baked.get(map_name)
            if map_name == "diffuse":
                # Prefer the variant with the tint already multiplied in. OmniPBR's
                # diffuse_tint does not modulate diffuse_texture, so binding the raw
                # bake would show the untinted canvas.
                pre = (baked.get("tinted") or {}).get(f"{rgb:06x}")
                if pre:
                    rel = pre
                elif rel and rgb != 0xFFFFFF:
                    # Only a preset that HAS a bake can be missing its tinted variant.
                    # White needs no pre-multiply, so its absence is expected and not
                    # worth a line that reads like a failure.
                    print(f"[dc.cfd_bridge] material {key}: no tinted bake for "
                          f"#{rgb:06x}; binding the untinted canvas")
            if not rel:
                continue
            ap = os.path.join(BAKED_ROOT, rel)
            if os.path.exists(ap):
                sh.CreateInput(input_name, Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(ap))
            else:
                print(f"[dc.cfd_bridge] material {key}: baked {map_name} missing at {ap}")
        if d.get("normalScale"):
            sh.CreateInput("bump_factor", Sdf.ValueTypeNames.Float).Set(
                float(d["normalScale"]))
        # Editor opacity -> OmniPBR. `enable_opacity` must be set as well as the
        # constant; the constant alone is ignored. opacity_threshold 0 selects blending
        # rather than a cutout, which is what MeshPhongMaterial.transparent does.
        if opacity < 0.999:
            sh.CreateInput("enable_opacity", Sdf.ValueTypeNames.Bool).Set(True)
            sh.CreateInput("opacity_constant", Sdf.ValueTypeNames.Float).Set(
                float(opacity))
            sh.CreateInput("opacity_threshold", Sdf.ValueTypeNames.Float).Set(0.0)


def _author_quads(stage, root_path: str, quads, materials: dict):
    """One UsdGeom.Mesh per quad under `root_path`, bound to its preset material."""
    from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt

    UsdGeom.Scope.Define(stage, root_path)
    used = set()
    for i, q in enumerate(quads):
        # Prim names must be unique and valid identifiers; the emitters' names come from
        # user-entered model names, so sanitise and de-duplicate rather than trust them.
        base = "".join(c if (c.isalnum() or c == "_") else "_" for c in q.name) or "q"
        if base[0].isdigit():
            base = "_" + base
        name = base if base not in used else f"{base}_{i}"
        used.add(name)

        pts = list(q.pts)
        # A triangle arrives as a quad with its last corner repeated (polygon floors).
        if len(pts) == 4 and pts[3] == pts[2]:
            pts = pts[:3]
        mesh = UsdGeom.Mesh.Define(stage, f"{root_path}/{name}")
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in pts]))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray([len(pts)]))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(list(range(len(pts)))))
        lo = [min(p[k] for p in pts) for k in range(3)]
        hi = [max(p[k] for p in pts) for k in range(3)]
        mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(*lo), Gf.Vec3f(*hi)]))
        if q.double_sided:
            mesh.CreateDoubleSidedAttr(True)

        # UVs from world extent, so texture density is physical, not per-object —
        # EXCEPT for a ClampToEdge photo, which the frontend stretches ONCE across the
        # face. Tiling one of those turns a single ACU return panel into a wall of
        # thumbnails.
        _p = presets().get(q.material, {})
        if (_p.get("photo") or {}).get("wrap") == "clamp":
            du = dv = 1.0
        else:
            du = max(hi[0] - lo[0], hi[2] - lo[2]) / UV_METRES_PER_REPEAT
            dv = max(hi[1] - lo[1], hi[2] - lo[2]) / UV_METRES_PER_REPEAT
        uv = [(0.0, 0.0), (du, 0.0), (du, dv), (0.0, dv)][:len(pts)]
        pv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
        pv.Set(Vt.Vec2fArray([Gf.Vec2f(*t) for t in uv]))

        mp = material_for(stage, q.material, getattr(q, "color", 0x7f848e),
                          getattr(q, "opacity", 1.0))
        if stage.GetPrimAtPath(mp).IsValid():
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(
                UsdShade.Material(stage.GetPrimAtPath(mp)))


def prototype_key(p, model_count: int = 1) -> str:
    """Content hash of everything that changes a placement's LOCAL geometry.

    Deliberately excludes origin and quaternion: those live on the instance Xform, which
    is exactly what lets four identically-sized racks share one prototype.

    `model_count` is in the key because it is an INPUT to the geometry, not just a hint:
    past SLOT_LINE_MODEL_BUDGET a rack drops its U-division lines, so the same rack in a
    small and a large project is two different prototypes.
    """
    payload = {
        "v": GEN_VERSION, "cls": p.cls,
        "size": [round(v, 6) for v in p.size],
        "opacity": round(p.opacity, 4),
        "polygon": [[round(float(a), 6), round(float(b), 6)] for a, b in (p.polygon or [])],
        "slot": p.slot, "slot_h": round(p.slot_height, 6),
        "swr": round(p.slot_width_ratio, 6),
        "mb": round(p.margin_bottom, 6), "mt": round(p.margin_top, 6),
        # The rack's own recess. Omitting these let two racks that differ ONLY in how far
        # their servers sit back share one prototype — the exact class of silent error
        # the content hash exists to prevent.
        "io": round(p.inlet_offset, 6), "oo": round(p.outlet_offset, 6),
        "df": {k: list(v) for k, v in sorted((p.deleted_faces or {}).items())},
        "srv": [[s.slot_position, s.slot_occupation, s.kind,
                 round(s.extra_inlet_offset, 6), round(s.extra_outlet_offset, 6),
                 _server_face_signature(s)] for s in p.servers],
        "part": [pt.index for pt in getattr(p, "partitions", []) if not pt.deleted],
        "faces": _face_patch_signature(p),
        "lines": p.cls == "racks" and int(model_count) <= _slot_line_budget(),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _slot_line_budget() -> int:
    from . import emitters
    return emitters.SLOT_LINE_MODEL_BUDGET


def _server_face_signature(s) -> list:
    """A server's per-face colour + patch rects — everything emit_server reads."""
    sig = []
    for fid, fv in sorted((getattr(s, "faces", None) or {}).items()):
        if not isinstance(fv, dict):
            continue
        sig.append([fid, str(fv.get("color") or "")])
        for patch in (fv.get("patches") or []):
            sig.append([fid, str(patch.get("type") or "rect")] +
                       [round(float(patch.get(k, 0) or 0), 6)
                        for k in ("x", "y", "w", "h", "cx", "cy", "r")])
    return sig


def _face_patch_signature(p) -> list:
    """Only the parts of `faces` that change geometry — patch rect + role."""
    sig = []
    for fid, fv in sorted((getattr(p, "faces", None) or {}).items()):
        for patch in (fv.get("patches") or []):
            sig.append([fid, round(float(patch.get("x", 0)), 6),
                        round(float(patch.get("y", 0)), 6),
                        round(float(patch.get("w", 0)), 6),
                        round(float(patch.get("h", 0)), 6),
                        ((patch.get("physics") or {}).get("role") or "wall")])
    return sig


def disabled_prim_paths(scene, groups: dict) -> list:
    """Prim paths for placements the project marks `meta.enabled: false`.

    Derived from the same name-sanitising build_scene uses, so a renamed or
    de-duplicated prim still matches.
    """
    out = []
    for p in scene.placements:
        if p.enabled:
            continue
        for path in groups.get(_safe_name(p.name) or p.uid, []):
            out.append(path)
    return out


def _safe_name(name: str) -> str:
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in (name or ""))


def build_scene(stage, scene, emit_fn, geo_root="/World/Geometry") -> dict:
    """Author every placement. Returns {group name: [prim paths]} for geo_groups.

    Prototypes are authored INLINE in the stage under `{geo_root}/_Prototypes` rather
    than as separate .usd files on disk. Same de-duplication, same instanceable
    collapse, and no filesystem round-trip inside the Kit update loop; the on-disk cache
    at kit/cache/ becomes worthwhile once scenes get big enough for authoring itself to
    dominate, which a 16-object case does not show.
    """
    from pxr import Gf, UsdGeom

    materials = ensure_materials(stage)
    UsdGeom.Xform.Define(stage, geo_root)
    # Prototypes live under a CLASS prim, outside the rendered hierarchy. A plain Scope
    # under /World/Geometry does not work: the prototypes then render in their own right,
    # unplaced at the world origin, on top of the instances that reference them. That
    # showed up as a 6 m wall panel standing at the origin with no matching placement —
    # visible in the stream while every per-object AABB read correct, because the extra
    # geometry belonged to no placement at all. Class prims are never imaged but remain
    # valid reference targets.
    proto_root = "/__Prototypes"
    stage.CreateClassPrim(proto_root)
    protos, groups, counts = {}, {}, {}

    for p in scene.placements:
        key = prototype_key(p, len(scene.placements))
        if key not in protos:
            ppath = f"{proto_root}/{p.cls}_{key}"
            _author_quads(stage, ppath, emit_fn(p, scene.placements), materials)
            protos[key] = ppath
        group = p.cls.capitalize()
        UsdGeom.Scope.Define(stage, f"{geo_root}/{group}")
        safe = _safe_name(p.name) or p.uid
        n = counts.get(safe, 0)
        counts[safe] = n + 1
        if n:
            safe = f"{safe}_{n}"
        path = f"{geo_root}/{group}/{safe}"

        x = UsdGeom.Xform.Define(stage, path)
        x.AddTranslateOp().Set(Gf.Vec3d(*p.origin))
        qx, qy, qz, qw = p.quat
        x.AddOrientOp().Set(Gf.Quatf(qw, Gf.Vec3f(qx, qy, qz)))
        # Op order is translate, orient, scale — USD applies them right-to-left, so a
        # point is scaled, then rotated, then translated, matching the editor. The scale
        # can be NEGATIVE (a mirror); see cfdproj.to_world for the case that proves it.
        if p.scale != (1.0, 1.0, 1.0):
            x.AddScaleOp().Set(Gf.Vec3f(*[float(v) for v in p.scale]))
        ref = UsdGeom.Xform.Define(stage, f"{path}/_ref").GetPrim()
        ref.GetReferences().AddInternalReference(protos[key])
        ref.SetInstanceable(True)
        if not p.enabled:
            UsdGeom.Imageable(x).MakeInvisible()
        groups.setdefault(group, []).append(path)
        groups[safe] = [path]

    # Receipt per material: the resolved colour AND the map count. A preset with zero
    # maps falls back to the model colour, and if that colour is white the surface
    # renders white — which is a data answer, not a rendering mystery.
    from pxr import UsdShade as _US
    _looks = stage.GetPrimAtPath("/World/Looks")
    _kids = list(_looks.GetChildren()) if _looks and _looks.IsValid() else []
    print(f"[dc.cfd_bridge] materials instantiated: {len(_kids)}")
    for _m in _kids:
        _sh = _US.Shader(stage.GetPrimAtPath(_m.GetPath().pathString + "/Shader"))
        _t = _sh.GetInput("diffuse_tint") if _sh else None
        _c = _sh.GetInput("diffuse_color_constant") if _sh else None
        _d = _sh.GetInput("diffuse_texture") if _sh else None
        print(f"[dc.cfd_bridge]   MAT {_m.GetName():<28} tint={_t.Get() if _t else None} "
              f"const={_c.Get() if _c else None} tex={'yes' if (_d and _d.Get()) else 'no'}")
    # Per-prototype receipt: which material each authored mesh actually ended up bound
    # to, and which way its face points. A 0.256-albedo floor rendering as dark as a
    # 0.109-albedo rack face is not a lighting result, so the binding and the winding are
    # the two things left to check — and both are cheap to state rather than infer.
    from pxr import UsdGeom as _UG2, UsdShade as _US2
    for _key, _ppath in sorted(protos.items(), key=lambda kv: kv[1]):
        _cls = _ppath.rsplit("/", 1)[-1].split("_")[0]
        if _cls not in ("floors", "racks", "acus"):
            continue
        _proto = stage.GetPrimAtPath(_ppath)
        for _m in list(_proto.GetAllChildren())[:2]:   # class-prim children are ABSTRACT;
            # GetChildren()'s default predicate filters them out entirely.
            _mesh = _UG2.Mesh(_m)
            if not _mesh:
                continue
            _b = _US2.MaterialBindingAPI(_m).GetDirectBinding()
            _mat = _b.GetMaterialPath().pathString if _b else "(none)"
            _pts = _mesh.GetPointsAttr().Get() or []
            _n = None
            if len(_pts) >= 3:
                _e1 = [_pts[1][i] - _pts[0][i] for i in range(3)]
                _e2 = [_pts[2][i] - _pts[0][i] for i in range(3)]
                _n = (_e1[1] * _e2[2] - _e1[2] * _e2[1],
                      _e1[2] * _e2[0] - _e1[0] * _e2[2],
                      _e1[0] * _e2[1] - _e1[1] * _e2[0])
                _L = max((sum(c * c for c in _n)) ** 0.5, 1e-9)
                _n = tuple(round(c / _L, 2) for c in _n)
            _ds = _mesh.GetDoubleSidedAttr().Get()
            print(f"[dc.cfd_bridge]   BIND {_m.GetName()[:26]:<27} "
                  f"mat={_mat.rsplit('/', 1)[-1]:<26} n={_n} 2sided={_ds}")

    print(f"[dc.cfd_bridge] cfdproj scene: {len(scene.placements)} placements, "
          f"{len(protos)} prototypes ({100 * (1 - len(protos) / max(1, len(scene.placements))):.0f}% shared)")
    return groups
