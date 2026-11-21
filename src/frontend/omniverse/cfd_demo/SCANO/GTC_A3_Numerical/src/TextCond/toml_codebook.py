"""TOML Codebook: OpenFOAM case -> structured dict -> serialized text for LLM encoding.

Parses OpenFOAM 0/ field files (U, T, p_rgh) and constant/ properties,
producing a solver-agnostic structured representation suitable for text
conditioning in GNN models.

Public API
----------
of_case_to_toml(case_path, bc_fields) -> Dict[str, Dict]
serialize_patch_toml(patch_dict) -> str
serialize_case_toml(all_patches) -> str
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Role classification (simple, GTC-specific)
# ---------------------------------------------------------------------------

def _classify_role(name: str, u_type: Optional[str] = None) -> str:
    """Classify a concrete patch name into a physics role for GTC cases.

    ``u_type`` is the momentum (``U``) boundary-condition type for the patch,
    when known. It disambiguates the newer dcdtuni CRAC/server naming, whose
    patch names do not carry explicit ``acu_*``/``server_*``/``cyclic`` tokens.
    When ``u_type`` is ``None`` the classifier falls back to the legacy
    name-substring rules, preserving behaviour on the original GTC cases.

    Note on convention: server patch *names* are server-centric (a face named
    ``*_inlet`` is where air enters the server), whereas the fluid-domain BC
    *type* is inverted (a server intake is a ``flowRateOutletVelocity`` outflow
    of the room domain). Server roles therefore key off the name; CRAC roles
    key off the BC type (supply = ``flowRateInletVelocity``); interface/periodic
    patches key off ``cyclic``.
    """
    low = name.lower()

    # --- Legacy explicit-name rules (original GTC dataset) -------------------
    if "acu_supply" in low:
        return "acu_supply"
    if "acu_return" in low:
        return "acu_return"
    if "server_outlet" in low:
        return "server_outlet"
    if "server_inlet" in low:
        return "server_inlet"
    if "opening" in low or "cyclic" in low:
        return "cyclic"

    # --- BC-type-driven rules (dcdtuni build; requires known U type) ---------
    ut = (u_type or "").lower()
    if ut:
        # Periodic / interface patches (perforated blanking panels, false-ceiling
        # openings) — flow passes through, so they are not solid walls.
        if ut == "cyclic":
            return "cyclic"

        is_crac = ("crac" in low) or ("acu" in low) or ("ahu" in low)
        is_srv = ("srv" in low) or ("server" in low)
        active = ("flowrate" in ut) or ("inletoutlet" in ut)

        # CRAC: supply injects air (flowRateInletVelocity); return draws it back
        # (pressureInletOutletVelocity / flowRateOutletVelocity / inletOutlet).
        if is_crac:
            if "flowrateinletvelocity" in ut:
                return "acu_supply"
            if "pressureinletoutletvelocity" in ut or "flowrateoutletvelocity" in ut or "inletoutlet" in ut:
                return "acu_return"

        # Server: the *name* carries the server-centric inlet/outlet role; only
        # active-flow faces get a flow role (the noSlip srv_*_wall/_bottom/_top/
        # _frame surfaces fall through to wall).
        if is_srv and active:
            if "inlet" in low:
                return "server_inlet"
            if "outlet" in low:
                return "server_outlet"

    # Everything else (room walls, raised floor, ceilings, frames, pillars,
    # rack panels, CRAC box walls, server casing) is a wall/surface.
    return "wall"


# ---------------------------------------------------------------------------
# OpenFOAM field file parsing
# ---------------------------------------------------------------------------

def _strip_of_comments(text: str) -> str:
    """Remove C-style block comments and // line comments, preserving strings."""
    # Remove block comments first
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # Remove line comments (but not inside quoted strings)
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        # Simple approach: remove // comments outside of quotes
        in_quote = False
        result = []
        i = 0
        while i < len(line):
            if line[i] == '"' and (i == 0 or line[i-1] != '\\'):
                in_quote = not in_quote
                result.append(line[i])
            elif not in_quote and i + 1 < len(line) and line[i:i+2] == '//':
                break
            else:
                result.append(line[i])
            i += 1
        cleaned.append(''.join(result))
    return '\n'.join(cleaned)


