#!/usr/bin/env bash
# Regenerate a kit-cae-compatible internal.vtu from the GTC CFD results.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"          # cfd_demo/
REPO="$(cd "$HERE/../../../.." && pwd)"           # repo root
# The solved GTC case is machine-specific; pass it as $1 on another host.
CASE="${1:-$REPO/case/GTC/log/2026-06-20-05-50-44_GTC}"
DEST="$HERE/data"
mkdir -p "$DEST"

echo "[convert] foamToVTK on $CASE (latest time, fields T U)"
conda run -n dcdtuni foamToVTK -case "$CASE" -latestTime -fields '(T U)' -no-finite-area

# foamToVTK writes VTK/<caseName>_<time>/internal.vtu
SRC="$(find "$CASE/VTK" -name internal.vtu | sort | tail -1)"
[ -f "$SRC" ] || { echo "[convert] ERROR: internal.vtu not produced"; exit 1; }
cp "$SRC" "$DEST/internal.vtu"
echo "[convert] wrote $DEST/internal.vtu ($(stat -c%s "$DEST/internal.vtu") bytes)"
