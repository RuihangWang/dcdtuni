#!/usr/bin/env bash
# Pytest-only MPI2 operators; no production solver or case workflow is run.
set -eo pipefail
TASK_REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
if [ -z "$CONDA_PREFIX" ] || [ "$(basename "$CONDA_PREFIX")" != dcdtuni ]; then
    echo "Activate the existing dcdtuni conda environment before running." >&2
    exit 2
fi
if [ ! -e "$CONDA_PREFIX/src/finiteVolume/lnInclude/EulerDdtScheme.C" ]; then
    echo "Existing OpenFOAM development header bridge is required." >&2
    exit 2
fi
source "$TASK_REPO/src/backend/aircooling/third_parties/foam/env.sh"
TASK_WORK="$TASK_REPO/.codex-tmp/pimple-startup-coupling/reproduced-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$TASK_WORK/tmp"
export TMPDIR="$TASK_WORK/tmp"
export PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES=
export DCDTUNI_STARTUP_KERNEL_TEST=1
cd "$TASK_REPO"
python -m pytest -q -p no:cacheprovider --basetemp "$TASK_WORK/pytest" \
  src/backend/tests/test_pimple_startup_coupling.py \
  src/backend/tests/test_audit_air_written_flux_conservation.py \
  src/backend/tests/test_audit_air_written_coupled_pairs.py
