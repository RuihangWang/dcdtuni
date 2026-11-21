"""Pure argument parsers for dc.cfd_bridge commands (no omni imports → unit-testable)."""
import json
import math


def parse_json(message: str) -> dict:
    if not message:
        return {}
    try:
        v = json.loads(message)
        return v if isinstance(v, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def parse_set_volume(message: str):
    d = parse_json(message)
    field = d.get("field", "T")
    if field not in ("T", "U"):
        field = "T"
    return field, bool(d.get("visible", True))


def parse_set_roi_visible(message: str) -> bool:
    return bool(parse_json(message).get("visible", True))


def parse_set_roi_clip(message: str) -> bool:
    return bool(parse_json(message).get("enabled", True))


def parse_set_volume_range(message: str):
    d = parse_json(message)
    return float(d.get("min", 0.0)), float(d.get("max", 1.0))


def parse_set_roi_box(message: str):
    d = parse_json(message)
    mn = [float(x) for x in d.get("min", [0, 0, 0])]
    mx = [float(x) for x in d.get("max", [1, 1, 1])]
    return mn, mx


def parse_set_object_visible(message: str):
    d = parse_json(message)
    return str(d.get("name", "")), bool(d.get("visible", True))


def parse_set_object_opacity(message: str):
    d = parse_json(message)
    return str(d.get("name", "")), float(d.get("opacity", 1.0))


def parse_set_grid(message: str) -> dict:
    """Only the keys the caller actually sent, coerced to the type carb expects.

    Sending every key with a default would clobber whatever the app or a previous call
    already set, so an absent key must stay absent rather than become a default.
    """
    d = parse_json(message)
    out = {}
    for k, cast in (("enabled", bool), ("showOrigin", bool), ("trackCamera", bool),
                    ("spacing", float), ("majorInterval", int), ("lineWidth", float),
                    ("scale", float), ("offset", float),
                    ("fadeStart", float), ("fadeEnd", float)):
        if d.get(k) is not None:
            out[k] = cast(d[k])
    if d.get("plane") is not None:
        out["plane"] = str(d["plane"])
    if isinstance(d.get("lineColor"), (list, tuple)):
        out["lineColor"] = [float(v) for v in d["lineColor"]]
    return out


def parse_extract_roi(message: str) -> str:
    """Output format for the extracted ROI: 'vtu' (native cells) or 'vti' (resampled).

    Both are offered because the repo contradicts itself about whether IndeX voxelises an
    unstructured grid at all — geometry.ts:97-99 and vtu_to_demo.py:64-75 say a raw .vtu
    "produces an empty grid", while a live Kit log shows a 609,990-cell internal.vtu
    converting and voxelising fine. Loading the same ROI both ways settles it.
    """
    f = str(parse_json(message).get("format", "vtu")).lower()
    return f if f in ("vtu", "vti") else "vtu"


def parse_select_object(message: str) -> str:
    return str(parse_json(message).get("name", ""))


def parse_load_dataset(message: str) -> str:
    return str(parse_json(message).get("path", ""))


def parse_load_scene(message: str) -> str:
    """Scene-definition JSON path for loadScene. Empty => backend default."""
    return str(parse_json(message).get("path", ""))


def _vec3(v, default):
    try:
        out = [float(x) for x in v]
        return out[:3] if len(out) >= 3 else default
    except (TypeError, ValueError):
        return default


def parse_set_streamlines(message: str):
    """setStreamlines: {enabled, field, seedCenter[3], seedRadius, nSeeds, maxSteps,
    direction}. field is the COLOUR field (T|U); integration always uses vec3 U."""
    d = parse_json(message)
    field = d.get("field", "T")
    if field not in ("T", "U"):
        field = "T"
    direction = d.get("direction", "forward")
    if direction not in ("forward", "backward", "both"):
        direction = "forward"
    return {
        "enabled": bool(d.get("enabled", True)),
        "field": field,
        "seed_center": _vec3(d.get("seedCenter", [0, 0, 0]), [0.0, 0.0, 0.0]),
        "seed_radius": float(d.get("seedRadius", 1.0)),
        "n_seeds": int(d.get("nSeeds", 27)),
        "max_steps": int(d.get("maxSteps", 500)),
        "direction": direction,
        # optional colour domain / palette from the editor's streamline set
        "domain": _optional_domain(d.get("domain")),
        "palette": d.get("palette") if d.get("palette") in ("coolwarm", "jet", "viridis") else None,
    }


def _optional_domain(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        return None
    try:
        lo, hi = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    return [lo, hi] if math.isfinite(lo) and math.isfinite(hi) and lo < hi else None


def parse_set_seed_sphere(message: str):
    """setSeedSphere: {visible, center[3], radius, nSeeds} — the draggable preview."""
    d = parse_json(message)
    return {
        "visible": bool(d.get("visible", True)),
        "center": _vec3(d.get("center", [0, 0, 0]), [0.0, 0.0, 0.0]),
        "radius": float(d.get("radius", 1.0)),
        "n_seeds": int(d.get("nSeeds", 27)),
    }


def parse_set_slice(message: str):
    """setSlice: {visible, origin[3], normal[3], radius, field, domain[2]|null}.

    Unlike the older parsers this one RAISES on a malformed plane (zero normal,
    non-finite components, non-positive radius): a silently-defaulted cut plane
    would render a slice the user never asked for. The render-command path turns
    the ValueError into an ok=False ack carrying the reason.
    """
    from . import slice_render
    d = parse_json(message)
    origin, normal = slice_render.normalize_plane(
        d.get("origin", [0, 0, 0]), d.get("normal", [0, 1, 0]))
    radius = float(d.get("radius", 1.0))
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("slice radius must be finite and positive")
    field = d.get("field", "T")
    if field not in ("T", "U", "Umag"):
        raise ValueError(f"unknown slice field {field!r}")
    domain = d.get("domain", None)
    if domain is not None:
        domain = [float(v) for v in domain]
        if len(domain) != 2 or not all(math.isfinite(v) for v in domain):
            raise ValueError("slice domain must be two finite numbers or null")
        if domain[0] >= domain[1]:
            raise ValueError("slice domain lo must be below hi")
    return {
        "visible": bool(d.get("visible", True)),
        "origin": origin,
        "normal": normal,
        "radius": radius,
        "field": field,
        "domain": domain,
        # optional: the editor's palette, and whether to show the drag disc
        "palette": d.get("palette") if d.get("palette") in ("coolwarm", "jet", "viridis") else None,
        "show_disc": bool(d.get("showDisc", True)),
    }


def parse_set_volume_display(message: str):
    """setVolumeDisplay (acked, OV display): {field: off|T|U, domain: [lo,hi]|null}.

    Raises on anything malformed — like setSlice, a silently defaulted colour
    domain would render a volume the user never asked for."""
    d = parse_json(message)
    field = d.get("field")
    if field not in ("off", "T", "U"):
        raise ValueError(f"unknown volume field {field!r}")
    domain = d.get("domain", None)
    if domain is not None:
        if not isinstance(domain, list) or len(domain) != 2:
            raise ValueError("volume domain must be two numbers or null")
        domain = [float(v) for v in domain]
        if not all(math.isfinite(v) for v in domain) or domain[0] >= domain[1]:
            raise ValueError("volume domain must be finite and ascending")
    return {"field": field, "domain": domain}


def parse_set_volume_display_palette(message: str):
    """setVolumeDisplay with the editor's optional colour-bar palette."""
    parsed = parse_set_volume_display(message)
    palette = parse_json(message).get("palette")
    if palette is not None and palette not in ("coolwarm", "jet", "viridis"):
        raise ValueError(f"unknown volume palette {palette!r}")
    parsed["palette"] = palette
    return parsed


def parse_set_surface_display(message: str):
    """setSurfaceDisplay (acked, OV display): {field: off|T|U, domain: [lo,hi]|null,
    palette: coolwarm|jet|viridis}."""
    parsed = parse_set_volume_display(message)
    palette = parse_json(message).get("palette", "coolwarm")
    if palette not in ("coolwarm", "jet", "viridis"):
        raise ValueError(f"unknown surface palette {palette!r}")
    parsed["palette"] = palette
    hidden = parse_json(message).get("hidden", [])
    if not isinstance(hidden, list) or len(hidden) > 200000 or not all(
            isinstance(h, str) and 0 < len(h) <= 512 for h in hidden):
        raise ValueError("surface hidden patches must be a list of patch names")
    parsed["hidden"] = hidden
    return parsed
