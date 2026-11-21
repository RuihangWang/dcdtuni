import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server_types_gtc as st


def test_min_u_span_is_4():
    assert st.MIN_U_SPAN == 4
    assert min(st.U_SIZES) == 4


def test_usable_slots():
    assert st.RACK_TOTAL_U == 45
    assert st.USABLE_SLOTS == st.RACK_TOTAL_U - 2 * st.SAFE_MARGIN_U


def test_four_tiers_sum_to_500():
    assert len(st.TIERS) == 4
    assert sum(t.sample_count for t in st.TIERS) == 500


def test_tier_load_ranges_monotone():
    names = [t.name for t in st.TIERS]
    assert names == ["Light", "Medium", "Heavy", "Super-heavy"]
    # per-rack fill increases across load tiers
    lows = [t.lambda_range[0] for t in st.TIERS]
    assert lows == sorted(lows)


def test_archetype_counts_sum_to_500():
    # geometry is now a discrete set of layouts, not per-case random subsets
    assert sum(st.ARCHETYPE_COUNTS.values()) == 500
    # every counted archetype is defined, and 'full' is the dominant layout
    assert set(st.ARCHETYPE_COUNTS) == set(st.ARCHETYPE_OPS)
    assert max(st.ARCHETYPE_COUNTS, key=st.ARCHETYPE_COUNTS.get) == "full"
    assert st.ARCHETYPE_OPS["full"] == []


def test_template_exists():
    assert Path(st.TEMPLATE_CFDPROJ).exists()
