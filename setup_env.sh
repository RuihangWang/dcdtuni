#!/bin/bash
set -euo pipefail

ENV_NAME="${ENV_NAME:-dcdtuni}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
echo ">>> Working directory: $SCRIPT_DIR"

if [[ ! -d "${SCRIPT_DIR}/src/frontend" ]]; then
    echo "Error: expected frontend at ${SCRIPT_DIR}/src/frontend." >&2
    exit 1
fi

# ── 1. Repository-contained Conda env ──────────────────────────────────────
ENV_PREFIX="${DCDTUNI_ENV_PREFIX:-$SCRIPT_DIR/.codex-tmp/envs/$ENV_NAME}"
ENV_PREFIX="$(realpath -m "$ENV_PREFIX")"
[[ "$ENV_PREFIX" == "$SCRIPT_DIR/"* && "$(basename "$ENV_PREFIX")" == "$ENV_NAME" ]] || {
    echo "Error: DCDTUNI_ENV_PREFIX must stay inside this checkout and end in $ENV_NAME." >&2
    exit 1
}
export DCDTUNI_ENV_PREFIX="$ENV_PREFIX"
export CONDA_REGISTER_ENVS=false
export CONDA_ENVS_PATH="$(dirname "$ENV_PREFIX")"
export CONDA_PKGS_DIRS="${CONDA_PKGS_DIRS:-$SCRIPT_DIR/.codex-tmp/bootstrap-cache/conda-packages}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$SCRIPT_DIR/.codex-tmp/bootstrap-cache/pip}"
mkdir -p "$(dirname "$ENV_PREFIX")" "$SCRIPT_DIR/.codex-tmp/bootstrap-cache/tmp"
export TMPDIR="$SCRIPT_DIR/.codex-tmp/bootstrap-cache/tmp"
if [[ -x "$ENV_PREFIX/bin/python" ]]; then
    echo ">>> Conda environment already exists: $ENV_PREFIX"
else
    echo ">>> Creating Conda environment: $ENV_PREFIX..."
    conda create -p "$ENV_PREFIX" --override-channels -c conda-forge \
        python=3.10 "python_abi=3.10=*_cp310" -y
fi

echo ""
echo ">>> Installing OpenFOAM 2412 + MPICH + GMP 6.3 + Boost headers from conda-forge..."
# Keep CPython's ABI explicit during both solves: an unconstrained OpenFOAM
# solve can replace Python with GraalPy and break pip's TLS/build isolation.
# dcCartMesh's exact arithmetic includes Boost.Multiprecision (header-only);
# a host without system Boost needs the headers in the environment.
conda install -p "$ENV_PREFIX" --override-channels -c conda-forge \
    python=3.10 "python_abi=3.10=*_cp310" openfoam=2412 mpich gmp=6.3.0 libboost-headers -y

# Exact native geometry uses GMP and Boost directly; require their build files.
[[ -r "$ENV_PREFIX/include/gmp.h" && -r "$ENV_PREFIX/lib/libgmp.so" ]] || {
    echo "Error: GMP development header/library missing from $ENV_PREFIX." >&2
    exit 1
}
[[ -r "$ENV_PREFIX/include/boost/version.hpp" ]] || {
    echo "Error: Boost headers missing from $ENV_PREFIX." >&2
    exit 1
}

# Fail before any pip operation if Conda selected another implementation or
# activation points to a different prefix. HTTPS uses CPython's OpenSSL backend.
conda run -p "$ENV_PREFIX" python -c '
import os, ssl, sys
from pathlib import Path
if sys.implementation.name != "cpython":
    raise SystemExit("CPython is required")
if sys.version_info[:2] != (3, 10):
    raise SystemExit("CPython 3.10 is required")
if Path(sys.prefix).resolve() != Path(os.environ["DCDTUNI_ENV_PREFIX"]).resolve():
    raise SystemExit("Unexpected Python prefix")
if not ssl.OPENSSL_VERSION.startswith("OpenSSL "):
    raise SystemExit("CPython OpenSSL backend is required")
ssl.create_default_context()
print("Verified CPython 3.10 / " + ssl.OPENSSL_VERSION)
'

# Match conda-forge's MPICH library layout without touching another environment.
_CONDA_LIB="$ENV_PREFIX/lib"
_MPICH_DIR=$(ls -d "${_CONDA_LIB}"/mpich-* 2>/dev/null | head -1)
if [[ -n "$_MPICH_DIR" ]] && [[ ! -e "${_CONDA_LIB}/sys-mpich" ]]; then
    ln -s "$(basename "$_MPICH_DIR")" "${_CONDA_LIB}/sys-mpich"
    echo "  Created sys-mpich -> $(basename "$_MPICH_DIR") for parallel Pstream"
fi

# ── 2. Python packages ───────────────────────────────────────────────────────
# Install with the conda env's OWN pip — NOT `poetry install`.
# Poetry does not understand conda environments: with virtualenvs.create=false it
# resolves the *base* conda interpreter and installs there instead of "$ENV_NAME",
# leaving this env empty (and polluting base).  `python -m pip` always targets the
# env it runs in.  poetry-core is the build backend, so `pip install -e ".[dev]"` installs
# the same deps declared in [tool.poetry.dependencies] from pyproject.toml.
echo ""
echo ">>> Installing core dependencies into '$ENV_NAME' (air-cooling CFD pipeline)..."
conda run -p "$ENV_PREFIX" python -m pip install -e ".[dev]"

