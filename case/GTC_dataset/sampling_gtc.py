"""Stratified LHS sampling, cabinet-subset selection, and rack filling for GTC."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np

from server_types_gtc import (
    ARCHETYPE_COUNTS, ARCHETYPE_OPS,
    CP, DELTA_T_ASSUMED,
    MIN_U_SPAN,
    NUM_RACKS, RHO, SAFE_MARGIN_U, TIERS, U_SIZES, USABLE_SLOTS,
    TierConfig,
)


@dataclass(frozen=True)
class RackMeta:
    uid: str
    name: str
    row: int
    x: float
    z: float


@dataclass(frozen=True)
class AcuMeta:
    uid: str
    name: str


def load_rack_inventory(template: dict) -> List[RackMeta]:
    racks = template["constructions"]["racks"]
    items = []
    for uid, r in racks.items():
        loc = r["geometry"]["location"]
        items.append((uid, r["meta"]["name"], float(loc["x"]), float(loc["z"])))
    # rows = distinct z values, ordered; row index is 1-based
    zs = sorted({round(z, 3) for *_, z in items})
    row_of = {z: i + 1 for i, z in enumerate(zs)}
    out = [RackMeta(uid, name, row_of[round(z, 3)], x, z) for uid, name, x, z in items]
    out.sort(key=lambda r: (r.row, r.x))
    return out


def load_acu_inventory(template: dict) -> List[AcuMeta]:
    acus = template["constructions"]["acus"]
    return [AcuMeta(uid, a["meta"]["name"]) for uid, a in acus.items()]


@dataclass
class PlacedServer:
    start_u: int
    span_u: int
    power_w: float
    flow_m3s: float


@dataclass
class RackConfig:
    uid: str
    name: str
    load_level: float
    servers: List[PlacedServer] = field(default_factory=list)

    @property
    def total_u_filled(self) -> int:
        return sum(s.span_u for s in self.servers)

    @property
    def total_power_w(self) -> float:
        return sum(s.power_w for s in self.servers)


def fill_rack(uid: str, name: str, load_level: float,
              power_kw_range: Tuple[float, float], flow_range: Tuple[float, float],
              rng: random.Random) -> RackConfig:
    """Place servers (>=4U) until target fill; sample each server's power & flow."""
    target_u = round(load_level * USABLE_SLOTS)
    rc = RackConfig(uid=uid, name=name, load_level=load_level)
    if target_u < MIN_U_SPAN:
        return rc

    occupied: set[int] = set()
    remaining = target_u
    min_slot = SAFE_MARGIN_U + 1
    max_attempts = 200

    for _ in range(max_attempts):
        if remaining < MIN_U_SPAN:
            break
        candidates = [u for u in U_SIZES if u <= remaining]
        if not candidates:
            break
        u_size = rng.choice(candidates)
        max_slot = SAFE_MARGIN_U + USABLE_SLOTS - u_size + 1
        valid_starts = [
            start for start in range(min_slot, max_slot + 1)
            if not set(range(start, start + u_size)) & occupied
        ]
        if not valid_starts:
            # try the smallest size that still fits somewhere
            placed = False
            for smaller in sorted(U_SIZES):
                if smaller > remaining:
                    continue
                smax = SAFE_MARGIN_U + USABLE_SLOTS - smaller + 1
                starts = [st0 for st0 in range(min_slot, smax + 1)
                          if not set(range(st0, st0 + smaller)) & occupied]
                if starts:
                    u_size, valid_starts, placed = smaller, starts, True
                    break
            if not placed:
                break
        start_pos = rng.choice(valid_starts)
        occupied.update(range(start_pos, start_pos + u_size))
        p_lo, p_hi = power_kw_range
        f_lo, f_hi = flow_range
        rc.servers.append(PlacedServer(
            start_u=start_pos,
            span_u=u_size,
            power_w=round(rng.uniform(p_lo, p_hi) * 1000.0, 1),
            flow_m3s=round(rng.uniform(f_lo, f_hi), 4),
        ))
        remaining -= u_size

    rc.servers.sort(key=lambda s: s.start_u)
    return rc


def apply_archetype(ops: list, inventory: List[RackMeta]) -> List[str]:
    """Apply a geometry archetype (list of ('remove'|'halve', aisle_row) ops) to the
    rack inventory and return the present rack uids, in inventory order. 'remove'
    empties a whole aisle; 'halve' decommissions the far-x half of an aisle. The
    edit is deterministic — every case sharing an archetype gets the same layout."""
    by_row: Dict[int, List[RackMeta]] = {}
    for r in inventory:
        by_row.setdefault(r.row, []).append(r)
    for row in by_row:
        by_row[row].sort(key=lambda r: r.x)

    removed: set[str] = set()
    for op, row in ops:
        racks = by_row.get(row, [])
        if op == "remove":
            removed.update(r.uid for r in racks)
        elif op == "halve":
            keep = (len(racks) + 1) // 2  # keep the near half, drop the far half
            removed.update(r.uid for r in racks[keep:])
        else:
            raise ValueError(f"unknown archetype op: {op!r}")
    return [r.uid for r in inventory if r.uid not in removed]


def build_archetype_assignment(rng: random.Random) -> List[str]:
    """Expand ARCHETYPE_COUNTS to one archetype name per case (sum == 500) and
    shuffle (seeded) so every load tier sees a representative spread of layouts."""
    assignment: List[str] = []
    for name, count in ARCHETYPE_COUNTS.items():
        assignment += [name] * count
    rng.shuffle(assignment)
    return assignment


