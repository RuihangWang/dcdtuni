"""The editor must not fetch its libraries from a CDN.

Every one of these already had a copy under `web/vendor/` and the page loaded
the remote one anyway. That made the editor depend on the network to start, on a
host that runs CFD jobs offline — and the failure is not graceful:
`polygon-clipping` is called by six geometry builders, so an unreachable CDN
means the racks silently do not build.

Google Fonts is allowed: it is presentation only and every rule that uses it
carries a local fallback stack, so a blocked request changes the typeface and
nothing else.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
# Where the vendored libraries actually live now. They are shared with the React
# editor, so they moved out of the legacy app's directory — a `vendor/...` URL in
# either app resolves here.
SHARED = REPO / "src" / "frontend" / "shared"
# The React editor's page. It is the one users open now, so it is the one that
# must not reach for a CDN.
REACT_INDEX = REPO / "src" / "frontend" / "react" / "index.html"
REACT_SRC = REPO / "src" / "frontend" / "react" / "src"

_ALLOWED_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")


def _remote_asset_refs(html: str) -> list[str]:
    out = []
    for m in re.finditer(r'<(?:script|link)\b[^>]*?(?:src|href)="(https?://[^"]+)"', html):
        url = m.group(1)
        if not any(h in url for h in _ALLOWED_HOSTS):
            out.append(url)
    return out



def test_react_editor_loads_no_remote_scripts():
    """The React page is the one users open, so it is the one that matters.

    This guard only ever covered the legacy editor, which meant the app that
    actually ships was unguarded. Same rule, same allowed hosts: Google Fonts is
    presentation-only with a local fallback stack, everything else would make the
    editor depend on the network to start on a host that runs CFD offline.
    """
    html = REACT_INDEX.read_text(encoding="utf-8")
    remote = _remote_asset_refs(html)
    assert not remote, f"react/index.html loads from a CDN: {remote}"


def test_the_vendored_copies_are_actually_there():
    """Every `shared/vendor/...` the React source imports must exist on disk.

    A missing local file fails the same way the CDN did: silently, at load, on a
    host that runs CFD offline. The legacy page declared its vendored libraries
    in `<script src>` tags and this test read them from there; React imports
    them, so the declarations are in the TypeScript now.
    """
    missing = []
    for src in REACT_SRC.rglob("*"):
        if src.suffix not in {".ts", ".tsx"} or not src.is_file():
            continue
        for m in re.finditer(r'[\'"](?:\.\./)+shared/vendor/([\w.-]+)[\'"]', src.read_text(encoding="utf-8")):
            if not (SHARED / "vendor" / m.group(1)).exists():
                missing.append(f"{src.relative_to(REPO)} -> vendor/{m.group(1)}")
    assert not missing, f"referenced but absent: {missing}"


def test_polygon_clipping_is_present():
    """Named explicitly because losing it breaks geometry, not just styling.

    Six geometry builders call it; without it racks silently do not build.
    """
    assert (SHARED / "vendor" / "polygon-clipping.umd.js").exists()
