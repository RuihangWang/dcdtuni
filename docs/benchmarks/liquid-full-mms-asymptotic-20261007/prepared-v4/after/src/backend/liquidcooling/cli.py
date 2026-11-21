"""CLI entry point: python -m backend.liquidcooling.cli --from-liquidrun <path>"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Differentiable Liquid Cooling Solver")
    parser.add_argument("--from-liquidrun", type=Path, required=True,
                        help="Path to .liquidrun JSON file")
    parser.add_argument("--device", default=None,
                        help="Explicit device override; v3 uses its manifest when omitted (legacy defaults to cpu)")
    parser.add_argument("--case-dir", type=Path, default=None,
                        help="Case directory (default: inferred from liquidrun path)")
    args = parser.parse_args()

    if not args.from_liquidrun.exists():
        print(f"Error: {args.from_liquidrun} not found")
        sys.exit(1)

    import torch
    torch.set_default_dtype(torch.float64)

    from backend.liquidcooling.runner import run_liquid_solve

    result = run_liquid_solve(
        args.from_liquidrun,
        case_dir=args.case_dir,
        device=args.device,
    )

    print(f"\n{'='*60}")
    print(f"Converged: {result.converged}")
    print(f"Final loss: {result.final_loss:.4e}")
    print(f"Epochs: {result.epochs}")

    # Per-node pressures
    for i, nid in enumerate(result.node_ids):
        print(f"  Node {nid}: P={result.node_pressure_Pa[i, -1]:.0f} Pa")

    # Per-edge flows
    for i in range(result.edge_flow_kg_s.shape[0]):
        m_val = result.edge_flow_kg_s[i, -1]
        print(f"  Edge {i}: m={m_val:.4f} kg/s ({m_val*1000*60/997:.1f} LPM)")

    # Chip temperatures
    if result.chip_temp_K.size > 0:
        for i in range(result.chip_temp_K.shape[0]):
            T = result.chip_temp_K[i, -1] - 273.15
            print(f"  ColdPlate {i}: T_chip={T:.1f} °C")

    print("DONE")


if __name__ == "__main__":
    main()