@dataclass
class ACUConfig:
    supply_temp_c: float
    flow_rate_m3s: float
    n_active: int
    active_uids: List[str] = field(default_factory=list)


@dataclass
class CaseConfig:
    case_id: str
    tier: str
    acu: ACUConfig
    archetype: str = "full"
    present_rack_uids: List[str] = field(default_factory=list)
    racks: Dict[str, RackConfig] = field(default_factory=dict)

    @property
    def total_power_w(self) -> float:
        return sum(r.total_power_w for r in self.racks.values())

    @property
    def heat_balance_ratio(self) -> float:
        q_cool = self.acu.n_active * RHO * CP * self.acu.flow_rate_m3s * DELTA_T_ASSUMED
        if q_cool <= 0:
            return float("inf")
        return self.total_power_w / q_cool


def _adjust_flow_for_balance(case: CaseConfig, tier: TierConfig) -> None:
    q_total = case.total_power_w
    if q_total <= 0:
        return
    q_target = q_total / (case.acu.n_active * RHO * CP * DELTA_T_ASSUMED)
    lo, hi = tier.acu_flow_range
    case.acu.flow_rate_m3s = round(max(lo, min(hi, q_target)), 4)


def _lhs(n: int, d: int, np_rng) -> np.ndarray:
    """Latin Hypercube samples in [0,1)^d, numpy-only (no scipy).

    Each column is a random permutation of n strata with uniform jitter, so
    every 1/n interval of every dimension is sampled exactly once.
    """
    out = np.empty((n, d))
    for j in range(d):
        perm = np_rng.permutation(n)
        out[:, j] = (perm + np_rng.random(n)) / n
    return out


def generate_sampling_plan(template: dict, seed: int = 42) -> List[CaseConfig]:
    """Geometry from a discrete archetype per case; load from a stratified LHS over
    [t_supply, acu_flow, 50 rack-lambdas] within each tier."""
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)
    inventory = load_rack_inventory(template)
    acus = load_acu_inventory(template)
    acu_uids = [a.uid for a in acus]

    total_cases = sum(t.sample_count for t in TIERS)
    assert sum(ARCHETYPE_COUNTS.values()) == total_cases, (
        f"archetype counts {sum(ARCHETYPE_COUNTS.values())} != total cases {total_cases}")
    archetype_of = build_archetype_assignment(rng)  # one name per global case index

    all_cases: List[CaseConfig] = []
    case_idx = 0
    for tier in TIERS:
        n_dims = 2 + NUM_RACKS  # t_supply, acu_flow, per-rack lambda
        samples = _lhs(tier.sample_count, n_dims, np_rng)
        lam_lo, lam_hi = tier.lambda_range
        t_lo, t_hi = tier.t_supply_range

        for i in range(tier.sample_count):
            row = samples[i]
            case_id = f"case_{case_idx:04d}"

            archetype = archetype_of[case_idx]
            present = apply_archetype(ARCHETYPE_OPS[archetype], inventory)
            present_set = set(present)

            t_supply = round(t_lo + row[0] * (t_hi - t_lo), 2)

            n_active = rng.randint(*tier.n_active_range)
            active_uids = rng.sample(acu_uids, n_active)
            a_lo, a_hi = tier.acu_flow_range
            acu = ACUConfig(
                supply_temp_c=t_supply,
                flow_rate_m3s=round(a_lo + row[1] * (a_hi - a_lo), 4),
                n_active=n_active,
                active_uids=active_uids,
            )

            racks: Dict[str, RackConfig] = {}
            for j, rmeta in enumerate(inventory):
                if rmeta.uid not in present_set:
                    continue
                lam = lam_lo + row[2 + j] * (lam_hi - lam_lo)
                racks[rmeta.uid] = fill_rack(
                    rmeta.uid, rmeta.name, lam,
                    tier.server_power_kw_range, tier.server_flow_range, rng,
                )

            case = CaseConfig(case_id=case_id, tier=tier.name, acu=acu,
                              archetype=archetype,
                              present_rack_uids=present, racks=racks)
            _adjust_flow_for_balance(case, tier)
            all_cases.append(case)
            case_idx += 1
    return all_cases


def plan_to_dict(cases: List[CaseConfig]) -> dict:
    out = {}
    for c in cases:
        out[c.case_id] = {
            "tier": c.tier,
            "archetype": c.archetype,
            "n_cabinets": len(c.present_rack_uids),
            "present_rack_uids": c.present_rack_uids,
            "acu": {
                "supply_temp_C": c.acu.supply_temp_c,
                "flow_rate_m3s": c.acu.flow_rate_m3s,
                "n_active": c.acu.n_active,
                "active_uids": c.acu.active_uids,
            },
            "racks": {
                uid: {
                    "load_level": round(rc.load_level, 4),
                    "servers": [
                        {"start_u": s.start_u, "span_u": s.span_u,
                         "power_W": s.power_w, "flow_m3s": s.flow_m3s}
                        for s in rc.servers
                    ],
                    "total_U_filled": rc.total_u_filled,
                    "total_power_W": rc.total_power_w,
                }
                for uid, rc in c.racks.items()
            },
            "total_power_W": c.total_power_w,
            "heat_balance_ratio": round(c.heat_balance_ratio, 4) if c.total_power_w > 0 else 0,
            "status": "pending",
        }
    return out
