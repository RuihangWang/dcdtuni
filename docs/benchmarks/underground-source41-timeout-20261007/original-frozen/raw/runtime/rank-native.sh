#!/bin/bash
set -e
rank=$PMI_RANK
test -n "$rank"
case "$rank" in 0|1|2|3|4|5|6|7) ;; *) exit 93 ;; esac
exec taskset -c "$((16+rank))" /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/parent-topology-finite-observer-whole-plan-private-20261007/closed-component-source41-owned-build-v1/platforms/linux64GccDPInt32Opt/bin/dcCartMesh -overwrite -timeLimit 299 -parallel > /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/parent-topology-finite-observer-whole-plan-private-20261007/closed-component-source41-whole-ringrear-runtime-v2/logs/native-rank-${rank}.stdout 2> /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/parent-topology-finite-observer-whole-plan-private-20261007/closed-component-source41-whole-ringrear-runtime-v2/logs/native-rank-${rank}.stderr
