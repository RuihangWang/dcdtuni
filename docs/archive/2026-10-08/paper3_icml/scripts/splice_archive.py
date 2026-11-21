"""Splice the generated archive section into main.tex.

This was a hand edit, which is how a generated section comes to differ from its
generator.  check_measurement_archive.py already fails on that difference; this is
the script that removes it, and `make archive` runs generator then splice then the
gate, so the section in main.tex is reproducible by anyone.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
OUT = HERE / "build" / "archive"
MAIN = HERE / "main.tex"
START = r"\section{Measurement archive: what compressing the body removed}"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([sys.executable, str(HERE / "scripts" / "build_archive.py"),
                        str(OUT)], capture_output=True, text=True, cwd=str(HERE))
    if r.returncode != 0:
        print(r.stdout[-2000:]); print(r.stderr[-2000:], file=sys.stderr)
        return r.returncode
    gen = (OUT / "archive_section.tex").read_text(encoding="utf-8").rstrip("\n")
    src = MAIN.read_text(encoding="utf-8")
    i = src.index(START)
    nxt = src.index(r"\section{", i + len(START))
    old = src[i:nxt].rstrip("\n")
    if old == gen:
        print(f"splice_archive: main.tex already carries the generated section "
              f"({len(gen.splitlines())} lines)")
        return 0
    MAIN.write_text(src[:i] + gen + "\n\n" + src[nxt:], encoding="utf-8")
    print(f"splice_archive: replaced {len(old.splitlines())} lines with the generated "
          f"{len(gen.splitlines())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
