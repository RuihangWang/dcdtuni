#!/usr/bin/env bash
set -euo pipefail
R=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
case "$R" in */FROZEN-v1) echo 'Copy frozen packet to an unfrozen private directory inside dcdtuni first.' >&2; exit 2;; esac
mkdir -p "$R/tmp"
export PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES= OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 TMPDIR="$R/tmp"
PY=/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python
taskset -c 32-33 "$PY" "$R/verify_guards.py"
taskset -c 32-33 "$PY" "$R/preflight.py"
taskset -c 32-33 "$PY" "$R/physical_control_ad.py"
taskset -c 32-33 "$PY" "$R/independent_balances.py"
