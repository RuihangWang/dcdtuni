#!/bin/bash
set -eo pipefail
export TMPDIR="/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/.codex-tmp/finite-native-dw-product-20261005/tmp"
export CONDA_EXE=/mnt/lv/home/litianyu/miniconda3/bin/conda
export PATH=/mnt/lv/home/litianyu/miniconda3/condabin:$PATH
source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh
conda activate dcdtuni
export CUDA_VISIBLE_DEVICES=""
export WM_NCOMPPROCS=2
cd "/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/src/backend/aircooling/third_parties/dcCartMesh"
bash ./Allwmake --cart-only -j 2
