#!/usr/bin/env python3
"""The suite must be green on a CLEAN CLONE, not only on the machine that produced it.

An audit found the gate suite green here and red on a fresh checkout, with three findings:

  * `self-test test_claim_scoped.py failed (exit 1)` -- the self-test's baseline needed
    `measure_e13.py --check` to pass, and that could only read a gitignored record;
  * `measure_e13.py --check: no results_e13.json found` -- the record itself;
  * `appendix freshness gate FAILED: ... differs ... in 6 lines` -- the generator read
    E11b's derivation record straight out of the live gitignored `loggers/` tree through a
    sandbox symlink, so three sentences of the appendix existed only on one machine.

Every one of those is a gate that passes because of untracked data on the author's disk.
Nothing in the suite could see it, because the suite only ever ran against the tree it was
invoked from -- and fixing the three findings without a check that they stay fixed means
the next gitignored read lands the same way.

So this clones HEAD into a temporary directory, builds it, runs the whole suite there, and
fails if the clone is red.  It is wired into `check_manuscript.py`, which makes the
recursion explicit rather than accidental: the nested run sets DCD_CLEAN_CLONE_DEPTH, and
`check_manuscript.py` prints a note that it is skipping this gate BECAUSE it is already
inside the clone.  A skip that is not printed is a gate that does not exist.

Cost here is about ten seconds (clone ~1 s, `make all` ~3 s, suite ~5 s), so the LaTeX
build is NOT skipped: without it `build/main.pdf` is absent and the page gate, the
rendered-PDF gate, the shipped-PDF gate and the archive's rendering probe all decline to
run, which would turn "the clone is green" into "the clone checked nothing".  `--no-build`
exists for iterating on a non-PDF gate and says so on stdout in as many words.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[2]
REL = HERE.relative_to(REPO)
DEPTH_VAR = "DCD_CLEAN_CLONE_DEPTH"


def git(*args, check=True):
    r = subprocess.run(["git", "-C", str(REPO)] + list(args), capture_output=True,
                       text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"`git {' '.join(args)}` failed: {r.stderr.strip()[:300]}")
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", default=None,
                    help="directory to clone into and leave behind, for debugging")
    ap.add_argument("--no-build", action="store_true",
                    help="skip `make all` in the clone.  Every PDF gate then declines to "
                         "run and this says so out loud; it is for iterating on a "
                         "non-PDF gate only")
    ap.add_argument("--rev", default="HEAD", help="what to clone (default HEAD)")
    args = ap.parse_args()

    if not (REPO / ".git").exists():
        r = git("rev-parse", "--git-dir", check=False)
        if r.returncode != 0:
            print("CLEAN-CLONE GATE FAILED: this is not a git working tree, so there is no "
                  "committed state to clone and no way to tell whether the suite depends on "
                  "untracked files", file=sys.stderr)
            return 1

    head = git("rev-parse", "--short", args.rev).stdout.strip()
    # mkdtemp() already made the directory; mkdir() on it again raised FileExistsError and
    # the gate crashed instead of reporting anything.
    if args.keep:
        tmp = Path(args.keep)
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
    else:
        tmp = Path(tempfile.mkdtemp(prefix="clean_clone."))
    print(f"clean-clone gate: cloning {head} into {tmp}")
    try:
        r = subprocess.run(["git", "clone", "--quiet", str(REPO), str(tmp / "repo")],
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            print(f"CLEAN-CLONE GATE FAILED: the clone itself failed: "
                  f"{r.stderr.strip()[:400]}", file=sys.stderr)
            return 1
        repo = tmp / "repo"
        got = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True,
                             errors="replace").stdout.strip()
        if got != head:
            print(f"CLEAN-CLONE GATE FAILED: cloned {got}, expected {head}",
                  file=sys.stderr)
            return 1

        paper = repo / REL
        env = dict(os.environ)
        env[DEPTH_VAR] = str(int(os.environ.get(DEPTH_VAR, "0")) + 1)
        env.setdefault("OMP_NUM_THREADS", "1")

        if args.no_build:
            print("clean-clone gate: --no-build, so `make all` was SKIPPED in the clone. "
                  "build/main.pdf is therefore absent and the page gate, the rendered-PDF "
                  "gate, the shipped-PDF gate and the archive's rendering probe all decline "
                  "to run: this result says nothing about the PDF half of the suite.")
        else:
            b = subprocess.run(["make", "all"], cwd=str(paper), capture_output=True,
                               text=True, env=env, errors="replace")
            if b.returncode != 0:
                print("CLEAN-CLONE GATE FAILED: `make all` does not build from a clean "
                      "clone (exit "
                      f"{b.returncode}):\n{(b.stdout + b.stderr)[-1500:]}", file=sys.stderr)
                return 1

        # errors="replace": the suite quotes LaTeX sources that carry a literal \S (0xa7)
        # and text=True would raise UnicodeDecodeError instead of reporting a red clone.
        s = subprocess.run([sys.executable, "scripts/check_manuscript.py"],
                           cwd=str(paper), capture_output=True, text=True, env=env,
                           errors="replace")
        out = (s.stdout or "") + (s.stderr or "")
        tail = [l for l in out.splitlines() if l.strip()][-14:]
        for line in tail:
            print("  clone: " + line)
        if s.returncode != 0:
            print(f"CLEAN-CLONE GATE FAILED: the suite is RED on a clean clone of {head} "
                  f"(exit {s.returncode}), i.e. it passes here only because of files that "
                  f"are not committed", file=sys.stderr)
            return 1
        print(f"CLEAN-CLONE GATE OK (a clean clone of {head} builds and passes the whole "
              f"suite{'; PDF gates SKIPPED, --no-build' if args.no_build else ''})")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
