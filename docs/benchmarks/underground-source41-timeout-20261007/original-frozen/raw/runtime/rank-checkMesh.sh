#!/bin/bash
set -e
rank=$PMI_RANK
test -n "$rank"
case "$rank" in 0|1|2|3|4|5|6|7) ;; *) exit 93 ;; esac
exec taskset -c "$((16+rank))" /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/checkMesh -allTopology -allGeometry -parallel > /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/parent-topology-finite-observer-whole-plan-private-20261007/closed-component-source41-whole-ringrear-runtime-v2/logs/checkMesh-rank-${rank}.stdout 2> /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/parent-topology-finite-observer-whole-plan-private-20261007/closed-component-source41-whole-ringrear-runtime-v2/logs/checkMesh-rank-${rank}.stderr
