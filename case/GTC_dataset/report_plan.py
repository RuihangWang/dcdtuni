#!/usr/bin/env python3
"""Summarize the sampling plan distributions for review."""
import json
import statistics as stx
from pathlib import Path

plan = json.load(open(Path(__file__).resolve().parent / "meta" / "sampling_plan.json"))
lines = [f"GTC dataset plan: {len(plan)} cases", ""]
tiers = {}
for c in plan.values():
    tiers.setdefault(c["tier"], []).append(c)
for tier, cs in tiers.items():
    powers = [c["total_power_W"] / 1000 for c in cs]
    cabs = [c["n_cabinets"] for c in cs]
    hbr = [c["heat_balance_ratio"] for c in cs if c["total_power_W"] > 0]
    lines.append(f"{tier}: n={len(cs)} | power {min(powers):.1f}-{max(powers):.1f} kW "
                 f"(med {stx.median(powers):.1f}) | cabinets {min(cabs)}-{max(cabs)} | "
                 f"heat-balance {min(hbr):.2f}-{max(hbr):.2f}")

lines.append("")
lines.append("Geometry layouts (archetypes):")
archs = {}
for c in plan.values():
    archs.setdefault(c["archetype"], []).append(c)
for arch, cs in sorted(archs.items(), key=lambda kv: -len(kv[1])):
    lines.append(f"  {arch}: n={len(cs)} cases | {cs[0]['n_cabinets']} cabinets")
lines.append(f"  ({len(archs)} distinct layouts across {len(plan)} cases)")
out = Path(__file__).resolve().parent / "meta" / "plan_report.txt"
out.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
print(f"\nWrote {out}")
