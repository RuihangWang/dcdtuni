cd /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air
export PATH=/mnt/lv/home/litianyu/miniconda3/condabin:$PATH
source src/backend/aircooling/third_parties/dcCartMesh/env.sh
export DCCART_REDUNDANT_EDGE_POINTS=1
export HWLOC_PLUGINS_PATH=$PWD/.codex-tmp/hwloc_plugins_nogl
export HYDRA_BINDING=user:16,17,18
cd .codex-tmp/native-redundant-emptyrank-smoke/Mesh
taskset -c 16,17,18 mpirun -np 3 dcCartMesh -overwrite -parallel > log.native 2>&1
native_rc=$?
taskset -c 16,17,18 mpirun -np 3 checkMesh -allTopology -allGeometry -parallel > log.checkMesh 2>&1
check_rc=$?
printf 'native=%s checker=%s\n' "$native_rc" "$check_rc"
tail -12 log.checkMesh
