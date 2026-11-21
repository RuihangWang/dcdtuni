#!/bin/bash
set -eo pipefail
source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh
conda activate dcdtuni
test -d "$CONDA_PREFIX/src/meshTools/lnInclude"
export CUDA_VISIBLE_DEVICES=""
export WM_NCOMPPROCS=2
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export TMPDIR="/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp/p20-complete-prefix-observation-normal-20261006/tmp"
export XDG_CACHE_HOME="/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp/p20-complete-prefix-observation-normal-20261006/cache"
export CONDA_PKGS_DIRS="/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp/p20-complete-prefix-observation-normal-20261006/packages"
export PYTHONDONTWRITEBYTECODE=1
cd "/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/src/backend/aircooling/third_parties/dcCartMesh"
exec bash ./Allwmake --cart-only -j2
