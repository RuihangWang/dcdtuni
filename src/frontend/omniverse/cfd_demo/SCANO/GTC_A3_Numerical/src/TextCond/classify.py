"""Keyword-based semantic role classifier for OpenFOAM patch names.

Maps concrete patch names (e.g. 'acu_supply_left_fan_wall_room_crac_1')
to abstract physics roles (e.g. 'hvac_supply') so that physically-identical
patches with different naming conventions produce identical text encodings.
"""

import re
from typing import List


def classify_patch_role(name: str) -> str:
    """Classify a patch name into an abstract semantic role.

    Priority-ordered rules match the PHYSICS FUNCTION of the patch,
    not its specific location or identifier number.

    Returns one of: hvac_supply, hvac_return, hvac_wall,
    heat_source_outlet, heat_source_inlet, wall, equipment_surface,
    cyclic, inlet, outlet, or a fallback lowercase name.
    """
    low = name.lower()

    # ── HVAC supply (CRAC / AHU / cooling unit) ──
    hvac_kw = ('acu', 'crac', 'ahu', 'cooling', 'hvac')
    supply_kw = ('supply', 'discharge')
    if _has_any(low, supply_kw) and _has_any(low, hvac_kw):
        return 'hvac_supply'
    # "cooling_unit_A_outlet_zone1" — outlet of a cooling device
    if _has_any(low, ('outlet',)) and _has_any(low, hvac_kw):
        return 'hvac_supply'

    # ── HVAC return ──
    return_kw = ('return', 'recirculation')
    if _has_any(low, return_kw) and _has_any(low, hvac_kw):
        return 'hvac_return'
    if _has_any(low, ('inlet',)) and _has_any(low, hvac_kw):
        return 'hvac_return'

    # ── HVAC wall (casing of HVAC unit) ──
    if _has_any(low, ('acu_wall', 'crac_wall', 'ahu_wall', 'hvac_wall')):
        return 'hvac_wall'

    # ── Server / heat source outlet ──
    if _has_any(low, ('server_outlet', 'hot_aisle', 'exhaust')):
        return 'heat_source_outlet'

    # ── Server / heat source inlet ──
    if _has_any(low, ('server_inlet', 'cold_aisle', 'intake')):
        return 'heat_source_inlet'

    # ── Cyclic / periodic ──
    if _has_any(low, ('opening', 'cyclic', 'periodic')):
        return 'cyclic'

    # ── Room walls ──
    if _has_any(low, ('room_wall',)):
        return 'wall'

    # ── Floor / ceiling (treated as wall boundaries) ──
    if _has_any(low, ('floor', 'ceiling')):
        return 'wall'

    # ── Equipment surfaces (racks, boxes, panels) ──
    if _has_any(low, ('rack', 'box', 'panel')):
        return 'equipment_surface'

    # ── Generic inlet / outlet (no HVAC context) ──
    if 'inlet' in low:
        return 'inlet'
    if 'outlet' in low:
        return 'outlet'

    # ── Fallback: lowercase, strip digits, join tokens ──
    return _fallback_normalize(name)


def _has_any(text: str, keywords: tuple) -> bool:
    return any(kw in text for kw in keywords)


def _fallback_normalize(name: str) -> str:
    tokens: List[str] = []
    for t in re.split(r'[^A-Za-z0-9]+', name.lower()):
        if not t or t.isdigit():
            continue
        t = re.sub(r'\d+$', '', t)
        if t:
            tokens.append(t)
    return '_'.join(tokens) if tokens else name.lower()
