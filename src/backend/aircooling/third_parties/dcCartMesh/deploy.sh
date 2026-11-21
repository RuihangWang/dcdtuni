#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# deploy.sh — one command to get the dcdtuni meshers built and verified on a
#             freshly cloned repo.
#
#   ./deploy.sh              # check env, build everything, smoke-test
#   ./deploy.sh --cart-only  # skip the (slow) snappyHexMeshDC rebuild
#   ./deploy.sh --no-test    # build only
#   ./deploy.sh --clean      # wipe platforms/ and rebuild from scratch
#
# Builds, NON-DESTRUCTIVELY, into this directory's platforms/ tree:
#
#   bin/dcCartMesh          the cut-cell Cartesian mesh generator
#   bin/snappyHexMeshDC     the editable snappyHexMesh (A/B reference)
#   bin/dcCheckMesh         checkMesh with one patch-topology sync (same output)
#   lib/libdcCartMesh.so    lib/libsnappyHexMeshDC.so
#
# Nothing in the conda prefix is ever overwritten: the stock `snappyHexMesh`
# stays exactly where it was.
# ---------------------------------------------------------------------------
set -eo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd -P)"

CART_ONLY=0
RUN_TEST=1
DO_CLEAN=0
for a in "$@"; do
    case "$a" in
        --cart-only) CART_ONLY=1 ;;
        --no-test)   RUN_TEST=0 ;;
        --clean)     DO_CLEAN=1 ;;
        -h|--help)   sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "deploy.sh: unknown option '$a'" >&2; exit 2 ;;
    esac
