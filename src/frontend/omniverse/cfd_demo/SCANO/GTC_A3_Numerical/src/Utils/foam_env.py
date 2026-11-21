"""Canonical OpenFOAM-environment resolver.

Single source of truth for locating the OpenFOAM ``etc/bashrc`` to source before
running any OpenFOAM solver/utility (e.g. ``buoyantBoussinesqSimpleFoam_new``,
``foamToVTK``, ``reconstructPar``).

Design goals:
  * **conda v2412 first** — the meshing/solving toolchain is the conda OpenFOAM
    (env ``dcdtuni`` by default); reconstruct/read must match it.
  * **portable** — resolves the same way on this box, on NSCC (conda-pack
    relocated env), and in the open-source mirror, via env vars + conda
    discovery rather than a hardcoded machine path.
  * **never system OpenFOAM** — it does NOT fall back to ``/usr/lib/openfoam`` or
    ``/opt/openfoam``; those are deliberately absent so a stale system install
    can never be picked up silently.

Resolution order:
  1. ``$FOAM_BASHRC``                         explicit override (a bashrc path)
  2. ``$FOAM_ETC/bashrc``                     an OpenFOAM env already sourced
  3. ``$CONDA_PREFIX/etc/bashrc``             running inside an OpenFOAM conda env
  4. sibling conda env ``$FOAM_CONDA_ENV``    e.g. FVGN-pt2.7 -> dcdtuni
     (default env name ``dcdtuni``), then any sibling env that has an
     OpenFOAM toolchain (``bin/reconstructPar`` present).
"""
import os
import sys
from pathlib import Path
from typing import Optional


def _conda_prefix() -> Optional[str]:
    """Best-effort conda env prefix: $CONDA_PREFIX, else derived from the running
    interpreter path (.../envs/<name>/bin/python)."""
    cp = os.environ.get("CONDA_PREFIX")
    if cp:
        return cp
    exe = Path(sys.executable).resolve()
    # .../envs/<name>/bin/python  ->  .../envs/<name>
    if exe.parent.name == "bin" and exe.parent.parent.parent.name == "envs":
        return str(exe.parent.parent)
    return None


def resolve_openfoam_bashrc() -> Optional[str]:
    """Return a path to an OpenFOAM ``etc/bashrc``, or ``None`` if none found.

    Conda v2412 first; never returns a system (``/usr/lib/openfoam`` /
    ``/opt/openfoam``) path. See module docstring for the resolution order.
    """
    # 1. explicit override
    fb = os.environ.get("FOAM_BASHRC")
    if fb and Path(fb).is_file():
        return fb

    # 2. an OpenFOAM env already sourced (FOAM_ETC set)
    fe = os.environ.get("FOAM_ETC")
    if fe and (Path(fe) / "bashrc").is_file():
        return str(Path(fe) / "bashrc")

    # 3. running inside an OpenFOAM-carrying conda env
    cp = _conda_prefix()
    if cp and (Path(cp) / "etc" / "bashrc").is_file():
        return str(Path(cp) / "etc" / "bashrc")

    # 4. a sibling conda env carries OpenFOAM (e.g. FVGN-pt2.7 -> dcdtuni)
    if cp:
        cpp = Path(cp)
        # envs dir whether cp is .../envs/<name> or the base prefix .../miniconda3
        envs_dir = cpp.parent if cpp.parent.name == "envs" else cpp / "envs"
        preferred = os.environ.get("FOAM_CONDA_ENV", "dcdtuni")
        candidates = []
        pref_bashrc = envs_dir / preferred / "etc" / "bashrc"
        if pref_bashrc.is_file():
            candidates.append(envs_dir / preferred)
        if envs_dir.is_dir():
            for env in sorted(envs_dir.iterdir()):
                if env.name != preferred:
                    candidates.append(env)
        for env in candidates:
            bashrc = env / "etc" / "bashrc"
            # confirm it is an OpenFOAM toolchain, not just any conda env
            if bashrc.is_file() and (env / "bin" / "reconstructPar").exists():
                return str(bashrc)

    return None


def resolve_openfoam_etc() -> Optional[str]:
    """Return the OpenFOAM ``etc`` directory (parent of ``bashrc``), or ``None``.

    Convenience wrapper for callers that need ``FOAM_ETC`` rather than the
    bashrc path itself.
    """
    bashrc = resolve_openfoam_bashrc()
    return str(Path(bashrc).parent) if bashrc else None


def require_openfoam_bashrc() -> str:
    """Like :func:`resolve_openfoam_bashrc` but raise a clear error if not found."""
    bashrc = resolve_openfoam_bashrc()
    if not bashrc:
        raise RuntimeError(
            "No OpenFOAM environment found. Set FOAM_BASHRC to the conda OpenFOAM "
            "etc/bashrc (e.g. $CONDA/envs/dcdtuni/etc/bashrc), or set FOAM_CONDA_ENV "
            "to the conda env name that carries OpenFOAM. System OpenFOAM is "
            "intentionally NOT searched."
        )
    return bashrc
