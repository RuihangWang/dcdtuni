#!/usr/bin/env bash
# Private API server for `python -m backend.api` (no web page). backend/api/server_session.py starts it as
#   src/start_server.sh --app api --port P --ws-port P+1 --case-dir D --auth-dir A
# with the administrator pair in DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD. Other arguments pass to server.py.
#
# Python: $DCTWIN_PYTHON, else the active conda environment ($CONDA_PREFIX/bin/python). OpenFOAM comes from that
# same environment (conda-forge openfoam=2412); any system OpenFOAM on PATH is removed first.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORT=8000
WS_PORT=""
PASSTHROUGH_ARGS=()
while (( $# )); do
    case "$1" in
        --app)
            [[ "${2:-}" == "api" ]] || { echo "error: this distribution has only --app api" >&2; exit 2; }
            shift 2 ;;
        --app=*)
            [[ "${1#--app=}" == "api" ]] || { echo "error: this distribution has only --app api" >&2; exit 2; }
            shift ;;
        --port) PORT="$2"; shift 2 ;;
        --ws-port) WS_PORT="$2"; shift 2 ;;
        *) PASSTHROUGH_ARGS+=("$1"); shift ;;
    esac
done
WS_PORT="${WS_PORT:-$((PORT + 1))}"

port_in_use() {
    ss -tln 2>/dev/null | awk 'NR>1 {print $4}' | grep -qE ":$1\$"
}
for p in "$PORT" "$WS_PORT"; do
    if port_in_use "$p"; then
        echo "error: port $p is in use; pick another --port" >&2
        exit 1
    fi
done

PYTHON="${DCTWIN_PYTHON:-}"
if [[ -z "$PYTHON" && -n "${CONDA_PREFIX:-}" ]]; then
    PYTHON="$CONDA_PREFIX/bin/python"
fi
[[ -n "$PYTHON" && -x "$PYTHON" ]] || {
    echo "error: no Python: activate the dctwin conda environment (setup_env.sh) or set DCTWIN_PYTHON" >&2; exit 1;
}
PREFIX="$(cd "$(dirname "$PYTHON")/.." && pwd)"

# OpenFOAM entirely from the conda environment.
export PATH="${PREFIX}/bin:$(echo "$PATH" | tr ':' '\n' | grep -iv 'openfoam' | grep -v '/openmpi/' | tr '\n' ':')"
export LD_LIBRARY_PATH="$(echo "${LD_LIBRARY_PATH:-}" | tr ':' '\n' | grep -iv 'openfoam' | grep -v '/openmpi/' | tr '\n' ':')"
export WM_PROJECT_DIR="$PREFIX"
export CONDA_PREFIX="$PREFIX"
export FOAM_LIBBIN="${PREFIX}/lib"
export FOAM_APPBIN="${PREFIX}/bin"
export FOAM_ETC="${PREFIX}/etc"
export FOAM_MPI=sys-mpich
export MPI_ARCH_PATH="$PREFIX"

export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" src/server/server.py --port "$PORT" --ws-port "$WS_PORT" --app api "${PASSTHROUGH_ARGS[@]}"
