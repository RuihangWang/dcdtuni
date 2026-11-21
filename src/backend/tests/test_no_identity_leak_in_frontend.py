"""The browser-served frontend must not carry the developer's identity.

SCANO is under double-blind review. The association chain that breaks anonymity
is the private repository name plus the host username, and anything the editor
sends to a browser is the easiest place for that pair to escape: it reaches
every viewer, it is not stripped by any build step, and nobody reads it.

`ppAiFillDemoModel` used to be exactly that — a hardcoded absolute path naming
both. It now reads the path from `/api/pp/ai/demo-model-path`, which returns
whatever `DCDTUNI_AI_DEMO_MODEL` holds, so a machine without that variable set
simply has no demo payload rather than leaking someone else's.

Scoped to what a browser actually receives. Backend research code under
`src/backend/` and `src/frontend/omniverse/.../SCANO/` is not served to anyone
and is out of scope here; the project-page guard covers the published surface.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
# The assets the editor serves. They moved out of web/ during the React
# cutover; a scope left pointing at their old home keeps passing while covering
# strictly less, which is the worst way for a guard to fail.
SHARED = REPO / "src" / "frontend" / "shared"
REACT_SRC = REPO / "src" / "frontend" / "react" / "src"
REACT_INDEX = REPO / "src" / "frontend" / "react" / "index.html"

# The two halves of the association chain, kept apart so this file does not
# itself become the leak it guards against.
_PRIVATE_REPO = "Gen-" + "FVGN"
_HOME_PREFIX = "/mnt/lv/" + "home/litianyu"
_HOME_PREFIX_ALT = "/mnt/lv/" + "litianyu"


def _served_files() -> list[Path]:
    """Everything the browser can fetch from the editor."""
    out: list[Path] = []
    out += sorted((SHARED / "js").glob("*.js"))
    out += sorted(SHARED.glob("i18n/*.json"))
    if REACT_INDEX.exists():
        out.append(REACT_INDEX)
    for pattern in ("*.ts", "*.tsx", "*.css"):
        out += sorted(REACT_SRC.rglob(pattern))
    # Vendored third-party bundles are not ours and are huge; skip them.
    return [p for p in out if "vendor" not in p.parts]


@pytest.mark.parametrize("needle", [_PRIVATE_REPO, _HOME_PREFIX, _HOME_PREFIX_ALT])
def test_no_identity_string_in_browser_served_files(needle: str):
    offenders = []
    for path in _served_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if needle in line:
                offenders.append(f"{path.relative_to(REPO)}:{i}")
    assert not offenders, (
        f"{needle!r} reaches the browser from: {offenders}. "
        "Serve it from the server via an environment variable instead."
    )


def test_demo_model_path_endpoint_exists():
    """The replacement has to be there, or the button silently does nothing."""
    server = (REPO / "src" / "server" / "server.py").read_text(encoding="utf-8")
    assert "/api/pp/ai/demo-model-path" in server
    assert "DCDTUNI_AI_DEMO_MODEL" in server


def test_frontend_asks_the_server_rather_than_hardcoding():
    """The demo button fetches the path; it does not carry one.

    Read out of the React source now — the legacy page this used to grep was
    deleted with the cutover, and the button lives in the React AI panel. The
    rule is unchanged and is the reason this file exists: an absolute host path
    in browser-served code names the machine's user, and half of the
    double-blind association chain with it.
    """
    hits = [
        f for f in REACT_SRC.rglob("*")
        if f.suffix in {".ts", ".tsx"} and f.is_file()
        and "demo-model-path" in f.read_text(encoding="utf-8")
    ]
    assert hits, "nothing in the React source asks for /api/pp/ai/demo-model-path"
    for f in hits:
        text = f.read_text(encoding="utf-8")
        assert "/api/pp/ai/demo-model-path" in text
        assert "/mnt/" not in text, f"an absolute host path is back in {f.name}"
