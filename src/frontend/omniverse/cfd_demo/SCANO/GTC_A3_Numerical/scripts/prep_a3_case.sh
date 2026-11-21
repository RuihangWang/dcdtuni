#!/usr/bin/env bash
# Full A3 chain for a SYNTHESISED boundary condition, and how long each step costs.
#
# A3 reads its boundary K/V from the per-face t=1 field, not from text, so a new operating
# point needs one relaxed OpenFOAM step before the model can see it. inference.py detects
# that itself: it hashes the 0/ files and re-runs buoyantBoussinesqSimpleFoam_new when the
# hash goes stale ("H5 exists but BC hash stale -> run OF"). So the chain is:
#
#   bc_synth writes 0/T,0/U   ->   inference.py notices the hash changed   ->   1 OF step
#   ->   t=1 written into the h5   ->   A3 forward   ->   vtu
#
# This script measures the wall clock of the whole thing, which is the number that decides
# whether a live "simulate" button is viable this afternoon or whether the demo should
# switch between pre-generated scenarios.
set -euo pipefail

A3="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# scripts -> bundle root -> SCANO/, so the sibling C1 bundle is one level up.
C1="${DC_CFD_C1_BUNDLE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/GTC_TransFVGN_FronzenText}"
# Same resolution order as start_demo.sh: explicit override, then PATH, then the
# conventional conda location under $HOME.
PY="${DC_CFD_AI_PYTHON:-$(command -v python3.11 2>/dev/null || echo "$HOME/miniconda3/envs/FVGN-pt2.7/bin/python")}"
CFG="$A3/cases/GTC/A3/configs/config_A3_inference.yaml"
SRC="${DC_CFD_GTC_DATASET:-/mnt/lv/home/Public/GTC/processed_dataset_unconverged}"
GPU="${AI_GPU:-2}"
OUT="${OUT_DIR:-/tmp/a3_chain}"
SCALE="${POWER_SCALE:-1.5}"
TAG="${TAG:-x$SCALE}"
mkdir -p "$OUT"

WORK="$OUT/ds"
rm -rf "$WORK"; mkdir -p "$WORK/case0/h5"

echo "=== [1/3] synthesising the boundary condition (power x$SCALE) ==="
t0=$(date +%s.%N)
# Reuse the C1 bundle's synthesiser: it rewrites 0/T and 0/U from a BC dict and was
# verified byte-identical against case0's own files.
"$PY" "$C1/scripts/bc_synth.py" --base-case "$SRC/case0" --out-case "$WORK/case0" \
    --power-scale "$SCALE" ${EXTRA_BC:-} >/dev/null
cp "$SRC/case0/h5/case0.h5" "$WORK/case0/h5/case0.h5"
cp "$SRC/patch_statistics.json" "$WORK/"
cp "$A3/dataset/meta.json" "$WORK/" 2>/dev/null || true
t1=$(date +%s.%N)
echo "    BC + h5 copy: $(echo "$t1-$t0" | bc | cut -c1-5)s"

echo "=== [2/3] inference (will trigger the OpenFOAM t=1 step) ==="
VTU_DIR="$A3/logger/vtu_output"; rm -rf "$VTU_DIR"
( cd "$A3/cases/GTC/A3" && CUDA_VISIBLE_DEVICES="$GPU" "$PY" inference.py \
    --config "$CFG" --dataset-path "$WORK" ) > "$OUT/infer.log" 2>&1 || true
t2=$(date +%s.%N)
echo "    inference phase total: $(echo "$t2-$t1" | bc | cut -c1-6)s"

echo "=== [3/3] what happened ==="
grep -aE "BC hash|stale|running buoyantBoussinesq|t=1 computed|cache valid|cold bootstrap" \
    "$OUT/infer.log" | head -5 || echo "    (no solver lines — check the log)"
grep -aoE "T:       RelL2=[0-9.]+ \| MAE=[0-9.]+ \| nRMSE=[0-9.]+" "$OUT/infer.log" | tail -1 || true
v=$(find "$VTU_DIR" -name '*.vtu' 2>/dev/null | head -1)
if [ -n "$v" ]; then
    sync; cp "$v" "$OUT/pred_$TAG.vtu"; sync
    echo "    -> $OUT/pred_x$SCALE.vtu"
else
    echo "    !! no vtu produced"; tail -20 "$OUT/infer.log"
fi
