"""Foamlib-based canonical serializer for OpenFOAM global configuration files.

Uses foamlib (v1.5.7) to parse OpenFOAM dictionary files into typed Python
values, then re-serializes with sorted keys for deterministic output.
Falls back to regex-based canonicalization if foamlib fails.
"""

import os
import re
import tempfile
from typing import List

from TextCond.canonicalize import (
    strip_comments,
    strip_foamfile_header,
    strip_dimensions,
    normalize_numerics,
    normalize_whitespace,
    parse_statements,
    sort_statements,
)


def canonicalize_global_text(text: str) -> str:
    """Canonicalize a global text string (may contain ``[section/path]`` headers).

    For each section body, attempts foamlib parse→re-serialize.
    Falls back to regex-based canonicalization on failure.
    """
    text = strip_comments(text)
    text = strip_foamfile_header(text)

    parts = re.split(r"(\[[^\]]+\])", text.strip())
    result_sections: List[str] = []

    i = 0
    while i < len(parts):
        part = parts[i].strip()
        if part.startswith("["):
            header = part
            body = parts[i + 1] if i + 1 < len(parts) else ""
            i += 2
            canon_body = _canonicalize_section_body(body)
            result_sections.append(header + "\n" + canon_body)
        else:
            if part:
                canon_body = _canonicalize_section_body(part)
                if canon_body.strip():
                    result_sections.append(canon_body)
            i += 1

    return "\n".join(result_sections)


def _canonicalize_section_body(body: str) -> str:
    """Try foamlib first, fall back to regex."""
    body = strip_foamfile_header(body)
    body = strip_dimensions(body)

    result = _try_foamlib(body)
    if result is not None:
        return result
    return _regex_fallback(body)


def _try_foamlib(body: str) -> str | None:
    """Parse body with foamlib and re-serialize with sorted keys.

    Only succeeds for flat dictionaries (all scalar/string values).
    Returns None for dicts with nested sub-dicts, dimensions, or
    complex types that foamlib represents as internal objects.
    """
    try:
        from foamlib import FoamFile
    except ImportError:
        return None

    # foamlib needs a valid FoamFile header
    foam_body = (
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        "    class       dictionary;\n    object      dict;\n}\n" + body
    )

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="foam_canon_")
        os.close(fd)
        with open(tmp_path, "w") as f:
            f.write(foam_body)

        foam = FoamFile(tmp_path)
        keys = list(foam.keys())
        if not keys:
            return None

        lines = []
        for key in sorted(keys):
            val = foam[key]
            # Skip dimensions (foamlib returns None)
            if val is None:
                continue
            # Bail out on nested dicts / complex types
            val_str = str(val)
            if "SubDict" in val_str or "FoamFile" in val_str:
                return None  # fall back to regex
            # Format vectors/lists as OpenFOAM tuples
            if isinstance(val, (list, tuple)):
                val_str = "(" + " ".join(str(v) for v in val) + ")"
            lines.append(f"{key} {val_str};")
        return "\n".join(lines)

    except Exception:
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _regex_fallback(body: str) -> str:
    """Regex-based fallback: normalize and sort statements.

    Handles nested blocks (e.g. ``RAS { ... }``) by ensuring closing
    braces act as statement terminators for sorting purposes.
    """
    body = normalize_numerics(body)
    body = normalize_whitespace(body)
    # Ensure top-level blocks (key { ... }) are treated as separate
    # statements by adding ';' after '}' when not already present.
    # This prevents block content from merging with the next statement.
    body = re.sub(r"\}\s*(?!;)", "};\n", body)
    stmts = parse_statements(body)
    stmts = sort_statements(stmts)
    return "\n".join(stmts)