done

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[1;32m✓\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
say "1/5  Checking prerequisites"
# ---------------------------------------------------------------------------
command -v conda >/dev/null 2>&1 || die "conda not found on PATH. Install miniconda/anaconda first."
ok "conda: $(command -v conda)"

DC_DEPLOY_REPO="$(cd "$ROOT/../../../../.." && pwd -P)"
PREFIX="${DCDTUNI_ENV_PREFIX:-}"
if [[ -z "$PREFIX" && -x "$DC_DEPLOY_REPO/.codex-tmp/envs/dcdtuni/bin/python" ]]; then
    PREFIX="$DC_DEPLOY_REPO/.codex-tmp/envs/dcdtuni"
fi
if [[ -n "$PREFIX" ]]; then
    PREFIX="$(realpath -m "$PREFIX")"
    [[ "$PREFIX" == "$DC_DEPLOY_REPO/"* && "$(basename "$PREFIX")" == dcdtuni ]] || die "DCDTUNI_ENV_PREFIX must stay inside this checkout and end in dcdtuni"
    export DCDTUNI_ENV_PREFIX="$PREFIX"
else
    PREFIX="$(conda env list | awk '$1=="dcdtuni"{print $NF}')"
fi
[ -x "$PREFIX/bin/python" ] || die "dcdtuni environment not found; run tools/bootstrap_dcdtuni.py"
ok "conda prefix: $PREFIX"

FOAM_API_GUESS="$(ls -d "$PREFIX"/include/OpenFOAM-* 2>/dev/null | head -1)"
[ -n "$FOAM_API_GUESS" ] \
    || die "OpenFOAM dev headers not found under $PREFIX/include/. Install with: conda install -n dcdtuni openfoam"
ok "OpenFOAM headers: $FOAM_API_GUESS"

command -v g++ >/dev/null 2>&1 || die "no C++ compiler. Install g++ (or: conda install -n dcdtuni gxx_linux-64)"
ok "compiler: $(g++ --version | head -1)"

# ---------------------------------------------------------------------------
say "2/5  Preparing the build environment"
# ---------------------------------------------------------------------------
# env.sh activates dcdtuni, creates the $CONDA_PREFIX/src header-bridge symlink
# that wmake's hardcoded LIB_SRC needs, and points the build output at
# platforms/ here.
# shellcheck disable=SC1091
source "$ROOT/env.sh"
# A bare wmake -j overrides WM_NCOMPPROCS with all available cores.
# Pass the caller's activated job budget explicitly instead.
BUILD_JOBS="${WM_NCOMPPROCS:-$(nproc)}"
[[ "$BUILD_JOBS" =~ ^[1-9][0-9]*$ ]] || die "WM_NCOMPPROCS must be a positive integer"
ok "WM_OPTIONS  = $WM_OPTIONS"
ok "output bin  = $FOAM_USER_APPBIN"

if [ "$DO_CLEAN" -eq 1 ]; then
    say "     --clean: removing platforms/ and stale wmake artefacts"
    rm -rf "$ROOT/platforms"
    find "$ROOT" -type d -name 'lnInclude' -prune -exec rm -rf {} + 2>/dev/null || true
    find "$ROOT" -type d -path '*/Make/linux*' -prune -exec rm -rf {} + 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
say "3/5  Building"
# ---------------------------------------------------------------------------
BUILD_ARGS=(-j "$BUILD_JOBS")
[ "$CART_ONLY" -eq 1 ] && BUILD_ARGS+=(--cart-only)
"$ROOT/Allwmake" "${BUILD_ARGS[@]}"

[ -x "$FOAM_USER_APPBIN/dcCartMesh" ] || die "dcCartMesh was not produced — see the build log above"
ok "dcCartMesh built"
[ -x "$FOAM_USER_APPBIN/dcCheckMesh" ] || die "dcCheckMesh was not produced — see the build log above"
ok "dcCheckMesh built"
if [ "$CART_ONLY" -eq 0 ]; then
    [ -x "$FOAM_USER_APPBIN/snappyHexMeshDC" ] || die "snappyHexMeshDC was not produced"
    ok "snappyHexMeshDC built"
fi

# ---------------------------------------------------------------------------
if [ "$RUN_TEST" -eq 1 ]; then
say "4/5  Smoke test — mesh a refined box and validate it with checkMesh"

TESTDIR="$(mktemp -d "${TMPDIR:-/tmp}/dcCartMesh-smoke-XXXXXX")"
trap 'rm -rf "$TESTDIR"' EXIT
mkdir -p "$TESTDIR/system" "$TESTDIR/constant"

cat > "$TESTDIR/system/controlDict" <<'EOF'
FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application dcCartMesh; startFrom startTime; startTime 0; stopAt endTime;
endTime 0; deltaT 1; writeControl timeStep; writeInterval 1;
writeFormat binary; writePrecision 6; writeCompression off;
timeFormat general; timePrecision 6; runTimeModifiable true;
EOF
cat > "$TESTDIR/system/fvSchemes" <<'EOF'
FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }
ddtSchemes { default steadyState; } gradSchemes { default Gauss linear; }
divSchemes { default none; } laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; } snGradSchemes { default corrected; }
EOF
printf 'FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\nsolvers { }\n' \
    > "$TESTDIR/system/fvSolution"

# No STL: an empty `geometry` keeps dcCartMesh from requiring a
# snappyHexMeshDict, and the carve needs a seed (kept off every grid plane).
python3 - "$TESTDIR/system/dcCartMeshDict" <<'PY'
import sys
n, L = 10, 1.0
v = " ".join(str(round(i * L / n, 6)) for i in range(n + 1))
open(sys.argv[1], "w").write(f"""FoamFile {{ version 2.0; format ascii; class dictionary; object dcCartMeshDict; }}
maxLevel            2;
nCellsBetweenLevels 1;
background {{ x ({v}); y ({v}); z ({v}); }}
refinementBoxes {{ core {{ min (0.35 0.35 0.35); max (0.65 0.65 0.65); level 2; }} }}
geometry {{ }}
locationInMesh (0.123 0.456 0.789);
""")
PY

( cd "$TESTDIR" && dcCartMesh -overwrite > log.dcCartMesh 2>&1 ) \
    || { tail -30 "$TESTDIR/log.dcCartMesh"; die "dcCartMesh failed on the smoke-test case"; }
grep -E '^Assembled:' "$TESTDIR/log.dcCartMesh" | sed 's/^/    /'

( cd "$TESTDIR" && checkMesh -allTopology > log.checkMesh 2>&1 ) || true
CM="$TESTDIR/log.checkMesh"