def _extract_boundary_field_body(text: str) -> str:
    """Extract content between boundaryField { ... }."""
    text = _strip_of_comments(text)
    m = re.search(r'\bboundaryField\s*\{', text)
    if not m:
        raise ValueError("boundaryField block not found")
    start = m.end()
    depth = 1
    i = start
    while i < len(text) and depth > 0:
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        i += 1
    return text[start:i - 1]


def _parse_field_blocks(field_text: str) -> List[Tuple[str, str]]:
    """Parse a boundaryField body into (name_or_regex, raw_block_content) pairs.

    Handles both quoted regex names like ``"room_wall_.*"``
    and bare concrete names like ``acu_supply_left_fan_wall_room_crac_1``.
    """
    body = _extract_boundary_field_body(field_text)
    blocks: List[Tuple[str, str]] = []
    i = 0
    n = len(body)

    while i < n:
        # Skip whitespace
        while i < n and body[i].isspace():
            i += 1
        if i >= n:
            break

        # Read name (possibly quoted)
        if body[i] == '"':
            i += 1
            tok_start = i
            while i < n and body[i] != '"':
                i += 1
            name = body[tok_start:i]
            if i < n:
                i += 1  # skip closing quote
        else:
            tok_start = i
            while i < n and not body[i].isspace() and body[i] != '{':
                i += 1
            name = body[tok_start:i].strip()

        if not name:
            break

        # Skip whitespace before opening brace
        while i < n and body[i].isspace():
            i += 1
        if i >= n or body[i] != '{':
            continue

        # Extract block content (matching braces)
        content_start = i + 1
        depth = 0
        while i < n:
            if body[i] == '{':
                depth += 1
            elif body[i] == '}':
                depth -= 1
                if depth == 0:
                    content = body[content_start:i].strip()
                    blocks.append((name, content))
                    i += 1
                    break
            i += 1

    return blocks


def _looks_like_regex(token: str) -> bool:
    """Check if a block name looks like a regex pattern.

    Note: intentionally excludes `+` and `?` alone because they're valid in
    OpenFOAM patch identifiers only as regex quantifiers — and concrete patch
    names never contain them. The `.*` / anchors / char classes are the real
    signals that this token is a regex selector.
    """
    return any(ch in token for ch in [".*", "^", "$", "[", "]"]) or token.endswith("+") or token.endswith("?")


def _resolve_block_for_patch(
    patch_name: str,
    blocks: List[Tuple[str, str]],
) -> Optional[str]:
    """Find the block content that applies to a given concrete patch name.

    First tries exact match, then tries regex patterns.
    """
    # Exact match first
    for name, content in blocks:
        if not _looks_like_regex(name) and name == patch_name:
            return content

    # Regex match
    for name, content in blocks:
        if _looks_like_regex(name):
            try:
                if re.fullmatch(name, patch_name):
                    return content
            except re.error:
                continue

    return None


# ---------------------------------------------------------------------------
# Block content → key-value dict
# ---------------------------------------------------------------------------

def _parse_kv_simple(block_content: str) -> Dict[str, str]:
    """Parse simple key-value pairs from an OpenFOAM block (type, value, etc.).

    Returns raw string values; numeric conversion is done later.
    """
    result: Dict[str, str] = {}
    for m in re.finditer(r'(\w+)\s+([^;]+);', block_content):
        key = m.group(1)
        val = m.group(2).strip()
        # Skip sub-blocks like variables ( ... )
        if key == 'variables':
            continue
        result[key] = val
    return result


def _try_float(s: str) -> object:
    """Try to convert a string to float, return original string on failure."""
    try:
        return float(s)
    except (ValueError, TypeError):
        return s


# ---------------------------------------------------------------------------
# exprFixedValue decomposition
# ---------------------------------------------------------------------------

def _parse_variables_block(block_content: str) -> List[str]:
    """Extract the list of quoted strings from a ``variables ( ... );`` block."""
    m = re.search(r'variables\s*\((.*?)\)\s*;', block_content, re.DOTALL)
    if not m:
        return []
    body = m.group(1)
    return re.findall(r'"([^"]*)"', body)


