#!/usr/bin/env bash
# Rebuild only repo-local binaries and run opt-in numerical pytest regressions.
# Activate the dcdtuni conda environment before invoking this script.
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo_dir"
if [ ! -d "${CONDA_PREFIX:-}/src/meshTools/lnInclude" ]; then
    echo "Activate the existing dcdtuni OpenFOAM v2412 environment first." >&2
    exit 2
fi
source src/backend/aircooling/third_parties/foam/env.sh
build_dir="$repo_dir/.codex-tmp/pressure-compat-face-build"
export FOAM_USER_APPBIN="$build_dir/platforms/$WM_OPTIONS/bin"
export FOAM_USER_LIBBIN="$build_dir/platforms/$WM_OPTIONS/lib"
export TMPDIR="$build_dir/tmp"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 CUDA_VISIBLE_DEVICES=
mkdir -p "$FOAM_USER_APPBIN" "$FOAM_USER_LIBBIN" "$TMPDIR"
wmake src/backend/tests/pressure_component_kernel > "$build_dir/kernel-build.log" 2>&1
wmake src/backend/aircooling/third_parties/foam/src/applications/solvers/buoyantBoussinesqSimpleFoam_new \
    > "$build_dir/solver-build.log" 2>&1
wmake src/backend/aircooling/third_parties/foam/src/applications/solvers/buoyantBoussinesqPimpleFoam_new \
    > "$build_dir/pimple-build.log" 2>&1
export DCDTUNI_PRESSURE_KERNEL="$FOAM_USER_APPBIN/pressureComponentKernel"
export DCDTUNI_PRESSURE_SOLVER="$FOAM_USER_APPBIN/buoyantBoussinesqSimpleFoam_new"
export DCDTUNI_PIMPLE_SOLVER="$FOAM_USER_APPBIN/buoyantBoussinesqPimpleFoam_new"
python -m pytest -q src/backend/tests/test_pressure_component_references.py \
    src/backend/tests/test_pimple_component_solver.py \
    src/backend/tests/test_pressure_component_compatibility.py \
    src/backend/tests/test_solver_binary_not_built.py \
    --basetemp="$build_dir/pytest-reproduced" "$@"
