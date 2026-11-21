"""Author quads as USD geometry, with a content-addressed prototype cache.

Two halves:

  * `material_for(stage, kind, ...)` — one /World/Looks material per distinct surface.
    The surface is `material_library.render_material(kind, ...)`: the numbers and pool
    textures of src/frontend/shared/textures/materials.json, under the same colour,
    roughness and UV rules the editor applies, i.e. exactly the material the editor's
    render snapshot sends the display bridge for that kind. It is authored with
    `render_scene.author_pool_material`, the bridge's own UsdPreviewSurface function, so
    the Kit-native .cfdproj path and the React display bridge give a kind one surface
    (tests/test_material_parity.py authors both and compares the graphs).

    UsdPreviewSurface, not OmniPBR: (1) it is what the display bridge already authors,
    so one function serves both paths; (2) its metallic/roughness model is
    MeshStandardMaterial's — base colour x colour map, scalar x roughness channel, a
    scaled normal map — so a manifest number means the same thing in Three.js and in
    RTX, while OmniPBR's tint/influence inputs needed per-colour pre-tinted bakes;
    (3) it is a USD schema, so a CPU export can be checked without Kit; RTX renders it
    natively, and translucency goes through the tracked DcdtuniFractionalPreview MDL
    adapter exactly as on the bridge. Textures are the pool files themselves.

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

from . import material_library
from . import render_scene

# Bump when the authored geometry changes shape. It is part of the cache key, so a bump
# invalidates every cached prototype without anyone having to remember to clear a dir.
# v3: manifest kinds and UV modes, six server faces, no U-division seam quads.
GEN_VERSION = "3"

LOOKS = "/World/Looks"


def _picture_value(picture):
    """A face picture as a texture value: its pool reference, or a PNG/JPEG data URI."""
    if not picture:
        return None
    ref = material_library.pool_ref_for_url(picture)
    if ref:
        return ref
    if picture.startswith("data:"):
        try:
            render_scene.texture_bytes(picture)
            # An embedded picture is written to the owned render texture cache;
            # without one (Kit started outside supervise_kit) it cannot be bound.
            from .owned_paths import render_texture_cache_path
            render_texture_cache_path()
        except ValueError as exc:
            print(f"[dc.cfd_bridge] embedded face picture not bound: {exc}")
            return None
        return picture
    return None


def resolve_surface(kind, color=None, opacity=1.0, keep_user_color=False, double_sided=False,
                    picture=None, fallback=None):
    """(kind drawn, its render material). A picture that cannot be bound falls back to
    the host kind, as an unloadable picture leaves the editor's face in its material."""
    value = _picture_value(picture) if kind == material_library.FACE_IMAGE else None
    if kind == material_library.FACE_IMAGE and value is None:
        if picture:
            print(f"[dc.cfd_bridge] face picture not bound ({str(picture)[:60]}): "
                  f"not in the texture pool and not a PNG/JPEG data URL")
        kind, color, keep_user_color = fallback or "box", None, False
    return kind, material_library.render_material(kind, color=color, keep_user_color=keep_user_color,
                                                  opacity=opacity, double_sided=double_sided,
                                                  picture=value)


def material_for(stage, kind: str, color=None, opacity: float = 1.0, keep_user_color: bool = False,
                 double_sided: bool = False, picture=None, fallback=None) -> str:
    """Material prim for one surface, created on first use; returns its path.

    Keyed by the whole render material (kind, resolved colour, opacity, sidedness,
    textures), so two quads that look the same share one material and a colour or an
    opacity change is a new one. Opacity being absent from the key once rendered
    GTC_2026 fully OPAQUE in OV while the editor drew it transparent.
    """
    kind, source = resolve_surface(kind, color, opacity, keep_user_color, double_sided, picture, fallback)
    return _material_at(stage, kind, source)


def _material_at(stage, kind, source) -> str:
    blob = json.dumps(source, sort_keys=True, separators=(",", ":"))
    name = "".join(c if c.isalnum() else "_" for c in kind)
    path = f"{LOOKS}/{name}__{hashlib.sha1(blob.encode()).hexdigest()[:12]}"
    if not stage.GetPrimAtPath(path).IsValid():
        texture_dir = None
        if any(isinstance(source.get(k), str) and not material_library.is_pool_ref(source[k])
               for k in ("map", "normalMap", "roughnessMap", "metalnessMap")):
            # An embedded picture is written to the owned render texture cache.
            from .owned_paths import render_texture_cache_path
            texture_dir = render_texture_cache_path()
        render_scene.author_pool_material(stage, path, source, texture_dir=texture_dir)
    return path


