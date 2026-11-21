"""Machine-specific locations (see docs/MACHINES.md and local.env.example).

Every location comes from an environment variable, normally set by sourcing the
gitignored ``local.env``.  Defaults follow the lab-machine layout relative to
``$HOME`` (repositories under ``$HOME/mycode``, the validated conda environment
under ``$HOME/miniconda3``), so no absolute path is written in the repository.
Data without a home-relative convention (the DCTS H5 set) has no default.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HINT = 'set it in local.env (see docs/MACHINES.md)'


def _env(name):
    value = os.environ.get(name, '').strip()
    return Path(value).expanduser() if value else None



def git_head(root=REPO_ROOT):
    """Commit checked out at ``root``, or None when ``root`` is not the top of a git checkout (the code release ships
    without git; its records then carry the sha256 of every source instead of a commit)."""
    import subprocess
    try:
        top = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=root, text=True,
                                      stderr=subprocess.DEVNULL).strip()
        if Path(top).resolve() != Path(root).resolve():
            return None
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None

def validated_python():
    """Interpreter approved for numerical work (conda env FVGN-pt2.7)."""
    return _env('SCANO_PY') or Path.home() / 'miniconda3/envs/FVGN-pt2.7/bin/python'


def refine_root():
    """Read-only manuscript repository Gen-FVGN-refine."""
    return _env('SCANO_REFINE_ROOT') or Path.home() / 'mycode/Gen-FVGN-refine'


def fixture_root():
    """Frozen industrial-model fixture used by c21/c26 and scano.Diagnostics."""
    return _env('SCANO_FIXTURE_ROOT') or refine_root() / 'scratchpad/nscc_forward_20260926'


def dcts_h5_root():
    """DCTS H5 cases (h5_500_nscc); required, no default."""
    path = _env('SCANO_DCTS_H5_ROOT') or _env('SCANO_GTC_H5_ROOT')    # the name before the rename (10-14)
    if path is None:
        raise RuntimeError(f'SCANO_DCTS_H5_ROOT is not set; {HINT}')
    return path


def outputs_dir(name):
    """Run directory under this repository's untracked outputs/."""
    return REPO_ROOT / 'outputs' / name