def _decompose_acu_supply_T(block_content: str, patch_name: str) -> Dict:
    """Decompose an ACU supply exprFixedValue T block into structured fields."""
    result: Dict = {"type": "exprFixedValue", "mode": "cooling"}
    variables = _parse_variables_block(block_content)

    for var_str in variables:
        # coolingCapacity = 250.0
        m = re.search(r'coolingCapacity\s*=\s*([0-9.eE+-]+)', var_str)
        if m:
            result["coolingCapacity_kW"] = float(m.group(1))
        # supplyAirMassFlowRate = 7.14
        m = re.search(r'supplyAirMassFlowRate\s*=\s*([0-9.eE+-]+)', var_str)
        if m:
            result["supplyAirMassFlowRate_kgs"] = float(m.group(1))
        # t2 = 287.15  (supply setpoint)
        m = re.match(r'\s*t2\s*=\s*([0-9.eE+-]+)\s*$', var_str)
        if m:
            result["supplySetpoint_K"] = float(m.group(1))
        # coupled patch: only the tSink_*{patchName} declaration carries the
        # thermal coupling topology. Stop at the first match to avoid being
        # overwritten by unrelated braced expressions in later variables.
        if "coupled_patch" not in result:
            m = re.search(r'tSink_\w+\{([^}]+)\}', var_str)
            if m:
                result["coupled_patch"] = m.group(1)

    return result


def _decompose_server_outlet_T(block_content: str, patch_name: str) -> Dict:
    """Decompose a server outlet exprFixedValue T block into structured fields."""
    result: Dict = {"type": "exprFixedValue", "mode": "heating"}

    # Extract signed temperature delta from valueExpr "tSink_xxx+9.644..." or
    # "tSink_xxx - 5.0" (hypothetical cooling outlet). The sign is preserved
    # so positive = heating, negative = cooling.
    m = re.search(r'valueExpr\s+"[^"]*?([+\-])\s*([0-9.]+(?:[eE][+\-]?[0-9]+)?)"', block_content)
    if m:
        sign = -1.0 if m.group(1) == '-' else 1.0
        result["temperatureDelta_K"] = sign * float(m.group(2))

    # Extract coupled patch from the tSink_*{patch} declaration.
    variables = _parse_variables_block(block_content)
    for var_str in variables:
        m = re.search(r'tSink_\w+\{([^}]+)\}', var_str)
        if m:
            result["coupled_patch"] = m.group(1)
            break

    return result


# ---------------------------------------------------------------------------
# Build per-field dict for a patch
# ---------------------------------------------------------------------------

def _build_field_dict(
    patch_name: str,
    field_name: str,
    block_content: Optional[str],
    role: str,
) -> Dict:
    """Convert raw block content for one field into a structured dict."""
    if block_content is None:
        return {"type": "unspecified"}

    kv = _parse_kv_simple(block_content)
    bc_type = kv.get("type", "unspecified")

    # --- exprFixedValue decomposition ---
    if bc_type == "exprFixedValue" and field_name == "T":
        if role == "acu_supply":
            return _decompose_acu_supply_T(block_content, patch_name)
        elif role == "server_outlet":
            return _decompose_server_outlet_T(block_content, patch_name)

    # --- Simple BC types ---
    result: Dict = {"type": bc_type}

    # Preserve key numerical parameters
    if "volumetricFlowRate" in kv:
        result["volumetricFlowRate"] = _try_float(kv["volumetricFlowRate"])
    if "p0" in kv:
        # Parse "uniform 0" → 0.0
        m = re.search(r'uniform\s+([0-9.eE+-]+)', kv["p0"])
        if m:
            result["p0"] = float(m.group(1))
        else:
            result["p0"] = kv["p0"]

    return result


# ---------------------------------------------------------------------------
# Global properties parsing
# ---------------------------------------------------------------------------

