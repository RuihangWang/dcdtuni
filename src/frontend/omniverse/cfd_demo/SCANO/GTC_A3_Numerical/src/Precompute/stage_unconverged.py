"""
src/Precompute/stage_unconverged.py
====================================
Generate unconverged t=1 field data by running OpenFOAM for exactly one
iteration and injecting the result into the case H5 file.

Algorithm
---------
For each H5 case file:

1. Resolve the corresponding OpenFOAM case directory from the H5 path.
2. Check if the ``field_unconverged_t1/`` group already exists (idempotent guard).
3. Back up ``system/fvSolution`` and patch it for guaranteed 1-iteration exit:
   all tolerances > 1.0, maxIter=1, nCorrectors=1, nNonOrthogonalCorrectors=0.
4. Run ``buoyantBoussinesqSimpleFoam_new`` for 1 iteration.
5. Read the resulting field values from the ``1/`` time directory via
   ``FieldReader`` + ``_place_field_data_to_cpd`` assembly in cpd_cell order.
6. Write the field data into the ``field_unconverged_t1/`` H5 group.
7. Restore the original fvSolution.

Constants
---------
GROUP_NAME : str
    H5 group name for the unconverged t=1 data.
OF_SOLVER : str
    OpenFOAM solver binary name.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

import h5py
import numpy as np

from src.Precompute.h5_layout import FIELD_KEYS, write_field_group

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

GROUP_NAME: str = "field_unconverged_t1"
GROUP_CONVERGED: str = "field_converged_t1"
OF_SOLVER: str = "buoyantBoussinesqSimpleFoam_new"

# Keys patched in fvSolution to force 1-iteration exit
_FVSOLUTION_PATCHES: dict[str, str] = {
    "relTol": "0",
    "tolerance": "10",
    "maxIter": "1",
    "nCorrectors": "1",
    "nNonOrthogonalCorrectors": "0",
}

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_unconverged_t1_single(
    h5_path: str,
    dataset_root: str,
    force: bool = False,
) -> bool:
    """Generate unconverged t=1 field for a single case and inject into H5.

    Parameters
    ----------
    h5_path:
        Absolute path to the H5 file for this case.
    dataset_root:
        Root directory of the dataset containing OpenFOAM case directories.
        Used by :func:`_find_of_case_dir` to locate the OF case.
    force:
        If True, regenerate even if ``field_unconverged_t1/`` already exists.

    Returns
    -------
    bool
        True if processed; False if skipped (group already exists and
        *force* is False, or the OF solver run fails).

    Raises
    ------
    FileNotFoundError
        If the H5 file does not exist.
    RuntimeError
        If the OpenFOAM case directory cannot be resolved.
    NotImplementedError
        Propagated from :func:`_read_field_via_pyvista` (stub).
    """
    if not os.path.isfile(h5_path):
        raise FileNotFoundError(f"H5 file not found: {h5_path}")

    # Idempotent guard -------------------------------------------------------
    if not force:
        with h5py.File(h5_path, "r") as f:
            if GROUP_NAME in f:
                logger.info(
                    "stage_unconverged: group '%s' already exists in %s — skipping (force=False)",
                    GROUP_NAME,
                    h5_path,
                )
                return False

    # Resolve OpenFOAM case directory ----------------------------------------
    case_dir = _find_of_case_dir(h5_path, dataset_root)
    logger.info("stage_unconverged: processing %s → OF case %s", h5_path, case_dir)

    fv_backup: Optional[str] = None
    cd_backup: Optional[str] = None
    t1_backup: Optional[str] = None
    try:
        # Back up existing 1/ directory if present (will be overwritten by solver)
        t1_dir = os.path.join(case_dir, "1")
        if os.path.isdir(t1_dir):
            t1_backup = t1_dir + f".bak.{os.getpid()}"
            os.rename(t1_dir, t1_backup)

        # Patch controlDict: startFrom startTime, endTime 1, writeInterval 1
        cd_backup = _patch_control_dict(case_dir)

        # Patch fvSolution: relax convergence, maxIter 1
        fv_backup = _patch_fv_solution(case_dir)

        # Run solver for 1 iteration -----------------------------------------
        ok = _run_of_one_iter(case_dir)
        if not ok:
            logger.error(
                "stage_unconverged: solver run failed for %s — skipping H5 write",
                case_dir,
            )
            return False

        # Read field data via PyVista in cpd_cell order -----------------------
        fields_raw = _read_field_via_pyvista(case_dir, h5_path, time_dir="1")

        # Validate keys before writing
        missing = [k for k in FIELD_KEYS if k not in fields_raw]
        if missing:
            raise ValueError(
                f"_read_field_via_pyvista returned dict missing keys: {missing}"
            )

        # Write to H5 --------------------------------------------------------
        write_field_group(h5_path, GROUP_NAME, fields_raw, force=force)
        logger.info(
            "stage_unconverged: wrote '%s' group to %s", GROUP_NAME, h5_path
        )
        return True

    finally:
        # Always restore fvSolution and controlDict ---------------------------
        if fv_backup is not None:
            _restore_fv_solution(case_dir, fv_backup)
        if cd_backup is not None:
            _restore_control_dict(case_dir, cd_backup)
        # Restore original 1/ directory
        t1_dir = os.path.join(case_dir, "1")
        if t1_backup is not None:
            if os.path.isdir(t1_dir):
                shutil.rmtree(t1_dir)
            os.rename(t1_backup, t1_dir)


def generate_converged_t1_single(
    h5_path: str,
    dataset_root: str,
    force: bool = False,
) -> bool:
    """Generate converged t=1 field for a single case and inject into H5.

    Unlike :func:`generate_unconverged_t1_single`, this function does **not**
    patch ``fvSolution``.  The original solver settings (tight tolerances,
    maxIter=100) are preserved so that inner iterations actually solve each
    field equation properly within the single time step.

    Only ``controlDict`` is patched (startFrom startTime, endTime 1,
    writeInterval 1) to run exactly 1 pseudo-time step from the ``0/``
    initial condition.
    """
    if not os.path.isfile(h5_path):
        raise FileNotFoundError(f"H5 file not found: {h5_path}")

    if not force:
        with h5py.File(h5_path, "r") as f:
            if GROUP_CONVERGED in f:
                logger.info(
                    "stage_converged: group '%s' already exists in %s — skipping",
                    GROUP_CONVERGED, h5_path,
                )
                return False

    case_dir = _find_of_case_dir(h5_path, dataset_root)
    logger.info("stage_converged: processing %s → OF case %s", h5_path, case_dir)

    cd_backup: Optional[str] = None
    t1_backup: Optional[str] = None
    try:
        t1_dir = os.path.join(case_dir, "1")
        if os.path.isdir(t1_dir):
            t1_backup = t1_dir + f".bak.{os.getpid()}"
            os.rename(t1_dir, t1_backup)

        cd_backup = _patch_control_dict(case_dir)

        ok = _run_of_one_iter(case_dir)
        if not ok:
            logger.error("stage_converged: solver failed for %s", case_dir)
            return False

        fields_raw = _read_field_via_pyvista(case_dir, h5_path, time_dir="1")

        missing = [k for k in FIELD_KEYS if k not in fields_raw]
        if missing:
            raise ValueError(
                f"_read_field_via_pyvista returned dict missing keys: {missing}"
            )

        write_field_group(h5_path, GROUP_CONVERGED, fields_raw, force=force)
        logger.info("stage_converged: wrote '%s' group to %s", GROUP_CONVERGED, h5_path)
        return True

    finally:
        if cd_backup is not None:
            _restore_control_dict(case_dir, cd_backup)
        t1_dir = os.path.join(case_dir, "1")
        if t1_backup is not None:
            if os.path.isdir(t1_dir):
                shutil.rmtree(t1_dir)
            os.rename(t1_backup, t1_dir)


# ---------------------------------------------------------------------------
# _find_of_case_dir
# ---------------------------------------------------------------------------


def _find_of_case_dir(h5_path: str, dataset_root: str) -> str:
    """Resolve the OpenFOAM case directory for a given H5 file.

    Strategy
    --------
    Walk *up* from the directory containing *h5_path*, stopping when we find
    a directory that has both a ``0/`` sub-directory and a
    ``constant/polyMesh/`` sub-directory (OpenFOAM case markers).  The search
    is bounded by *dataset_root* — we never walk above it.

    Parameters
    ----------
    h5_path:
        Absolute path to the H5 file.
    dataset_root:
        Absolute path to the dataset root.  Acts as an upper bound: if
        traversal reaches this directory without finding an OF case, a
        ``RuntimeError`` is raised.

    Returns
    -------
    str
        Absolute path to the OpenFOAM case directory.

    Raises
    ------
    RuntimeError
        If no valid OF case directory is found.
    """
    dataset_root = os.path.abspath(dataset_root)
    current = os.path.abspath(os.path.dirname(h5_path))

    visited: list[str] = []
    while True:
        visited.append(current)
        if _is_of_case(current):
            return current

        parent = os.path.dirname(current)
        if parent == current:
            # Reached filesystem root without finding a case
            break
        if os.path.abspath(current) == dataset_root:
            # At the dataset root boundary — check it too, then stop
            break
        current = parent

    raise RuntimeError(
        f"No OpenFOAM case directory found walking up from {h5_path!r} "
        f"to dataset_root={dataset_root!r}. "
        f"Visited: {visited}"
    )


def _is_of_case(directory: str) -> bool:
    """Return True if *directory* looks like a valid OpenFOAM case.

    A valid case must have both:
    - a ``0/`` sub-directory (initial conditions)
    - a ``constant/polyMesh/`` sub-directory (mesh definition)
    """
    has_zero = os.path.isdir(os.path.join(directory, "0"))
    has_mesh = os.path.isdir(os.path.join(directory, "constant", "polyMesh"))
    return has_zero and has_mesh


# ---------------------------------------------------------------------------
# _patch_fv_solution / _restore_fv_solution
# ---------------------------------------------------------------------------


def _patch_fv_solution(case_dir: str) -> str:
    """Patch ``system/fvSolution`` to force exactly 1 iteration.

    Replaces the following keys wherever they appear in the file:

    - ``relTol``                  → ``"0"``
    - ``tolerance``               → ``"10"``   (always satisfied after 1 iter)
    - ``maxIter``                 → ``"1"``
    - ``nCorrectors``             → ``"1"``
    - ``nNonOrthogonalCorrectors``→ ``"0"``

    The original file is copied to ``<fvSolution>.bak.<pid>`` before patching.

    Parameters
    ----------
    case_dir:
        Absolute path to the OpenFOAM case directory.

    Returns
    -------
    str
        Path of the backup file.

    Raises
    ------
    FileNotFoundError
        If ``system/fvSolution`` does not exist in *case_dir*.
    """
    fv_path = os.path.join(case_dir, "system", "fvSolution")
    if not os.path.isfile(fv_path):
        raise FileNotFoundError(f"fvSolution not found: {fv_path}")

    backup_path = f"{fv_path}.bak.{os.getpid()}"
    shutil.copy2(fv_path, backup_path)
    logger.debug("stage_unconverged: backed up fvSolution to %s", backup_path)

    with open(fv_path, "r") as fh:
        content = fh.read()

    patched = _apply_fvsolution_patches(content)

    with open(fv_path, "w") as fh:
        fh.write(patched)

    logger.debug("stage_unconverged: patched fvSolution at %s", fv_path)
    return backup_path


# ---------------------------------------------------------------------------
# _patch_control_dict / _restore_control_dict
# ---------------------------------------------------------------------------

_CONTROLDICT_PATCHES: dict[str, str] = {
    "startFrom": "startTime",
    "startTime": "0",
    "endTime": "1",
    "writeInterval": "1",
}


def _patch_control_dict(case_dir: str) -> str:
    """Patch ``system/controlDict`` to run exactly 1 time step from t=0.

    Sets ``startFrom startTime``, ``endTime 1``, ``writeInterval 1`` so the
    solver starts from the ``0/`` directory and writes a single ``1/`` result.

    Returns path to the backup file.
    """
    cd_path = os.path.join(case_dir, "system", "controlDict")
    if not os.path.isfile(cd_path):
        raise FileNotFoundError(f"controlDict not found: {cd_path}")

    backup_path = f"{cd_path}.bak.{os.getpid()}"
    shutil.copy2(cd_path, backup_path)

    with open(cd_path, "r") as fh:
        content = fh.read()

    patched = _apply_controldict_patches(content)

    with open(cd_path, "w") as fh:
        fh.write(patched)

    logger.debug("stage_unconverged: patched controlDict at %s", cd_path)
    return backup_path


def _apply_controldict_patches(content: str) -> str:
    """Apply ``_CONTROLDICT_PATCHES`` substitutions to controlDict content."""
    for key, val in _CONTROLDICT_PATCHES.items():
        pattern = rf"(\b{re.escape(key)}\s+)\S+;"
        replacement = rf"\g<1>{val};"
        content = re.sub(pattern, replacement, content)
    return content


def _restore_control_dict(case_dir: str, backup_path: str) -> None:
    """Restore ``system/controlDict`` from backup."""
    cd_path = os.path.join(case_dir, "system", "controlDict")
    if not os.path.isfile(backup_path):
        logger.warning("stage_unconverged: controlDict backup not found at %s", backup_path)
        return
    shutil.copy2(backup_path, cd_path)
    os.remove(backup_path)
    logger.debug("stage_unconverged: restored controlDict from %s", backup_path)


def _apply_fvsolution_patches(content: str) -> str:
    """Apply all ``_FVSOLUTION_PATCHES`` substitutions to *content*.

    Each substitution uses the pattern ``rf"(\\b{key}\\s+)\\S+;"`` so that
    only the *value* portion of a ``key  value;`` OpenFOAM directive is
    replaced, leaving surrounding whitespace and indentation intact.

    Parameters
    ----------
    content:
        Raw text of an fvSolution file.

    Returns
    -------
    str
        Patched text.
    """
    for key, val in _FVSOLUTION_PATCHES.items():
        pattern = rf"(\b{re.escape(key)}\s+)\S+;"
        replacement = rf"\g<1>{val};"
        content = re.sub(pattern, replacement, content)
    return content


def _restore_fv_solution(case_dir: str, backup_path: str) -> None:
    """Restore ``system/fvSolution`` from a backup created by :func:`_patch_fv_solution`.

    Parameters
    ----------
    case_dir:
        Absolute path to the OpenFOAM case directory.
    backup_path:
        Path to the backup file (as returned by :func:`_patch_fv_solution`).
    """
    fv_path = os.path.join(case_dir, "system", "fvSolution")

    if not os.path.isfile(backup_path):
        logger.warning(
            "stage_unconverged: backup not found at %s — fvSolution not restored",
            backup_path,
        )
        return

    shutil.copy2(backup_path, fv_path)
    os.remove(backup_path)
    logger.debug(
        "stage_unconverged: restored fvSolution from %s and removed backup",
        backup_path,
    )


# ---------------------------------------------------------------------------
# _run_of_one_iter
# ---------------------------------------------------------------------------


def _find_openfoam_bashrc() -> str:
    """Find the OpenFOAM bashrc to source (conda v2412 first; never system).

    Delegates to the single canonical resolver in ``Utils.foam_env``.
    """
    from Utils.foam_env import resolve_openfoam_bashrc
    return resolve_openfoam_bashrc() or ""


def _run_of_one_iter(case_dir: str) -> bool:
    """Run ``buoyantBoussinesqSimpleFoam_new`` for one iteration.

    Sources the OpenFOAM environment before execution to ensure shared
    libraries are found.

    Parameters
    ----------
    case_dir:
        Absolute path to the OpenFOAM case directory.

    Returns
    -------
    bool
        True if the solver exited with return code 0; False otherwise.
    """
    bashrc = _find_openfoam_bashrc()
    if bashrc:
        cmd = f"source {bashrc} && {OF_SOLVER} -case {case_dir}"
        logger.info("stage_unconverged: running (with OF env) %s", cmd)
    else:
        cmd = f"{OF_SOLVER} -case {case_dir}"
        logger.info("stage_unconverged: running (direct) %s", cmd)

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            executable="/bin/bash",
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "stage_unconverged: solver timed out after 300 s for case %s", case_dir
        )
        return False

    if result.returncode != 0:
        logger.error(
            "stage_unconverged: solver exited with code %d\nSTDOUT:\n%s\nSTDERR:\n%s",
            result.returncode,
            result.stdout[-2000:],
            result.stderr[-2000:],
        )
        return False

    logger.debug(
        "stage_unconverged: solver stdout (last 1000 chars):\n%s",
        result.stdout[-1000:],
    )
    return True


# ---------------------------------------------------------------------------
# _read_field_via_pyvista  (STUB)
# ---------------------------------------------------------------------------


def _read_field_via_pyvista(case_dir: str, h5_path: str, time_dir: str = "1") -> dict:
    """Read field data from an OpenFOAM result directory using PyVista.

    Uses ``FieldReader.read_all_fields_direct()`` to read raw fields, then
    ``OpenFOAMFieldUpdater._place_field_data_to_cpd()`` to assemble them into
    cpd_cell order (internal cells first, then boundary patches in patch_list
    order) matching the H5 layout.

    Parameters
    ----------
    case_dir:
        Absolute path to the OpenFOAM case directory.
    h5_path:
        Path to the case's H5 file — used to read ``patch_list`` (root attr),
        ``cpd|centroid`` (for N), and ``cpd|cell_type`` (for internal count).
    time_dir:
        Time directory to read (default ``"1"``).

    Returns
    -------
    dict[str, np.ndarray]
        Keys: ``uvw_cpd_cell (N,3)``, ``press_cpd_cell (N,1)``,
        ``p_rgh_cpd_cell (N,1)``, ``temp_cpd_cell (N,1)``.
    """
    import json
    import torch
    from pathlib import Path
    from src.Parse_mesh.parse_fielddata import FieldReader
    from src.Utils.of_field_updater import OpenFOAMFieldUpdater

    # 1. Read cpd_cell metadata from H5
    with h5py.File(h5_path, "r") as hf:
        patch_list_raw = hf.attrs["patch_list"]
        if isinstance(patch_list_raw, bytes):
            patch_list_raw = patch_list_raw.decode("utf-8")
        patch_list = json.loads(patch_list_raw)
        num_cpd_cells = hf["cpd|centroid"].shape[0]
        num_internal_cells = int(hf.attrs["num_cells"])

    # 2. Ensure .foam file exists (required by PyVista OpenFOAMReader)
    foam_created = False
    foam_file = None
    foam_files = list(Path(case_dir).glob("*.foam"))
    if not foam_files:
        foam_file = os.path.join(case_dir, f"{os.path.basename(case_dir)}.foam")
        Path(foam_file).touch()
        foam_created = True

    try:
        # 3. Read fields via FieldReader
        reader = FieldReader(case_dir)
        raw_fields = reader.read_all_fields_direct(
            time_value=float(time_dir), patch_list=patch_list
        )
    finally:
        if foam_created and foam_file is not None and os.path.isfile(foam_file):
            os.remove(foam_file)

    # 4. Assemble into cpd_cell order
    field_map = {
        "U": ("uvw_cpd_cell", 3),
        "p": ("press_cpd_cell", 1),
        "p_rgh": ("p_rgh_cpd_cell", 1),
        "T": ("temp_cpd_cell", 1),
    }

    result = {}
    for foam_name, (h5_key, dim) in field_map.items():
        if foam_name in raw_fields:
            cpd_tensor = OpenFOAMFieldUpdater._place_field_data_to_cpd(
                raw_fields[foam_name], num_cpd_cells, num_internal_cells,
                patch_list, field_dim=dim,
            )
            result[h5_key] = cpd_tensor.numpy().astype(np.float32)
        else:
            logger.warning(
                "stage_unconverged: field '%s' not found in OF output, using zeros",
                foam_name,
            )
            result[h5_key] = np.zeros((num_cpd_cells, dim), dtype=np.float32)

    return result
