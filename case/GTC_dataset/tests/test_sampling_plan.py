import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sampling_gtc as s
import server_types_gtc as st


def _plan():
    tmpl = json.load(open(st.TEMPLATE_CFDPROJ))
    return s.generate_sampling_plan(tmpl, seed=42), tmpl


def test_total_500_cases():
    cases, _ = _plan()
    assert len(cases) == 500
    assert [c.case_id for c in cases[:2]] == ["case_0000", "case_0001"]


def test_tier_counts():
    cases, _ = _plan()
    counts = {t.name: sum(1 for c in cases if c.tier == t.name) for t in st.TIERS}
    assert counts == {"Light": 100, "Medium": 150, "Heavy": 150, "Super-heavy": 100}


def test_cabinet_counts_match_archetype():
    cases, tmpl = _plan()
    inv = s.load_rack_inventory(tmpl)
    expected = {name: len(s.apply_archetype(ops, inv))
                for name, ops in st.ARCHETYPE_OPS.items()}
    for c in cases:
        assert c.archetype in st.ARCHETYPE_OPS
        assert len(c.present_rack_uids) == expected[c.archetype]


def test_archetype_distribution_matches_counts():
    cases, _ = _plan()
    got = {name: sum(1 for c in cases if c.archetype == name)
           for name in st.ARCHETYPE_COUNTS}
    assert got == dict(st.ARCHETYPE_COUNTS)


def test_acu_active_subset_valid():
    cases, tmpl = _plan()
    acu_uids = {a.uid for a in s.load_acu_inventory(tmpl)}
    for c in cases:
        assert 1 <= c.acu.n_active <= st.NUM_ACUS
        assert len(c.acu.active_uids) == c.acu.n_active
        assert set(c.acu.active_uids) <= acu_uids


def test_heat_balance_mostly_in_band():
    cases, _ = _plan()
    powered = [c for c in cases if c.total_power_w > 0]
    ok = [c for c in powered if st.HEAT_BALANCE_MIN <= c.heat_balance_ratio <= st.HEAT_BALANCE_MAX]
    assert len(ok) / len(powered) > 0.9


def test_plan_to_dict_roundtrips_json():
    cases, _ = _plan()
    d = s.plan_to_dict(cases)
    s2 = json.dumps(d)  # must be JSON-serializable
    assert json.loads(s2)["case_0000"]["tier"] == "Light"
    assert "present_rack_uids" in d["case_0000"]
