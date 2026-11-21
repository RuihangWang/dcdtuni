# ---------------------------------------------------------------------------
# env.sh -- build environment for the dcdtuni custom OpenFOAM solvers
#
#   Source this file BEFORE building or running:  source env.sh
#
# It wires wmake to compile against the conda-forge OpenFOAM v2412 install
# (headers + libs that ship inside the `dcdtuni` conda env) and directs the
# build OUTPUT into this directory's platforms/ tree, so nothing in the conda
# prefix is ever overwritten.
#
# Sibling of third_parties/dcCartMesh/env.sh — same mechanism, different output
# directory. Keep the two in step when either is changed.
# ---------------------------------------------------------------------------

# --- locate this directory (works whether sourced from bash or via a path) ---
if [ -n "${BASH_SOURCE:-}" ]; then
    DC_FOAM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
else
    DC_FOAM_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
fi
export DC_FOAM_ROOT

# --- activate the explicitly selected environment, defaulting to this repo ---
__dc_repo="$(cd "${DC_FOAM_ROOT}/../../../../.." && pwd -P)"
__dc_prefix="${DCDTUNI_ENV_PREFIX:-}"
if [ -z "$__dc_prefix" ] && [ -x "$__dc_repo/.codex-tmp/envs/dcdtuni/bin/python" ]; then
    __dc_prefix="$__dc_repo/.codex-tmp/envs/dcdtuni"
fi
if [ -n "$__dc_prefix" ]; then
    __dc_prefix="$(realpath -m "$__dc_prefix")"
    if [[ "$__dc_prefix" != "$__dc_repo/"* || "$(basename "$__dc_prefix")" != dcdtuni ]]; then
        echo "env.sh ERROR: DCDTUNI_ENV_PREFIX must stay inside this checkout and end in dcdtuni." >&2
        return 1 2>/dev/null || exit 1
    fi
    if [ "${CONDA_PREFIX:-}" != "$__dc_prefix" ] || [ -z "${WM_OPTIONS:-}" ]; then
        __conda_base="$(conda info --base 2>/dev/null)"
        . "$__conda_base/etc/profile.d/conda.sh"
        conda activate "$__dc_prefix"
    fi
elif [ -z "${CONDA_PREFIX:-}" ] || [ "$(basename "${CONDA_PREFIX:-}")" != dcdtuni ]; then
    __conda_base="$(conda info --base 2>/dev/null)"
    . "$__conda_base/etc/profile.d/conda.sh"
    conda activate dcdtuni
fi

if [ -z "${WM_OPTIONS:-}" ]; then
    echo "env.sh ERROR: OpenFOAM env not active (WM_OPTIONS unset). Activate 'dcdtuni' first." 1>&2
    return 1 2>/dev/null || exit 1
fi

# --- OpenFOAM API version (conda relocates headers under include/OpenFOAM-<api>) ---
: "${FOAM_API:=2412}"
export FOAM_API

# --- make wmake's $(LIB_SRC) resolve to the conda dev headers --------------------
# wmake's makefiles/general HARDCODES  LIB_SRC = $(WM_PROJECT_DIR)/src  (this beats
# any exported LIB_SRC). conda-forge ships the headers under
# include/OpenFOAM-<api>/src instead, so we bridge the two with a symlink:
#     $CONDA_PREFIX/src -> $CONDA_PREFIX/include/OpenFOAM-<api>/src
# conda's openfoam package ships NO 'src' entry, so this clobbers nothing and is
# reversible with:  rm "$CONDA_PREFIX/src"
__of_headers="$CONDA_PREFIX/include/OpenFOAM-${FOAM_API}/src"
if [ ! -d "$__of_headers/meshTools/lnInclude" ]; then
    echo "env.sh ERROR: OpenFOAM dev headers not found at $__of_headers" 1>&2
    echo "  (expected conda-forge 'openfoam' package layout). Aborting." 1>&2
    return 1 2>/dev/null || exit 1
fi
if [ ! -e "$CONDA_PREFIX/src" ]; then
    ln -s "include/OpenFOAM-${FOAM_API}/src" "$CONDA_PREFIX/src" \
        && echo "env.sh: created header bridge symlink \$CONDA_PREFIX/src -> include/OpenFOAM-${FOAM_API}/src"
elif [ ! -d "$CONDA_PREFIX/src/meshTools/lnInclude" ]; then
    echo "env.sh WARNING: \$CONDA_PREFIX/src exists but does not point at the OF headers." 1>&2
fi
export FOAM_SRC="$CONDA_PREFIX/src"
export LIB_SRC="$FOAM_SRC"

# --- our own headers (writeBCFields.H etc.), used by the solver builds ---
export DC_FOAM_SRC="$DC_FOAM_ROOT/src"

# --- the C++ compiler wmake substitutes into  CC := $(GXX) -std=c++17 ----------
# conda patched the rule to use $(GXX); the conda compiler is NOT installed here,
# so fall back to the system g++ (build-old / run-new against conda libstdc++ is
# the supported, ABI-safe direction). Override by exporting GXX yourself, or
# `conda install -n dcdtuni gxx_linux-64` for an exact-match toolchain.
export GXX="${GXX:-g++}"

# --- send build artefacts into project-local platforms/ (NON-destructive) ------
export FOAM_USER_LIBBIN="$DC_FOAM_ROOT/platforms/$WM_OPTIONS/lib"
export FOAM_USER_APPBIN="$DC_FOAM_ROOT/platforms/$WM_OPTIONS/bin"
mkdir -p "$FOAM_USER_LIBBIN" "$FOAM_USER_APPBIN"

# --- make the custom binary/library findable at runtime ------------------------
# NB: this host ALSO has a system OpenFOAM 2406 that the login shell sources, which
# pollutes LD_LIBRARY_PATH. Prepend the conda v2412 lib dirs (incl. the MPI-specific
# sys-mpich subdir holding libPstream) so v2412 always wins.
export PATH="$FOAM_USER_APPBIN:$PATH"
export LD_LIBRARY_PATH="$FOAM_USER_LIBBIN:$CONDA_PREFIX/lib/$FOAM_MPI:$CONDA_PREFIX/lib:${LD_LIBRARY_PATH:-}"

echo "dcdtuni solver build env ready:"
echo "  OpenFOAM  : $WM_PROJECT_VERSION  ($WM_OPTIONS)"
echo "  compiler  : $($GXX --version 2>/dev/null | head -1)"
echo "  LIB_SRC   : $LIB_SRC"
echo "  out lib   : $FOAM_USER_LIBBIN"
echo "  out bin   : $FOAM_USER_APPBIN"
