#!/usr/bin/env bash
set -euo pipefail
: "${DCDT_MESHER:?Set DCDT_MESHER to the executable under test}"
case_dir=${1:?Pass a new scratch case directory}
mkdir -p "$case_dir/system" "$case_dir/constant"
cat > "$case_dir/system/controlDict" <<'EOF'
FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application dcCartMesh; startFrom startTime; startTime 0; stopAt endTime; endTime 0;
deltaT 1; writeControl timeStep; writeInterval 1; writeFormat ascii;
EOF
cat > "$case_dir/system/dcCartMeshDict" <<'EOF'
FoamFile { version 2.0; format ascii; class dictionary; object dcCartMeshDict; }
maxLevel 0; maxGlobalCells 100; timeLimitSeconds 5;
background { x (0 0.5 1); y (0 0.5 1); z (0 0.5 1); }
EOF
cat > "$case_dir/system/snappyHexMeshDict" <<'EOF'
FoamFile { version 2.0; format ascii; class dictionary; object snappyHexMeshDict; }
geometry {}
castellatedMeshControls
{
    refinementSurfaces {}
    refinementRegions {}
    locationInMesh (0.23 0.24 0.26);
}
dcdtuniSourceSurfaces
{
    ghost.stl
    {
        missing { sourceObjectUid "object-1"; sourceFaceUid "face-1"; representation zeroThicknessSheet; }
    }
}
EOF
set +e
"$DCDT_MESHER" -case "$case_dir" -overwrite > "$case_dir/driver.log" 2>&1
status=$?
set -e
if [ "$status" -eq 0 ]; then
    echo 'FAIL: driver accepted source identity metadata for unknown STL ghost.stl'
    exit 1
fi
if ! grep -q 'unknown STL file.*ghost.stl' "$case_dir/driver.log"; then
    echo 'FAIL: driver failed for a reason other than metadata admission'
    tail -20 "$case_dir/driver.log"
    exit 1
fi
echo 'PASS: driver rejects unknown source STL before meshing'
