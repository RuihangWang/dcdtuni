#!/usr/bin/env python3
"""Generate the GTC LHS sampling plan and write meta/sampling_plan.json."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sampling_gtc import generate_sampling_plan, plan_to_dict
from server_types_gtc import DATASET_ROOT, SERVER_SIZES, TEMPLATE_CFDPROJ, TIERS


def main():
    p = argparse.ArgumentParser(description="Generate GTC dataset sampling plan")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--template", type=str, default=TEMPLATE_CFDPROJ)
    p.add_argument("--output-dir", type=str, default=DATASET_ROOT)
    args = p.parse_args()

    out_root = Path(args.output_dir)
    meta_dir = out_root / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)

    (meta_dir / "server_types.json").write_text(json.dumps(
        {str(k): {"u_size": v.u_size, "kind": v.kind} for k, v in SERVER_SIZES.items()}, indent=2))

    template = json.load(open(args.template))
    print(f"Generating sampling plan (seed={args.seed})...")
    cases = generate_sampling_plan(template, seed=args.seed)
    plan = plan_to_dict(cases)

    for tier in TIERS:
        tc = [c for c in cases if c.tier == tier.name]
        powers = [c.total_power_w / 1000 for c in tc]
        cabs = [len(c.present_rack_uids) for c in tc]
        print(f"  {tier.name}: {len(tc)} cases | power {min(powers):.1f}-{max(powers):.1f} kW | "
              f"cabinets {min(cabs)}-{max(cabs)} | ACUs {tier.n_active_range}")

    bad = [c for c in cases if c.total_power_w > 0 and not (0.3 <= c.heat_balance_ratio <= 1.3)]
    if bad:
        print(f"WARNING: {len(bad)} cases outside heat-balance band [0.3, 1.3]")

    (meta_dir / "sampling_plan.json").write_text(json.dumps(plan, indent=2))
    print(f"Wrote {meta_dir / 'sampling_plan.json'} ({len(plan)} cases)")


if __name__ == "__main__":
    main()