def _parse_transport_properties(case_path: str) -> Dict:
    """Parse constant/transportProperties into a flat dict."""
    fpath = os.path.join(case_path, "constant", "transportProperties")
    if not os.path.isfile(fpath):
        return {}
    text = _strip_of_comments(Path(fpath).read_text(errors="ignore"))

    result: Dict = {}
    # Match simple key-value: key  value;
    for m in re.finditer(r'(\w+)\s+([^;{}\n]+);', text):
        key = m.group(1)
        val = m.group(2).strip()
        # Skip FoamFile header keys
        if key in ("version", "format", "class", "object", "location"):
            continue
        result[key] = _try_float(val)

    return result


def _parse_gravity(case_path: str) -> Optional[Dict]:
    """Parse constant/g into a dict with 'value' key."""
    fpath = os.path.join(case_path, "constant", "g")
    if not os.path.isfile(fpath):
        return None
    text = _strip_of_comments(Path(fpath).read_text(errors="ignore"))

    m = re.search(r'value\s+\(\s*([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s*\)', text)
    if m:
        return {"value": [float(m.group(1)), float(m.group(2)), float(m.group(3))]}
    return None


# ---------------------------------------------------------------------------
# Patch name list from polyMesh/boundary
# ---------------------------------------------------------------------------

def _parse_boundary_patch_names(case_path: str) -> List[str]:
    """Read concrete patch names from constant/polyMesh/boundary."""
    boundary_path = os.path.join(case_path, "constant", "polyMesh", "boundary")
    content = Path(boundary_path).read_text(errors="ignore")

    start = content.find("(")
    end = content.rfind(")")
    if start < 0 or end <= start:
        raise ValueError(f"Invalid polyMesh boundary file: {boundary_path}")

    body = content[start + 1:end]
    patch_names: List[str] = []
    i = 0
    n = len(body)
    while i < n:
        # Skip whitespace
        while i < n and body[i].isspace():
            i += 1
        if i >= n:
            break

        # Read token (patch name)
        tok_start = i
        while i < n and not body[i].isspace() and body[i] != '{':
            i += 1
        patch_name = body[tok_start:i].strip()
        if not patch_name:
            break

        # Skip whitespace before brace
        while i < n and body[i].isspace():
            i += 1
        if i >= n or body[i] != '{':
            break

        # Skip the brace-delimited block
        depth = 0
        while i < n:
            if body[i] == '{':
                depth += 1
            elif body[i] == '}':
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1

        patch_names.append(patch_name)

    return patch_names


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def of_case_to_toml(
    case_path: str,
    bc_fields: Optional[List[str]] = None,
) -> Dict[str, Dict]:
    """Parse an OpenFOAM case directory into a structured dict.

    Parameters
    ----------
    case_path : str
        Path to the OpenFOAM case directory (must contain 0/ and constant/).
    bc_fields : list of str, optional
        Field names to parse. Defaults to ["U", "T", "p_rgh"].

    Returns
    -------
    dict
        Keys are patch names (str) plus ``"global_properties"``.
        Each patch value is a dict with ``"meta"``, and one sub-dict per field.
    """
    if bc_fields is None:
        bc_fields = ["U", "T", "p_rgh"]

    # 1. Get concrete patch names from polyMesh/boundary
    patch_names = _parse_boundary_patch_names(case_path)

    # 2. Parse each field file's boundaryField blocks
    field_blocks: Dict[str, List[Tuple[str, str]]] = {}
    for field in bc_fields:
        fpath = os.path.join(case_path, "0", field)
        if os.path.isfile(fpath):
            text = Path(fpath).read_text(errors="ignore")
            field_blocks[field] = _parse_field_blocks(text)
        else:
            field_blocks[field] = []

    # 3. Build per-patch structured dicts
    result: Dict[str, Dict] = {}
    for pname in patch_names:
        # Resolve the U BC type first so role classification can disambiguate
        # the dcdtuni CRAC/server naming (see _classify_role docstring).
        u_block = _resolve_block_for_patch(pname, field_blocks.get("U", []))
        u_type = _parse_kv_simple(u_block).get("type") if u_block else None
        role = _classify_role(pname, u_type)

        patch_dict: Dict = {
            "meta": {
                "role": role,
            }
        }

        for field in bc_fields:
            block_content = _resolve_block_for_patch(pname, field_blocks[field])
            field_dict = _build_field_dict(pname, field, block_content, role)

            # Propagate coupled_patch to meta
            if "coupled_patch" in field_dict:
                patch_dict["meta"]["coupled_patch"] = field_dict.pop("coupled_patch")

            patch_dict[field] = field_dict

        result[pname] = patch_dict

    # 4. Global properties
    global_props: Dict = {}
    tp = _parse_transport_properties(case_path)
    if tp:
        global_props["transportProperties"] = tp
    g = _parse_gravity(case_path)
    if g:
        global_props["gravity"] = g
    result["global_properties"] = global_props

    return result


