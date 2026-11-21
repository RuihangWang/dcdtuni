#!/usr/bin/env python3
"""Run the paper-experiment cases through mesh (and solve) as ONE agent.scripted_api batch.

    conda activate dcdtuni          # from the repository root
    python docs/manuscript/paper_experiment/run_all_cases.py --mesh-only \
        --mesh-processes 4 --max-parallel 2 --max-processes 8
    python docs/manuscript/paper_experiment/run_all_cases.py --only case_02_leakage \
        --mesh-processes 4 --solve-processes 4 --max-processes 8 --end-time 10

``--end-time 10`` (the default) is a pipeline smoke run: the return-air verdict
needs at least 1000 iterations, so each solve reads INCONCLUSIVE (too-short).
Pass ``--end-time 1500`` for runs you want judged.

For every ``case_*`` directory (``--only`` / ``--start-from`` narrow the list):

* source ``params``: the Builder's ``workdir/dc-params.json`` is built headlessly
  (``batch_cfd_pipeline.build_from_params``: automation-contract steps in JSDOM,
  the save button's bytes) into a new round directory;
* source ``cfdproj``: the Builder's ``workdir/<case>.cfdproj`` is run as it is
  (the API copies it into the new round directory);
* ``auto`` (default): ``params`` when ``dc-params.json`` exists, else
  ``cfdproj`` when the Builder's project exists, else the case is skipped.

Every prepared case then runs in one ``agent.scripted_api.run_batch``: each item gets
its own private server (``start_server.sh`` on its own port pair, per-run
credentials, stopped with ``fuser`` on those ports only) and
``max_processes // max_parallel`` MPI ranks. The round directory is
``<case>/workdir/cfd/<YYYY-MM-DD_HHMMSS>/``; the run's ``result.json`` is under
its ``log/api/``; the batch summary is ``batch-result.json`` plus
``paper-summary.json`` in a new dated directory under ``--out-root``. Nothing is
deleted or overwritten -- every round stays traceable -- and no server is
started by hand, so no login is needed.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for entry in (str(REPO / "src"), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import batch_cfd_pipeline as pipeline  # noqa: E402
from agent.scripted_api import identity as ident  # noqa: E402
from agent.scripted_api.batch import run_batch  # noqa: E402
from backend.aircooling.runner.convergence import minimum_run_iterations as _minimum_run_iterations  # noqa: E402

DEFAULT_OUT_ROOT = REPO / "_tmp" / "paper_experiment_batches"


def _cases(dataset, only=None, start_from=None):
    cases = sorted(d.name for d in Path(dataset).iterdir() if d.is_dir() and d.name.startswith("case_"))
    if only:
        cases = [c for c in cases if c == only]
        if not cases:
            raise SystemExit(f"case {only} not found under {dataset}")
    elif start_from:
        if start_from not in cases:
            raise SystemExit(f"case {start_from} not found under {dataset}")
        cases = cases[cases.index(start_from):]
    return cases


def _source(workdir, case, wanted):
    params, built = workdir / "dc-params.json", workdir / f"{case}.cfdproj"
    if wanted in ("auto", "params") and params.is_file():
        return "params"
    if wanted in ("auto", "cfdproj") and built.is_file():
        return "cfdproj"
    return None


def _cells(result_json):
    try:
        result = json.loads(Path(result_json).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None, None
    stages = result.get("stages") or {}
    convergence = ((result.get("metrics") or {}).get("convergence") or {}).get("verdict")
    return (stages.get("mesh") or {}).get("cells"), convergence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-dir", default=str(HERE), help="folder holding the case_* directories")
    parser.add_argument("--only", help="run only this case")
    parser.add_argument("--start-from", help="skip the cases before this one")
    parser.add_argument("--source", choices=("auto", "params", "cfdproj"), default="auto")
    parser.add_argument("--mesh-only", action="store_true", help="export and mesh; no solve")
    parser.add_argument("--end-time", type=int, default=10,
                        help="solver iterations (default 10: a pipeline smoke run whose verdict is INCONCLUSIVE; "
                             f"the return-air verdict needs at least {_minimum_run_iterations()} iterations)")
    parser.add_argument("--mesh-processes", type=int, help="mesh MPI ranks per case (default: the per-job share)")
    parser.add_argument("--solve-processes", type=int, help="solve MPI ranks per case (default: the per-job share)")
    parser.add_argument("--max-parallel", type=int, default=1, help="cases at once (default 1)")
    parser.add_argument("--max-processes", type=int, default=8, help="MPI rank budget of the whole batch (default 8)")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT),
                        help=f"batch summaries go to a new dated directory here (default {DEFAULT_OUT_ROOT})")
    args = parser.parse_args(argv)

    dataset = Path(args.dataset_dir).resolve()
    share = max(1, args.max_processes // max(1, args.max_parallel))
    stamp = ident.now_sgt().strftime("%Y-%m-%d_%H%M%S")
    items, rows = [], []
    for case in _cases(dataset, args.only, args.start_from):
        workdir = dataset / case / "workdir"
        source = _source(workdir, case, args.source)
        if source is None:
            print(f"- {case:22s} skip: no workdir/dc-params.json and no workdir/{case}.cfdproj")
            rows.append({"case": case, "status": "SKIP_NO_INPUT"})
            continue
        round_dir = pipeline.new_round_dir(workdir, stamp)
        if source == "params":
            params = json.loads((workdir / "dc-params.json").read_text(encoding="utf-8"))
            params = pipeline._normalize_params(params)
            params.setdefault("cfdSettings", {})["endTime"] = args.end_time
            cfdproj = round_dir / f"{case}.cfdproj"
            build = pipeline.build_from_params(params, cfdproj, project_name=case)
            if build.get("status") != "succeeded" or not (build.get("selfCheck") or {}).get("ok"):
                reason = (build.get("failure") or {}).get("reason") or json.dumps(build.get("selfCheck"), default=str)
                print(f"- {case:22s} FAIL_BUILD: {reason[:300]}")
                rows.append({"case": case, "status": "FAIL_BUILD", "round_dir": str(round_dir), "error": reason})
                continue
            layout = (build.get("layoutCheck") or {}).get("problems") or []
            for problem in layout:  # advisory: the case still runs, the row keeps the finding
                print(f"  {case:22s} layout {problem['kind']}: {problem.get('model') or ' / '.join(problem['models'])}")
        else:
            cfdproj = workdir / f"{case}.cfdproj"
        items.append({"cfdproj": str(cfdproj), "caseDir": str(round_dir), "label": case,
                      "stages": ["mesh"] if args.mesh_only else ["mesh", "solve"],
                      "meshProcesses": args.mesh_processes or share,
                      "solveProcesses": args.solve_processes or share, "endTime": args.end_time})
        rows.append({"case": case, "source": source, "round_dir": str(round_dir), "cfdproj": str(cfdproj),
                     "layout_problems": layout if source == "params" else None})
        print(f"  {case:22s} {source}: {round_dir}")

    if not items:
        print("nothing to run")
        return 1
    batch = run_batch(items, out_root=args.out_root, max_parallel=args.max_parallel,
                      max_processes=args.max_processes, label="paper")
    by_label = {row.get("label"): row for row in batch.get("items") or []}
    print(f"\n{'Case':<22} {'Status':<12} {'Stage':<8} {'Cells':>9}  {'Convergence':<14} result.json")
    for row in rows:
        item = by_label.get(row["case"])
        if item is None:
            print(f"{row['case']:<22} {row['status']:<12}")
            continue
        cells, verdict = _cells(item.get("resultJson"))
        row.update(status=item.get("status"), failure=item.get("failure"), result_json=item.get("resultJson"),
                   cell_count=cells, convergence=verdict)
        stage = (item.get("failure") or {}).get("stage") or ""
        print(f"{row['case']:<22} {str(item.get('status')):<12} {stage:<8} {str(cells or '-'):>9}  "
              f"{str(verdict or '-'):<14} {item.get('resultJson')}")
    batch_dir = Path(batch.get("batchDir") or "")
    if batch.get("batchDir") and batch_dir.is_dir():
        (batch_dir / "paper-summary.json").write_text(json.dumps({"rows": rows, "batch": batch.get("resultJson")},
                                                                 indent=2, default=str))
        print(f"\nsummary: {batch_dir / 'paper-summary.json'}")
    print(f"batch: {batch.get('resultJson')}  status: {batch.get('status')}")
    failed_builds = any(row.get("status") == "FAIL_BUILD" for row in rows)
    return 0 if batch.get("status") == "succeeded" and not failed_builds else 1


if __name__ == "__main__":
    sys.exit(main())
