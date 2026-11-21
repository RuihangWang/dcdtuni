#!/bin/bash
cd /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air
fuser -k -TERM 8396/tcp 8397/tcp || true
for i in $(seq 1 20); do if ! fuser 8396/tcp 8397/tcp >/dev/null 2>&1; then break; fi; sleep 1; done
nohup taskset -c 16-23 env CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 CONDA_EXE=/mnt/lv/home/litianyu/miniconda3/bin/conda DCDTUNI_BROWSER_SOFTWARE=1 DCDTUNI_BROWSER_HEADLESS=1 DCCART_COMPARE_REGION_QUERIES=1 TMPDIR=$PWD/.codex-tmp/tmp ./src/start_server.sh --app react --port 8396 --case-dir $PWD/.codex-tmp/cases/Shinaide_Liquid_Refine4 > .codex-tmp/service/8396.log 2>&1 < /dev/null &
