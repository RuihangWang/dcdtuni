#!/usr/bin/env bash
set -eo pipefail
repo=/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration
cd "$repo"
source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh
conda activate dcdtuni
test -d "$CONDA_PREFIX/src/meshTools/lnInclude"
source src/backend/aircooling/third_parties/foam/env.sh
set -u
export FOAM_USER_APPBIN="$repo/.codex-tmp/pimple-time-boundaries/build/platforms/$WM_OPTIONS/bin"
export FOAM_USER_LIBBIN="$repo/.codex-tmp/pimple-time-boundaries/build/platforms/$WM_OPTIONS/lib"
export TMPDIR="$repo/.codex-tmp/pimple-time-boundaries/build/tmp"
export WM_NCOMPPROCS=4 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 CUDA_VISIBLE_DEVICES=
mkdir -p "$FOAM_USER_APPBIN" "$FOAM_USER_LIBBIN" "$TMPDIR"
taskset -c 36-39 wmake src/backend/aircooling/third_parties/foam/src/applications/solvers/buoyantBoussinesqPimpleFoam_new
