"""The exported rack frame is a consistently oriented closed shell: every
facet faces the fluid, so its winding number is 1 in the solid walls and 0 in
the cavity and outside. The inner cavity walls used to face into the solid
(winding 0.6 / 1.5 / -0.3), and dcCart meshed only 2.2 of DAML R2's 10.7 m2
frame -- its side walls above the top switch were left as fluid."""
import numpy as np
import pytest

from agent.scripted_api import headless
from agent.scripted_api.identity import REPO

try:
    headless.find_node()
    _NODE = all((headless.node_modules_dir() / name / "package.json").is_file() for name in ("esbuild", "jsdom", "three"))
except FileNotFoundError:
    _NODE = False
pytestmark = pytest.mark.skipif(not _NODE, reason="Node.js or frontend node_modules not installed")

SINGLE_RACK = REPO / "case" / "SingleRack" / "SingleRack.cfdproj"


def _solids(text):
    out, cur, v = {}, None, []
    for line in text.splitlines():
        s = line.split()
        if not s:
            continue
        if s[0] == "solid":
            cur = s[1]
        elif s[0] == "vertex":
            v.append([float(x) for x in s[1:4]])
        elif s[0] == "endfacet":
            out.setdefault(cur, []).append(v[-3:])
    return {k: np.array(t) for k, t in out.items()}


def _winding(tris, p):
    a, b, c = tris[:, 0] - p, tris[:, 1] - p, tris[:, 2] - p
    la, lb, lc = (np.linalg.norm(x, axis=1) for x in (a, b, c))
    det = np.einsum("ij,ij->i", a, np.cross(b, c))
    den = (la * lb * lc + np.einsum("ij,ij->i", a, b) * lc
           + np.einsum("ij,ij->i", b, c) * la + np.einsum("ij,ij->i", c, a) * lb)
    return float((2 * np.arctan2(det, den)).sum() / (4 * np.pi))


@pytest.mark.skipif(not SINGLE_RACK.is_file(), reason="case/SingleRack not checked out")
def test_rack_frame_shell_faces_the_fluid():
    from agent.scripted_api.air import export_air_case
    reply = export_air_case(SINGLE_RACK, timestamp="2026-10-20T02:00:00.000Z")
    frames = _solids(reply["stlFiles"]["merged_rack_frame.stl"])
    assert frames
    for name, tris in frames.items():
        lo, hi = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
        centre = (lo + hi) / 2
        assert abs(_winding(tris, centre)) < 1e-6, f"{name}: the cavity is not fluid"
        assert abs(_winding(tris, hi + 0.3)) < 1e-6, f"{name}: outside is not fluid"
        # a point a hair inside the outer shell, at mid-height, lies in a
        # solid wall (the frame is solid along its faces' thin margins)
        walls = [_winding(tris, np.array([centre[0], centre[1], z])) for z in (lo[2] + 1e-3, hi[2] - 1e-3)]
        walls += [_winding(tris, np.array([x, centre[1], centre[2]])) for x in (lo[0] + 1e-3, hi[0] - 1e-3)]
        assert max(walls) == pytest.approx(1.0, abs=1e-6), f"{name}: no solid wall at the shell {walls}"
        assert all(abs(w) < 1e-6 or abs(w - 1) < 1e-6 for w in walls), f"{name}: winding {walls}"