def _author_quads(stage, root_path: str, quads):
    """One UsdGeom.Mesh per quad under `root_path`, bound to its manifest material."""
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
        uv_m = list(q.uv) if q.uv else None
        # A triangle arrives as a quad with its last corner repeated (polygon floors).
        if len(pts) == 4 and pts[3] == pts[2]:
            pts = pts[:3]
            uv_m = uv_m[:3] if uv_m else None
        mesh = UsdGeom.Mesh.Define(stage, f"{root_path}/{name}")
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*pt) for pt in pts]))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray([len(pts)]))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(list(range(len(pts)))))
        lo = [min(pt[k] for pt in pts) for k in range(3)]
        hi = [max(pt[k] for pt in pts) for k in range(3)]
        mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(*lo), Gf.Vec3f(*hi)]))

        kind, source = resolve_surface(q.material, q.color, q.opacity, q.keep_user_color,
                                       q.double_sided, q.picture, q.fallback)
        if source["doubleSided"]:
            mesh.CreateDoubleSidedAttr(True)
        if render_scene.casts_no_shadow(source):
            mesh.GetPrim().CreateAttribute("primvars:doNotCastShadows", Sdf.ValueTypeNames.Bool).Set(True)

        # Texture coordinates exactly as the editor's: metres in the face frame, then
        # the kind's UV mode (physical tiles per tileM, stretch fits the face, one tile
        # per rack unit).
        if uv_m is None:
            uv_m = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)][:len(pts)]
        mode = q.uv_mode if kind == q.material else None
        uv = material_library.texture_uv(kind, uv_m, u_count=q.u_count, mode=mode, tile_m=q.tile_m)
        pv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
        pv.Set(Vt.Vec2fArray([Gf.Vec2f(*t) for t in uv]))

        mp = _material_at(stage, kind, source)
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(
            UsdShade.Material(stage.GetPrimAtPath(mp)))


def _quad_signature(q) -> list:
    """Everything a quad authors except its prim name (names differ between copies)."""
    def r(values):
        return [[round(float(c), 6) for c in v] for v in values] if values else None
    picture = (hashlib.sha1(q.picture.encode()).hexdigest() if q.picture else None)
    color = material_library.hex_of(q.color) if q.color is not None else None
    return [q.material, r(q.pts), r(q.uv), color, round(float(q.opacity), 4), bool(q.double_sided),
            bool(q.keep_user_color), q.uv_mode, int(q.u_count or 1),
            None if q.tile_m is None else round(float(q.tile_m), 6), picture, q.fallback]


def prototype_key(p, all_placements=None, emit_fn=None) -> str:
    """Content hash of a placement's LOCAL geometry AND its surfaces.

    Computed from the quads the emitter actually produces (kind, points, UVs, colour,
    opacity, picture...), so nothing the prototype authors can be left out of the key:
    an earlier hand-listed key shared one prototype between the room floor and the
    ceiling, which are the same panel in different materials. Origin and orientation
    live on the instance Xform and are not in the quads, which is exactly what lets
    four identically-sized racks share one prototype.
    """
    if emit_fn is None:
        from . import emitters
        emit_fn = emitters.emit
    payload = {"v": GEN_VERSION, "cls": p.cls,
               "quads": [_quad_signature(q) for q in emit_fn(p, all_placements)]}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


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

    library = material_library.manifest()
    print(f"[dc.cfd_bridge] materials from {material_library.MANIFEST_PATH.name} "
          f"({len(library['materials'])} kinds, {len(library['sets'])} texture sets; "
          f"instances authored on first use per distinct surface)")
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
        quads = emit_fn(p, scene.placements)
        key = prototype_key(p, scene.placements, emit_fn=lambda *_: quads)
        if key not in protos:
            ppath = f"{proto_root}/{p.cls}_{key}"
            _author_quads(stage, ppath, quads)
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

    # Receipt per material: its linear base colour, roughness, metallic and the pool
    # files it binds. A surface that renders wrong is then a data answer (which colour,
    # which file), not a rendering mystery.
    from pxr import UsdShade as _US
    _looks = stage.GetPrimAtPath(LOOKS)
    _kids = list(_looks.GetChildren()) if _looks and _looks.IsValid() else []
    print(f"[dc.cfd_bridge] materials instantiated: {len(_kids)}")
    for _m in _kids:
        _sh = _US.Shader(_m.GetChild("Surface"))
        if not _sh:
            continue
        _get = lambda n: (_sh.GetInput(n).Get() if _sh.GetInput(n) else None)  # noqa: E731
        _files = []
        for _t in _m.GetChildren():
            _tex = _US.Shader(_t)
            _f = _tex.GetInput("file") if _tex else None
            if _f and _f.Get():
                _files.append("/".join(str(_f.Get().path).split("/")[-2:]))
        _c = _get("diffuseColor")
        print(f"[dc.cfd_bridge]   MAT {_m.GetName():<34} rgb={tuple(round(v, 3) for v in _c) if _c else None} "
              f"rough={_get('roughness')} metal={_get('metallic')} maps={','.join(_files) or '-'}")
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
