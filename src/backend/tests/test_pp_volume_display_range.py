"""The volume's default colour limits from the server equal the editor's percentileRange (floor / ceil index)."""
import math

import numpy as np

from backend.aircooling.runner.postprocess import _display_range


def editor_percentile_range(values, lo=0.02, hi=0.98):
    kept = sorted(float(v) for v in values)
    n = len(kept)
    return [kept[max(0, math.floor(lo * (n - 1)))], kept[min(n - 1, math.ceil(hi * (n - 1)))]]


def test_same_samples_as_the_editor():
    rng = np.random.default_rng(3)
    for n in (1, 2, 49, 50, 51, 1000, 12345):
        values = rng.normal(300.0, 4.0, n).astype(np.float32)
        assert _display_range(values) == editor_percentile_range(values)


def test_empty_grid_has_no_range():
    assert _display_range(np.array([], dtype=np.float32)) is None
