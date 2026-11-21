"""V5 Semantic Canonical Serializer for OpenFOAM boundary condition text.

Applies 7 rules in sequence to produce a deterministic canonical form:
  R0. Strip comments + normalize whitespace
  R1. Strip FoamFile headers
  R2. Strip dimensions lines
  R3. Expand $variable macros (fix double-uniform)
  R4. Normalize numeric formats
  R5. Fill BC-type default values / remove known-redundant fields
  R6. Abstract cross-reference identifiers (tSink_XXX → tSink, {patch} → {COUPLED})
  R7. Sort statements alphabetically by key

Validated in experiments v3-v5: 98.6% format-noise reduction, <2.1% residual noise/signal.
"""

import re
from typing import Dict, List, Optional, Tuple

from TextCond.classify import classify_patch_role

# ── BC-type default values (OpenFOAM implicit defaults) ──────────────────
# If a field omits these, we add them; if present, we keep them.
# This ensures both "type noSlip;" and "type noSlip; value uniform (0 0 0);"
# produce identical canonical output.
BC_TYPE_DEFAULTS: Dict[str, Dict[str, str]] = {
    "noSlip": {"value": "uniform (0 0 0)"},
    "fixedFluxPressure": {"value": "uniform 0"},
}

# Fields that are always implicit for a BC type and should be removed
BC_TYPE_REDUNDANT: Dict[str, set] = {
    "zeroGradient": {"gradient"},
}


# ── R0: Comments ─────────────────────────────────────────────────────────

