#!/bin/bash
source /tmp/claude-0/env.sh
cd /home/user/dcdtuni/src
for c in Tri_angle/Tri_angle Base_leakage/Base_leakage Slab/Slab DAML-Testbed/DMAL_Testbed-patched Shinaide/Shinaide; do
  name=${c%%/*}
  for np in 1 8; do
    t0=$(date +%s)
    python -m backend.api air ../case/$c.cfdproj --mesh-only --mesh-processes $np --max-processes 8 > /tmp/claude-0/np/$name-np$np.log 2>&1
    rc=$?
    t1=$(date +%s)
    st=$(python3 -c "import json;r=json.load(open('../case/$name/mesh-quality-receipt.json'));print(r['status'],r['failedChecks'],r['ranks'])" 2>/dev/null)
    S=/tmp/claude-0/np/snap-$name-np$np; rm -rf $S; mkdir -p $S
    M=../case/$name/Mesh
    cp -r $M/system $S/; cp -r $M/constant $S/ 2>/dev/null
    for p in $M/processor*; do [ -d "$p" ] && { mkdir -p $S/$(basename $p)/constant; cp -r $p/constant/polyMesh $S/$(basename $p)/constant/; }; done
    sed -i 's/^writeFormat .*/writeFormat     ascii;/' $S/system/controlDict
    if ls -d $S/processor* >/dev/null 2>&1; then
      n=$(ls -d $S/processor* | wc -l)
      (cd $S && mpirun -np $n postProcess -parallel -constant -funcs '(writeCellCentres writeCellVolumes)' > pp.log 2>&1)
    else
      (cd $S && postProcess -constant -funcs '(writeCellCentres writeCellVolumes)' > pp.log 2>&1)
    fi
    sig=$(python /tmp/claude-0/np/signature.py $S /tmp/claude-0/np/sig-$name-np$np 2>&1 | tail -1)
    echo "$name np$np rc=$rc mesh+check $((t1-t0))s receipt=[$st] sig=[$sig]" | tee -a /tmp/claude-0/np/summary.txt
  done
  python3 - "$name" <<'PY' | tee -a /tmp/claude-0/np/summary.txt
import json, sys, numpy as np
n = sys.argv[1]; b = '/tmp/claude-0/np/sig-' + n
a1, a8 = np.load(b + '-np1.npy'), np.load(b + '-np8.npy')
j1, j8 = json.load(open(b + '-np1.json')), json.load(open(b + '-np8.json'))
same_n = a1.shape == a8.shape
dev = float(np.abs(a1 - a8).max()) if same_n else None
print(f"  {n}: cells {j1['cells']} vs {j8['cells']}; max |diff| of sorted (centre, volume) = {dev}; patches equal = {j1['patches'] == j8['patches']}")
PY
done
echo DONE >> /tmp/claude-0/np/summary.txt
