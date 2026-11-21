#!/usr/bin/env bash
# Run only in this existing unfrozen PRIVATE audit root. Writes outputs beside scripts.
# This launcher starts no server, runtime job, browser, GPU or Git operation.
set -euo pipefail
R=/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/shinaide-ring-full20s-control-ad-private-20261006
export PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES= OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
export TMPDIR="$R/tmp" XDG_CACHE_HOME="$R/cache" TORCH_HOME="$R/torch-cache"
# Before numerical work, normal sources/actual originals must equal normal-before-guards.json SHA.
taskset -c 32-33 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python "$R/verify_guards.py"
taskset -c 32-33 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python "$R/full20s_control_ad_v2.py"
taskset -c 32-33 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python "$R/capture_layouts.py"
taskset -c 32-33 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python "$R/independent_balances.py"
taskset -c 32-33 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python "$R/verify_guards.py"
