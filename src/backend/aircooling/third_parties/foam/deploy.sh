#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# deploy.sh — build and verify the dcdtuni custom OpenFOAM solvers on a
#             freshly cloned repo.
#
#   ./deploy.sh            # check env, build, smoke-test
#   ./deploy.sh --no-test  # build only
#   ./deploy.sh --clean    # wipe platforms/ and rebuild from scratch
#
# Builds NON-DESTRUCTIVELY into this directory's platforms/ tree:
#
#   bin/buoyantBoussinesqSimpleFoam_new
#
# Nothing in the conda prefix is overwritten; the stock
# `buoyantBoussinesqSimpleFoam` stays exactly where it was. The dcdtuni runner
# resolves the binary from platforms/ by path, so no PATH setup is needed.
# ---------------------------------------------------------------------------
set -eo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd -P)"

RUN_TEST=1
DO_CLEAN=0
for a in "$@"; do
    case "$a" in
        --no-test) RUN_TEST=0 ;;
        --clean)   DO_CLEAN=1 ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "deploy.sh: unknown option '$a'" >&2; exit 2 ;;
    esac
done

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()  { printf '    \033[1;32m✓\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
say "1/4  Checking prerequisites"
# ---------------------------------------------------------------------------
command -v conda >/dev/null 2>&1 || die "conda not found on PATH."
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

ls -d "$PREFIX"/include/OpenFOAM-* >/dev/null 2>&1 \
    || die "OpenFOAM dev headers missing under $PREFIX/include/"
ok "OpenFOAM headers present"

command -v g++ >/dev/null 2>&1 || die "no C++ compiler (install g++)"
ok "compiler: $(g++ --version | head -1)"

# ---------------------------------------------------------------------------
say "2/4  Preparing the build environment"
# ---------------------------------------------------------------------------
# shellcheck disable=SC1091
source "$ROOT/env.sh"
# A bare wmake -j overrides WM_NCOMPPROCS with all available cores.
# Pass the caller's activated job budget explicitly instead.
BUILD_JOBS="${WM_NCOMPPROCS:-$(nproc)}"
[[ "$BUILD_JOBS" =~ ^[1-9][0-9]*$ ]] || die "WM_NCOMPPROCS must be a positive integer"
ok "WM_OPTIONS = $WM_OPTIONS"
ok "output bin = $FOAM_USER_APPBIN"

if [ "$DO_CLEAN" -eq 1 ]; then
    say "     --clean: removing platforms/ and stale wmake artefacts"
    rm -rf "$ROOT/platforms"
    find "$ROOT" -type d -path '*/Make/linux*' -prune -exec rm -rf {} + 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
say "3/4  Building"
# ---------------------------------------------------------------------------
"$ROOT/Allwmake" -j "$BUILD_JOBS"

SOLVER="$FOAM_USER_APPBIN/buoyantBoussinesqSimpleFoam_new"
[ -x "$SOLVER" ] || die "buoyantBoussinesqSimpleFoam_new was not produced — see the build log"
ok "buoyantBoussinesqSimpleFoam_new built"

# ---------------------------------------------------------------------------
if [ "$RUN_TEST" -eq 1 ]; then
say "4/4  Smoke test — solve the hotRoom tutorial and check the boundary values"

TUT="$(ls -d "$PREFIX"/tutorials/heatTransfer/buoyantBoussinesqSimpleFoam/hotRoom 2>/dev/null | head -1)"
if [ -z "$TUT" ]; then
    printf '    \033[1;33m!\033[0m hotRoom tutorial not found under %s — skipping the smoke test\n' \
        "$PREFIX/tutorials"
else
    TESTDIR="$(mktemp -d "${TMPDIR:-/tmp}/bbsf-new-smoke-XXXXXX")"
    trap 'rm -rf "$TESTDIR"' EXIT
    cp -r "$TUT/." "$TESTDIR/"
    ( cd "$TESTDIR" && rm -rf 0 processor* && cp -r 0.orig 0 )

    python3 - "$TESTDIR/system/controlDict" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text()
s = re.sub(r'^endTime\s+\S+;',      'endTime         5;',  s, flags=re.M)
s = re.sub(r'^writeInterval\s+\S+;','writeInterval   5;',  s, flags=re.M)
s = re.sub(r'^writeFormat\s+\S+;',  'writeFormat     ascii;', s, flags=re.M)
if 'explicitBoundaryValues' not in s:
    s += "\nexplicitBoundaryValues\n{\n    mode        preserveType;\n}\n"
p.write_text(s)
PY

    ( cd "$TESTDIR" && blockMesh > log.blockMesh 2>&1 ) || die "blockMesh failed on the tutorial"
    ( cd "$TESTDIR" && setFields > log.setFields 2>&1 ) || true
    ( cd "$TESTDIR" && "$SOLVER" > log.solve 2>&1 ) \
        || { tail -30 "$TESTDIR/log.solve"; die "solver run failed"; }

    grep -q "writeBCFields (preserveType)" "$TESTDIR/log.solve" \
        || { tail -20 "$TESTDIR/log.solve"; die "writeBCFields did not run"; }
    ok "writeBCFields ran in preserveType mode"

    # The point of the whole exercise: no physical patch may be value-less.
    python3 - "$TESTDIR/5" <<'PY' || exit 1
import re, sys, pathlib
d = pathlib.Path(sys.argv[1])
bad = []
checked = 0
for f in sorted(d.iterdir()):
    if not f.is_file() or f.name in ("phi",) or f.name.startswith("uniform"):
        continue
    s = f.read_text(errors="replace")
    i = s.find("boundaryField")
    if i < 0:
        continue
    checked += 1
    for name, body in re.findall(r"\n    (\w[\w.]*)\n    \{(.*?)\n    \}", s[i:], re.S):
        if "type" not in body:
            continue
        # coupled / constraint patches legitimately carry no value
        if re.search(r"type\s+(cyclic|processor|empty|wedge|symmetry|symmetryPlane|nonuniformTransformCyclic)", body):
            continue
        if "value" not in body:
            bad.append(f"{f.name}:{name}")
if not checked:
    print("    no fields found in", d); sys.exit(1)
if bad:
    print("    value-less physical patches still present:", ", ".join(bad[:8]))
    sys.exit(1)
print(f"    every physical patch in {checked} fields carries explicit values")
PY
    ok "no value-less physical patches remain"

    # And zeroGradient must have SURVIVED as a type (preserveType, not fixedValue).
    grep -qE 'type\s+zeroGradient;' "$TESTDIR/5/T" \
        && ok "preserveType kept the original BC types (T still has zeroGradient)" \
        || printf '    \033[1;33m!\033[0m T has no zeroGradient patch in this tutorial — type preservation not exercised\n'
fi
fi

# ---------------------------------------------------------------------------
say "Done"
# ---------------------------------------------------------------------------
cat <<EOF
    Binary is in
        $FOAM_USER_APPBIN

    The dcdtuni runner resolves it from platforms/ by path — no PATH setup.
    To use it by hand:
        source $ROOT/env.sh
        buoyantBoussinesqSimpleFoam_new -help
EOF