# torch + networkx power liquid cooling; torch + scipy + torch-geometric + torch-scatter power the SCANO AI
# inference (the `liquid` and `ai` extras); torch + scipy + torch-geometric + warp-lang + pyamg + h5py power the
# GPU air solver (the `gpu-solver` extra, `start_server.sh --gpu`). They are heavy and hardware-dependent, so a
# failure here MUST NOT abort the core install: on any error, say how to install them by hand and carry on.
# torch is pinned to the version the PyG wheels are built for, with CUDA 12.8: the first builds with kernels for
# every card in use (sm_89 RTX 4090, sm_120 RTX PRO 6000 Blackwell). One environment, named dcdtuni, per machine.
# DCDTUNI_TORCH_CUDA=cpu installs CPU builds. PyG publishes the wheels of a torch minor release under its .0 page.
TORCH_VERSION="${DCDTUNI_TORCH_VERSION:-2.7.1}"
TORCH_CUDA="${DCDTUNI_TORCH_CUDA:-cu128}"
TORCH_INDEX="https://download.pytorch.org/whl/${TORCH_CUDA}"
PYG_WHEELS="https://data.pyg.org/whl/torch-${TORCH_VERSION%.*}.0+${TORCH_CUDA}.html"
echo ""
echo ">>> Installing torch ${TORCH_VERSION} (${TORCH_CUDA}) + liquid-cooling and AI-inference dependencies (best-effort)..."
if conda run -p "$ENV_PREFIX" python -m pip install "torch==${TORCH_VERSION}" --index-url "$TORCH_INDEX" \
   && conda run -p "$ENV_PREFIX" python -m pip install "networkx>=3.0" "scipy>=1.10,<2.0" "torch-geometric==2.8.0" \
   && conda run -p "$ENV_PREFIX" python -m pip install "torch-scatter==2.1.2" -f "$PYG_WHEELS" \
   && conda run -p "$ENV_PREFIX" python -m pip install "warp-lang==1.18.0" "pyamg>=5.0,<6" "h5py>=3.9" "tomli>=2.0"; then
    echo ">>> torch + liquid-cooling, AI-inference and GPU-solver dependencies installed."
else
    echo ""
    echo "!!! ───────────────────────────────────────────────────────────────────────"
    echo "!!! WARNING: torch / liquid-cooling / AI-inference / GPU-solver dependencies failed to install."
    echo "!!! The core air-cooling CFD pipeline is installed and usable; liquid cooling, the AI solve"
    echo "!!! and the GPU air solver need these. Install them by hand for YOUR hardware:"
    echo "!!!   conda run -p \"$ENV_PREFIX\" python -m pip install torch==${TORCH_VERSION} --index-url ${TORCH_INDEX}"
    echo "!!!   conda run -p \"$ENV_PREFIX\" python -m pip install networkx scipy torch-geometric==2.8.0"
    echo "!!!   conda run -p \"$ENV_PREFIX\" python -m pip install torch-scatter==2.1.2 -f ${PYG_WHEELS}"
    echo "!!!   conda run -p \"$ENV_PREFIX\" python -m pip install warp-lang==1.18.0 'pyamg>=5.0,<6' 'h5py>=3.9' 'tomli>=2.0'"
    echo "!!! (CPU only: rerun with DCDTUNI_TORCH_CUDA=cpu bash setup_env.sh)"
    echo "!!! ───────────────────────────────────────────────────────────────────────"
fi

# ── 3. Verify ─────────────────────────────────────────────────────────────────
echo ""
echo ">>> Verifying install..."

BACKEND_LOC=$(conda run -p "$ENV_PREFIX" python -c "import backend; print(backend.__file__)" 2>/dev/null || echo "NOT FOUND")
echo "  backend package: $BACKEND_LOC"

TORCH_INFO=$(conda run -p "$ENV_PREFIX" python -c "
import torch
cuda = 'CUDA ' + torch.version.cuda if torch.cuda.is_available() else 'CPU only'
print(f'torch {torch.__version__} ({cuda})')
" 2>/dev/null || echo "torch: not installed (optional — liquid cooling / AI postprocess only)")
echo "  $TORCH_INFO"

NX_VER=$(conda run -p "$ENV_PREFIX" python -c "import networkx; print('networkx', networkx.__version__)" 2>/dev/null || echo "networkx: not installed (optional)")
echo "  $NX_VER"

AI_VER=$(conda run -p "$ENV_PREFIX" python -c "import scipy, torch_geometric, torch_scatter; print('AI inference: scipy', scipy.__version__, '· torch-geometric', torch_geometric.__version__, '· torch-scatter', torch_scatter.__version__)" 2>/dev/null || echo "AI inference: dependencies not installed (optional)")
echo "  $AI_VER"

MPL_VER=$(conda run -p "$ENV_PREFIX" python -c "import matplotlib; print('matplotlib', matplotlib.__version__)" 2>/dev/null || echo "NOT FOUND")
echo "  $MPL_VER"

BM_LOC=$(conda run -p "$ENV_PREFIX" which blockMesh 2>/dev/null || echo "NOT FOUND")
echo "  blockMesh: $BM_LOC"

if [[ "$BACKEND_LOC" != *"$SCRIPT_DIR"* ]]; then
    echo ""
    echo "  WARNING: backend is NOT loading from this repo ($SCRIPT_DIR)."
    echo "           Run: conda run -p \"$ENV_PREFIX\" python -m pip install -e \".[dev]\""
fi

echo ""
echo ">>> Setup complete!"
echo "dcdtuni + OpenFOAM 2412 installed at $ENV_PREFIX."
echo ""
echo "Next, build both repository native trees (or use tools/bootstrap_dcdtuni.py):"
echo "    conda activate \"$ENV_PREFIX\""
echo "    (cd src/backend/aircooling/third_parties/dcCartMesh && ./deploy.sh)"
echo "    (cd src/backend/aircooling/third_parties/foam && ./deploy.sh)"
echo ""
echo "To start working:"
echo "    conda activate \"$ENV_PREFIX\""
echo "    ./src/start_server.sh"
