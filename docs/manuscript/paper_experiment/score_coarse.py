#!/usr/bin/env python3
"""Score the coarse 0.5 m-grid build of each case (the Excel coarse+align mode).

The original LLM-Builder reads the spreadsheet's 0.5 m visual grid and aligns
equipment to grid cells; that build is preserved as `<case>.cfdproj.gridbuild_backup`.
Scoring it against the label (same bounded detection metric as score_case.py) gives
the coarse-mode geometry accuracy, which degrades for dense layouts the grid cannot
resolve. Writes score_coarse.json next to score_result.json.

Usage: python score_coarse.py <case> [<case> ...]   (or: all)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_case import score_cfdproj, DEMO


def main():
    cases = sys.argv[1:]
    if not cases or cases == ["all"]:
        cases = sorted(d.name for d in DEMO.iterdir()
                       if d.is_dir() and d.name.startswith("case_"))
    for cn in cases:
        bak = DEMO / cn / "workdir" / f"{cn}.cfdproj.gridbuild_backup"
        if not bak.exists():
            print(f"- {cn:22s} no gridbuild_backup (skip)")
            continue
        r = score_cfdproj(cn, gen_path=bak, out_name="score_coarse.json")
        print(f"  {cn:22s} {r['score_pct']:6.1f}% ({r['matched']}/{r['total_label']}) "
              f"miss={r.get('missing',0)} extra={r.get('extra',0)}")


if __name__ == "__main__":
    main()
