#!/bin/bash
# Re-mesh the ten release cases with the rebuilt dcCartMesh (mesh only), each in its own scratch case dir.
cd "$(dirname "$0")/../../../src"
OUT=${OUT:-$(cd "$(dirname "$0")/../../.." && pwd)/_tmp/release-remesh}
while read case project ranks; do
  mkdir -p "$OUT/$case"
  start=$(date +%s)
  timeout 3600 conda run -n dcdtuni python -m backend.api air "../$project" --case-dir "$OUT/$case" --mesh-only \
      --mesh-processes "$ranks" --max-processes "$ranks" > "$OUT/$case/api.log" 2>&1
  echo "$case rc=$? $(( $(date +%s) - start ))s" >> "$OUT/progress.txt"
done <<LIST
Base_leakage case/Base_leakage/Base_leakage.cfdproj 8
Tri_angle case/Tri_angle/Tri_angle.cfdproj 8
Slab case/Slab/Slab.cfdproj 8
STDCT case/STDCT/STDCT.cfdproj 8
CMI-China case/CMI-China/CMI_China.cfdproj 8
Base604 case/Base604/Base604.cfdproj 16
Base2-9 case/Base2-9/Base2-9.cfdproj 16
DAML-Testbed case/DAML-Testbed/DMAL_Testbed-patched.cfdproj 8
Shinaide case/Shinaide/Shinaide.cfdproj 8
Shinaide_Liquid case/Shinaide_Liquid/Shinaide_liquid.cfdproj 8
LIST
echo done >> "$OUT/progress.txt"
