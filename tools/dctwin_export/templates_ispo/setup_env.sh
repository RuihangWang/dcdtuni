#!/usr/bin/env bash
# Create the conda environment of the data-centre design evaluator and build what it needs.
#
#   ./setup_env.sh                  # environment "dceval" (DCEVAL_ENV=<name> to change)
#   DCEVAL_TORCH_CUDA=cpu ./setup_env.sh
#
# 1. conda-forge: CPython 3.10, OpenFOAM 2412, MPICH, Node.js (the prebuilt mesher links against this OpenFOAM)
# 2. pip: the backend (pyproject.toml), torch for the liquid-cooling DAE and the GPU air solver's packages
# 3. npm: jsdom for the headless project compiler (src/headless)
# 4. wmake: the buoyant Boussinesq solvers (src/backend/aircooling/third_parties/foam)
set -eo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd -P)"

ENV_NAME="${DCEVAL_ENV:-dceval}"
command -v conda >/dev/null || { echo "error: conda not found on PATH" >&2; exit 1; }
BASE="$(conda info --base)"
PREFIX="$BASE/envs/$ENV_NAME"

if [[ ! -x "$PREFIX/bin/python" ]]; then
    echo ">>> Creating conda environment $ENV_NAME"
    conda create -n "$ENV_NAME" --override-channels -c conda-forge python=3.10 "python_abi=3.10=*_cp310" -y
fi
echo ">>> OpenFOAM 2412 + MPICH + Node.js from conda-forge"
conda install -n "$ENV_NAME" --override-channels -c conda-forge \
    python=3.10 "python_abi=3.10=*_cp310" openfoam=2412 mpich gmp=6.3.0 libboost-headers "nodejs>=20" -y

# conda-forge puts the MPICH Pstream under lib/mpich-<ver>; OpenFOAM looks for lib/sys-mpich.
MPICH_DIR=$(ls -d "$PREFIX"/lib/mpich-* 2>/dev/null | head -1)
if [[ -n "$MPICH_DIR" && ! -e "$PREFIX/lib/sys-mpich" ]]; then
    ln -s "$(basename "$MPICH_DIR")" "$PREFIX/lib/sys-mpich"
fi

echo ">>> Python packages"
conda run -n "$ENV_NAME" python -m pip install -e ".[dev]"
# torch 2.7.1: the ISPO policy loads Qwen3.5 through transformers 5, which needs torch 2.6 or newer
TORCH_CUDA="${DCEVAL_TORCH_CUDA:-cu126}"
conda run -n "$ENV_NAME" python -m pip install "torch==2.7.1" --index-url "https://download.pytorch.org/whl/${TORCH_CUDA}"
conda run -n "$ENV_NAME" python -m pip install "networkx>=3.0" "scipy>=1.10,<2.0"
# the GPU air solver (src/backend/aircooling/gpu_solver): Warp kernels, AMG set-up, mesh containers
conda run -n "$ENV_NAME" python -m pip install "torch-geometric==2.8.0" "warp-lang==1.18.0" "pyamg>=5.0,<6" "h5py>=3.9" "tomli>=2.0" "tomlkit>=0.12"

echo ">>> Headless compiler runtime (jsdom)"
(cd src/headless && conda run -n "$ENV_NAME" npm install --no-audit --no-fund)

echo ">>> Solvers"
(cd src/backend/aircooling/third_parties/foam && conda run -n "$ENV_NAME" ./deploy.sh)

echo ""
echo "Done. conda activate $ENV_NAME, then see README.md."