for probe in \
    "Upper triangular ordering OK" \
    "Face-face connectivity OK" \
    "Topological cell zip-up check OK" \
    "Number of regions: 1"
do
    grep -q "$probe" "$CM" || { sed -n '/Checking topology/,/^$/p' "$CM"; die "checkMesh: '$probe' not reported"; }
    ok "$probe"
done

# Total volume must equal the domain exactly (1 m cube).
VOL="$(grep -oE 'Total volume = [0-9.e+-]+' "$CM" | awk '{print $4}')"
python3 -c "
import sys
v = float('$VOL')
sys.exit(0 if abs(v - 1.0) < 1e-9 else 1)
" || die "checkMesh reports total volume $VOL, expected 1.0 — the carve is leaking"
ok "total volume $VOL (exact)"

# The ONLY check an octree mesh is expected to fail is 'concave cells': the
# transition cells whose refined side is four coplanar sub-faces. Stock
# snappyHexMesh reports exactly the same on its own castellated meshes.
FAILED="$(grep -cE '^ \*\*\*' "$CM" || true)"
CONCAVE="$(grep -cE '^ \*\*\*Concave cells' "$CM" || true)"
if [ "$FAILED" -gt "$CONCAVE" ]; then
    grep -E '^ \*\*\*' "$CM"
    die "checkMesh reported a failure other than the expected octree 'concave cells'"
fi
ok "no unexpected checkMesh failures"

# The same case on two ranks. dcCartMesh partitions itself and reads only
# numberOfSubdomains from decomposeParDict, but the processor<i> case
# directories must exist. This is the any-np == serial red line in miniature
# (identical cell count), and it exercises the rank-dependent bookkeeping --
# per-rank patch lists, reductions -- that a serial run never touches: the
# 2026-09-22 census reduce that aborted every np8 run passed the serial smoke.
if command -v mpirun >/dev/null 2>&1; then
    say "5/5  Parallel smoke — the same case on 2 ranks"
    SERIAL_CELLS="$(grep -oE '^Cut mesh: cells [0-9]+' "$TESTDIR/log.dcCartMesh" | awk '{print $4}')"
    printf 'FoamFile { version 2.0; format ascii; class dictionary; object decomposeParDict; }\nnumberOfSubdomains 2;\nmethod scotch;\n' \
        > "$TESTDIR/system/decomposeParDict"
    mkdir -p "$TESTDIR/processor0" "$TESTDIR/processor1"
    ( cd "$TESTDIR" && mpirun -np 2 dcCartMesh -overwrite -parallel > log.dcCartMesh.np2 2>&1 ) \
        || { tail -30 "$TESTDIR/log.dcCartMesh.np2"; die "dcCartMesh -parallel failed on 2 ranks"; }
    PAR_CELLS="$(grep -oE '^Cut mesh: cells [0-9]+ over 2 rank' "$TESTDIR/log.dcCartMesh.np2" | awk '{print $4}')"
    [ -n "$SERIAL_CELLS" ] && [ -n "$PAR_CELLS" ] \
        || die "could not read the cell totals (serial '$SERIAL_CELLS', 2 ranks '$PAR_CELLS')"
    [ "$SERIAL_CELLS" = "$PAR_CELLS" ] \
        || die "2 ranks meshed $PAR_CELLS cells, serial $SERIAL_CELLS — the any-np == serial red line is broken"
    for i in 0 1; do
        [ -f "$TESTDIR/processor$i/constant/polyMesh/faces" ] || die "rank $i wrote no processor mesh"
    done
    ok "2 ranks: $PAR_CELLS cells, same as serial; both processor meshes written"
else
    say "5/5  Parallel smoke skipped (no mpirun on PATH)"
fi
fi

# ---------------------------------------------------------------------------
say "Done"
# ---------------------------------------------------------------------------
cat <<EOF
    Binaries are in
        $FOAM_USER_APPBIN

    To use them in a shell, source the build env (it also puts them on PATH):
        source $ROOT/env.sh
        dcCartMesh -help

    The dcdtuni runner finds them by path and needs no PATH setup.
    Stock 'snappyHexMesh' from the conda prefix is untouched.
EOF
