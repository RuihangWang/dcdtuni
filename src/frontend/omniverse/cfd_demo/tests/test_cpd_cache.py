"""cpd_cache: exact ROI queries, and exactly-once fill bookkeeping."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "kit/extensions/dc.cfd_bridge"))
from dc.cfd_bridge.cpd_cache import CpdCache, CpdLayout, mesh_id   # noqa: E402


@pytest.fixture
def cen():
    rng = np.random.default_rng(0)
    return rng.random((20000, 3)).astype(np.float32) * np.array([4.4, 3.6, 6.0], np.float32)


def test_query_is_exact_not_approximate(cen):
    """The bins prune; they must never change the answer. A spatial index that is only
    ~right silently drops cells at the box faces."""
    lay = CpdLayout(cen)
    for mn, mx in [((0, 0, 0), (4.4, 3.6, 6.0)),
                   ((1.0, 0.9, 2.0), (3.0, 2.0, 4.0)),
                   ((0.0, 0.0, 0.0), (0.1, 0.1, 0.1)),
                   ((2.2, 1.8, 3.0), (2.2, 1.8, 3.0))]:
        got = np.sort(lay.query(mn, mx))
        brute = np.sort(np.nonzero(
            ((cen >= np.array(mn, np.float32)) & (cen <= np.array(mx, np.float32))
             ).all(axis=1))[0])
        assert np.array_equal(got, brute), f"box {mn}-{mx}: {got.size} vs {brute.size}"


def test_query_covers_the_whole_domain(cen):
    lay = CpdLayout(cen)
    assert lay.query(cen.min(axis=0) - 1, cen.max(axis=0) + 1).size == len(cen)


def test_query_matches_brute_force_over_random_boxes(cen):
    """Hammered, because the extraction path now USES this answer instead of recomputing.

    Four hand-picked boxes were enough while the query was advisory; it is load-bearing
    now, so the equality has to hold for boxes nobody chose — including empty ones, boxes
    that straddle bin faces, and boxes larger than the domain.
    """
    lay = CpdLayout(cen)
    rng = np.random.default_rng(7)
    lo_d, hi_d = cen.min(axis=0), cen.max(axis=0)
    for _ in range(200):
        a = (lo_d + rng.random(3).astype(np.float32) * (hi_d - lo_d)).astype(np.float32)
        b = (lo_d + rng.random(3).astype(np.float32) * (hi_d - lo_d)).astype(np.float32)
        mn, mx = np.minimum(a, b), np.maximum(a, b)
        got = np.sort(lay.query(mn, mx))
        brute = np.sort(np.nonzero(((cen >= mn) & (cen <= mx)).all(axis=1))[0])
        assert np.array_equal(got, brute), f"box {mn}-{mx}: {got.size} vs {brute.size}"


def test_todo_never_returns_an_already_committed_cell(tmp_path, cen):
    """The no-duplicate-computation contract, which is the whole point of 3.2."""
    src = tmp_path / "result.vtu"
    src.write_bytes(b"x" * 16)
    c = CpdCache.open(str(tmp_path / "cache"), str(src), lambda: cen)
    ids = c.layout.query((1.0, 0.9, 2.0), (3.0, 2.0, 4.0))
    assert ids.size > 0

    first = c.todo("g1", ids)
    assert np.array_equal(np.sort(first), np.sort(ids)), "nothing committed yet"
    c.commit("g1", first, np.ones((first.size, 5), np.float32))
    assert c.todo("g1", ids).size == 0, "a second identical ROI must compute nothing"

    # An overlapping, larger ROI must only compute the NEW cells.
    ids2 = c.layout.query((0.5, 0.5, 1.0), (3.5, 2.5, 5.0))
    todo2 = c.todo("g1", ids2)
    assert todo2.size == ids2.size - np.intersect1d(ids, ids2).size
    assert np.intersect1d(todo2, first).size == 0


def test_overlap_accumulates_rather_than_overwrites(tmp_path, cen):
    """sum+count is what makes the merge associative and restart-safe. A cell committed
    twice must read back as the MEAN, not as whichever write landed last."""
    src = tmp_path / "r.vtu"
    src.write_bytes(b"y" * 8)
    c = CpdCache.open(str(tmp_path / "cache"), str(src), lambda: cen)
    ids = np.array([1, 2, 3], dtype=np.int64)
    c.commit("g", ids, np.full((3, 5), 2.0, np.float32))
    c.commit("g", ids, np.full((3, 5), 4.0, np.float32))
    vals, valid = c.read("g", ids)
    assert valid.all()
    assert np.allclose(vals, 3.0), "mean of 2 and 4, not the last writer"


def test_unfilled_cells_report_invalid_not_zero(tmp_path, cen):
    """A partially filled domain must be able to say "unknown". Returning 0.0 is how a
    half-filled room renders as 0 K air with nothing flagging it."""
    src = tmp_path / "r.vtu"
    src.write_bytes(b"z" * 8)
    c = CpdCache.open(str(tmp_path / "cache"), str(src), lambda: cen)
    c.commit("g", np.array([5], np.int64), np.ones((1, 5), np.float32))
    _, valid = c.read("g", np.array([5, 6], np.int64))
    assert valid[0] and not valid[1]


def test_layout_is_built_once_and_reused_without_touching_the_result(tmp_path, cen):
    """The point of requirement 2: a cache hit must not read the result file at all."""
    src = tmp_path / "r.vtu"
    src.write_bytes(b"w" * 8)
    calls = {"n": 0}

    def centroids():
        calls["n"] += 1
        return cen

    CpdCache.open(str(tmp_path / "cache"), str(src), centroids)
    assert calls["n"] == 1
    CpdCache.open(str(tmp_path / "cache"), str(src), centroids, n_cells=len(cen))
    assert calls["n"] == 1, "a hit re-read the source"


def test_mesh_fingerprint_changes_when_the_mesh_does(tmp_path, cen):
    """Cell order is not invariant across a re-mesh or a different nProcs. A layout that
    survived that would return the WRONG cells for every query."""
    src = tmp_path / "r.vtu"
    src.write_bytes(b"a" * 8)
    a = mesh_id(str(src), len(cen))
    assert mesh_id(str(src), len(cen) + 1) != a, "cell count must be in the fingerprint"
    src.write_bytes(b"bb" * 8)
    assert mesh_id(str(src), len(cen)) != a, "file identity must be in the fingerprint"


def test_generations_never_mix(tmp_path, cen):
    """Two operating points are two different fields; merging them is nonsense."""
    src = tmp_path / "r.vtu"
    src.write_bytes(b"q" * 8)
    c = CpdCache.open(str(tmp_path / "cache"), str(src), lambda: cen)
    ids = np.array([7, 8], np.int64)
    c.commit("cold", ids, np.full((2, 5), 1.0, np.float32))
    assert c.todo("hot", ids).size == 2, "a new operating point starts empty"
    v, _ = c.read("cold", ids)
    assert np.allclose(v, 1.0)


def test_coverage_reports_progress_toward_a_full_domain(tmp_path, cen):
    src = tmp_path / "r.vtu"
    src.write_bytes(b"c" * 8)
    c = CpdCache.open(str(tmp_path / "cache"), str(src), lambda: cen)
    assert c.coverage("g")[2] == 0.0
    c.commit("g", np.arange(1000, dtype=np.int64), np.ones((1000, 5), np.float32))
    filled, total, frac = c.coverage("g")
    assert filled == 1000 and total == len(cen)
    assert frac == pytest.approx(1000 / len(cen))