def strip_comments(text: str) -> str:
    """Remove C-style block comments and C++ line comments."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*?$", "", text, flags=re.M)
    return text


# ── R1: FoamFile header ─────────────────────────────────────────────────

def strip_foamfile_header(text: str) -> str:
    """Remove FoamFile { ... } header blocks."""
    return re.sub(r"FoamFile\s*\{[^}]*\}\s*", "", text)


# ── R2: Dimensions ──────────────────────────────────────────────────────

def strip_dimensions(text: str) -> str:
    """Remove ``dimensions [...]`` lines."""
    return re.sub(r"dimensions\s*\[[^\]]*\]\s*;?", "", text)


# ── R3: Macro expansion ─────────────────────────────────────────────────

def expand_macros(text: str) -> str:
    """Expand ``$internalField`` and fix double-uniform artifacts."""
    # "value uniform $internalField" → "value uniform 0"
    text = re.sub(r"(\bvalue\s+)uniform\s+\$internalField\b", r"\1uniform 0", text)
    # "value $internalField" → "value uniform 0"
    text = re.sub(r"(\bvalue\s+)\$internalField\b", r"\1uniform 0", text)
    # standalone $internalField
    text = text.replace("$internalField", "uniform 0")
    # fix double-uniform (can occur from pre-expanded input)
    text = re.sub(r"\buniform\s+uniform\b", "uniform", text)
    return text


# ── R4: Numeric normalization ────────────────────────────────────────────

_NUM_RE = re.compile(
    r"(?<![A-Za-z_0-9])[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?(?![A-Za-z_\d])"
)


def _normalize_one_number(match: re.Match) -> str:
    s = match.group(0)
    try:
        val = float(s)
        # integer literals stay as integers
        if "." not in s and "e" not in s.lower():
            if val == int(val) and abs(val) < 1e15:
                return str(int(val))
        # very small → scientific
        if 0 < abs(val) < 1e-3:
            return f"{val:.6e}"
        # very large → 2dp
        if abs(val) >= 1e6:
            return f"{val:.2f}"
        # normal → strip trailing zeros, keep ≥1 decimal
        fmt = f"{val:.6f}".rstrip("0")
        return fmt + "0" if fmt.endswith(".") else fmt
    except (ValueError, OverflowError):
        return s


def normalize_numerics(text: str) -> str:
    """Normalize all numeric literals to a consistent format."""
    return _NUM_RE.sub(_normalize_one_number, text)


# ── R0b: Whitespace ─────────────────────────────────────────────────────

def normalize_whitespace(text: str) -> str:
    """Collapse whitespace: one statement per line, single spaces."""
    lines = []
    for line in text.splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


# ── Statement parsing ────────────────────────────────────────────────────

def parse_statements(content: str) -> List[str]:
    """Split text into semicolon-terminated statements respecting nesting."""
    content = content.strip()
    if not content:
        return []

    statements: List[str] = []
    buf: List[str] = []
    paren_d = brace_d = 0
    quote_ch: Optional[str] = None

    for ch in content:
        if quote_ch:
            buf.append(ch)
            if ch == quote_ch:
                quote_ch = None
            continue
        if ch in ('"', "'"):
            quote_ch = ch
            buf.append(ch)
            continue

        if ch == "(":
            paren_d += 1
        elif ch == ")":
            paren_d = max(0, paren_d - 1)
        elif ch == "{":
            brace_d += 1
        elif ch == "}":
            brace_d = max(0, brace_d - 1)

        if ch == ";" and paren_d == 0 and brace_d == 0:
            stmt = " ".join("".join(buf).split()).strip()
            if stmt:
                statements.append(stmt + ";")
            buf.clear()
            continue
        buf.append(ch)

    trailing = " ".join("".join(buf).split()).strip()
    if trailing:
        statements.append(trailing)
    return statements


def sort_statements(statements: List[str]) -> List[str]:
    """Sort statements alphabetically by their first token (key)."""
    def key_fn(s: str) -> str:
        parts = s.split(maxsplit=1)
        return parts[0].lower() if parts else s
    return sorted(statements, key=key_fn)


# ── R5: BC-type defaults ────────────────────────────────────────────────

def _extract_bc_type(statements: List[str]) -> Optional[str]:
    for stmt in statements:
        if stmt.startswith("type "):
            return stmt.split(maxsplit=1)[1].rstrip(";").strip()
    return None


def fill_bc_defaults(text: str) -> str:
    """Add missing default values based on BC type."""
    stmts = parse_statements(text)
    if not stmts:
        return text

    bc_type = _extract_bc_type(stmts)
    if bc_type is None or bc_type not in BC_TYPE_DEFAULTS:
        return text

    defaults = BC_TYPE_DEFAULTS[bc_type]
    if not defaults:
        return text

    existing_keys = {s.split(maxsplit=1)[0] for s in stmts}
    for key, default_val in defaults.items():
        if key not in existing_keys:
            stmts.append(f"{key} {default_val};")

    return "\n".join(stmts)


def remove_redundant_fields(text: str) -> str:
    """Remove fields that are always implicit for a given BC type."""
    stmts = parse_statements(text)
    if not stmts:
        return text

    bc_type = _extract_bc_type(stmts)
    redundant_keys = BC_TYPE_REDUNDANT.get(bc_type, set())
    if not redundant_keys:
        return "\n".join(stmts)

    return "\n".join(s for s in stmts if s.split(maxsplit=1)[0] not in redundant_keys)


# ── R6: Cross-reference abstraction ─────────────────────────────────────

def abstract_crossrefs(text: str) -> str:
    """Replace concrete patch identifiers in expressions with generic tokens.

    - ``{concrete_patch_name}`` → ``{COUPLED}``
    - ``tSink_anything`` → ``tSink``
    """
    text = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", "{COUPLED}", text)
    text = re.sub(r"\btSink_[A-Za-z0-9_]+\b", "tSink", text)
    return text


# ── Full pipeline ────────────────────────────────────────────────────────

def canonicalize_patch_text(text: str) -> str:
    """Apply the full V5 semantic canonicalization to a patch text string.

    The input format is a multi-section string with ``[section_header]`` blocks,
    as produced by ``_build_patch_texts()`` in ``text_conditioning.py``.
    """
    text = strip_comments(text)
    text = strip_foamfile_header(text)
    text = strip_dimensions(text)

    sections = re.split(r"(\[[^\]]+\])", text)
    result: List[str] = []

    in_patch_role = False
    for section in sections:
        if section.startswith("["):
            result.append(section)
            in_patch_role = section == "[patch_role]"
        else:
            if in_patch_role:
                clean = section.strip()
                if clean:
                    clean = classify_patch_role(clean)
                result.append("\n" + clean if clean else "")
                in_patch_role = False
            else:
                section = expand_macros(section)
                section = abstract_crossrefs(section)
                section = normalize_numerics(section)
                section = normalize_whitespace(section)
                section = fill_bc_defaults(section)
                section = remove_redundant_fields(section)

                stmts = parse_statements(section)
                stmts = sort_statements(stmts)
                if stmts:
                    result.append("\n" + "\n".join(stmts))
                else:
                    clean = section.strip()
                    if clean:
                        result.append("\n" + clean)

    return "\n".join(result)