def serialize_patch_toml(patch_dict: Dict) -> str:
    """Serialize a single patch dict into a flat text line for LLM tokenization.

    Format::

        [role] <role> [pos] (x y z) [unv] (x y z) [U] key=val ... [T] key=val ...

    Spatial fields [pos] and [unv] are emitted only when a ``geometry`` key is
    present in the dict (injected by the text_sidecar spatial pipeline).
    Coordinates use 6 decimal places; positions are mean-centered (not normalized).
    """
    parts: List[str] = []

    # Meta section — role only, no concrete patch names
    meta = patch_dict.get("meta", {})
    parts.append(f"[role] {meta.get('role', 'unknown')}")

    # Spatial section (optional — present when h5_path is provided)
    geom = patch_dict.get("geometry")
    if geom is not None:
        pos = geom.get("mean_pos", [0, 0, 0])
        unv = geom.get("mean_unv", [0, 0, 0])
        parts.append(f"[pos] ({pos[0]:.6f} {pos[1]:.6f} {pos[2]:.6f})")
        parts.append(f"[unv] ({unv[0]:.6f} {unv[1]:.6f} {unv[2]:.6f})")

    # Field sections (deterministic order)
    for field in ["U", "T", "p_rgh"]:
        if field not in patch_dict:
            continue
        fd = patch_dict[field]
        kv_parts = []
        for k, v in fd.items():
            if isinstance(v, float):
                kv_parts.append(f"{k}={v:g}")
            else:
                kv_parts.append(f"{k}={v}")
        parts.append(f"[{field}] " + " ".join(kv_parts))

    return " ".join(parts)


def serialize_case_toml(all_patches: Dict[str, Dict]) -> str:
    """Serialize all patches and global properties into a single text string.

    Each patch is prefixed with a role-indexed token so the LLM can distinguish
    multiple instances of the same role without seeing layout-specific patch
    names. Format::

        <acu_supply_0> [role] acu_supply [U] ... [T] ... [p_rgh] ...
        <acu_supply_1> [role] acu_supply [U] ... [T] ... [p_rgh] ...
        ...
        <server_outlet_0> [role] server_outlet ...
        ...
        <global> [transportProperties] nu=... beta=... ...

    Patches are grouped and re-indexed per role (0..N-1 within each role).
    The concrete case-specific patch name (e.g.
    ``acu_supply_left_fan_wall_room_crac_1``) is never emitted, preserving
    generalization to new datasets.
    """
    lines: List[str] = []

    # Group patches by role and assign sequential indices within each role
    role_counters: Dict[str, int] = {}
    for pname, pdict in all_patches.items():
        if pname == "global_properties":
            continue
        role = pdict.get("meta", {}).get("role", "unknown")
        idx = role_counters.get(role, 0)
        role_counters[role] = idx + 1
        text = serialize_patch_toml(pdict)
        lines.append(f"<{role}_{idx}> {text}")

    # Global properties
    gp = all_patches.get("global_properties", {})
    if gp:
        gp_parts: List[str] = []
        for section_name, section_dict in gp.items():
            kv_strs = []
            if isinstance(section_dict, dict):
                for k, v in section_dict.items():
                    if isinstance(v, float):
                        kv_strs.append(f"{k}={v:g}")
                    elif isinstance(v, list):
                        kv_strs.append(f"{k}=({' '.join(str(x) for x in v)})")
                    else:
                        kv_strs.append(f"{k}={v}")
            gp_parts.append(f"[{section_name}] " + " ".join(kv_strs))
        lines.append("<global> " + " ".join(gp_parts))

    return "\n".join(lines)
