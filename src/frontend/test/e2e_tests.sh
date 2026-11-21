#!/bin/bash
# End-to-end tests using browser-use eval
# Tests pure computation functions and state operations
# Run: bash test/e2e_tests.sh

set -e

# The port to drive. Not a hardcoded 8005 — on a shared host that is usually
# someone else's server, and one server holds ONE global case dir.
PORT="${DCDTUNI_PORT:-8040}"
export PATH="$HOME/.local/bin:$PATH"

PASS=0
FAIL=0
ERRORS=""

run_test() {
    local name="$1"
    local js="$2"
    local expected="$3"

    local result
    result=$(browser-use eval "$js" 2>&1) || true
    # Trim whitespace
    result=$(echo "$result" | tr -d '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    if [ "$result" = "$expected" ]; then
        echo "  PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $name"
        echo "    Expected: $expected"
        echo "    Got:      $result"
        FAIL=$((FAIL + 1))
        ERRORS="$ERRORS\n  FAIL: $name (expected '$expected', got '$result')"
    fi
}

echo "=== Opening browser ==="
browser-use open http://localhost:$PORT/STL_generate.html 2>/dev/null || true
sleep 3

echo ""
echo "=== Test Suite: computePolygonArea ==="

run_test "Triangle area" \
    "computePolygonArea([[0,0],[10,0],[5,8.66]]).toFixed(2)" \
    "43.30"

run_test "Rectangle area" \
    "computePolygonArea([[0,0],[10,0],[10,8],[0,8]]).toFixed(2)" \
    "80.00"

run_test "Unit square area" \
    "computePolygonArea([[0,0],[1,0],[1,1],[0,1]]).toFixed(2)" \
    "1.00"

run_test "Empty polygon area" \
    "computePolygonArea([]).toFixed(2)" \
    "0.00"

run_test "2-vertex area" \
    "computePolygonArea([[0,0],[1,1]]).toFixed(2)" \
    "0.00"

run_test "L-shape area" \
    "computePolygonArea([[0,0],[6,0],[6,3],[3,3],[3,6],[0,6]]).toFixed(2)" \
    "27.00"

echo ""
echo "=== Test Suite: syncFloorBaseFromPolygon ==="

run_test "Sync triangle base" \
    "var m={kind:'floor',base:{W:0,D:0,H:0},polygon:[[0,0],[10,0],[5,8.66]]}; syncFloorBaseFromPolygon(m); m.base.W.toFixed(2)+','+m.base.D.toFixed(2)" \
    "10.00,8.66"

run_test "Sync rectangle base" \
    "var m={kind:'floor',base:{W:0,D:0,H:0},polygon:[[2,3],[12,3],[12,11],[2,11]]}; syncFloorBaseFromPolygon(m); m.base.W.toFixed(2)+','+m.base.D.toFixed(2)" \
    "10.00,8.00"

run_test "Skip non-floor" \
    "var m={kind:'wall',base:{W:5,D:0,H:3},polygon:[[0,0],[10,0],[5,8]]}; syncFloorBaseFromPolygon(m); m.base.W.toFixed(2)" \
    "5.00"

echo ""
echo "=== Test Suite: getModelOBB ==="

run_test "Axis-aligned OBB center" \
    "var o=getModelOBB({anchor:{x:0,y:0,z:0},base:{W:2,D:4,H:6},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}}); o.center.x.toFixed(1)+','+o.center.y.toFixed(1)+','+o.center.z.toFixed(1)" \
    "1.0,2.0,3.0"

run_test "OBB half extents" \
    "var o=getModelOBB({anchor:{x:0,y:0,z:0},base:{W:2,D:4,H:6},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}}); o.half[0]+','+o.half[1]+','+o.half[2]" \
    "1,2,3"

run_test "OBB axes identity" \
    "var o=getModelOBB({anchor:{x:0,y:0,z:0},base:{W:2,D:4,H:6},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}}); o.axes[0].x.toFixed(0)+','+o.axes[1].y.toFixed(0)+','+o.axes[2].z.toFixed(0)" \
    "1,1,1"

run_test "OBB 8 corners count" \
    "var o=getModelOBB({anchor:{x:0,y:0,z:0},base:{W:2,D:2,H:2},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}}); getOBBCorners(o).length" \
    "8"

echo ""
echo "=== Test Suite: obbOverlapSAT ==="

run_test "Overlapping boxes" \
    "var a=getModelOBB({anchor:{x:0,y:0,z:0},base:{W:2,D:2,H:2},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}}); var b=getModelOBB({anchor:{x:1,y:0,z:0},base:{W:2,D:2,H:2},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}}); obbOverlapSAT(a,b,1e-4)" \
    "true"

run_test "Face-touching boxes (no overlap)" \
    "var a=getModelOBB({anchor:{x:0,y:0,z:0},base:{W:2,D:2,H:2},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}}); var b=getModelOBB({anchor:{x:2,y:0,z:0},base:{W:2,D:2,H:2},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}}); obbOverlapSAT(a,b,1e-4)" \
    "false"

run_test "Separated boxes" \
    "var a=getModelOBB({anchor:{x:0,y:0,z:0},base:{W:2,D:2,H:2},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}}); var b=getModelOBB({anchor:{x:5,y:0,z:0},base:{W:2,D:2,H:2},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}}); obbOverlapSAT(a,b,1e-4)" \
    "false"

echo ""
echo "=== Test Suite: hasModelRotation ==="

run_test "Identity = no rotation" \
    "hasModelRotation({transform:{quaternion:{x:0,y:0,z:0,w:1}}})" \
    "false"

run_test "Z rotation detected" \
    "hasModelRotation({transform:{quaternion:{x:0,y:0,z:0.383,w:0.924}}})" \
    "true"

run_test "No transform = no rotation" \
    "hasModelRotation({})" \
    "false"

echo ""
echo "=== Test Suite: Model Group CRUD ==="

run_test "Create group" \
    "state.models=[{id:'a',kind:'rack',name:'R1',enabled:true,anchor:{x:0,y:0,z:0},base:{W:1,D:1,H:1},faces:{},opacity:1},{id:'b',kind:'rack',name:'R2',enabled:true,anchor:{x:2,y:0,z:0},base:{W:1,D:1,H:1},faces:{},opacity:1}]; state.groups=[]; _rebuildModelMap(); createModelGroup('TestGroup',['a','b']); state.groups.length" \
    "1"

run_test "Group has 2 members" \
    "state.groups[0].memberIds.length" \
    "2"

run_test "getModelGroup finds group" \
    "getModelGroup('a') !== null" \
    "true"

run_test "Dissolve group" \
    "dissolveModelGroup(state.groups[0].id); state.groups.length" \
    "0"

echo ""
echo "=== Test Suite: normalizeState validation ==="

run_test "Invalid polygon nulled" \
    "var s=normalizeState({models:[{kind:'floor',name:'F',polygon:'bad'}]}); s.models[0].polygon" \
    "null"

run_test "Valid polygon preserved" \
    "var s=normalizeState({models:[{kind:'floor',name:'F',polygon:[[0,0],[1,0],[1,1]]}]}); s.models[0].polygon.length" \
    "3"

run_test "Invalid roomPolygon nulled" \
    "var s=normalizeState({roomPolygon:{vertices:[[0,0]],height:3}}); s.roomPolygon" \
    "null"

run_test "Valid roomPolygon preserved" \
    "var s=normalizeState({roomPolygon:{vertices:[[0,0],[10,0],[5,8]],height:3}}); s.roomPolygon.vertices.length" \
    "3"

run_test "Groups with <2 members dissolved" \
    "var s=normalizeState({models:[{id:'x',kind:'box',name:'B'}],groups:[{id:'g1',name:'G',memberIds:['x'],collapsed:false}]}); s.groups.length" \
    "0"

echo ""
echo "=== Closing browser ==="
browser-use close 2>/dev/null || true

echo ""
echo "==============================="
echo "  Results: $PASS passed, $FAIL failed"
echo "==============================="

if [ $FAIL -gt 0 ]; then
    echo -e "\nFailures:$ERRORS"
    exit 1
fi
