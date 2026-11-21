"""
Post-processing module for OpenFOAM CFD results using PyVista.

Renders temperature, velocity, and streamlines from native OpenFOAM case
directories or explicitly imported VTK/VTM files. Image rendering is off-screen;
mesh and field buffers also drive the interactive browser views.

Environment requirements:
    - pyvista >= 0.44  (tested with 0.47.1)
    - vtk >= 9.0       (tested with 9.5.1)
    - xvfb or osmesa for headless rendering (pyvista.start_xvfb())

Typical usage from a REST API handler:

    from backend.aircooling.runner.postprocess import CFDPostProcessor, PostProcessor

    # --- Low-level API (VTM-centric) ---
    pp = CFDPostProcessor.from_vtm("/path/to/case_500.vtm")
    # -- or --
    pp = CFDPostProcessor.from_openfoam_case("/path/to/case", time_value=500)

    b64_png = pp.render_temperature(clim=(288, 310))
    b64_png = pp.render_velocity_magnitude()
    b64_png = pp.render_streamlines(seed_center=(4, 4, 1.2), seed_radius=0.5)
    b64_png = pp.render_slice_temperature(normal='z', origin=(4, 4, 0.6))

    # --- High-level API (server.py integration) ---
    post = PostProcessor(run_dir=Path("/path/to/log/2026-03-22_case"))
    # Legacy method name; all cases are read directly from OpenFOAM.
    info = post.load_vtk()
    b64 = post.render_surface_field("T")
    b64 = post.render_streamlines()

    # Return b64_png in JSON response to browser
"""

from __future__ import annotations

import base64
import hashlib
import json
import locale
import logging
import os
import re
import sys
import struct
import subprocess
import tempfile
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

import numpy as np

from .mesh_layout import common_result_times, mesh_directories, processor_mesh_dirs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy pyvista import with headless setup
# ---------------------------------------------------------------------------

_pv = None  # module-level cache
_openfoam_read_lock = threading.RLock()


def _get_pyvista():
    """Import pyvista with off-screen rendering configured.

    Uses VTK's OSMesa backend for clean headless rendering without
    requiring Xvfb, EGL, or a display server.  Must set environment
    variables BEFORE the first VTK/PyVista import.
    """
    global _pv
    if _pv is not None:
        return _pv

    # Force OSMesa backend — avoids GLX/EGL failures on headless servers
    os.environ.setdefault("VTK_DEFAULT_OPENGL_WINDOW", "vtkOSOpenGLRenderWindow")
    os.environ["PYVISTA_OFF_SCREEN"] = "true"

    import pyvista as pv

    pv.OFF_SCREEN = True

    # Suppress VTK warnings in logs
    pv.set_error_output_file(os.devnull)
    _drop_vtk_python_overrides()

    _pv = pv
    return pv


def _drop_vtk_python_overrides() -> None:
    """Return VTK's plain Python wrappers for its data classes.

    VTK >= 9.4 registers numpy-style subclasses (vtkmodules.util.data_model)
    for vtkPolyData, vtkCellData, vtkFieldData and the rest. Their wrappers
    carry an instance dict, so each one dropped while its VTK object lives on
    is kept as a "ghost", and every later wrapper costs time in proportion to
    the ghosts: after 30,000 patch-sized objects a GetCellData() was ~200x
    slower, quadratic over Base604's thousands of patch blocks. This module
    and PyVista use PyVista's own classes, never that API.
    """
    from vtkmodules import vtkCommonDataModel

    for name in ("vtkFieldData", "vtkDataSetAttributes", "vtkPointData", "vtkCellData", "vtkPolyData",
                 "vtkUnstructuredGrid", "vtkImageData", "vtkRectilinearGrid", "vtkStructuredGrid",
                 "vtkMultiBlockDataSet", "vtkPartitionedDataSet", "vtkPartitionedDataSetCollection",
                 "vtkOverlappingAMR"):
        cls = getattr(vtkCommonDataModel, name, None)
        if cls is not None:
            cls.override(None)


def read_openfoam_case(
    case_dir: str | Path, time_value: Optional[float] = None
) -> Tuple[Any, dict]:
    """Read native OpenFOAM data and preserve the server's process locale."""
    # vtkOpenFOAMReader changes LC_ALL to C during UpdateTimeStep, including
    # LC_CTYPE (and therefore Python's default text encoding). Serialize these
    # native reads and restore all locale categories even if a read fails.
    with _openfoam_read_lock:
        previous_locale = locale.setlocale(locale.LC_ALL)
        try:
            return _read_openfoam_case(case_dir, time_value)
        finally:
            locale.setlocale(locale.LC_ALL, previous_locale)


def _openfoam_result_layout(
    case_dir: Path, time_value: Optional[float] = None,
) -> Tuple[List[Path], List[float]]:
    """Select the mesh carrying the newest complete native result.

    A retained reconstructed root can predate processor results. Compare
    field-bearing times in both layouts without changing the mesh discovery
    rules used for meshing and solving. Ties retain the reconstructed root.
    """
    root = case_dir / "constant" / "polyMesh"
    authoritative = mesh_directories(case_dir)
    layouts = [authoritative] if authoritative and authoritative[0] == root else []
    partitions = processor_mesh_dirs(case_dir)
    if partitions:
        layouts.append(partitions)
    if not layouts:
        raise FileNotFoundError(f"No complete OpenFOAM mesh found in {case_dir}")
    candidates = []
    for meshes in layouts:
        common = None
        for mesh in meshes:
            # Each processor directory itself has a serial constant/polyMesh.
            times = set(common_result_times(mesh.parent.parent, require_fields=True))
            common = times if common is None else common & times
        candidates.append((meshes, sorted(common or ())))
    available = candidates
    if time_value is not None:
        available = [(meshes, times) for meshes, times in candidates
                     if time_value in times or (time_value == 0 and not times)]
        if not available:
            times = sorted({value for _, times in candidates for value in times})
            raise ValueError(f"Time {time_value} is not available on every mesh: {times}")
    return max(available, key=lambda candidate: candidate[1][-1] if candidate[1] else float("-inf"))


def _trim_empty_boundary_entries(boundary_path: Path) -> Tuple[Optional[bytes], int, int]:
    """Omit zero-face dictionaries while preserving all non-empty entries verbatim.

    Only the small polyBoundaryMesh dictionary is edited in a temporary read
    view. Geometry labels and all field files, including binary fields, remain
    untouched. An unfamiliar dictionary layout falls back to the original.
    """
    import re

    raw = boundary_path.read_bytes()
    header = re.search(rb"(?m)^[ \t]*(\d+)[ \t\r\n]*\(", raw)
    if header is None:
        return None, 0, 0
    original_count = int(header.group(1))
    position = header.end()
    kept = []
    for _ in range(original_count):
        while position < len(raw) and raw[position:position + 1].isspace():
            position += 1
        start = position
        opening = raw.find(b"{", position)
        if opening < 0 or not re.fullmatch(rb"[^\s(){}]+", raw[start:opening].strip()):
            return None, original_count, original_count
        depth, position = 1, opening + 1
        while depth and position < len(raw):
            next_opening = raw.find(b"{", position)
            next_closing = raw.find(b"}", position)
            if next_closing < 0:
                return None, original_count, original_count
            if 0 <= next_opening < next_closing:
                depth += 1
                position = next_opening + 1
            else:
                depth -= 1
                position = next_closing + 1
        if depth:
            return None, original_count, original_count
        entry = raw[start:position]
        n_faces = re.search(rb"\bnFaces\s+(\d+)\s*;", entry)
        if n_faces is None:
            return None, original_count, original_count
        if int(n_faces.group(1)):
            kept.append(entry)
    while position < len(raw) and raw[position:position + 1].isspace():
        position += 1
    if raw[position:position + 1] != b")" or len(kept) == original_count:
        return None, original_count, original_count
    trimmed = (raw[:header.start()] + str(len(kept)).encode("ascii") + b"\n(\n"
               + b"\n".join(kept) + b"\n)" + raw[position + 1:])
    return trimmed, original_count, len(kept)


def _trim_field_boundary_entries(raw: bytes, keep: set) -> Optional[bytes]:
    """A field file with only the boundaryField entries of the patches in `keep`.

    A processor's field files list every global patch, nearly all of them
    empty there (Base604: ~4,300 entries for ~540 faces-bearing patches), and
    vtkFoamDict looks each enabled patch up linearly in every field -- the same
    quadratic cost `_trim_empty_boundary_entries` removes from the mesh. The
    internalField and every kept entry stay byte for byte; regex (quoted)
    entries are always kept. None when the layout is not the familiar one.
    """
    import re

    start = raw.find(b"\nboundaryField")
    if start < 0:
        return None
    opening = raw.find(b"{", start)
    if opening < 0:
        return None
    space = re.compile(rb"\s*")
    position, kept, removed = opening + 1, [], 0
    while True:
        position = space.match(raw, position).end()
        if position >= len(raw):
            return None
        if raw[position:position + 1] == b"}":
            break
        brace = raw.find(b"{", position)
        if brace < 0:
            return None
        name = raw[position:brace].strip()
        if not name or b";" in name or b"}" in name:
            return None
        depth, end = 1, brace + 1
        while depth:
            next_open, next_close = raw.find(b"{", end), raw.find(b"}", end)
            if next_close < 0:
                return None
            if 0 <= next_open < next_close:
                depth, end = depth + 1, next_open + 1
            else:
                depth, end = depth - 1, next_close + 1
        if name.startswith(b'"') or name.decode("utf-8", "replace") in keep:
            kept.append(raw[position:end])
        else:
            removed += 1
        position = end
    if not removed:
        return None
    return raw[:opening + 1] + b"\n" + b"\n".join(kept) + b"\n" + raw[position:]


@contextmanager
def _partition_reader_view(case_dir: Path, meshes: List[Path], decomposed: bool):
    """Give VTK sparse per-rank patch dictionaries without copying result data.

    vtkFoamDict::Lookup performs a linear regex lookup for every patch,
    including empty/inactive patches. Dropping empty dictionaries avoids the
    replicated global patch count becoming quadratic on every processor.
    """
    started = time.perf_counter()
    entries = [_trim_empty_boundary_entries(mesh / "boundary") for mesh in meshes] if decomposed else []
    info = {"emptyPatchesRemoved": sum(before - after for _, before, after in entries),
            "patchCountsBefore": [before for _, before, _ in entries],
            "patchCountsAfter": [after for _, _, after in entries]}
    if not info["emptyPatchesRemoved"]:
        info["seconds"] = round(time.perf_counter() - started, 4)
        yield case_dir, meshes, info
        return
    with tempfile.TemporaryDirectory(prefix=".pyvista-partitions-", dir=case_dir) as temp:
        view = Path(temp)
        for name in ("system", "constant"):
            if (case_dir / name).is_dir():
                (view / name).symlink_to(case_dir / name, target_is_directory=True)
        view_meshes = []
        for mesh, (trimmed, _, _) in zip(meshes, entries):
            original_rank = mesh.parent.parent
            rank = view / original_rank.name
            rank.mkdir()
            for child in original_rank.iterdir():
                if child.name != "constant":
                    (rank / child.name).symlink_to(child, target_is_directory=child.is_dir())
            constant = rank / "constant"
            constant.mkdir()
            for child in original_rank.joinpath("constant").iterdir():
                if child.name != "polyMesh":
                    (constant / child.name).symlink_to(child, target_is_directory=child.is_dir())
            view_mesh = constant / "polyMesh"
            view_mesh.mkdir()
            for child in mesh.iterdir():
                target = view_mesh / child.name
                if child.name == "boundary" and trimmed is not None:
                    target.write_bytes(trimmed)
                else:
                    target.symlink_to(child, target_is_directory=child.is_dir())
            view_meshes.append(view_mesh)
        info["seconds"] = round(time.perf_counter() - started, 4)
        yield view, view_meshes, info


def _read_openfoam_case(
    case_dir: str | Path, time_value: Optional[float] = None
) -> Tuple[Any, dict]:
    """Read a serial or decomposed case without reconstruction or VTK export.

    Only times present on every processor are eligible. This avoids combining
    a newly written processor with stale data while a parallel solver writes.
    VTK joins processor pieces in memory. Processor-cyclic baffle pieces are
    restored to their physical patches; ordinary processor interfaces stay hidden.
    """
    case_dir = Path(case_dir).resolve()
    meshes, times = _openfoam_result_layout(case_dir, time_value)
    decomposed = meshes[0] != case_dir / "constant" / "polyMesh"
    selected = time_value if time_value is not None else (times[-1] if times else 0.0)
    pv = _get_pyvista()
    reader_timing = {}
    source_info = {"sourceFormat": "openfoam", "decomposed": decomposed, "timeValue": selected,
                   "readerTiming": reader_timing}
    with _partition_reader_view(case_dir, meshes, decomposed) as (reader_case_dir, reader_meshes, view_info):
        if decomposed:
            source_info["readerView"] = view_info
            source_info["readerMode"] = "partitionViews"
            print(f"[PP-TIMING] OpenFOAM read view: {view_info['seconds']}s; "
                  f"removed {view_info['emptyPatchesRemoved']} empty patch entries", flush=True)
            multiblock, partition_timing = _read_partition_views(case_dir, reader_meshes, selected)
            reader_timing.update(partition_timing)
        else:
            metadata_started = time.perf_counter()
            with tempfile.NamedTemporaryFile(suffix=".foam", prefix="pyvista_", dir=reader_case_dir) as marker:
                reader = pv.OpenFOAMReader(marker.name)
                reader.skip_zero_time = False
                reader.cell_to_point_creation = True
                reader.enable_all_patch_arrays()
                reader.enable_all_cell_arrays()
                if selected not in reader.time_values:
                    raise ValueError(f"OpenFOAM reader cannot load time {selected}: {reader.time_values}")
                reader_timing["metadata"] = round(time.perf_counter() - metadata_started, 4)
                native_started = time.perf_counter()
                reader.set_active_time_value(selected)
                # reader.read() is this plus pv.wrap, which took 180 s over
                # CMI-Guichou's 7,360 patches; the VTK read itself is seconds.
                from pyvista.core.filters import _update_alg
                _update_alg(reader.reader)
                multiblock = _wrap_multiblock_shallow(reader.reader.GetOutputDataObject(0))
                reader_timing["native_read"] = round(time.perf_counter() - native_started, 4)
        if "internalMesh" not in multiblock.keys() or multiblock["internalMesh"] is None:
            raise RuntimeError(f"OpenFOAM reader returned no internal mesh for {case_dir}")
    if decomposed:
        source_info["pointNormalization"] = _normalize_decomposed_points(multiblock)
        print(f"[PP-TIMING] OpenFOAM point normalization: {source_info['pointNormalization']['seconds']}s", flush=True)
    return multiblock, source_info


@contextmanager
def _vtk_threads(count: int):
    """Run the VTK filters inside on `count` STDThread workers: the VTK wheel
    starts on its Sequential (single-thread) SMP backend."""
    from vtkmodules.vtkCommonCore import vtkSMPTools

    previous = vtkSMPTools.GetBackend()
    vtkSMPTools.SetBackend("STDThread")
    vtkSMPTools.Initialize(count)
    try:
        yield
    finally:
        vtkSMPTools.SetBackend(previous)


def _merge_mesh_points(mesh: Any) -> Any:
    """Merge exactly coincident points, then interpolate connected cell fields.

    Decomposed data contain separate copies of interface points. Merging
    before interpolation gives them one consistent value. Cell fields remain
    authoritative. Point-only arrays are retained and averaged
    when there is no corresponding cell array to interpolate.
    """
    from vtkmodules.vtkFiltersCore import (
        vtkCellDataToPointData, vtkStaticCleanPolyData, vtkStaticCleanUnstructuredGrid,
    )

    pv = _get_pyvista()
    is_surface = isinstance(mesh, pv.PolyData)
    cleaner = vtkStaticCleanPolyData() if is_surface else vtkStaticCleanUnstructuredGrid()
    cleaner.SetInputDataObject(mesh)
    cleaner.SetToleranceIsAbsolute(True)
    cleaner.SetAbsoluteTolerance(0.0)
    cleaner.SetTolerance(0.0)
    point_data, cell_data = mesh.GetPointData(), mesh.GetCellData()
    point_only = any(not cell_data.HasArray(point_data.GetArrayName(index))
                     for index in range(point_data.GetNumberOfArrays()))
    cleaner.SetAveragePointData(point_only)
    cleaner.SetProduceMergeMap(False)
    if is_surface:
        cleaner.SetConvertLinesToPoints(False)
        cleaner.SetConvertPolysToLines(False)
        cleaner.SetConvertStripsToPolys(False)
    if cell_data.GetNumberOfArrays():
        interpolate = vtkCellDataToPointData()
        interpolate.SetInputConnection(cleaner.GetOutputPort())
        interpolate.SetPassCellData(True)
        interpolate.Update()
        raw_output = interpolate.GetOutput()
    else:
        cleaner.Update()
        raw_output = cleaner.GetOutput()
    # PyVista.wrap + its cell_data_to_point_data wrapper each run a complete
    # Python-side mesh-data validation. Repeating that for 4,000+ patches costs
    # tens of seconds. Keep the identical native filters in one pipeline and
    # validate tuple counts through VTK before one shallow Python wrapper.
    if raw_output.GetNumberOfCells() != mesh.n_cells:
        raise RuntimeError(f"Point merge changed cell count: {mesh.n_cells} -> {raw_output.GetNumberOfCells()}")
    for attributes, size in ((raw_output.GetPointData(), raw_output.GetNumberOfPoints()),
                             (raw_output.GetCellData(), raw_output.GetNumberOfCells())):
        for index in range(attributes.GetNumberOfArrays()):
            if attributes.GetAbstractArray(index).GetNumberOfTuples() != size:
                raise RuntimeError(f"Invalid array length after point merge: {attributes.GetArrayName(index)}")
    return pv.PolyData(raw_output, deep=False) if is_surface else pv.UnstructuredGrid(raw_output, deep=False)


def _normalize_decomposed_points(multiblock: Any) -> dict:
    """Make interpolation continuous across ranks without writing a root mesh."""
    started = time.perf_counter()
    internal = multiblock["internalMesh"]
    info = {"nCells": internal.n_cells, "internalPointsBefore": internal.n_points}
    # The one big merge on 8 threads (Base604 8.4 s -> 2.9 s, identical output);
    # the per-patch merges below stay sequential, threads only slowed them.
    with _vtk_threads(8):
        internal = _merge_mesh_points(internal)
    multiblock["internalMesh"] = internal
    info["internalPointsAfter"] = internal.n_points
    info["internalSeconds"] = round(time.perf_counter() - started, 4)
    print(f"[PP-TIMING] OpenFOAM normalize internal: {info['internalSeconds']}s "
          f"({info['internalPointsBefore']} -> {info['internalPointsAfter']} points)", flush=True)
    boundary_started = time.perf_counter()
    boundary_before = boundary_after = 0
    if "boundary" in multiblock.keys():
        boundary = multiblock["boundary"]
        # Never merge across physical patches: porous master/slave surfaces
        # may share coordinates while legitimately carrying different values.
        for index, name in enumerate(boundary.keys()):
            patch = boundary[index]
            if patch is None or not patch.n_points:
                continue
            boundary_before += patch.n_points
            merged = _merge_mesh_points(patch)
            boundary[index] = merged
            boundary.set_block_name(index, name)
            boundary_after += merged.n_points
    info.update(boundaryPointsBefore=boundary_before, boundaryPointsAfter=boundary_after,
                boundarySeconds=round(time.perf_counter() - boundary_started, 4),
                seconds=round(time.perf_counter() - started, 4))
    print(f"[PP-TIMING] OpenFOAM normalize boundaries: {info['boundarySeconds']}s", flush=True)
    return info


def _vtk_blocks(multiblock: Any) -> Dict[Optional[str], Any]:
    """{block name: block} of a VTK multiblock, none of them wrapped."""
    from vtkmodules.vtkCommonDataModel import vtkCompositeDataSet

    out: Dict[Optional[str], Any] = {}
    for index in range(multiblock.GetNumberOfBlocks()):
        meta = multiblock.GetMetaData(index) if multiblock.HasMetaData(index) else None
        name = meta.Get(vtkCompositeDataSet.NAME()) if meta is not None else None
        out[name] = multiblock.GetBlock(index)
    return out


def _wrap_multiblock_shallow(multiblock: Any) -> Any:
    """A VTK multiblock as the pv.MultiBlock pv.wrap makes of it, its leaves
    wrapped shallowly: pv.wrap validates every block, and its nested-block
    handling is quadratic in their count (CMI-Guichou's 7,360 patches: 180 s)."""
    from vtkmodules.vtkCommonDataModel import vtkCompositeDataSet

    pv = _get_pyvista()
    out = pv.MultiBlock()
    for index in range(multiblock.GetNumberOfBlocks()):
        meta = multiblock.GetMetaData(index) if multiblock.HasMetaData(index) else None
        name = meta.Get(vtkCompositeDataSet.NAME()) if meta is not None else None
        block = multiblock.GetBlock(index)
        if block is None:
            wrapped = None
        elif block.IsA("vtkMultiBlockDataSet"):
            wrapped = _wrap_multiblock_shallow(block)
        elif block.IsA("vtkUnstructuredGrid"):
            wrapped = pv.UnstructuredGrid(block, deep=False)
        elif block.IsA("vtkPolyData"):
            wrapped = pv.PolyData(block, deep=False)
        else:
            wrapped = pv.wrap(block)
        out.append(wrapped, name)
    return out


def _physical_patch_name(patch: dict) -> Optional[str]:
    """A processor-cyclic face is still part of its original physical baffle."""
    if patch.get("type") == "processorCyclic" and patch.get("referPatch"):
        return patch["referPatch"]
    if patch.get("type", "").startswith("processor"):
        return None
    return patch["name"]


def _rank_read_processes(ranks: int) -> int:
    """Worker processes for the rank reads: DCDTUNI_PP_READ_PROCESSES, else up to 8."""
    wanted = int(os.environ.get("DCDTUNI_PP_READ_PROCESSES", "8") or 1)
    return max(1, min(wanted, ranks))


def _read_ranks_in_workers(case_dir: Path, meshes: List[Path], time_value: float, processes: int):
    """Yield (index, _read_rank_packed result) as `processes` workers finish ranks.

    Each worker is `python -m backend.aircooling.runner.partition_read_worker`
    (0.5 s to start with VTK imported) fed through its stdin; it exits when its
    input closes, so nothing outlives the read.
    """
    import pickle
    import queue
    import subprocess

    pending: "queue.Queue[Tuple[int, Path]]" = queue.Queue()
    for index, mesh in enumerate(meshes):
        pending.put((index, mesh))
    finished: "queue.Queue[Tuple[Optional[int], str, Any]]" = queue.Queue()
    stop = threading.Event()
    src_root = str(Path(__file__).resolve().parents[3])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        part for part in (src_root, os.environ.get("PYTHONPATH", "")) if part)}

    def serve():
        try:
            worker = subprocess.Popen([sys.executable, "-m", "backend.aircooling.runner.partition_read_worker"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        except OSError as exc:
            finished.put((None, "error", f"rank reader did not start: {exc}"))
            return
        try:
            while not stop.is_set():
                try:
                    index, mesh = pending.get_nowait()
                except queue.Empty:
                    break
                pickle.dump((str(case_dir), str(mesh), time_value), worker.stdin, protocol=pickle.HIGHEST_PROTOCOL)
                worker.stdin.flush()
                status, value = pickle.load(worker.stdout)
                finished.put((index, status, value))
                if status != "ok":
                    break
        except Exception as exc:  # noqa: BLE001 - a dead worker (EOF) or a broken pipe
            finished.put((None, "error", f"rank reader failed: {exc!r} (exit {worker.poll()})"))
        finally:
            try:
                worker.stdin.close()
            except OSError:  # the worker is gone already
                pass
            worker.wait()

    threads = [threading.Thread(target=serve, name=f"pp-rank-reader-{n}", daemon=True) for n in range(processes)]
    for thread in threads:
        thread.start()
    try:
        for _ in meshes:
            index, status, value = finished.get()
            if status != "ok":
                raise RuntimeError(f"OpenFOAM rank read failed: {value}")
            yield index, value
    finally:
        stop.set()
        for thread in threads:
            thread.join()


def _read_rank_packed(case_dir: str, mesh: str, time_value: float):
    """_read_rank in a worker process, its blocks returned as _pack_vtk arrays."""
    name, seconds, internal, pieces = _read_rank(Path(case_dir), Path(mesh), time_value)
    return name, seconds, _pack_vtk(internal), [(physical, _pack_vtk(piece)) for physical, piece in pieces]


def _pack_vtk(data: Any) -> dict:
    """An unstructured grid or polydata as numpy arrays: points, cells (with
    polyhedron faces), every point/cell/field array with its VTK type and the
    active attributes. Pickling these is a copy; VTK's own marshalling took
    0.8-1.6 s per Base604 rank."""
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkCommonDataModel import vtkDataSetAttributes

    def array(values):
        if values is None:
            return None
        return values.GetName(), values.GetDataType(), vtk_to_numpy(values)

    def cells(cell_array):
        return array(cell_array.GetOffsetsArray()), array(cell_array.GetConnectivityArray())

    def arrays(attributes):
        out = []
        for index in range(attributes.GetNumberOfArrays()):
            values = attributes.GetArray(index)
            if values is None:
                raise TypeError(f"non-numeric array {attributes.GetAbstractArray(index).GetName()!r}")
            out.append(array(values))
        return out

    def active(attributes):
        return {kind: attributes.GetAttribute(kind).GetName()
                for kind in range(vtkDataSetAttributes.NUM_ATTRIBUTES) if attributes.GetAttribute(kind) is not None}

    packed = {"class": data.GetClassName(),
              "points": array(data.GetPoints().GetData()) if data.GetPoints() is not None else None,
              "pointData": arrays(data.GetPointData()), "pointActive": active(data.GetPointData()),
              "cellData": arrays(data.GetCellData()), "cellActive": active(data.GetCellData()),
              "fieldData": arrays(data.GetFieldData())}
    if packed["class"] == "vtkUnstructuredGrid":
        packed["types"] = array(data.GetCellTypesArray())
        packed["cells"] = cells(data.GetCells())
        faces = data.GetPolyhedronFaces()
        packed["faces"] = None if faces is None else (cells(data.GetPolyhedronFaceLocations()), cells(faces))
    elif packed["class"] == "vtkPolyData":
        for kind in ("Verts", "Lines", "Polys", "Strips"):
            packed[kind] = cells(getattr(data, "Get" + kind)())
    else:
        raise TypeError(f"cannot pack {packed['class']}")
    return packed


def _unpack_vtk(packed: dict) -> Any:
    """The dataset _pack_vtk described, in arrays VTK owns.

    One copy of each array. numpy_to_vtk(deep=False) shares the memory by
    hanging the numpy array on the VTK object as a Python attribute, and every
    such object that outlives its wrapper goes into VTK's ghost map, which
    made each further wrapper slower: Base604's ~9,000 pieces took 781 s.
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkCommonCore import vtkDataArray, vtkPoints, vtkTypeInt32Array, vtkTypeInt64Array
    from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData, vtkUnstructuredGrid

    def copied(out, values):
        out.SetNumberOfComponents(1 if values.ndim == 1 else values.shape[1])
        out.SetNumberOfTuples(len(values))
        if len(values):
            vtk_to_numpy(out)[...] = values
        return out

    def array(entry):
        name, data_type, values = entry
        out = copied(vtkDataArray.CreateDataArray(data_type), values)
        if name is not None:
            out.SetName(name)
        return out

    def index_array(entry):
        # vtkCellArray takes vtkTypeInt32/64Array, not the vtkIntArray /
        # vtkLongLongArray CreateDataArray makes for the same type code.
        values = entry[2]
        return copied({np.dtype(np.int64): vtkTypeInt64Array, np.dtype(np.int32): vtkTypeInt32Array}[values.dtype](),
                      values)

    def cells(entry):
        out = vtkCellArray()
        out.SetData(index_array(entry[0]), index_array(entry[1]))  # the typed overloads return nothing
        if out.GetNumberOfCells() != max(len(entry[0][2]) - 1, 0):
            raise RuntimeError("invalid packed cell array")
        return out

    def fill(attributes, entries, active=None):
        for entry in entries:
            attributes.AddArray(array(entry))
        for kind, name in (active or {}).items():
            attributes.SetActiveAttribute(name, kind)

    if packed["class"] == "vtkUnstructuredGrid":
        data = vtkUnstructuredGrid()
        if packed["faces"] is None:
            data.SetCells(array(packed["types"]), cells(packed["cells"]))
        else:
            locations, faces = packed["faces"]
            data.SetPolyhedralCells(array(packed["types"]), cells(packed["cells"]), cells(locations), cells(faces))
    else:
        data = vtkPolyData()
        for kind in ("Verts", "Lines", "Polys", "Strips"):
            getattr(data, "Set" + kind)(cells(packed[kind]))
    if packed["points"] is not None:
        points = vtkPoints()
        points.SetData(array(packed["points"]))
        data.SetPoints(points)
    fill(data.GetPointData(), packed["pointData"], packed["pointActive"])
    fill(data.GetCellData(), packed["cellData"], packed["cellActive"])
    fill(data.GetFieldData(), packed["fieldData"])
    return data


def _read_rank(case_dir: Path, mesh: Path, time_value: float):
    """One processor directory's internal mesh and physical patch pieces.

    POpenFOAMReader in its single-case mode on a temporary view of the rank;
    its mesh and fields stay linked to the original processor directory.
    """
    pv = _get_pyvista()
    rank_started = time.perf_counter()
    rank = mesh.parent.parent
    listed = MeshPreviewLoader._parse_boundary(mesh / "boundary")
    physical = [patch for patch in listed
                if patch.get("nFaces", 0) > 0 and _physical_patch_name(patch) is not None]
    keep = {patch["name"] for patch in listed}
    trimmed_entries = 0
    with tempfile.TemporaryDirectory(prefix=".pyvista-rank-", dir=case_dir) as temp:
        view = Path(temp)
        (view / "constant").symlink_to(rank / "constant", target_is_directory=True)
        if (case_dir / "system").is_dir():
            (view / "system").symlink_to(case_dir / "system", target_is_directory=True)
        for time_dir in rank.iterdir():
            try:
                matches_time = float(time_dir.name) == time_value
            except ValueError:
                continue
            if not (matches_time and time_dir.is_dir()):
                continue
            # The selected time with each field's boundaryField cut to the
            # patches this rank's (trimmed) boundary lists; anything else
            # is linked as it is.
            view_time = view / time_dir.name
            view_time.mkdir()
            for item in time_dir.iterdir():
                trimmed = None
                if item.is_file() and not item.is_symlink():
                    trimmed = _trim_field_boundary_entries(item.read_bytes(), keep)
                if trimmed is not None:
                    (view_time / item.name).write_bytes(trimmed)
                    trimmed_entries += 1
                else:
                    (view_time / item.name).symlink_to(item, target_is_directory=item.is_dir())
        marker = view / "case.foam"
        marker.touch()
        reader = pv.POpenFOAMReader(str(marker))
        reader.case_type = "reconstructed"  # this temporary view contains one rank
        reader.skip_zero_time = False
        reader.cell_to_point_creation = False  # interpolate once after global point merging
        reader.disable_all_patch_arrays()
        reader.enable_patch_array("internalMesh")
        reader.enable_all_cell_arrays()
        reader.enable_all_point_arrays()
        for patch in physical:
            reader.enable_patch_array("patch/" + patch["name"])
        reader.set_active_time_value(time_value)
        # Update the VTK reader itself rather than reader.read(): PyVista
        # wraps the output and each of its ~560 patch blocks, and every
        # wrap runs a full Python-side validate_mesh -- 6.6 of Base604
        # processor8's 9.2 s. Only the kept blocks are wrapped, shallowly.
        from pyvista.core.filters import _update_alg
        _update_alg(reader.reader)
        blocks = _vtk_blocks(reader.reader.GetOutputDataObject(0))
        if blocks.get("internalMesh") is None:
            raise RuntimeError(f"No internal mesh returned for {rank.name}")
        surfaces = _vtk_blocks(blocks["boundary"]) if blocks.get("boundary") is not None else {}
        pieces = []
        for patch in physical:
            surface = surfaces.get(patch["name"])
            if surface is None or surface.GetNumberOfCells() != patch["nFaces"]:
                raise RuntimeError(f"Failed to read physical boundary {rank.name}/{patch['name']}")
            pieces.append((_physical_patch_name(patch), pv.PolyData(surface, deep=False)))
        internal = pv.UnstructuredGrid(blocks["internalMesh"], deep=False)
    seconds = round(time.perf_counter() - rank_started, 4)
    print(f"[PP-TIMING] OpenFOAM {rank.name}: {seconds}s "
          f"({len(physical)} physical patch pieces, {trimmed_entries} fields trimmed)", flush=True)
    return rank.name, seconds, internal, pieces


def _read_partition_views(case_dir: Path, meshes: List[Path], time_value: float) -> Tuple[Any, dict]:
    """Read each sparse rank once and explicitly assemble physical patches.

    vtkPOpenFOAMReader's global append aligns boundary blocks by index, even
    with ReadAllFilesToDetermineStructure enabled. Sparse rank dictionaries
    therefore need explicit assembly by name. No reconstructed data are
    written.
    """
    pv = _get_pyvista()
    started = time.perf_counter()
    rank_timings = {}
    # Ranks are independent readers. VTK holds the GIL for a whole read, so
    # threads did not overlap (Base604 serial 55 s, 4 threads 36 s on 4 ranks);
    # processes do. Each returns its blocks as plain arrays (_pack_vtk), which
    # cost next to nothing to send and rebuild, and assembly stays in rank order.
    processes = _rank_read_processes(len(meshes))
    if processes == 1:
        results = [_read_rank(case_dir, mesh, time_value) for mesh in meshes]
    else:
        results = [None] * len(meshes)
        # Rebuild each rank as it arrives, while the others are still read
        # (Base604: 7,854 pieces, 4-5 s of this process's time).
        for index, (name, seconds, internal, pieces) in _read_ranks_in_workers(
                case_dir, meshes, time_value, processes):
            results[index] = (name, seconds, pv.UnstructuredGrid(_unpack_vtk(internal), deep=False),
                              [(physical, pv.PolyData(_unpack_vtk(piece), deep=False)) for physical, piece in pieces])
    internal_parts = []
    boundary_parts: Dict[str, List[Any]] = {}
    for name, seconds, internal_part, pieces in results:
        rank_timings[name] = seconds
        internal_parts.append(internal_part)
        for physical_name, surface in pieces:
            boundary_parts.setdefault(physical_name, []).append(surface)
    read_seconds = time.perf_counter() - started
    assemble_started = time.perf_counter()
    # What MultiBlock.combine(merge_points=False) and append_polydata run, in
    # the same input order, without wrapping (and validating) each output:
    # 4,336 patch wraps were most of Base604's 33 s assembly.
    from vtkmodules.vtkFiltersCore import vtkAppendFilter, vtkAppendPolyData
    append = vtkAppendFilter()
    for part in internal_parts:
        append.AddInputData(part)
    append.SetMergePoints(False)
    append.SetTolerance(0.0)
    append.Update()
    internal = pv.UnstructuredGrid(append.GetOutputDataObject(0), deep=False)
    boundary = pv.MultiBlock()
    for name, parts in boundary_parts.items():
        if len(parts) > 1:
            appender = vtkAppendPolyData()
            for part in parts:
                appender.AddInputData(part)
            appender.Update()
            merged = pv.PolyData(appender.GetOutputDataObject(0), deep=False)
        else:
            merged = parts[0]
        boundary.append(merged, name)
    result = pv.MultiBlock({"internalMesh": internal, "boundary": boundary})
    timing = {"native_read": round(read_seconds, 4), "ranks": rank_timings,
              "assemble": round(time.perf_counter() - assemble_started, 4)}
    print(f"[PP-TIMING] OpenFOAM rank reads: {timing['native_read']}s; assemble: {timing['assemble']}s", flush=True)
    return result, timing


# ---------------------------------------------------------------------------
# Data classes for configuration
# ---------------------------------------------------------------------------


@dataclass
class RenderConfig:
    """Common rendering parameters."""
    window_size: Tuple[int, int] = (1920, 1080)
    background_color: str = "white"
    camera_position: Optional[str | Sequence] = "iso"  # 'xy','xz','yz','iso' or [(pos),(focal),(viewup)]
    camera_zoom: float = 1.0
    show_edges: bool = False
    opacity: float = 1.0
    lighting: bool = True
    smooth_shading: bool = True


@dataclass
class ScalarBarConfig:
    """Color bar / scalar bar appearance."""
    title: str = ""
    n_labels: int = 5
    fmt: str = "%.1f"
    vertical: bool = True
    width: float = 0.08
    height: float = 0.5
    position_x: float = 0.87
    position_y: float = 0.25
    title_font_size: int = 16
    label_font_size: int = 14
    color: str = "black"
    bold: bool = True


@dataclass
class StreamlineConfig:
    """Streamline computation parameters."""
    seed_center: Optional[Tuple[float, float, float]] = None
    seed_radius: float = 0.5
    n_seed_points: int = 50
    seed_points: Optional[np.ndarray] = None  # explicit seed points (N,3)
    integration_direction: Literal["both", "forward", "backward"] = "both"
    max_steps: int = 2000
    initial_step_length: float = 0.5
    max_step_length: float = 1.0
    terminal_speed: float = 1e-12
    tube_radius: float = 0.005
    color_by: Literal["T", "speed", "solid"] = "speed"  # color streamlines by field


# ---------------------------------------------------------------------------

# The room's own shell -- the editor exports it as Room_Ceiling and
# Room_Wall_<n> -- encloses everything a result is opened to look at: drawn
# by default, its walls and ceiling hid every rack, slice and streamline
# inside (Base2-9, 2026-10-19), and the patch list was the only way in. They
# start hidden; the floor stays, it is what the room stands on.
_ROOM_SHELL_RE = re.compile(r"^Room_(Ceiling|Wall_\d+)$", re.IGNORECASE)


def _patch_default_visible(name: str) -> bool:
    """Whether the results view draws this patch before the user picks."""
    if name.endswith("_slave") or name in ("internalMesh", "internal"):
        return False
    return not _ROOM_SHELL_RE.match(name)


# Main post-processor class
# ---------------------------------------------------------------------------


class CFDPostProcessor:
    """
    Headless renderer for OpenFOAM CFD results.

    Loads a VTM multi-block dataset (from foamToVTK) or reads an OpenFOAM
    case directly via PyVista's OpenFOAMReader or POpenFOAMReader. Provides methods
    to render various field visualizations and return them as base64 PNG
    strings.

    Attributes:
        multiblock: The loaded pyvista.MultiBlock dataset.
        internal:   The internal (volume) mesh (UnstructuredGrid).
        boundary:   The boundary MultiBlock containing all patch surfaces.
        patch_names: List of boundary patch names.
    """

    def __init__(self, multiblock):
        """
        Initialize from an already-loaded pyvista.MultiBlock.

        Use the class methods `from_vtm()` or `from_openfoam_case()` instead
        of calling this directly.
        """
        pv = _get_pyvista()

        self.multiblock = multiblock

        # ---- Locate internal mesh and boundary block ----
        # foamToVTK layout:    [0] = "internal" (UnstructuredGrid)
        #                      [1] = "boundary" (MultiBlock of PolyData)
        # OpenFOAMReader layout: keys "internalMesh" and "boundary"
        self.internal = None
        self.boundary = None

        for i, key in enumerate(multiblock.keys()):
            block = multiblock[i]
            if block is None:
                continue
            if key in ("internal", "internalMesh"):
                self.internal = block
            elif key == "boundary" and isinstance(block, pv.MultiBlock):
                self.boundary = block

        if self.internal is None:
            raise ValueError(
                "Could not find internal mesh in MultiBlock. "
                f"Available keys: {list(multiblock.keys())}"
            )

        self.patch_names: List[str] = []
        if self.boundary is not None:
            self.patch_names = list(self.boundary.keys())

        # Pre-compute velocity magnitude on internal mesh if U is present
        if "U" in self.internal.point_data:
            U = self.internal.point_data["U"]
            self.internal.point_data["Umag"] = np.linalg.norm(U, axis=1)
            # Also store individual components
            self.internal.point_data["Ux"] = U[:, 0]
            self.internal.point_data["Uy"] = U[:, 1]
            self.internal.point_data["Uz"] = U[:, 2]
            self.internal.set_active_vectors("U")

        # Pre-compute on boundary patches too
        if self.boundary is not None:
            for i in range(self.boundary.n_blocks):
                patch = self.boundary[i]
                if patch is not None and "U" in patch.point_data:
                    U = patch.point_data["U"]
                    patch.point_data["Umag"] = np.linalg.norm(U, axis=1)
                    patch.point_data["Ux"] = U[:, 0]
                    patch.point_data["Uy"] = U[:, 1]
                    patch.point_data["Uz"] = U[:, 2]

        logger.info(
            "CFDPostProcessor initialized: %d internal cells, %d boundary patches",
            self.internal.n_cells,
            len(self.patch_names),
        )

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_vtm(cls, vtm_path: str | Path) -> "CFDPostProcessor":
        """
        Load from a VTM (VTK Multi-block) file produced by foamToVTK.

        Parameters
        ----------
        vtm_path : str or Path
            Path to the .vtm file, e.g.
            ``case/VTK/casename_500.vtm``

        Returns
        -------
        CFDPostProcessor
        """
        pv = _get_pyvista()
        vtm_path = str(vtm_path)
        if not os.path.isfile(vtm_path):
            raise FileNotFoundError(f"VTM file not found: {vtm_path}")
        mb = pv.read(vtm_path)
        return cls(mb)

    @classmethod
    def from_openfoam_case(
        cls,
        case_dir: str | Path,
        time_value: Optional[float] = None,
    ) -> "CFDPostProcessor":
        """
        Load a serial or decomposed OpenFOAM case directly using PyVista.

        This avoids the need to run ``foamToVTK`` first. PyVista reads the
        ``constant/polyMesh`` and time directories natively.

        Parameters
        ----------
        case_dir : str or Path
            Path to the OpenFOAM case directory (containing ``system/``,
            ``constant/``, and time step directories).
        time_value : float, optional
            Which time step to load. If None, loads the latest available.

        Returns
        -------
        CFDPostProcessor
        """
        mb, _ = read_openfoam_case(case_dir, time_value)
        return cls(mb)

    # ------------------------------------------------------------------
    # Informational methods
    # ------------------------------------------------------------------

    def list_patches(self) -> List[Dict[str, Any]]:
        """
        Return a list of all boundary patches with metadata.

        Returns
        -------
        list of dict
            Each dict has keys: ``name``, ``n_cells``, ``n_points``,
            ``fields`` (list of field names).
        """
        results = []
        if self.boundary is None:
            return results
        for i, name in enumerate(self.boundary.keys()):
            patch = self.boundary[i]
            if patch is None:
                results.append({"name": name, "n_cells": 0, "n_points": 0, "fields": []})
            else:
                results.append({
                    "name": name,
                    "n_cells": patch.n_cells,
                    "n_points": patch.n_points,
                    "fields": list(patch.point_data.keys()),
                })
        return results

    def get_field_range(
        self,
        field_name: str,
        source: Literal["internal", "boundary", "all"] = "all",
    ) -> Tuple[float, float]:
        """
        Get the min/max range of a scalar field.

        Parameters
        ----------
        field_name : str
            Name of the field (e.g. 'T', 'Umag', 'p').
        source : str
            'internal', 'boundary', or 'all'.

        Returns
        -------
        (min_val, max_val)
        """
        vals = []
        if source in ("internal", "all") and field_name in self.internal.point_data:
            vals.append(self.internal.point_data[field_name])
        if source in ("boundary", "all") and self.boundary is not None:
            for i in range(self.boundary.n_blocks):
                patch = self.boundary[i]
                if patch is not None and field_name in patch.point_data:
                    vals.append(patch.point_data[field_name])
        if not vals:
            raise KeyError(f"Field '{field_name}' not found in {source} mesh(es)")
        combined = np.concatenate(vals)
        return float(combined.min()), float(combined.max())

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------

    def _make_plotter(self, config: RenderConfig):
        """Create a configured off-screen Plotter."""
        pv = _get_pyvista()
        p = pv.Plotter(
            off_screen=True,
            window_size=list(config.window_size),
        )
        p.set_background(config.background_color)
        return p

    def _set_camera(self, plotter, config: RenderConfig):
        """Apply camera settings to the plotter."""
        if isinstance(config.camera_position, str):
            plotter.camera_position = config.camera_position
        elif config.camera_position is not None:
            plotter.camera_position = config.camera_position
        if config.camera_zoom != 1.0:
            plotter.camera.zoom(config.camera_zoom)

    def _render_to_base64(
        self,
        plotter,
        config: RenderConfig,
    ) -> str:
        """
        Finalize the plotter and return the rendered image as a base64 PNG string.

        Parameters
        ----------
        plotter : pv.Plotter
            The configured plotter with meshes already added.
        config : RenderConfig
            Rendering configuration (camera, etc).

        Returns
        -------
        str
            Base64-encoded PNG image data (no ``data:image/png;base64,`` prefix).
        """
        self._set_camera(plotter, config)
        plotter.show(auto_close=False)

        # Write screenshot to a temporary file, then read back as bytes
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            plotter.screenshot(tmp_path)
            with open(tmp_path, "rb") as f:
                png_bytes = f.read()
        finally:
            os.unlink(tmp_path)
            plotter.close()

        return base64.b64encode(png_bytes).decode("ascii")

    # ------------------------------------------------------------------
    # Rendering methods
    # ------------------------------------------------------------------

    def render_temperature(
        self,
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "coolwarm",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        patches: Optional[List[str]] = None,
        show_internal: bool = False,
    ) -> str:
        """
        Render the temperature field (T) on boundary surfaces.

        Parameters
        ----------
        clim : tuple of (min, max), optional
            Color range. If None, uses the data range.
        cmap : str
            Matplotlib colormap name. Default "coolwarm".
        render_config : RenderConfig, optional
            Rendering parameters. If None, uses defaults.
        scalar_bar_config : ScalarBarConfig, optional
            Color bar parameters. If None, uses defaults with title "Temperature [K]".
        patches : list of str, optional
            Specific patch names to render. If None, renders all boundary patches.
        show_internal : bool
            If True, also render the internal mesh as a translucent volume.

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        return self._render_scalar_field(
            field_name="T",
            clim=clim,
            cmap=cmap,
            default_title="Temperature [K]",
            render_config=render_config,
            scalar_bar_config=scalar_bar_config,
            patches=patches,
            show_internal=show_internal,
        )

    def render_velocity_magnitude(
        self,
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "jet",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        patches: Optional[List[str]] = None,
        show_internal: bool = False,
    ) -> str:
        """
        Render the velocity magnitude field (|U|) on boundary surfaces.

        Parameters
        ----------
        clim : tuple of (min, max), optional
            Color range for velocity magnitude [m/s].
        cmap : str
            Matplotlib colormap name. Default "jet".
        render_config : RenderConfig, optional
        scalar_bar_config : ScalarBarConfig, optional
        patches : list of str, optional
        show_internal : bool

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        return self._render_scalar_field(
            field_name="Umag",
            clim=clim,
            cmap=cmap,
            default_title="Velocity Magnitude [m/s]",
            render_config=render_config,
            scalar_bar_config=scalar_bar_config,
            patches=patches,
            show_internal=show_internal,
        )

    def render_velocity_component(
        self,
        component: Literal["x", "y", "z"] = "x",
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "RdBu_r",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        patches: Optional[List[str]] = None,
    ) -> str:
        """
        Render a single velocity component (Ux, Uy, or Uz) on boundary surfaces.

        Parameters
        ----------
        component : 'x', 'y', or 'z'
        clim, cmap, render_config, scalar_bar_config, patches : see render_temperature

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        field_name = f"U{component}"
        label_map = {"x": "Ux", "y": "Uy", "z": "Uz"}
        return self._render_scalar_field(
            field_name=field_name,
            clim=clim,
            cmap=cmap,
            default_title=f"Velocity {label_map[component]} [m/s]",
            render_config=render_config,
            scalar_bar_config=scalar_bar_config,
            patches=patches,
            show_internal=False,
        )

    def render_pressure(
        self,
        field: Literal["p", "p_rgh"] = "p_rgh",
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "viridis",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        patches: Optional[List[str]] = None,
    ) -> str:
        """
        Render pressure field on boundary surfaces.

        Parameters
        ----------
        field : 'p' or 'p_rgh'
        clim, cmap, render_config, scalar_bar_config, patches : see render_temperature

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        title = "Pressure p [Pa]" if field == "p" else "Pressure p_rgh [Pa]"
        return self._render_scalar_field(
            field_name=field,
            clim=clim,
            cmap=cmap,
            default_title=title,
            render_config=render_config,
            scalar_bar_config=scalar_bar_config,
            patches=patches,
            show_internal=False,
        )

    def _render_scalar_field(
        self,
        field_name: str,
        clim: Optional[Tuple[float, float]],
        cmap: str,
        default_title: str,
        render_config: Optional[RenderConfig],
        scalar_bar_config: Optional[ScalarBarConfig],
        patches: Optional[List[str]],
        show_internal: bool = False,
    ) -> str:
        """Internal: render any scalar field on boundary patches."""
        rc = render_config or RenderConfig()
        sbc = scalar_bar_config or ScalarBarConfig(title=default_title)
        if not sbc.title:
            sbc.title = default_title

        plotter = self._make_plotter(rc)

        scalar_bar_args = {
            "title": sbc.title,
            "n_labels": sbc.n_labels,
            "fmt": sbc.fmt,
            "vertical": sbc.vertical,
            "width": sbc.width,
            "height": sbc.height,
            "position_x": sbc.position_x,
            "position_y": sbc.position_y,
            "title_font_size": sbc.title_font_size,
            "label_font_size": sbc.label_font_size,
            "color": sbc.color,
            "bold": sbc.bold,
        }

        # Determine actual clim
        if clim is None:
            try:
                clim = self.get_field_range(field_name, source="boundary")
            except KeyError:
                clim = self.get_field_range(field_name, source="all")

        # Add boundary patches
        if self.boundary is not None:
            added_first = False
            for i, name in enumerate(self.boundary.keys()):
                patch = self.boundary[i]
                if patch is None or patch.n_points == 0:
                    continue
                if patches is not None and name not in patches:
                    continue
                if field_name not in patch.point_data:
                    continue

                plotter.add_mesh(
                    patch,
                    scalars=field_name,
                    cmap=cmap,
                    clim=clim,
                    show_edges=rc.show_edges,
                    opacity=rc.opacity,
                    lighting=rc.lighting,
                    smooth_shading=rc.smooth_shading,
                    scalar_bar_args=scalar_bar_args if not added_first else None,
                    show_scalar_bar=not added_first,
                    name=f"patch_{name}",
                )
                added_first = True

        # Optionally show internal mesh as translucent
        if show_internal and field_name in self.internal.point_data:
            # Extract surface of internal mesh for visualization
            surface = self.internal.extract_surface()
            plotter.add_mesh(
                surface,
                scalars=field_name,
                cmap=cmap,
                clim=clim,
                opacity=0.15,
                show_scalar_bar=False,
                name="internal_surface",
            )

        return self._render_to_base64(plotter, rc)

    def render_slice(
        self,
        field_name: str = "T",
        normal: Union[str, Tuple[float, float, float]] = "z",
        origin: Optional[Tuple[float, float, float]] = None,
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "coolwarm",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        show_boundary_wireframe: bool = True,
    ) -> str:
        """
        Render a planar slice through the internal volume mesh.

        This is one of the most useful views for data center CFD: a horizontal
        slice at a given height showing the temperature distribution.

        Parameters
        ----------
        field_name : str
            Field to color the slice by (e.g. 'T', 'Umag', 'Ux').
        normal : str or tuple
            Slice plane normal. Use 'x', 'y', 'z' for axis-aligned,
            or provide a (nx, ny, nz) tuple.
        origin : tuple, optional
            Point on the slice plane. If None, uses mesh center.
        clim : tuple, optional
        cmap : str
        render_config : RenderConfig, optional
        scalar_bar_config : ScalarBarConfig, optional
        show_boundary_wireframe : bool
            If True, overlay boundary patch wireframes for context.

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        rc = render_config or RenderConfig()
        title = f"{field_name} (slice)"
        sbc = scalar_bar_config or ScalarBarConfig(title=title)
        if not sbc.title:
            sbc.title = title

        plotter = self._make_plotter(rc)

        # Map string normals to vectors. Y-up convention: fallback default
        # for an unknown string is (0, 1, 0) (horizontal slice at fixed Y).
        normal_map = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
        if isinstance(normal, str):
            normal = normal_map.get(normal.lower(), (0, 1, 0))

        if origin is None:
            origin = self.internal.center

        # Create the slice
        sliced = self.internal.slice(normal=normal, origin=origin)

        if clim is None:
            if field_name in sliced.point_data:
                data = sliced.point_data[field_name]
                clim = (float(data.min()), float(data.max()))
            else:
                clim = self.get_field_range(field_name, source="internal")

        scalar_bar_args = {
            "title": sbc.title,
            "n_labels": sbc.n_labels,
            "fmt": sbc.fmt,
            "vertical": sbc.vertical,
            "width": sbc.width,
            "height": sbc.height,
            "position_x": sbc.position_x,
            "position_y": sbc.position_y,
            "title_font_size": sbc.title_font_size,
            "label_font_size": sbc.label_font_size,
            "color": sbc.color,
            "bold": sbc.bold,
        }

        plotter.add_mesh(
            sliced,
            scalars=field_name,
            cmap=cmap,
            clim=clim,
            show_edges=False,
            lighting=rc.lighting,
            scalar_bar_args=scalar_bar_args,
            name="slice",
        )

        # Overlay boundary wireframe for spatial context
        if show_boundary_wireframe and self.boundary is not None:
            combined = self.boundary.combine()
            plotter.add_mesh(
                combined,
                style="wireframe",
                color="gray",
                opacity=0.15,
                show_scalar_bar=False,
                name="boundary_wireframe",
            )

        return self._render_to_base64(plotter, rc)

    def render_streamlines(
        self,
        streamline_config: Optional[StreamlineConfig] = None,
        color_field: str = "speed",
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "plasma",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        show_boundary_wireframe: bool = True,
    ) -> str:
        """
        Render velocity streamlines through the internal mesh.

        Parameters
        ----------
        streamline_config : StreamlineConfig, optional
            Streamline computation parameters. If None, seeds from mesh center.
        color_field : str
            Field to color streamlines by. Special values:
            - 'speed': color by velocity magnitude along streamline
            - 'T': color by temperature
            - any other field name present in the data
        clim : tuple, optional
        cmap : str
        render_config : RenderConfig, optional
        scalar_bar_config : ScalarBarConfig, optional
        show_boundary_wireframe : bool

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        pv = _get_pyvista()
        rc = render_config or RenderConfig()
        sc = streamline_config or StreamlineConfig()
        sbc = scalar_bar_config or ScalarBarConfig(title="Streamlines")
        if not sbc.title:
            sbc.title = "Streamlines"

        plotter = self._make_plotter(rc)

        # Ensure active vectors are set
        self.internal.set_active_vectors("U")

        # Compute streamlines
        if sc.seed_points is not None:
            # Use explicit seed points
            seed = pv.PolyData(np.asarray(sc.seed_points))
            streams = self.internal.streamlines_from_source(
                source=seed,
                vectors="U",
                integration_direction=sc.integration_direction,
                max_steps=sc.max_steps,
                initial_step_length=sc.initial_step_length,
                max_step_length=sc.max_step_length,
                terminal_speed=sc.terminal_speed,
            )
        else:
            # Use spherical seed region
            center = sc.seed_center or tuple(self.internal.center)
            streams = self.internal.streamlines(
                vectors="U",
                source_center=center,
                source_radius=sc.seed_radius,
                n_points=sc.n_seed_points,
                max_steps=sc.max_steps,
                initial_step_length=sc.initial_step_length,
            )

        if streams.n_points == 0:
            logger.warning("No streamlines were generated. Check seed point locations.")
            plotter.add_text(
                "No streamlines generated",
                position="upper_left",
                font_size=18,
                color="red",
            )
            return self._render_to_base64(plotter, rc)

        # Determine color scalar
        if color_field == "speed":
            if "U" in streams.point_data:
                U = streams.point_data["U"]
                streams.point_data["speed"] = np.linalg.norm(U, axis=1)
                scalars = "speed"
                sbc.title = "Speed [m/s]"
            else:
                scalars = None
        elif color_field in streams.point_data:
            scalars = color_field
        else:
            scalars = None

        # Convert streamlines to tubes for better visualization
        if sc.tube_radius > 0 and streams.n_points > 1:
            try:
                tubes = streams.tube(radius=sc.tube_radius)
                render_mesh = tubes
            except Exception:
                render_mesh = streams
        else:
            render_mesh = streams

        scalar_bar_args = {
            "title": sbc.title,
            "n_labels": sbc.n_labels,
            "fmt": sbc.fmt,
            "vertical": sbc.vertical,
            "width": sbc.width,
            "height": sbc.height,
            "position_x": sbc.position_x,
            "position_y": sbc.position_y,
            "title_font_size": sbc.title_font_size,
            "label_font_size": sbc.label_font_size,
            "color": sbc.color,
            "bold": sbc.bold,
        }

        plotter.add_mesh(
            render_mesh,
            scalars=scalars,
            cmap=cmap,
            clim=clim,
            show_edges=False,
            lighting=True,
            scalar_bar_args=scalar_bar_args if scalars else None,
            show_scalar_bar=scalars is not None,
            name="streamlines",
        )

        # Overlay boundary wireframe
        if show_boundary_wireframe and self.boundary is not None:
            combined = self.boundary.combine()
            plotter.add_mesh(
                combined,
                style="wireframe",
                color="gray",
                opacity=0.1,
                show_scalar_bar=False,
                name="boundary_wireframe",
            )

        return self._render_to_base64(plotter, rc)

    def render_combined(
        self,
        show_temperature: bool = True,
        show_streamlines: bool = True,
        t_clim: Optional[Tuple[float, float]] = None,
        t_cmap: str = "coolwarm",
        streamline_config: Optional[StreamlineConfig] = None,
        stream_cmap: str = "plasma",
        render_config: Optional[RenderConfig] = None,
    ) -> str:
        """
        Render a combined view with temperature on surfaces and velocity streamlines.

        Parameters
        ----------
        show_temperature : bool
        show_streamlines : bool
        t_clim : tuple, optional
        t_cmap : str
        streamline_config : StreamlineConfig, optional
        stream_cmap : str
        render_config : RenderConfig, optional

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        pv = _get_pyvista()
        rc = render_config or RenderConfig()
        plotter = self._make_plotter(rc)

        # Temperature on boundary surfaces
        if show_temperature and self.boundary is not None:
            if t_clim is None:
                try:
                    t_clim = self.get_field_range("T", source="boundary")
                except KeyError:
                    t_clim = None

            added_bar = False
            for i in range(self.boundary.n_blocks):
                patch = self.boundary[i]
                if patch is None or patch.n_points == 0:
                    continue
                if "T" not in patch.point_data:
                    continue

                sbar = None
                if not added_bar:
                    sbar = {
                        "title": "Temperature [K]",
                        "n_labels": 5,
                        "fmt": "%.1f",
                        "vertical": True,
                        "position_x": 0.05,
                        "position_y": 0.25,
                        "color": "black",
                    }
                    added_bar = True

                plotter.add_mesh(
                    patch,
                    scalars="T",
                    cmap=t_cmap,
                    clim=t_clim,
                    opacity=0.6,
                    show_scalar_bar=sbar is not None,
                    scalar_bar_args=sbar,
                    smooth_shading=True,
                )

        # Streamlines
        if show_streamlines:
            sc = streamline_config or StreamlineConfig()
            self.internal.set_active_vectors("U")

            center = sc.seed_center or tuple(self.internal.center)
            streams = self.internal.streamlines(
                vectors="U",
                source_center=center,
                source_radius=sc.seed_radius,
                n_points=sc.n_seed_points,
                max_steps=sc.max_steps,
            )

            if streams.n_points > 0:
                if "U" in streams.point_data:
                    U = streams.point_data["U"]
                    streams.point_data["speed"] = np.linalg.norm(U, axis=1)

                try:
                    tubes = streams.tube(radius=sc.tube_radius)
                except Exception:
                    tubes = streams

                plotter.add_mesh(
                    tubes,
                    scalars="speed" if "speed" in tubes.point_data else None,
                    cmap=stream_cmap,
                    show_scalar_bar=True,
                    scalar_bar_args={
                        "title": "Speed [m/s]",
                        "n_labels": 5,
                        "fmt": "%.1f",
                        "vertical": True,
                        "position_x": 0.87,
                        "position_y": 0.25,
                        "color": "black",
                    },
                    name="streamlines",
                )

        return self._render_to_base64(plotter, rc)


# ---------------------------------------------------------------------------
# Convenience functions (for quick use in API handlers)
# ---------------------------------------------------------------------------


def render_vtm_temperature(
    vtm_path: str,
    clim: Optional[Tuple[float, float]] = None,
    cmap: str = "coolwarm",
    window_size: Tuple[int, int] = (1920, 1080),
    camera_position: str = "iso",
) -> str:
    """
    One-shot: load VTM, render temperature, return base64 PNG.

    Parameters
    ----------
    vtm_path : str
    clim : tuple, optional
    cmap : str
    window_size : tuple
    camera_position : str

    Returns
    -------
    str
        Base64-encoded PNG.
    """
    pp = CFDPostProcessor.from_vtm(vtm_path)
    return pp.render_temperature(
        clim=clim,
        cmap=cmap,
        render_config=RenderConfig(
            window_size=window_size,
            camera_position=camera_position,
        ),
    )


def render_vtm_velocity(
    vtm_path: str,
    clim: Optional[Tuple[float, float]] = None,
    cmap: str = "jet",
    window_size: Tuple[int, int] = (1920, 1080),
    camera_position: str = "iso",
) -> str:
    """One-shot: load VTM, render velocity magnitude, return base64 PNG."""
    pp = CFDPostProcessor.from_vtm(vtm_path)
    return pp.render_velocity_magnitude(
        clim=clim,
        cmap=cmap,
        render_config=RenderConfig(
            window_size=window_size,
            camera_position=camera_position,
        ),
    )


def render_vtm_streamlines(
    vtm_path: str,
    seed_center: Optional[Tuple[float, float, float]] = None,
    seed_radius: float = 0.5,
    n_seed_points: int = 50,
    window_size: Tuple[int, int] = (1920, 1080),
    camera_position: str = "iso",
) -> str:
    """One-shot: load VTM, render streamlines, return base64 PNG."""
    pp = CFDPostProcessor.from_vtm(vtm_path)
    return pp.render_streamlines(
        streamline_config=StreamlineConfig(
            seed_center=seed_center,
            seed_radius=seed_radius,
            n_seed_points=n_seed_points,
        ),
        render_config=RenderConfig(
            window_size=window_size,
            camera_position=camera_position,
        ),
    )


# ---------------------------------------------------------------------------
# PostProcessor — High-level wrapper for server.py integration
# ---------------------------------------------------------------------------

def _resolve_foam_bashrc() -> str:
    """OpenFOAM etc/bashrc to source for foamToVTK — conda v2412 first, never
    system OpenFOAM.

    In order: ``$FOAM_BASHRC`` (explicit override), ``$WM_PROJECT_DIR`` (set by
    the launcher), the prefix the RUNNING INTERPRETER lives in, and only then
    ``$CONDA_PREFIX``. ``CONDA_PREFIX`` is last because it is the launching
    SHELL's, not the server's: ``start_server.sh`` runs the dcdtuni python by
    path, so a shell with ``base`` active hands the server
    ``CONDA_PREFIX=.../miniconda3`` — which has no ``etc/bashrc`` — while the
    interpreter is ``.../envs/dcdtuni/bin/python3.10``, which does. Trusting
    ``CONDA_PREFIX`` first returned "" there and the conversion ran
    ``source ""``. Every candidate is accepted only if the file exists."""
    candidates = []
    fb = os.environ.get("FOAM_BASHRC")
    if fb:
        candidates.append(fb)
    wm = os.environ.get("WM_PROJECT_DIR")
    if wm:
        candidates.append(os.path.join(wm, "etc", "bashrc"))
    exe = os.path.realpath(sys.executable)
    if os.path.basename(os.path.dirname(exe)) == "bin":
        candidates.append(os.path.join(os.path.dirname(os.path.dirname(exe)), "etc", "bashrc"))
    cp = os.environ.get("CONDA_PREFIX")
    if cp:
        candidates.append(os.path.join(cp, "etc", "bashrc"))
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return ""


def _foam_source_prefix() -> str:
    """The ``source <bashrc> &&`` that precedes every OpenFOAM tool call.

    Raises rather than producing ``source ""``: that fails inside bash with
    ``line 1: : No such file or directory``, which names neither the missing
    file nor the variable that would fix it."""
    bashrc = _resolve_foam_bashrc()
    if not bashrc:
        raise RuntimeError(
            "OpenFOAM etc/bashrc not found: set FOAM_BASHRC to it, or run the server "
            "from the dcdtuni conda environment (checked FOAM_BASHRC, WM_PROJECT_DIR, "
            f"the interpreter prefix of {sys.executable}, and CONDA_PREFIX)."
        )
    return f'source "{bashrc}" && '


# Kept for callers that read it; the conversion resolves at call time so a
# server whose environment changed does not keep a stale answer.
_FOAM_BASHRC = _resolve_foam_bashrc()


class PostProcessor:
    """
    High-level post-processing wrapper for the frontend server.

    Reads native OpenFOAM results directly, supports explicit legacy VTM export,
    and renders surface fields / streamlines. Designed for lazy
    initialisation and caching in server.py (one instance per case).

    Parameters
    ----------
    run_dir : Path
        The solve run directory, e.g.
        ``<case>/log/2026-03-22-05-01-31_V5_CFD_Station``.
        Contains time-step directories and ``constant/polyMesh`` in the root
        or in each ``processorN`` directory.
    """

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir).resolve()
        if not self.run_dir.is_dir():
            raise NotADirectoryError(f"Run directory does not exist: {self.run_dir}")
        self._pp: Optional[CFDPostProcessor] = None  # lazy
        self._vtk_dir: Optional[Path] = None
        self._vtm_path: Optional[Path] = None
        self.nan_warnings: List[str] = []  # fields that had NaN sanitized

    # ------------------------------------------------------------------
    # 1.  foamToVTK conversion
    # ------------------------------------------------------------------

    def convert_to_vtk(self) -> Path:
        """Run ``foamToVTK -latestTime`` on the run directory.

        If a VTK/ directory already exists and contains .vtm files, the
        conversion is skipped and the existing directory is reused.

        Returns
        -------
        Path
            Path to the ``VTK/`` directory created inside ``run_dir``.

        Raises
        ------
        RuntimeError
            If foamToVTK fails.
        """
        vtk_dir = self.run_dir / "VTK"

        # Skip if VTK/ already exists with valid .vtm files that are not stale
        if vtk_dir.is_dir():
            existing_vtm = sorted(vtk_dir.glob("*.vtm"))
            if existing_vtm:
                vtm_mtime = existing_vtm[-1].stat().st_mtime
                # Check if any time-step dir is newer than VTK output
                stale = False
                for item in self.run_dir.iterdir():
                    if item.is_dir():
                        try:
                            t = float(item.name)
                            if t > 0 and item.stat().st_mtime > vtm_mtime:
                                stale = True
                                break
                        except ValueError:
                            continue
                if not stale:
                    self._vtk_dir = vtk_dir
                    self._vtm_path = existing_vtm[-1]
                    logger.info("foamToVTK skipped: reusing existing %s", self._vtm_path)
                    return vtk_dir
                else:
                    logger.info("foamToVTK: VTK output is stale, re-converting")

        # Find the latest time-step directory (excluding 0)
        time_dirs: List[Tuple[float, Path]] = []
        for item in self.run_dir.iterdir():
            if item.is_dir():
                try:
                    t = float(item.name)
                    if t > 0:
                        time_dirs.append((t, item))
                except ValueError:
                    continue

        if not time_dirs:
            raise RuntimeError(
                f"No time-step directories found in {self.run_dir}. "
                "Has the solver completed?"
            )

        time_dirs.sort(key=lambda x: x[0])
        latest_time = time_dirs[-1][0]
        latest_str = str(int(latest_time)) if latest_time == int(latest_time) else str(latest_time)
        logger.info("foamToVTK: converting time %s in %s", latest_str, self.run_dir)

        # Defensive: sanitize NaN in field files before foamToVTK
        # (reconstructPar can produce NaN on cyclic/porousBaffle patches
        #  when the solver didn't converge)
        self._sanitize_nan_fields(time_dirs[-1][1])

        cmd = f"{_foam_source_prefix()}foamToVTK -time {latest_str}"
        result = subprocess.run(
            cmd,
            shell=True,
            executable="/bin/bash",
            cwd=str(self.run_dir),
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.error("foamToVTK failed:\n%s", result.stderr)
            raise RuntimeError(f"foamToVTK failed (exit {result.returncode}): {result.stderr[:500]}")

        if not vtk_dir.is_dir():
            raise RuntimeError(
                "foamToVTK completed but VTK/ directory was not created. "
                f"stdout: {result.stdout[:500]}"
            )

        self._vtk_dir = vtk_dir

        # Find the .vtm file
        vtm_files = sorted(vtk_dir.glob("*.vtm"))
        if not vtm_files:
            raise RuntimeError(f"No .vtm files found in {vtk_dir}")
        self._vtm_path = vtm_files[-1]  # take the latest

        logger.info("foamToVTK complete: %s", self._vtm_path)
        return vtk_dir

    def _sanitize_nan_fields(self, time_dir: Path) -> None:
        """Scan OpenFOAM field files for NaN and replace with 0.

        reconstructPar can produce NaN on cyclic/porousBaffle boundary
        patches when the solver did not converge.  This causes foamToVTK
        to fail.  We replace NaN tokens with 0 and record which fields
        were affected so the user can be warned.
        """
        import re
        nan_re = re.compile(r'\bnan\b', re.IGNORECASE)

        for fpath in sorted(time_dir.iterdir()):
            if not fpath.is_file():
                continue
            # Skip non-field files (e.g. uniform/)
            try:
                raw = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if nan_re.search(raw):
                field_name = fpath.name
                cleaned = nan_re.sub("0", raw)
                fpath.write_text(cleaned, encoding="utf-8")
                self.nan_warnings.append(field_name)
                logger.warning(
                    "NaN sanitized in field '%s' (%s) — "
                    "solver likely did not converge for this variable",
                    field_name, fpath,
                )

    # ------------------------------------------------------------------
    # 2.  Load native result data (legacy public method name)
    # ------------------------------------------------------------------

    def load_vtk(self) -> dict:
        """Load result data and return metadata about patches and fields.

        Both serial and decomposed cases are read directly. Existing VTK
        exports are ignored so they cannot hide newer native results.

        Returns
        -------
        dict
            ``{patches: [...], fields: [...], runDir: str}``
        """
        mb, source_info = read_openfoam_case(self.run_dir)
        self._pp = CFDPostProcessor(mb)
        self._vtm_path = None
        return {
            "patches": self._pp.patch_names,
            "fields": list(self._pp.internal.point_data.keys()) if self._pp.internal is not None else [],
            "runDir": str(self.run_dir),
            "vtmPath": None,
            **source_info,
        }

    # ------------------------------------------------------------------
    # 3.  Surface field rendering
    # ------------------------------------------------------------------

    def render_surface_field(
        self,
        field_name: str,
        component: Optional[str] = None,
        clim: Optional[Tuple[float, float]] = None,
        visible_patches: Optional[List[str]] = None,
    ) -> str:
        """Render a surface field and return a base64-encoded PNG image.

        Parameters
        ----------
        field_name : str
            ``'T'`` for temperature, ``'U'`` for velocity.
        component : str or None
            For velocity: ``'x'``, ``'y'``, ``'z'`` for components,
            ``None`` or ``'mag'`` for magnitude.  Ignored for scalars.
        clim : tuple of (min, max) or None
            Colour-bar range.  If None, auto-range is used (data range
            contracted by 10% at each end).
        visible_patches : list of str or None
            Patch names to show.  None means all patches.

        Returns
        -------
        str
            Base64-encoded PNG (no ``data:image/png;base64,`` prefix).
        """
        self._ensure_loaded()
        pp = self._pp

        # Resolve field name for velocity components
        if field_name == "U":
            if component in ("x", "y", "z"):
                resolved = f"U{component}"
            else:
                resolved = "Umag"
        else:
            resolved = field_name

        # Auto-range: contract by 10% at each end
        actual_clim = clim
        if actual_clim is None:
            try:
                fmin, fmax = pp.get_field_range(resolved, source="boundary")
            except KeyError:
                fmin, fmax = pp.get_field_range(resolved, source="all")
            data_range = fmax - fmin
            if data_range > 0:
                actual_clim = (fmin + 0.1 * data_range, fmax - 0.1 * data_range)
            else:
                actual_clim = (fmin, fmax)

        # Choose colormap
        if field_name == "T":
            cmap = "coolwarm"
        elif field_name == "U":
            cmap = "jet" if component is None or component == "mag" else "RdBu_r"
        else:
            cmap = "viridis"

        # Choose title
        title_map = {
            "T": "Temperature [K]",
            "Umag": "Velocity Magnitude [m/s]",
            "Ux": "Velocity Ux [m/s]",
            "Uy": "Velocity Uy [m/s]",
            "Uz": "Velocity Uz [m/s]",
        }
        title = title_map.get(resolved, resolved)

        rc = RenderConfig(camera_position="iso")
        sbc = ScalarBarConfig(title=title)

        b64 = pp._render_scalar_field(
            field_name=resolved,
            clim=actual_clim,
            cmap=cmap,
            default_title=title,
            render_config=rc,
            scalar_bar_config=sbc,
            patches=visible_patches,
            show_internal=False,
        )
        return b64

    # ------------------------------------------------------------------
    # 4.  Streamline rendering
    # ------------------------------------------------------------------

    def render_streamlines(
        self,
        visible_patches: Optional[List[str]] = None,
        n_y_layers: int = 3,
        n_xz_points: int = 9,
    ) -> str:
        """Render velocity streamlines and return a base64-encoded PNG.

        Seed points are auto-generated within the mesh bounding box under
        Y-up convention (Y is vertical):

        * ``n_y_layers`` horizontal layers evenly spaced within the Y range
          (at y_min + h*k/(n_y_layers+1) for k=1..n_y_layers).
        * ``n_xz_points`` seed points per layer in a sqrt(n)xsqrt(n) XZ grid.
          Default 9 gives a 3x3 grid on the horizontal floor plan.

        Parameters
        ----------
        visible_patches : list of str or None
            Patches to show as wireframe context.  None means all.
        n_y_layers : int
            Number of Y (vertical) layers (default 3).
        n_xz_points : int
            Number of XZ seed points per layer (default 9, i.e. 3x3).

        Returns
        -------
        str
            Base64-encoded PNG.
        """
        self._ensure_loaded()
        pp = self._pp
        pv = _get_pyvista()

        # Compute mesh bounds for seed-point generation
        bounds = pp.internal.bounds  # (xmin, xmax, ymin, ymax, zmin, zmax)
        xmin, xmax, ymin, ymax, zmin, zmax = bounds

        # Generate seed points (Y-up: Y is vertical, XZ is floor-plan)
        h = ymax - ymin
        n_side = max(1, int(round(n_xz_points ** 0.5)))
        seed_points = []

        for k in range(1, n_y_layers + 1):
            y = ymin + h * k / (n_y_layers + 1)
            for ix in range(n_side):
                for iz in range(n_side):
                    x = xmin + (xmax - xmin) * (ix + 0.5) / n_side
                    z = zmin + (zmax - zmin) * (iz + 0.5) / n_side
                    seed_points.append([x, y, z])

        seed_arr = np.array(seed_points, dtype=np.float64)
        logger.info(
            "Streamline seeds: %d points (%d Y-layers x %d XZ-grid)",
            len(seed_arr), n_y_layers, n_side * n_side,
        )

        sc = StreamlineConfig(
            seed_points=seed_arr,
            max_steps=2000,
            initial_step_length=0.5,
            max_step_length=1.0,
            tube_radius=0.005,
        )
        rc = RenderConfig(camera_position="iso")

        # Filter boundary patches for wireframe overlay
        patches_for_wireframe = visible_patches

        b64 = pp.render_streamlines(
            streamline_config=sc,
            color_field="speed",
            cmap="plasma",
            render_config=rc,
            show_boundary_wireframe=True,
        )
        return b64

    # ------------------------------------------------------------------
    # 5.  Query helpers
    # ------------------------------------------------------------------

    def get_patch_names(self) -> List[str]:
        """Return list of all patch names from the loaded VTM data."""
        self._ensure_loaded()
        return list(self._pp.patch_names)

    def get_field_range(
        self,
        field_name: str,
        component: Optional[str] = None,
    ) -> Tuple[float, float]:
        """Return (min, max) of a field across all patches.

        Parameters
        ----------
        field_name : str
            ``'T'`` or ``'U'``.
        component : str or None
            For velocity: ``'x'``, ``'y'``, ``'z'``, ``'mag'``, or ``None``
            (defaults to magnitude).

        Returns
        -------
        tuple of (float, float)
        """
        self._ensure_loaded()
        if field_name == "U":
            if component in ("x", "y", "z"):
                resolved = f"U{component}"
            else:
                resolved = "Umag"
        else:
            resolved = field_name
        return self._pp.get_field_range(resolved, source="all")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Make sure VTM data has been loaded, auto-loading if necessary."""
        if self._pp is None:
            self.load_vtk()

    # ------------------------------------------------------------------
    # Class method: find latest run directory from case dir
    # ------------------------------------------------------------------

    @classmethod
    def find_latest_run_dir(cls, case_dir: Path) -> Optional[Path]:
        """Find the latest solve run directory under ``<case_dir>/log/``.

        The run directory is the one with the latest timestamp prefix
        (format: ``YYYY-MM-DD-HH-MM-SS_CaseName``).

        Returns None if no run directories are found.
        """
        log_dir = Path(case_dir) / "log"
        if not log_dir.is_dir():
            return None

        run_dirs = []
        for d in log_dir.iterdir():
            if d.is_dir():
                # Check it looks like a run directory (has constant/polyMesh)
                if (d / "constant" / "polyMesh").is_dir():
                    run_dirs.append(d)

        if not run_dirs:
            return None

        # Sort by name (timestamp prefix gives chronological order)
        run_dirs.sort(key=lambda p: p.name)
        return run_dirs[-1]


# ---------------------------------------------------------------------------
# CLI entry point (for testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Render CFD post-processing images from foamToVTK output."
    )
    parser.add_argument("vtm_path", help="Path to .vtm file")
    parser.add_argument(
        "--field",
        choices=["T", "Umag", "Ux", "Uy", "Uz", "p", "p_rgh", "streamlines", "combined", "slice"],
        default="T",
        help="Field to render (default: T)",
    )
    parser.add_argument("--clim", type=float, nargs=2, metavar=("MIN", "MAX"), help="Color range")
    parser.add_argument("--cmap", default=None, help="Matplotlib colormap")
    parser.add_argument("--camera", default="iso", help="Camera position: iso, xy, xz, yz")
    parser.add_argument("--width", type=int, default=1920, help="Image width")
    parser.add_argument("--height", type=int, default=1080, help="Image height")
    parser.add_argument("--output", "-o", help="Output PNG file path (default: stdout as base64)")
    parser.add_argument(
        "--slice-normal", default="z", help="Slice normal direction (for --field slice)"
    )
    parser.add_argument(
        "--slice-origin", type=float, nargs=3, metavar=("X", "Y", "Z"),
        help="Slice origin point (for --field slice)",
    )
    parser.add_argument(
        "--seed-center", type=float, nargs=3, metavar=("X", "Y", "Z"),
        help="Streamline seed center (for --field streamlines)",
    )
    parser.add_argument(
        "--seed-radius", type=float, default=0.5,
        help="Streamline seed radius (for --field streamlines)",
    )
    parser.add_argument("--list-patches", action="store_true", help="List patches and exit")

    args = parser.parse_args()

    pp = CFDPostProcessor.from_vtm(args.vtm_path)

    if args.list_patches:
        patches = pp.list_patches()
        print(json.dumps(patches, indent=2))
        sys.exit(0)

    rc = RenderConfig(
        window_size=(args.width, args.height),
        camera_position=args.camera,
    )
    clim = tuple(args.clim) if args.clim else None

    if args.field == "T":
        b64 = pp.render_temperature(clim=clim, cmap=args.cmap or "coolwarm", render_config=rc)
    elif args.field == "Umag":
        b64 = pp.render_velocity_magnitude(clim=clim, cmap=args.cmap or "jet", render_config=rc)
    elif args.field in ("Ux", "Uy", "Uz"):
        comp = args.field[1].lower()
        b64 = pp.render_velocity_component(
            component=comp, clim=clim, cmap=args.cmap or "RdBu_r", render_config=rc
        )
    elif args.field in ("p", "p_rgh"):
        b64 = pp.render_pressure(
            field=args.field, clim=clim, cmap=args.cmap or "viridis", render_config=rc
        )
    elif args.field == "slice":
        b64 = pp.render_slice(
            field_name="T",
            normal=args.slice_normal,
            origin=tuple(args.slice_origin) if args.slice_origin else None,
            clim=clim,
            cmap=args.cmap or "coolwarm",
            render_config=rc,
        )
    elif args.field == "streamlines":
        sc = StreamlineConfig(
            seed_center=tuple(args.seed_center) if args.seed_center else None,
            seed_radius=args.seed_radius,
        )
        b64 = pp.render_streamlines(streamline_config=sc, render_config=rc)
    elif args.field == "combined":
        b64 = pp.render_combined(render_config=rc, t_clim=clim)
    else:
        print(f"Unknown field: {args.field}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        png_bytes = base64.b64decode(b64)
        with open(args.output, "wb") as f:
            f.write(png_bytes)
        logger.info("Saved %d bytes to %s", len(png_bytes), args.output)
    else:
        # Output base64 to stdout (for piping to an API)
        print(b64)


# ---------------------------------------------------------------------------
#  CachedPostProcessor — in-memory singleton for binary post-processing API
# ---------------------------------------------------------------------------


def _surface_triangle_cache(mesh: Any) -> dict:
    """Prepare display triangles with sharp edges and aligned point fields.

    A fan from vertex zero can bridge a concave notch and create overlapping
    triangles. VTK's polygon triangulator clips valid ears instead. Split
    display vertices at sharp edges so browser normal averaging cannot round
    equipment corners. Field interpolation precedes triangulation/splitting;
    duplicated display points inherit their original values exactly.
    """
    from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
    from vtkmodules.vtkCommonDataModel import vtkPolyData
    from vtkmodules.vtkFiltersCore import vtkCellDataToPointData, vtkPolyDataNormals, vtkTriangleFilter

    # Each display point remembers the patch point it came from (sharp-edge splitting duplicates points), so a
    # field replaced later (CachedPostProcessor.replace_fields) maps onto the display without re-triangulating.
    tagged = vtkPolyData()
    tagged.ShallowCopy(mesh)
    source = numpy_to_vtk(np.arange(mesh.n_points, dtype=np.int64), deep=True)
    source.SetName(_SOURCE_POINT)
    tagged.GetPointData().AddArray(source)
    field_mesh = tagged
    missing_points = [name for name in mesh.cell_data.keys() if name not in mesh.point_data]
    if missing_points:
        interpolate = vtkCellDataToPointData()
        interpolate.SetInputDataObject(tagged)
        interpolate.SetProcessAllArrays(False)
        interpolate.SetPassCellData(True)
        for name in missing_points:
            interpolate.AddCellDataArray(name)
        interpolate.Update()
        field_mesh = interpolate.GetOutput()
    triangulator = vtkTriangleFilter()
    triangulator.SetInputDataObject(field_mesh)
    triangulator.PassVertsOff()
    triangulator.PassLinesOff()
    normals = vtkPolyDataNormals()
    normals.SetInputConnection(triangulator.GetOutputPort())
    normals.SetFeatureAngle(45.0)
    normals.SplittingOn()
    normals.ComputePointNormalsOn()
    normals.ComputeCellNormalsOff()
    normals.ConsistencyOff()
    normals.AutoOrientNormalsOff()
    normals.Update()
    surface = normals.GetOutput()
    connectivity = vtk_to_numpy(surface.GetPolys().GetConnectivityArray())
    indices = np.ascontiguousarray(connectivity.reshape(-1, 3), dtype=np.uint32)
    src = vtk_to_numpy(surface.GetPointData().GetArray(_SOURCE_POINT)).astype(np.int64)
    surface.GetPointData().RemoveArray(_SOURCE_POINT)
    display = _get_pyvista().PolyData(surface, deep=False)
    return {"points": np.ascontiguousarray(display.points, dtype=np.float32),
            "indices": indices, "tri": display, "src": src}


_SOURCE_POINT = "dcdtuniSourcePoint"


class _PatchPointFields:
    """One patch's display-point fields, as views into the batched surface.

    Quacks like the per-patch PolyData `_surface_triangle_cache` keeps under
    "tri", for the only reader of it, `CachedPostProcessor.get_field`.
    """

    def __init__(self, point_data: Dict[str, np.ndarray], n_points: int) -> None:
        self.point_data = point_data
        self.cell_data: Dict[str, np.ndarray] = {}
        self.n_points = n_points


def _surface_triangle_caches(named: List[Tuple[str, Any]]) -> Dict[str, dict]:
    """`_surface_triangle_cache` for many patches with ONE pass of the filters.

    Base604's 4,336 patches spent 13 s in per-patch VTK calls. The patches are
    appended without merging points (so no interpolation or sharp-edge split
    can cross a patch), tagged with a patch id, triangulated and split once,
    then grouped back by patch with numpy. Per patch the output equals the
    per-patch path's: the same triangles, points and point fields. Falls back
    to the per-patch path when the patches do not carry the same arrays.
    """
    from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
    from vtkmodules.vtkCommonDataModel import vtkPolyData
    from vtkmodules.vtkFiltersCore import (
        vtkAppendPolyData, vtkCellDataToPointData, vtkPolyDataNormals, vtkTriangleFilter,
    )

    live = [(name, mesh) for name, mesh in named if mesh is not None and mesh.n_points]
    if not live:
        return {}
    signature = {(tuple(sorted(m.point_data.keys())), tuple(sorted(m.cell_data.keys()))) for _, m in live}
    if len(signature) != 1 or len(live) == 1:
        return {name: _surface_triangle_cache(mesh) for name, mesh in live}
    append = vtkAppendPolyData()
    for index, (_, mesh) in enumerate(live):
        tagged = vtkPolyData()
        tagged.ShallowCopy(mesh)
        tag = numpy_to_vtk(np.full(mesh.n_cells, index, dtype=np.int32), deep=True)
        tag.SetName("dcdtuniPatchId")
        tagged.GetCellData().AddArray(tag)
        source = numpy_to_vtk(np.arange(mesh.n_points, dtype=np.int64), deep=True)
        source.SetName(_SOURCE_POINT)                  # see _surface_triangle_cache
        tagged.GetPointData().AddArray(source)
        append.AddInputData(tagged)
    append.Update()
    combined = append.GetOutput()
    field_mesh = combined
    first = live[0][1]
    missing_points = [name for name in first.cell_data.keys() if name not in first.point_data]
    if missing_points:
        interpolate = vtkCellDataToPointData()
        interpolate.SetInputDataObject(combined)
        interpolate.SetProcessAllArrays(False)
        interpolate.SetPassCellData(True)
        for name in missing_points:
            interpolate.AddCellDataArray(name)
        interpolate.Update()
        field_mesh = interpolate.GetOutput()
    triangulator = vtkTriangleFilter()
    triangulator.SetInputDataObject(field_mesh)
    triangulator.PassVertsOff()
    triangulator.PassLinesOff()
    normals = vtkPolyDataNormals()
    normals.SetInputConnection(triangulator.GetOutputPort())
    normals.SetFeatureAngle(45.0)
    normals.SplittingOn()
    normals.ComputePointNormalsOn()
    normals.ComputeCellNormalsOff()
    normals.ConsistencyOff()
    normals.AutoOrientNormalsOff()
    normals.Update()
    surface = normals.GetOutput()
    tris = vtk_to_numpy(surface.GetPolys().GetConnectivityArray()).reshape(-1, 3).astype(np.int64)
    tri_patch = vtk_to_numpy(surface.GetCellData().GetArray("dcdtuniPatchId")).astype(np.int64)
    points = vtk_to_numpy(surface.GetPoints().GetData())
    n_points = len(points)
    # Every display point belongs to one patch, the patch of the triangles
    # using it; a point no triangle uses is not drawn and is dropped.
    point_patch = np.full(n_points, -1, dtype=np.int64)
    point_patch[tris.ravel()] = np.repeat(tri_patch, 3)
    order = np.lexsort((np.arange(n_points), point_patch))
    used = order[point_patch[order] >= 0]
    new_index = np.full(n_points, -1, dtype=np.int64)
    new_index[used] = np.arange(len(used))
    patch_of_used = point_patch[used]
    point_bounds = np.searchsorted(patch_of_used, np.arange(len(live) + 1))
    tri_order = np.argsort(tri_patch, kind="stable")
    tri_bounds = np.searchsorted(tri_patch[tri_order], np.arange(len(live) + 1))
    point_fields = {}
    data = surface.GetPointData()
    for k in range(data.GetNumberOfArrays()):
        name = data.GetArrayName(k)
        if name and name != "Normals":
            point_fields[name] = vtk_to_numpy(data.GetArray(k))[used]
    src = point_fields.pop(_SOURCE_POINT).astype(np.int64)
    pts = np.ascontiguousarray(points[used], dtype=np.float32)
    out = {}
    for index, (name, _) in enumerate(live):
        p0, p1 = point_bounds[index], point_bounds[index + 1]
        t = tris[tri_order[tri_bounds[index]:tri_bounds[index + 1]]]
        out[name] = {
            "points": pts[p0:p1],
            "indices": np.ascontiguousarray(new_index[t] - p0, dtype=np.uint32),
            "tri": _PatchPointFields({k: v[p0:p1] for k, v in point_fields.items()}, int(p1 - p0)),
            "src": src[p0:p1],
        }
    return out


def _volume_index(internal: Any) -> Tuple[np.ndarray, Any, np.ndarray]:
    """CachedPostProcessor._volume_centres for an internal mesh: cell centres,
    a KD-tree over them and each cell's spacing to its nearest neighbour."""
    from scipy.spatial import cKDTree
    # mean of each cell's points, straight from the connectivity:
    # vtkCellCenters walks every polyhedron (52 s on 6.69 M cells)
    conn = np.asarray(internal.cell_connectivity)
    offs = np.asarray(internal.offset)
    pts = np.asarray(internal.points, dtype=np.float64)
    counts = np.diff(offs)
    sums = np.add.reduceat(pts[conn], offs[:-1], axis=0)
    centres = sums / np.maximum(counts, 1)[:, None]
    tree = cKDTree(centres)
    spacing = tree.query(centres, k=2, workers=-1)[0][:, 1]
    return centres, tree, spacing


class _ResultPrefetch:
    """One background read of a solved result, handed to the next matching init.

    Opening post-processing on GTC spent 25 s in the OpenFOAM reader after the
    click. The server starts this read when a case is selected; the init that
    asks for the same run and time takes the result (waiting if the read is
    still going) instead of reading again. It takes no post-processing lock, so
    selecting another case is never blocked by it; a newer start replaces it.
    The native input bytes are pinned around the read and checked again when
    the result is taken, so a changed result is read afresh.
    """

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._key: Optional[Tuple[str, float]] = None
        self._done = None
        self._value = None

    @staticmethod
    def _key_for(run_dir: Path, time_value: float) -> Tuple[str, float]:
        return str(Path(run_dir).resolve()), float(time_value)

    def start(self, run_dir: Path, time_value: float) -> bool:
        import threading

        key = self._key_for(run_dir, time_value)
        with self._lock:
            if self._key == key:
                return False
            done = threading.Event()
            self._key, self._done, self._value = key, done, None

        def work() -> None:
            value = None
            started = time.perf_counter()
            try:
                from .native_numeric_input import capture_native_inputs
                run = Path(key[0])
                try:
                    before = capture_native_inputs(run, key[1])
                except (ValueError, OSError):
                    before = None
                multiblock, info = read_openfoam_case(run, key[1])
                after = capture_native_inputs(run, key[1]) if before is not None else None
                if before == after:
                    # get_mesh's triangulation as well: Base604's 6.9 M
                    # triangles took 8-15 s after the click.
                    boundary = multiblock["boundary"] if "boundary" in multiblock.keys() else None
                    triangles = {} if boundary is None else _surface_triangle_caches(
                        [(name, boundary[index]) for index, name in enumerate(boundary.keys())])
                    # And the first volume render's KD-tree (Base604: 24 s).
                    internal = multiblock["internalMesh"]
                    volume = (_volume_index(internal) if internal is not None
                              and internal.n_cells > CachedPostProcessor._VOLUME_PROBE_MAX_CELLS else None)
                    value = (before, multiblock, info, triangles, volume)
                print(f"[PP-PREFETCH] {run.name} t={key[1]:g}: {time.perf_counter() - started:.1f}s", flush=True)
            except Exception as exc:  # noqa: BLE001 - a failed prefetch only means the init reads itself
                print(f"[PP-PREFETCH] {key[0]} skipped: {exc}", flush=True)
            finally:
                with self._lock:
                    if self._key == key:
                        self._value = value
                done.set()

        threading.Thread(target=work, name="pp-prefetch", daemon=True).start()
        return True

    def take(self, run_dir: Path, time_value: float, timeout: float):
        """(native snapshot, multiblock, source info, boundary triangle caches,
        volume index or None) for this run/time, else None."""
        key = self._key_for(run_dir, time_value)
        with self._lock:
            if self._key != key:
                return None
            done = self._done
        if not done.wait(max(0.0, timeout)):
            return None
        with self._lock:
            if self._key != key:
                return None
            value, self._key, self._done, self._value = self._value, None, None, None
        return value


RESULT_PREFETCH = _ResultPrefetch()


class PostprocessRevisionError(RuntimeError):
    """A request does not name the currently loaded postprocess snapshot."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _display_range(values: np.ndarray):
    """The 2 % / 98 % samples, chosen as the editor's percentileRange chooses them (floor / ceil of the index),
    so the server can hand the volume its default colour limits."""
    n = int(values.size)
    if not n:
        return None
    lo, hi = int(np.floor(0.02 * (n - 1))), int(np.ceil(0.98 * (n - 1)))
    part = np.partition(values, (lo, hi))
    return [float(part[lo]), float(part[hi])]


def _result_roi(run_dir: Path) -> Optional[dict]:
    """The region of an AI result that holds values only inside a box (scano_prediction.json "roi"), or None."""
    rec = Path(run_dir) / "scano_prediction.json"
    try:
        roi = json.loads(rec.read_text()).get("roi") if rec.is_file() else None
    except (OSError, ValueError):
        return None
    return roi if isinstance(roi, dict) else None


class CachedPostProcessor:
    """Singleton that holds result data in memory with mesh/field separation.

    Lifecycle:
      1.  init(run_dir) — reads native OpenFOAM, or an explicitly selected VTM file
      2.  get_mesh()    — triangulates boundary patches, caches geometry
      3.  get_field(name) — extracts scalar array, caches per field
      4.  invalidate()  — clears caches (called when switching run dir)
    """

    _instance: Optional["CachedPostProcessor"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._state_lock = threading.RLock()
        self._revision: Optional[str] = None
        self._run_id: Optional[str] = None
        self._ai_worker_revision: Optional[str] = None
        self._case_version: Optional[int] = None
        self.scope_case_dir: Optional[Path] = None
        self.run_dir: Optional[Path] = None
        self.multi_block: Optional[Any] = None
        self._internal: Optional[Any] = None
        self._boundary: Optional[Any] = None
        self._tri_cache: Dict[str, dict] = {}
        self._mesh_binary: Optional[bytes] = None
        self._mesh_meta: Optional[List[dict]] = None
        self._field_cache: Dict[str, bytes] = {}
        self._field_info: Dict[str, dict] = {}
        self._zones_cache: Optional[bytes] = None
        self._kdtree_cache: Optional[Any] = None  # cached cKDTree for volume resampling
        self._zones_meta: Optional[List[dict]] = None
        self._available_fields: List[dict] = []
        self._vtm_path: Optional[Path] = None
        self._source_info: dict = {}
        self._requested_time_value: Optional[float] = None
        self._native_input_snapshot: Optional[dict] = None
        self._numeric_product_store = None

    @classmethod
    def get_instance(cls) -> "CachedPostProcessor":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _identity(self) -> dict:
        return {
            "runId": self._run_id,
            "timeValue": self._source_info.get("timeValue"),
            "revision": self._revision,
        }

    @contextmanager
    def state_transition(self):
        """Serialize a selected-case transition with cache loads and pinned reads.

        Callers which also need the server case lock must acquire this scope
        first. The existing RLock allows init/invalidate/require_revision to
        re-enter it while publishing one atomic case/cache transition.
        """
        with self._state_lock:
            yield

    @contextmanager
    def require_revision(self, revision: Optional[str], case_version: Optional[int] = None):
        """Pin one complete snapshot while an HTTP data request is built."""
        if not revision:
            raise PostprocessRevisionError("pp_revision_required", 428)
        with self._state_lock:
            if (self.multi_block is None or revision != self._revision
                    or (case_version is not None and case_version != self._case_version)):
                raise PostprocessRevisionError("pp_revision_stale", 409)
            yield self._identity()

    def bind_ai_worker(self, revision: str) -> None:
        with self.require_revision(revision):
            self._ai_worker_revision = revision

    def require_ai_worker(self, revision: str) -> None:
        with self.require_revision(revision):
            if self._ai_worker_revision != revision:
                raise PostprocessRevisionError("pp_ai_worker_stale", 409)

    def current_identity(self) -> dict:
        with self._state_lock:
            if self.multi_block is None:
                raise PostprocessRevisionError("pp_revision_stale", 409)
            return self._identity()

    def _parallel_read_vtm(self, vtm_path: Path) -> Tuple[Optional[Any], Optional[Any]]:
        """Read VTM sub-files in parallel using ThreadPoolExecutor.

        Instead of one slow pv.read(vtm), read internal.vtu and individual
        boundary .vtp files concurrently (8 threads). VTK I/O releases the
        GIL so threads give real parallelism.

        Also pre-triangulates patches while they're hot in memory and stores
        results in ``self._tri_cache`` so ``get_mesh()`` can skip VTK access.

        Returns:
            (internal_mesh, boundary_multiblock) or falls back to serial read.
        """
        pv = _get_pyvista()
        vtm_dir = vtm_path.parent
        ts_dir_name = vtm_path.stem  # e.g. "2026-03-22-07-03-03_GTC_1000"
        ts_dir = vtm_dir / ts_dir_name
        internal_vtu = ts_dir / "internal.vtu"
        boundary_dir = ts_dir / "boundary"

        # Fallback: if sub-files don't exist, use serial read
        if not internal_vtu.exists() or not boundary_dir.exists():
            logger.info("[PP] Parallel read unavailable, falling back to serial pv.read")
            mb = pv.read(str(vtm_path))
            internal, boundary = None, None
            for i, key in enumerate(mb.keys()):
                block = mb[i]
                if block is None:
                    continue
                if key in ("internal", "internalMesh"):
                    internal = block
                elif key == "boundary":
                    boundary = block
            return internal, boundary

        patch_files = sorted(boundary_dir.glob("*.vtp"))

        # Read all sub-files in parallel
        with ThreadPoolExecutor(max_workers=8) as pool:
            fut_internal = pool.submit(pv.read, str(internal_vtu))
            fut_patches = [(pf.stem, pool.submit(pv.read, str(pf))) for pf in patch_files]

            internal = fut_internal.result()
            patch_results = [(name, fut.result()) for name, fut in fut_patches]

        # Assemble boundary MultiBlock (preserving patch names for downstream)
        boundary = pv.MultiBlock()
        for name, mesh in patch_results:
            boundary.append(mesh, name)

        # Pre-triangulate while patches are hot in memory (avoids VTK lazy-load
        # penalty when get_mesh() accesses them later via self._boundary)
        for name, mesh in patch_results:
            if mesh is None or mesh.n_points == 0:
                continue
            self._tri_cache[name] = _surface_triangle_cache(mesh)

        return internal, boundary

    def init(
        self, run_dir: Path, force: bool = False, timeout: float = 3600.0,
        time_value: Optional[float] = None,
        case_version: Optional[int] = None,
        case_dir: Optional[Path] = None,
    ) -> dict:
        """Load result fields for the given run directory. Skip if already loaded.

        Args:
            timeout: Maximum seconds for the entire init. Steps that complete
                     before the deadline are kept; the first step to exceed
                     it triggers an early return with partial timing data.
        """
        run_dir = Path(run_dir).resolve()
        scoped_case = Path(case_dir).resolve() if case_dir is not None else None
        if scoped_case is not None and not run_dir.is_relative_to(scoped_case):
            raise ValueError("Postprocess run must be inside its bound case directory")
        with self._state_lock:
            if (not force and self.run_dir == run_dir and self.multi_block is not None
                    and self._requested_time_value == time_value
                    and self._case_version == case_version
                    and self.scope_case_dir == scoped_case
                    and not getattr(self, "_fields_replaced", False)):   # a real-time update is not the file
                return {
                    "success": True,
                    "message": f"Already loaded: {len(self._mesh_meta or [])} patches",
                    "cached": True,
                    **self._source_info,
                    **self._identity(),
                }
            self._fields_replaced = False
            is_vtm = run_dir.is_file() and run_dir.suffix.lower() == ".vtm"
            if is_vtm and time_value is not None:
                raise ValueError("An explicit OpenFOAM time cannot be selected for a VTM file")
            # Validate the requested time before replacing a previously valid
            # in-memory frame. A stale UI selection must not destroy that frame.
            selected_layout = None
            if time_value is not None and not is_vtm:
                selected_layout = _openfoam_result_layout(run_dir, time_value)
                if time_value not in selected_layout[1]:
                    raise ValueError(
                        f"Time {time_value} is not available on every mesh: {selected_layout[1]}"
                    )
            self.invalidate()
            self.run_dir = run_dir
            self._requested_time_value = time_value
            self._case_version = case_version
            self.scope_case_dir = scoped_case
            timing: Dict[str, float] = {}
            t0_total = time.perf_counter()
            deadline = t0_total + timeout

            def _tlog(msg: str) -> None:
                print(msg, flush=True)
                logger.info(msg)

            def _check_timeout(step_name: str) -> Optional[dict]:
                """Return an error dict if deadline exceeded, else None."""
                elapsed = time.perf_counter() - t0_total
                if elapsed > timeout:
                    timing["total"] = round(elapsed, 2)
                    timing["timed_out_at"] = step_name
                    _tlog(f"[PP-TIMING] TIMEOUT after {elapsed:.1f}s at step '{step_name}' (limit={timeout}s)")
                    self.invalidate()
                    return {
                        "success": False,
                        "error": f"PP init timed out after {elapsed:.0f}s at step '{step_name}' (limit={timeout:.0f}s). "
                                 f"This case may be too large for the current timeout. "
                                 f"Completed steps: {list(timing.keys())}",
                        "timing": timing,
                        "timed_out": True,
                    }
                return None

            _tlog(f"[PP-TIMING] Starting init for {run_dir.name} (timeout={timeout}s)...")
            source_info = {"sourceFormat": "vtm", "decomposed": False, "timeValue": None}
            # VTM is an explicit file import only. Directory-based GUI loads
            # always use native fields, never cached exports or foamToVTK.
            if not is_vtm:
                meshes, times = selected_layout or _openfoam_result_layout(run_dir)
                solved_times = [t for t in times if t > 0]
                if time_value is None and not solved_times:
                    raise RuntimeError(f"No solved time available on every mesh in {run_dir}")
                if time_value is not None and time_value not in times:
                    raise ValueError(f"Time {time_value} is not available on every mesh: {times}")
                selected_time = time_value if time_value is not None else solved_times[-1]
                t0 = time.perf_counter()
                # Pin actual native bytes around the EXISTING reader. An import
                # without complete T/U dimensions can still be inspected, but
                # cannot obtain a signed accepted numeric display product.
                from .native_numeric_input import capture_native_inputs
                try:
                    native_before = capture_native_inputs(run_dir, selected_time)
                except (ValueError, OSError):
                    native_before = None
                # A read the server started when the case was selected, if it
                # is of this run/time and of these very bytes.
                prefetched = RESULT_PREFETCH.take(run_dir, selected_time, deadline - time.perf_counter())
                if prefetched is not None and prefetched[0] != native_before:
                    prefetched = None
                prefetched_triangles: Dict[str, dict] = {}
                prefetched_volume = None
                if prefetched is not None:
                    _, mb, source_info, prefetched_triangles, prefetched_volume = prefetched
                    source_info = {**source_info, "prefetched": True}
                    _tlog("[PP-TIMING] openfoam_read: taken from the background prefetch")
                else:
                    mb, source_info = read_openfoam_case(run_dir, selected_time)
                if native_before is not None:
                    native_after = capture_native_inputs(run_dir, selected_time)
                    if native_before != native_after:
                        self.invalidate()
                        raise ValueError("Native result bytes changed during postprocess read")
                    self._native_input_snapshot = native_after
                self._internal = mb["internalMesh"]
                self._boundary = mb["boundary"] if "boundary" in mb.keys() else None
                self._roi = _result_roi(run_dir)
                if self._roi is not None:
                    t_roi = time.perf_counter()
                    self._clip_to_roi()
                    timing["roi_clip"] = round(time.perf_counter() - t_roi, 2)
                    _tlog(f"[PP-TIMING] roi_clip: {timing['roi_clip']}s, {self._internal.n_cells} cells kept")
                else:
                    # The prefetch's _surface_triangle_caches of this very boundary,
                    # in its patch order: what get_mesh would compute.
                    self._tri_cache.update(prefetched_triangles)
                    if prefetched_volume is not None and self._internal is not None:
                        self._volume_index = ((id(self._internal), int(self._internal.n_cells)), prefetched_volume)
                timing["openfoam_read"] = round(time.perf_counter() - t0, 2)
                # Keep this timing key for clients comparing the old pipeline.
                timing["foamToVTK"] = 0.0
                read_step = "openfoam_read"
            else:
                self._vtm_path = run_dir
                timing["foamToVTK"] = 0.0
                t0 = time.perf_counter()
                self._internal, self._boundary = self._parallel_read_vtm(self._vtm_path)
                timing["pv_read_parallel"] = round(time.perf_counter() - t0, 2)
                read_step = "pv_read_parallel"

            _tlog(f"[PP-TIMING] {read_step}: {timing[read_step]}s")
            if self._internal is not None:
                _tlog(f"[PP-TIMING]   internal: {self._internal.n_cells} cells, {self._internal.n_points} points")
            if self._boundary is not None:
                _tlog(f"[PP-TIMING]   boundary: {self._boundary.n_blocks} patches")
            err = _check_timeout(read_step)
            if err:
                return err

            # --- Step 5: velocity preprocessing ---
            t0 = time.perf_counter()
            if self._internal is not None and "U" in self._internal.point_data:
                U = self._internal.point_data["U"]
                # np.linalg.norm(U, axis=1), bit for bit, in a third of its time.
                squares = U * U
                self._internal.point_data["Umag"] = np.sqrt(squares[:, 0] + squares[:, 1] + squares[:, 2])
                self._internal.point_data["Ux"] = U[:, 0]
                self._internal.point_data["Uy"] = U[:, 1]
                self._internal.point_data["Uz"] = U[:, 2]
            timing["velocity_preprocess"] = round(time.perf_counter() - t0, 2)
            _tlog(f"[PP-TIMING] velocity_preprocess: {timing['velocity_preprocess']}s")
            err = _check_timeout("velocity_preprocess")
            if err:
                return err

            # --- Step 6: field discovery ---
            t0 = time.perf_counter()
            self._available_fields = self._discover_fields()
            timing["field_discovery"] = round(time.perf_counter() - t0, 2)
            _tlog(f"[PP-TIMING] field_discovery: {timing['field_discovery']}s")

            timing["total"] = round(time.perf_counter() - t0_total, 2)
            _tlog(f"[PP-TIMING] TOTAL: {timing['total']}s")

            n_patches = self._boundary.n_blocks if self._boundary else 0
            n_cells = self._internal.n_cells if self._internal else 0
            n_fields = len(self._available_fields)
            result: dict = {
                "success": True,
                "message": f"{source_info['sourceFormat']} loaded, {n_patches} patches, {n_fields} fields, {n_cells} cells",
                **source_info,
                "cached": False,
                "timing": timing,
                "n_cells": n_cells,
            }
            roi = getattr(self, "_roi", None)
            if roi is not None:
                result["roi"] = roi
            self._source_info = {**source_info, "n_cells": n_cells, **({"roi": roi} if roi is not None else {})}
            self._run_id = hashlib.sha256(str(run_dir).encode("utf-8")).hexdigest()
            self._revision = uuid.uuid4().hex
            self.multi_block = True  # Mark ready only after every init step succeeds.
            return {**result, **self._identity()}

    def capture_numeric_product(self, kind, request, payload, metadata) -> dict:
        """Receipt for actual numeric response bytes under the common revision.

        No producer algorithm lives here. A route calls this after the ordinary
        mesh/field/slice/stream/volume computation, while require_revision is held.
        """
        from .native_numeric_input import capture_native_inputs
        from backend.aircooling.runner.numeric_product_receipts import NumericProductStore
        with self._state_lock:
            if self.multi_block is None or self._native_input_snapshot is None:
                raise ValueError("No pinned native input for this numeric product")
            current = capture_native_inputs(self.run_dir, self._identity()["timeValue"])
            if current != self._native_input_snapshot:
                raise ValueError("Native result changed after the common postprocess read")
            if self._numeric_product_store is None:
                self._numeric_product_store = NumericProductStore(self._identity(), current["sha256"])
            return self._numeric_product_store.capture(
                kind, request, payload, metadata, session=self._identity(),
                native_input_sha256=current["sha256"],
            )

    def _discover_fields(self) -> List[dict]:
        """Scan internal mesh and boundary patches for available fields."""
        fields: List[dict] = []
        seen: set = set()
        source = self._internal if self._internal is not None else None
        if source is None and self._boundary is not None and self._boundary.n_blocks > 0:
            source = self._boundary[0]
        if source is None:
            return fields

        data_sources = []
        if hasattr(source, "point_data"):
            data_sources.append(source.point_data)
        if hasattr(source, "cell_data"):
            data_sources.append(source.cell_data)

        for data_store in data_sources:
            for name in data_store.keys():
                if name in seen:
                    continue
                arr = data_store[name]
                if arr.ndim == 1:
                    fields.append({"name": name, "type": "scalar", "label": name})
                    seen.add(name)
                elif arr.ndim == 2 and arr.shape[1] == 3:
                    fields.append({
                        "name": name, "type": "vector", "label": name,
                        "components": [f"{name}x", f"{name}y", f"{name}z"],
                    })
                    seen.add(name)
                    for suffix in ("mag", "x", "y", "z"):
                        derived = f"{name}{suffix}"
                        if derived not in seen:
                            seen.add(derived)
        return fields

    def get_mesh(self) -> Tuple[bytes, List[dict]]:
        """Return binary geometry + patch metadata. Cached after first call."""
        if self._mesh_binary is not None and self._mesh_meta is not None:
            return self._mesh_binary, self._mesh_meta

        if self._boundary is None:
            return b"", []

        t0_mesh = time.perf_counter()

        # If _tri_cache was pre-populated by _parallel_read_vtm, use it directly
        # (avoids VTK lazy-load penalty from accessing self._boundary blocks)
        if self._tri_cache:
            patches = [{"name": n, "points": c["points"], "indices": c["indices"]}
                       for n, c in self._tri_cache.items()]
        else:
            # Triangulate the boundary MultiBlock, every patch in one pass.
            named = [(name, self._boundary[i]) for i, name in enumerate(self._boundary.keys())]
            self._tri_cache.update(_surface_triangle_caches(named))
            patches = [{"name": name, "points": self._tri_cache[name]["points"],
                        "indices": self._tri_cache[name]["indices"]}
                       for name, _ in named if name in self._tri_cache]

        dt_tri = time.perf_counter() - t0_mesh

        t0_pack = time.perf_counter()
        self._mesh_binary, self._mesh_meta = self.pack_mesh_binary(patches)
        for m in self._mesh_meta:
            m["defaultVisible"] = _patch_default_visible(m["name"])
        dt_pack = time.perf_counter() - t0_pack
        dt_total = time.perf_counter() - t0_mesh
        print(f"[PP-TIMING] get_mesh: {dt_total:.2f}s (tri={dt_tri:.2f}s, pack={dt_pack:.2f}s, {len(patches)} patches)", flush=True)
        return self._mesh_binary, self._mesh_meta

    def _resolve_field_name(self, field_name: str) -> Tuple[str, bool, int]:
        """Resolve user-facing field name to internal name + component info.

        Returns (resolved_name, is_component, component_idx).
        """
        resolved = field_name
        is_component = False
        component_idx = -1

        for vec_field in self._available_fields:
            if vec_field["type"] != "vector":
                continue
            base = vec_field["name"]
            if field_name == f"{base}mag":
                # Always use _magnitude convention — boundary patches only
                # have the raw vector ("U"), not precomputed "Umag", so
                # _extract_scalars_from_mesh must compute magnitude on-the-fly.
                resolved = f"{base}_magnitude"
                break
            for ci, suffix in enumerate(("x", "y", "z")):
                if field_name == f"{base}{suffix}":
                    resolved = base
                    is_component = True
                    component_idx = ci
                    break

        return resolved, is_component, component_idx

    @staticmethod
    def _extract_scalars_from_mesh(mesh, resolved: str, is_component: bool, component_idx: int) -> np.ndarray:
        """Extract scalar values from a single mesh for a resolved field name."""
        if resolved in mesh.point_data:
            arr = mesh.point_data[resolved]
        elif resolved in mesh.cell_data:
            mesh_pd = mesh.cell_data_to_point_data()
            arr = mesh_pd.point_data[resolved]
        elif resolved.endswith("_magnitude"):
            base_name = resolved.replace("_magnitude", "")
            if base_name in mesh.point_data:
                arr = np.linalg.norm(mesh.point_data[base_name], axis=1)
            else:
                arr = np.zeros(mesh.n_points, dtype=np.float32)
        else:
            arr = np.zeros(mesh.n_points, dtype=np.float32)

        if is_component and arr.ndim == 2:
            arr = arr[:, component_idx]
        elif arr.ndim == 2:
            arr = np.linalg.norm(arr, axis=1)

        return np.asarray(arr, dtype=np.float32)

    def get_field(self, field_name: str) -> Tuple[bytes, dict]:
        """Return binary scalars + field info. Cached per field."""
        if field_name in self._field_cache:
            return self._field_cache[field_name], self._field_info[field_name]

        if self._boundary is None:
            return b"", {"min": 0, "max": 0, "nComponents": 1, "nPoints": 0}

        if not self._tri_cache:
            self.get_mesh()

        resolved, is_component, component_idx = self._resolve_field_name(field_name)

        all_scalars: List[np.ndarray] = []
        for name in [m["name"] for m in (self._mesh_meta or [])]:
            cached = self._tri_cache.get(name)
            if cached is None:
                continue
            tri = cached["tri"]
            all_scalars.append(self._extract_scalars_from_mesh(tri, resolved, is_component, component_idx))

        if all_scalars:
            combined = np.concatenate(all_scalars)
        else:
            combined = np.array([], dtype=np.float32)

        binary = self.pack_field_binary(combined, n_components=1)
        info = {
            "min": float(combined.min()) if len(combined) > 0 else 0.0,
            "max": float(combined.max()) if len(combined) > 0 else 0.0,
            "nComponents": 1,
            "nPoints": len(combined),
        }
        self._field_cache[field_name] = binary
        self._field_info[field_name] = info
        return binary, info

    def get_available_fields(self) -> List[dict]:
        """Return list of available field descriptors."""
        return self._available_fields

    def get_internal_mesh(self) -> Optional[Any]:
        """Return internal mesh for streamline computation."""
        return self._internal

    def get_slice(
        self,
        normal: Tuple[float, float, float],
        origin: Tuple[float, float, float],
        field_name: str,
    ) -> Tuple[bytes, dict]:
        """Slice the internal mesh with a plane and return binary geometry + field.

        Returns:
            (binary, info) where binary layout is:
              [uint32 headerLen][JSON header][float32 positions][uint32 indices][float32 scalars]
            and info is the parsed header dict.
        """
        internal = self._internal  # snapshot for thread safety
        if internal is None:
            return b"", {"error": "No internal mesh loaded"}

        pv = _get_pyvista()

        sliced = internal.slice(normal=normal, origin=origin)
        if sliced is None or sliced.n_points == 0:
            header = json.dumps({"nPoints": 0, "nTriangles": 0, "scalarRange": [0, 0], "field": field_name}).encode("utf-8")
            return struct.pack("<I", len(header)) + header, {"nPoints": 0, "nTriangles": 0}

        tri = sliced.triangulate()
        points = np.ascontiguousarray(tri.points, dtype=np.float32)

        # Extract triangle indices
        if hasattr(tri, "regular_faces") and tri.regular_faces is not None and len(tri.regular_faces) > 0:
            indices = np.ascontiguousarray(tri.regular_faces, dtype=np.uint32)
        else:
            faces_arr = tri.faces
            tris_list = []
            fidx = 0
            while fidx < len(faces_arr):
                n = int(faces_arr[fidx])
                for k in range(1, n - 1):
                    tris_list.append([int(faces_arr[fidx + 1]), int(faces_arr[fidx + 1 + k]), int(faces_arr[fidx + 2 + k])])
                fidx += 1 + n
            indices = np.array(tris_list, dtype=np.uint32) if tris_list else np.empty((0, 3), dtype=np.uint32)

        resolved, is_component, component_idx = self._resolve_field_name(field_name)
        scalars = self._extract_scalars_from_mesh(tri, resolved, is_component, component_idx).ravel()
        smin = float(scalars.min()) if len(scalars) > 0 else 0.0
        smax = float(scalars.max()) if len(scalars) > 0 else 0.0

        n_tris = len(indices)
        header_dict = {
            "nPoints": int(tri.n_points),
            "nTriangles": n_tris,
            "scalarRange": [smin, smax],
            "field": field_name,
        }
        header = json.dumps(header_dict).encode("utf-8")
        # Pad header to 4-byte alignment so typed arrays after it are aligned.
        # headerLen field stores the padded length; JSON parsing ignores trailing NULs.
        pad_len = (4 - (len(header) % 4)) % 4
        header_padded = header + b"\x00" * pad_len

        payload = (
            struct.pack("<I", len(header_padded))
            + header_padded
            + points.tobytes()
            + indices.tobytes()
            + scalars.tobytes()
        )
        return payload, header_dict

    def get_volume(self, field_name: str, resolution: int = 128) -> Tuple[bytes, dict]:
        """Resample internal mesh onto a regular 3D grid and return binary volume data.

        Uses PyVista's grid.sample() (wraps vtkProbeFilter) for both interpolation
        and inside/outside masking.  This guarantees correct memory layout (VTK
        X-fastest == Three.js DataTexture3D) and accurate cell-boundary-aware masking.

        Returns:
            (binary, info) where binary layout is:
              [uint32 headerLen][JSON header padded to 4-byte][float32 NxNxN scalars][uint8 NxNxN validity]
            and info is the parsed header dict.
        """
        t0_total = time.perf_counter()
        internal = self._internal
        if internal is None:
            return b"", {"error": "No internal mesh loaded"}

        pv = _get_pyvista()
        bounds = internal.bounds  # (xmin,xmax, ymin,ymax, zmin,zmax)
        N = max(16, min(resolution, 512))  # clamp to sane range
        fast = self._volume_by_cell_centres(field_name, N, bounds)
        if fast is not None:
            return fast

        origin = [bounds[0], bounds[2], bounds[4]]
        spacing = [
            (bounds[1] - bounds[0]) / max(N - 1, 1),
            (bounds[3] - bounds[2]) / max(N - 1, 1),
            (bounds[5] - bounds[4]) / max(N - 1, 1),
        ]

        # --- Step 1: resample internal mesh onto regular grid via vtkProbeFilter ---
        t0 = time.perf_counter()
        grid = pv.ImageData(dimensions=(N, N, N), spacing=spacing, origin=origin)
        resampled = grid.sample(internal)
        dt_resample = time.perf_counter() - t0

        # --- Step 2: extract scalars from the resampled grid ---
        t0 = time.perf_counter()
        SENTINEL = -9999.0
        resolved, is_component, component_idx = self._resolve_field_name(field_name)
        scalars = self._extract_scalars_from_mesh(resampled, resolved, is_component, component_idx)
        scalars = scalars.ravel().astype(np.float32)

        # Preserve vtkProbeFilter's original occupancy independently of scalar
        # values. A legal physical value can equal the legacy sentinel.
        mask = np.asarray(resampled["vtkValidPointMask"]).ravel()
        if mask.size != N**3 or not np.all((mask == 0) | (mask == 1)):
            raise ValueError("VTK volume validity mask is incomplete or not binary")
        mask = mask.astype(np.uint8)
        valid = mask == 1
        if not np.all(np.isfinite(scalars[valid])):
            raise ValueError("VTK volume has nonfinite valid physical samples")
        scalars[~valid] = SENTINEL  # legacy wire consumers; validity is authority
        smin = float(scalars[valid].min()) if valid.any() else 0.0
        smax = float(scalars[valid].max()) if valid.any() else 0.0
        dt_extract = time.perf_counter() - t0

        # --- Step 3: pack ---
        t0 = time.perf_counter()
        header_dict = {
            "dimensions": [N, N, N],
            "origin": origin,
            "spacing": spacing,
            "scalarRange": [smin, smax],
            "sentinel": SENTINEL,
            "field": field_name,
            "validityMask": {"encoding": "uint8-01", "byteCount": N**3},
        }
        header_bytes = json.dumps(header_dict).encode("utf-8")
        # Pad to 4-byte alignment
        pad = (4 - len(header_bytes) % 4) % 4
        header_bytes += b"\x00" * pad

        payload = struct.pack("<I", len(header_bytes)) + header_bytes + scalars.tobytes() + mask.tobytes()
        dt_pack = time.perf_counter() - t0

        dt_total = time.perf_counter() - t0_total
        print(f"[PP-TIMING] get_volume: {dt_total:.2f}s "
              f"(resample={dt_resample:.2f}s, extract={dt_extract:.2f}s, "
              f"pack={dt_pack:.2f}s, N={N}, cells={internal.n_cells}, "
              f"field={field_name})", flush=True)

        return payload, header_dict

    # The volume is sampled from the cell centres at every mesh size (DCDTUNI_PP_VOLUME_PROBE=1 restores VTK's
    # exact probe). The probe locates every grid point in the cut-cell polyhedra one at a time: 12 s for a
    # 66 k-cell room at 128^3, no answer after five minutes on Base2-9 (6.69 M cells). A volume render is a
    # picture of the field, and the cell values blended from the nearest centres are that picture.
    _VOLUME_PROBE_MAX_CELLS = 0

    def _volume_centres(self):
        """Cell centres, a KD-tree over them and each cell's spacing to its
        nearest neighbour, once per loaded internal mesh."""
        internal = self._internal
        key = (id(internal), int(internal.n_cells))
        cached = getattr(self, "_volume_index", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        value = _volume_index(internal)
        self._volume_index = (key, value)
        return value

    def warm_volume(self, resolution: int = 128) -> None:
        """Compute the volume sampling of the loaded mesh ahead of the first volume request (a background thread
        after init): the cell-centre index and the grid weights take seconds once, then each field is a gather."""
        internal = self._internal
        if internal is None or internal.n_cells <= self._VOLUME_PROBE_MAX_CELLS:
            return
        bounds = internal.bounds
        N = max(16, min(resolution, 512))
        origin = [bounds[0], bounds[2], bounds[4]]
        step = [(bounds[2 * a + 1] - bounds[2 * a]) / max(N - 1, 1) for a in range(3)]
        t0 = time.perf_counter()
        self._volume_weights(N, origin, step)
        print(f"[PP-TIMING] volume sampling warmed: {time.perf_counter() - t0:.2f}s N={N}", flush=True)

    def _volume_weights(self, N: int, origin, step):
        """(valid mask over the N^3 grid, the 4 nearest cell indices and normalised inverse-distance weights of the
        valid samples). Depends on the mesh and the grid only, so it is computed once per mesh and resolution and
        every field (and every real-time update of the fields) is one gather."""
        key = (id(self._internal), int(self._internal.n_cells), N, tuple(origin), tuple(step))
        cached = getattr(self, "_volume_weight_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        centres, tree, spacing = self._volume_centres()
        # X fastest, as VTK's ImageData and the Three.js 3D texture lay it out
        zs = origin[2] + step[2] * np.arange(N)
        ys = origin[1] + step[1] * np.arange(N)
        xs = origin[0] + step[0] * np.arange(N)
        Z, Y, X = np.meshgrid(zs, ys, xs, indexing="ij")
        samples = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
        k = min(4, len(centres))                    # a tiny mesh has fewer than 4 centres to blend
        dist, idx = tree.query(samples, k=k, workers=-1)
        dist, idx = dist.reshape(len(samples), k), idx.reshape(len(samples), k)
        # a sample is in the fluid when its nearest centre is no farther than that cell's own spacing to its
        # neighbours (inside a rack or a wall the nearest fluid centre is farther away)
        valid = dist[:, 0] <= 0.9 * spacing[idx[:, 0]]
        w = 1.0 / np.maximum(dist[valid], 1e-9)
        w = (w / w.sum(axis=1, keepdims=True)).astype(np.float32)
        value = (valid, idx[valid].astype(np.int32), w)
        self._volume_weight_cache = (key, value)    # one resolution at a time: 256^3 weights are large
        return value

    def _volume_cell_values(self, field_name: str) -> Optional[np.ndarray]:
        """The field per cell, or None when the internal mesh has no cell data
        for it (the exact probe then answers)."""
        resolved, is_component, component_idx = self._resolve_field_name(field_name)
        cd = self._internal.cell_data
        if resolved in cd:
            arr = np.asarray(cd[resolved])
        elif resolved.endswith("_magnitude") and resolved[: -len("_magnitude")] in cd:
            arr = np.asarray(cd[resolved[: -len("_magnitude")]])
        else:
            return None
        if arr.ndim == 2:
            arr = arr[:, component_idx] if is_component else np.linalg.norm(arr, axis=1)
        return np.asarray(arr, dtype=np.float64)

    def _volume_by_cell_centres(self, field_name: str, N: int, bounds) -> Optional[Tuple[bytes, dict]]:
        internal = self._internal
        if (internal is None or internal.n_cells <= self._VOLUME_PROBE_MAX_CELLS
                or os.environ.get("DCDTUNI_PP_VOLUME_PROBE")):
            return None
        values = self._volume_cell_values(field_name)
        if values is None or values.shape[0] != internal.n_cells:
            return None
        t0_total = time.perf_counter()
        origin = [bounds[0], bounds[2], bounds[4]]
        step = [(bounds[2 * a + 1] - bounds[2 * a]) / max(N - 1, 1) for a in range(3)]
        valid, idx, w = self._volume_weights(N, origin, step)
        SENTINEL = -9999.0
        scalars = np.full(N ** 3, SENTINEL, dtype=np.float32)
        # the weights are normalised: one gather and a row sum per field
        scalars[valid] = np.einsum("ij,ij->i", w, values.astype(np.float32)[idx])
        if not np.all(np.isfinite(scalars[valid])):
            raise ValueError("volume has nonfinite valid physical samples")
        mask = valid.astype(np.uint8)
        kept = scalars[valid]
        smin = float(kept.min()) if kept.size else 0.0
        smax = float(kept.max()) if kept.size else 0.0
        header_dict = {
            "dimensions": [N, N, N],
            "origin": origin,
            "spacing": step,
            "scalarRange": [smin, smax],
            "displayRange": _display_range(kept),
            "sentinel": SENTINEL,
            "field": field_name,
            "validityMask": {"encoding": "uint8-01", "byteCount": N**3},
            "sampling": "cellCentres",
        }
        header_bytes = json.dumps(header_dict).encode("utf-8")
        header_bytes += b"\x00" * ((4 - len(header_bytes) % 4) % 4)
        payload = struct.pack("<I", len(header_bytes)) + header_bytes + scalars.tobytes() + mask.tobytes()
        print(f"[PP-TIMING] get_volume (cell centres): {time.perf_counter() - t0_total:.2f}s "
              f"N={N}, cells={internal.n_cells}, field={field_name}", flush=True)
        return payload, header_dict

    def _clip_to_roi(self) -> None:
        """Keep only the region an ROI result holds values in (its cells and boundary faces with finite T): every
        product (surfaces, slices, streamlines, volume, extremes) then covers that region alone. Point values are
        the plain cell average on the kept cells, as replace_fields computes them, so no value from outside leaks in.
        `_roi_ids` maps the kept cells / faces back to the full mesh for replace_fields."""
        import pyvista as pv
        internal = self._internal
        keep = np.flatnonzero(np.isfinite(np.asarray(internal.cell_data["T"])))
        sub = internal.extract_cells(keep)
        self._recompute_point_data(sub)
        ids = {"cells": keep, "patches": {}}
        clipped = pv.MultiBlock()
        if self._boundary is not None:
            for index, name in enumerate(self._boundary.keys()):
                patch = self._boundary[index]
                if patch is None or not patch.n_cells or "T" not in patch.cell_data:
                    continue
                finite = np.isfinite(np.asarray(patch.cell_data["T"]))
                if not finite.any():
                    continue
                part = patch if finite.all() else patch.remove_cells(np.flatnonzero(~finite), inplace=False)
                self._recompute_point_data(part)
                clipped[name] = part
                ids["patches"][name] = np.flatnonzero(finite)
        self._internal, self._boundary, self._roi_ids = sub, clipped, ids
        self._tri_cache.clear()
        self._volume_index = None

    @classmethod
    def _recompute_point_data(cls, mesh: Any) -> None:
        """Point arrays of `mesh` as the plain average of its cells' arrays (the cells' own values only)."""
        op = cls._cell_average_operator(mesh)
        for name in list(mesh.cell_data.keys()):
            if name.startswith("vtk"):
                continue
            values = np.asarray(mesh.cell_data[name], dtype=np.float64)
            cls._write_vtk_array(mesh.GetPointData(), name, (op @ values).astype(np.float32))

    @staticmethod
    def _cell_average_operator(mesh: Any):
        """Sparse [points x cells] matrix of vtkCellDataToPointData: each point the plain mean of the cells using
        it (polyhedra by their point lists, polygons by their vertices)."""
        from scipy.sparse import csr_matrix
        from vtkmodules.util.numpy_support import vtk_to_numpy

        if hasattr(mesh, "GetPolys"):
            polys = mesh.GetPolys()
            conn = vtk_to_numpy(polys.GetConnectivityArray()).astype(np.int64)
            offs = vtk_to_numpy(polys.GetOffsetsArray()).astype(np.int64)
        else:
            conn = np.asarray(mesh.cell_connectivity, dtype=np.int64)
            offs = np.asarray(mesh.offset, dtype=np.int64)
        cells = np.repeat(np.arange(len(offs) - 1), np.diff(offs))
        count = np.bincount(conn, minlength=mesh.n_points).astype(np.float64)
        weights = 1.0 / np.maximum(count, 1.0)[conn]
        return csr_matrix((weights, (conn, cells)), shape=(mesh.n_points, len(offs) - 1))

    @staticmethod
    def _write_vtk_array(attributes: Any, name: str, values: np.ndarray) -> None:
        """Set the array `name` of a VTK attribute set: written in place when one of the same shape exists (no
        PyVista wrapper: its per-assignment validation cost 2.5 s for a DCTS room's 685 patches), else added."""
        from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
        values = np.asarray(values)
        current = attributes.GetArray(name)
        comps = 1 if values.ndim == 1 else values.shape[1]
        if (current is not None and current.GetNumberOfTuples() == values.shape[0]
                and current.GetNumberOfComponents() == comps):
            view = vtk_to_numpy(current)
            view[...] = values.reshape(view.shape)
            current.Modified()
            return
        fresh = numpy_to_vtk(np.ascontiguousarray(values, dtype=np.float32), deep=True)
        fresh.SetName(name)
        attributes.AddArray(fresh)

    def _boundary_replace_plan(self, fields: List[str]) -> dict:
        """Everything replace_fields needs per patch, built once per loaded result: the patches with faces, a
        block-diagonal averaging operator over all of them (one product per field instead of one per patch), the
        numpy views of each patch's cell and point arrays (written in place: the arrays are created when missing).
        The display triangles are looked up per call: the triangle cache can be rebuilt between calls."""
        from scipy.sparse import block_diag
        from vtkmodules.util.numpy_support import vtk_to_numpy

        names, ops, n_faces, n_points, views = [], [], [], [], []
        shape = {}
        for index, pname in enumerate(self._boundary.keys()):
            patch = self._boundary[index]
            if patch is None or not patch.GetNumberOfCells():
                continue
            names.append(pname)
            ops.append(self._cell_average_operator(patch))
            n_faces.append(patch.GetNumberOfCells())
            n_points.append(patch.GetNumberOfPoints())
            per = {}
            for name in fields:
                comps = 3 if name == "U" else 1
                shape[name] = (3,) if comps == 3 else ()
                cells, points = patch.GetCellData(), patch.GetPointData()
                for attributes, n in ((cells, n_faces[-1]), (points, n_points[-1])):
                    current = attributes.GetArray(name)
                    if (current is None or current.GetNumberOfTuples() != n
                            or current.GetNumberOfComponents() != comps):
                        self._write_vtk_array(attributes, name, np.zeros((n, comps) if comps > 1 else n, np.float32))
                carr, parr = cells.GetArray(name), points.GetArray(name)
                per[name] = (vtk_to_numpy(carr), vtk_to_numpy(parr), carr, parr)
            views.append(per)
        return {"fields": fields, "names": names, "n_faces": n_faces, "shape": shape, "views": views,
                "op": block_diag(ops, format="csr"),
                "face_offsets": np.concatenate([[0], np.cumsum(n_faces)]),
                "point_offsets": np.concatenate([[0], np.cumsum(n_points)])}

    def replace_fields(self, internal: Dict[str, np.ndarray],
                       boundary: Dict[str, Dict[str, np.ndarray]]) -> List[dict]:
        """Replace loaded cell fields in place (the AI solve's real-time update): `internal` {field: [cells(, 3)]} on
        the internal mesh, `boundary` {patch: {field: [faces(, 3)]}} on the boundary patches. Point data follow
        by the averaging operator of each mesh (cached), the display triangles of each patch through their source
        points, the U components and magnitude as at load; every derived cache is dropped and the revision
        changes, as inject_internal_fields does. No file is read or written."""
        with self._state_lock:
            if self._internal is None or self._boundary is None:
                raise RuntimeError("No result loaded")
            self._fields_replaced = True
            roi_ids = getattr(self, "_roi_ids", None)
            if roi_ids is not None:               # an ROI result: the full-mesh values of the kept cells / faces
                internal = {k: np.asarray(v)[roi_ids["cells"]] for k, v in internal.items()}
                boundary = {p: {k: np.asarray(v)[roi_ids["patches"][p]] for k, v in f.items()}
                            for p, f in boundary.items() if p in roi_ids["patches"]}
            ops = getattr(self, "_replace_ops", None)
            if ops is None or ops.get("internal_cells") != self._internal.n_cells:
                ops = self._replace_ops = {"internal_cells": self._internal.n_cells,
                                           "internal": self._cell_average_operator(self._internal), "patches": {}}
            write = self._write_vtk_array
            cells_in, points_in = self._internal.GetCellData(), self._internal.GetPointData()
            for name, values in internal.items():
                arr = np.asarray(values, dtype=np.float32)
                if arr.shape[0] != self._internal.n_cells:
                    raise ValueError(f"{name}: {arr.shape[0]} values for {self._internal.n_cells} cells")
                write(cells_in, name, arr)
                point = ops["internal"] @ arr
                write(points_in, name, point)
                if name == "U":
                    write(points_in, "Umag", np.sqrt((point * point).sum(axis=1)))
                    for i, c in enumerate("xyz"):
                        write(points_in, "U" + c, point[:, i])
            if self._tri_cache and any("src" not in c for c in self._tri_cache.values()):
                self._tri_cache.clear()                   # built without source points: rebuild on demand
                self._mesh_binary = self._mesh_meta = None
            plan = ops.get("boundary")
            fields = sorted({name for f in boundary.values() for name in f})
            if plan is None or plan["fields"] != fields:
                plan = ops["boundary"] = self._boundary_replace_plan(fields)
            for name in fields:
                faces = np.concatenate([np.asarray(boundary[pname][name], dtype=np.float32)
                                        if pname in boundary else np.zeros((n,) + plan["shape"][name], np.float32)
                                        for pname, n in zip(plan["names"], plan["n_faces"])])
                if faces.shape[0] != plan["face_offsets"][-1]:
                    raise ValueError(f"boundary {name}: {faces.shape[0]} values for {plan['face_offsets'][-1]} faces")
                points = (plan["op"] @ faces).astype(np.float32, copy=False)
                fo, po = plan["face_offsets"], plan["point_offsets"]
                for k, pname in enumerate(plan["names"]):
                    if pname not in boundary:
                        continue
                    cview, pview, carr, parr = plan["views"][k][name]
                    cview[...] = faces[fo[k]:fo[k + 1]].reshape(cview.shape)
                    pview[...] = points[po[k]:po[k + 1]].reshape(pview.shape)
                    carr.Modified()
                    parr.Modified()
                    cached = self._tri_cache.get(pname)
                    if cached is not None:
                        display = points[po[k]:po[k + 1]][cached["src"]]
                        tri = cached["tri"]
                        if isinstance(tri, _PatchPointFields):
                            tri.point_data[name] = display
                        else:
                            self._write_vtk_array(tri.GetPointData(), name, display)
            self._field_cache.clear()
            self._field_info.clear()
            # the volume's cell-centre index and grid weights are geometry: they hold across a field replacement
            self._available_fields = self._discover_fields()
            old_revision = self._revision
            if self._numeric_product_store is not None:
                self._numeric_product_store.close()
            self._numeric_product_store = None
            self._native_input_snapshot = None
            self._revision = uuid.uuid4().hex
            if self._ai_worker_revision == old_revision:
                self._ai_worker_revision = self._revision
            return self._available_fields

    def inject_internal_fields(self, fields: Dict[str, np.ndarray]) -> List[dict]:
        """Attach AI or custom fields to the internal mesh and refresh discovery state."""
        with self._state_lock:
            if self._internal is None:
                raise RuntimeError("No internal mesh loaded")

            expected_cells = int(self._internal.n_cells)
            validated = {}
            for field_name, values in fields.items():
                arr = np.asarray(values, dtype=np.float32)
                if arr.ndim not in (1, 2):
                    raise ValueError(f"Unsupported field shape for {field_name}: {arr.shape}")
                if arr.shape[0] != expected_cells:
                    raise ValueError(
                        f"Field {field_name} expects {expected_cells} cells, got {arr.shape[0]}"
                    )
                validated[field_name] = arr
            for field_name, arr in validated.items():
                self._internal.cell_data[field_name] = arr

            self._field_cache.clear()
            self._field_info.clear()
            self._available_fields = self._discover_fields()
            old_revision = self._revision
            if self._numeric_product_store is not None:
                self._numeric_product_store.close()
            self._numeric_product_store = None
            # Injected arrays have a separate producer identity. Until they
            # have their own accepted receipt, never sign them as native bytes.
            self._native_input_snapshot = None
            self._revision = uuid.uuid4().hex
            if self._ai_worker_revision == old_revision:
                self._ai_worker_revision = self._revision
            return self._available_fields

    def invalidate(self) -> None:
        """Clear all caches."""
        with self._state_lock:
            if self._numeric_product_store is not None:
                self._numeric_product_store.close()
            self._numeric_product_store = None
            self._native_input_snapshot = None
            self._revision = None
            self._run_id = None
            self._ai_worker_revision = None
            self.multi_block = None
            self._internal = None
            self._roi = self._roi_ids = None
            self._boundary = None
            self._tri_cache.clear()
            self._mesh_binary = None
            self._mesh_meta = None
            self._field_cache.clear()
            self._field_info.clear()
            self._zones_cache = None
            self._zones_meta = None
            self._kdtree_cache = None
            self._volume_index = None
            self._volume_weight_cache = None
            self._available_fields = []
            self._vtm_path = None
            self._source_info = {}
            self._requested_time_value = None
            self._case_version = None
            self.scope_case_dir = None

    # ── Static binary packing methods ──

    @staticmethod
    def pack_mesh_binary(patches: List[dict]) -> Tuple[bytes, List[dict]]:
        """Pack triangulated patches into a single binary buffer.

        Layout:
          [Float32 x totalPoints x 3]    -- positions (x,y,z interleaved)
          [Uint32 x totalTriangles x 3]  -- triangle indices (offset per patch)
          [Uint32 x totalEdges x 2]      -- original polygon edge pairs (offset per patch)

        Returns (binary_bytes, patch_metadata_list).
        """
        all_points: List[np.ndarray] = []
        all_indices: List[np.ndarray] = []
        all_edges: List[np.ndarray] = []
        meta: List[dict] = []
        point_offset = 0

        for p in patches:
            pts = np.asarray(p["points"], dtype=np.float32)
            idx = np.asarray(p["indices"], dtype=np.uint32)
            n_points = pts.shape[0]
            n_tris = idx.shape[0]

            all_points.append(pts)
            all_indices.append(idx + point_offset)

            # Original polygon edges (may be absent for legacy callers)
            edges = p.get("edges")
            if edges is not None and len(edges) > 0:
                edge_arr = np.asarray(edges, dtype=np.uint32) + point_offset
                n_edges = edge_arr.shape[0]
            else:
                edge_arr = np.empty((0, 2), dtype=np.uint32)
                n_edges = 0
            all_edges.append(edge_arr)

            meta.append({
                "name": p["name"],
                "nPoints": int(n_points),
                "nTriangles": int(n_tris),
                "nEdges": int(n_edges),
            })
            point_offset += n_points

        if all_points:
            positions = np.concatenate(all_points).ravel()
            indices = np.concatenate(all_indices).ravel()
        else:
            positions = np.array([], dtype=np.float32)
            indices = np.array([], dtype=np.uint32)

        if all_edges and any(len(e) > 0 for e in all_edges):
            edges_flat = np.concatenate(all_edges).ravel()
        else:
            edges_flat = np.array([], dtype=np.uint32)

        buf = positions.tobytes() + indices.tobytes() + edges_flat.tobytes()
        return buf, meta

    @staticmethod
    def pack_field_binary(values: np.ndarray, n_components: int = 1) -> bytes:
        """Pack scalar/vector field into binary buffer.

        Layout:
          Header (16 bytes): [Float32 min] [Float32 max] [Uint32 nComp] [Uint32 nPts]
          Data: [Float32 x nPoints x nComponents]
        """
        values = np.asarray(values, dtype=np.float32).ravel()
        n_points = len(values) // max(n_components, 1)
        fmin = float(values.min()) if len(values) > 0 else 0.0
        fmax = float(values.max()) if len(values) > 0 else 0.0
        header = struct.pack("<ffII", fmin, fmax, n_components, n_points)
        return header + values.tobytes()


def _triangulate_polygon(pts, verts):
    """Ear-clip a planar (possibly concave) polygon given as local vertex
    indices into `pts`; falls back to a fan when the polygon is degenerate."""
    n = len(verts)
    P = np.asarray([pts[v] for v in verts], dtype=np.float64)
    c = P.mean(axis=0)
    normal = np.zeros(3)
    for k in range(n):
        normal += np.cross(P[k] - c, P[(k + 1) % n] - c)
    nl = np.linalg.norm(normal)
    fan = [[verts[0], verts[k], verts[k + 1]] for k in range(1, n - 1)]
    if nl < 1e-30:
        return fan
    normal /= nl
    ax = np.array([1.0, 0, 0]) if abs(normal[0]) < 0.9 else np.array([0, 1.0, 0])
    u = ax - np.dot(ax, normal) * normal
    u /= np.linalg.norm(u)
    w = np.cross(normal, u)
    Q = [(float(np.dot(p - c, u)), float(np.dot(p - c, w))) for p in P]
    idx = list(range(n))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, cc):
        return cross(a, b, p) >= -1e-14 and cross(b, cc, p) >= -1e-14 and cross(cc, a, p) >= -1e-14

    out = []
    guard = 0
    while len(idx) > 3 and guard < 4 * n:
        guard += 1
        clipped = False
        m = len(idx)
        for i in range(m):
            i0, i1, i2 = idx[(i - 1) % m], idx[i], idx[(i + 1) % m]
            a, b, cc = Q[i0], Q[i1], Q[i2]
            if cross(a, b, cc) <= 1e-16:
                continue  # reflex or degenerate corner
            ok = True
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                if inside(Q[j], a, b, cc):
                    ok = False
                    break
            if ok:
                out.append([verts[i0], verts[i1], verts[i2]])
                idx.pop(i)
                clipped = True
                break
        if not clipped:
            break
    if len(idx) == 3:
        out.append([verts[idx[0]], verts[idx[1]], verts[idx[2]]])
    elif len(idx) > 3:
        # could not finish (self-intersecting or numerically flat): fan the rest
        for k in range(1, len(idx) - 1):
            out.append([verts[idx[0]], verts[idx[k]], verts[idx[k + 1]]])
    return out


class MeshPreviewLoader:
    """Load boundary surfaces from polyMesh for mesh preview.

    Reads OpenFOAM polyMesh files natively (no PyVista/VTK dependency).
    Only reads boundary faces + referenced points, skipping internal mesh
    entirely. For a 3.7M-cell mesh this reads ~9% of faces, reducing load
    time from ~660s (PyVista OpenFOAMReader) to a few seconds.
    """

    def __init__(self, mesh_dir: Path) -> None:
        self.mesh_dir = Path(mesh_dir).resolve()
        self._binary: Optional[bytes] = None
        self._meta: Optional[List[dict]] = None

    # ------------------------------------------------------------------
    # Volume mesh counts
    # ------------------------------------------------------------------

    def _mesh_dirs(self) -> List[Path]:
        meshes = mesh_directories(self.mesh_dir)
        if meshes:
            return meshes
        # Preserve boundary-only diagnostics for incomplete serial meshes.
        root = self.mesh_dir / "constant" / "polyMesh"
        return [root] if (root / "boundary").exists() else []

    def read_counts(self) -> dict:
        """Count all cells and physical boundary faces across processor meshes."""
        meshes = self._mesh_dirs()
        if len(meshes) <= 1 and (not meshes or meshes[0] == self.mesh_dir / "constant" / "polyMesh"):
            return self._read_poly_counts(self.mesh_dir / "constant" / "polyMesh")
        counts = [self._read_poly_counts(mesh) for mesh in meshes]
        interfaces = sum(
            patch["nFaces"] for mesh in meshes
            for patch in self._parse_boundary(mesh / "boundary")
            if _physical_patch_name(patch) is None
        )
        result = {}
        for key in ("nCells", "nFaces", "nInternalFaces", "nBoundaryFaces", "nBoundarySum"):
            values = [count[key] for count in counts]
            result[key] = sum(values) if all(value is not None for value in values) else None
        # Every processor interface is stored twice. In global counts it is
        # one internal face and never an external boundary surface.
        if result["nFaces"] is not None:
            result["nFaces"] -= interfaces // 2
        if result["nInternalFaces"] is not None:
            result["nInternalFaces"] += interfaces // 2
        if result["nBoundaryFaces"] is not None:
            result["nBoundaryFaces"] -= interfaces
        result["nBoundarySum"] -= interfaces
        return result

    @classmethod
    def _read_poly_counts(cls, poly: Path) -> dict:
        """Return volume cell + boundary face counts from polyMesh.

        OpenFOAM writes ``note "nPoints:... nCells:... nFaces:... nInternalFaces:..."``
        into the ``owner`` file header. When present we use it directly —
        it's authoritative and costs a few bytes to read. When absent
        (very old meshes, or custom writers that drop the note) we return
        ``None`` for counts that require it and still report the sum of
        ``nFaces`` parsed from the ``boundary`` file.

        Keys:
          - ``nCells`` — total volume cells (or ``None`` if note absent)
          - ``nFaces`` — total faces (internal + boundary) or ``None``
          - ``nInternalFaces`` — internal faces or ``None``
          - ``nBoundaryFaces`` — ``nFaces - nInternalFaces`` or ``None``
          - ``nBoundarySum`` — sum of per-patch ``nFaces`` from ``boundary``
            (always a concrete int; ``0`` when polyMesh missing)
        """
        import re

        result = {
            "nCells": None,
            "nFaces": None,
            "nInternalFaces": None,
            "nBoundaryFaces": None,
            "nBoundarySum": 0,
        }
        owner = poly / "owner"
        boundary = poly / "boundary"

        if owner.exists():
            # Header is ~20 lines; read a bounded chunk to avoid touching the
            # binary payload on large meshes.
            with owner.open("rb") as stream:
                head = stream.read(4096).decode("utf-8", errors="replace")
            m = re.search(
                r'note\s+"([^"]*)"',
                head,
            )
            if m:
                note = m.group(1)
                pairs = dict(re.findall(r"(\w+):(\d+)", note))
                if "nCells" in pairs:
                    result["nCells"] = int(pairs["nCells"])
                if "nFaces" in pairs:
                    result["nFaces"] = int(pairs["nFaces"])
                if "nInternalFaces" in pairs:
                    result["nInternalFaces"] = int(pairs["nInternalFaces"])
                if result["nFaces"] is not None and result["nInternalFaces"] is not None:
                    result["nBoundaryFaces"] = result["nFaces"] - result["nInternalFaces"]

        if boundary.exists():
            patches = cls._parse_boundary(boundary)
            result["nBoundarySum"] = sum(int(p.get("nFaces", 0)) for p in patches)

        return result

    # ------------------------------------------------------------------
    # polyMesh parsers (ASCII and OpenFOAM binary)
    # ------------------------------------------------------------------

    @staticmethod
    def _foam_header_info(raw: bytes) -> dict:
        """Read ``format`` and the ``arch`` label/scalar widths from a FoamFile header.

        The mesh pipeline writes ``constant/polyMesh`` in BINARY (it is several
        times faster to write and re-read than ASCII); ``boundary`` stays an
        ASCII dictionary either way. Each file records its own format in its
        header, so this is the only thing the reader has to branch on.
        """
        import re

        head = raw[:2048].decode("utf-8", errors="replace")
        fmt = "ascii"
        m = re.search(r"format\s+(\w+)\s*;", head)
        if m:
            fmt = m.group(1)
        label_bytes, scalar_bytes = 4, 8
        m = re.search(r'arch\s+"([^"]*)"', head)
        if m:
            arch = m.group(1)
            m2 = re.search(r"label=(\d+)", arch)
            if m2:
                label_bytes = int(m2.group(1)) // 8
            m2 = re.search(r"scalar=(\d+)", arch)
            if m2:
                scalar_bytes = int(m2.group(1)) // 8
        return {"format": fmt, "label_bytes": label_bytes, "scalar_bytes": scalar_bytes}

    @staticmethod
    def _binary_list_at(raw: bytes, pos: int) -> Tuple[int, int, int]:
        """Locate one binary list starting at/after *pos*.

        A binary OpenFOAM list is ``<count>\n(`` followed by ``count`` fixed-width
        records and a closing ``)``. Returns ``(count, data_start, header_end)``
        where ``header_end`` is the offset just past the ``(``.
        """
        n = len(raw)
        while pos < n and raw[pos:pos + 1] in b" \t\r\n":
            pos += 1
        start = pos
        while pos < n and raw[pos:pos + 1].isdigit():
            pos += 1
        if pos == start:
            raise ValueError("binary polyMesh list: no count found")
        count = int(raw[start:pos])
        while pos < n and raw[pos:pos + 1] in b" \t\r\n":
            pos += 1
        if raw[pos:pos + 1] != b"(":
            raise ValueError("binary polyMesh list: missing '(' after count")
        return count, pos + 1, pos + 1

    @staticmethod
    def _payload_start(raw: bytes) -> int:
        """Offset just past the ``// * * * … * //`` separator line."""
        marker = raw.find(b"// * * *")
        if marker < 0:
            return 0
        nl = raw.find(b"\n", marker)
        return len(raw) if nl < 0 else nl + 1

    @staticmethod
    def _skip_foam_header(lines: List[str]) -> int:
        """Return the index of the first data line after the FoamFile header."""
        i = 0
        n = len(lines)
        # Skip to the count line (first line that is just an integer)
        while i < n:
            stripped = lines[i].strip()
            if stripped.isdigit():
                return i
            i += 1
        return i

    @staticmethod
    def _parse_boundary(boundary_path: Path) -> List[dict]:
        """Parse boundary file → list of {name, type, nFaces, startFace}."""
        text = boundary_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        patches = []
        i = 0
        n = len(lines)
        # Find the opening '(' after the count
        while i < n and lines[i].strip() != "(":
            i += 1
        i += 1  # skip '('

        while i < n:
            line = lines[i].strip()
            if line == ")":
                break
            if line == "{" or line == "" or line.startswith("//"):
                i += 1
                continue
            # Patch name (a word not starting with special chars)
            if line and not line.startswith(("{", "}", "(", ")")):
                patch_name = line
                patch_info: dict = {"name": patch_name, "type": "patch", "nFaces": 0, "startFace": 0}
                # Read until closing '}'
                i += 1
                while i < n:
                    pline = lines[i].strip()
                    if pline == "}":
                        break
                    if pline.startswith("type"):
                        patch_info["type"] = pline.split()[-1].rstrip(";")
                    elif pline.startswith("nFaces"):
                        patch_info["nFaces"] = int(pline.split()[-1].rstrip(";"))
                    elif pline.startswith("startFace"):
                        patch_info["startFace"] = int(pline.split()[-1].rstrip(";"))
                    elif pline.startswith("referPatch"):
                        patch_info["referPatch"] = pline.split()[-1].rstrip(";")
                    i += 1
                patches.append(patch_info)
            i += 1
        return patches

    @staticmethod
    def _parse_points(points_path: Path) -> np.ndarray:
        """Parse points file → (N, 3) float32 array. Handles ASCII and binary."""
        raw = points_path.read_bytes()
        info = MeshPreviewLoader._foam_header_info(raw)
        if info["format"] == "binary":
            pos = MeshPreviewLoader._payload_start(raw)
            count, data_start, _ = MeshPreviewLoader._binary_list_at(raw, pos)
            dtype = np.float64 if info["scalar_bytes"] == 8 else np.float32
            nbytes = count * 3 * info["scalar_bytes"]
            arr = np.frombuffer(raw, dtype=dtype, count=count * 3, offset=data_start)
            del nbytes
            return arr.reshape(-1, 3).astype(np.float32)
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        idx = MeshPreviewLoader._skip_foam_header(lines)
        count = int(lines[idx].strip())
        idx += 1
        while idx < len(lines) and lines[idx].strip() != "(":
            idx += 1
        idx += 1
        data_start = sum(len(l) + 1 for l in lines[:idx])
        closing = text.find("\n)", data_start)
        if closing < 0:
            closing = len(text)
        chunk = text[data_start:closing].replace("(", "").replace(")", "")
        nums = np.fromstring(chunk, dtype=np.float64, sep=" ")
        return nums[: (len(nums) // 3) * 3].reshape(-1, 3).astype(np.float32)

    @staticmethod
    def _parse_faces_range(faces_path: Path, start: int, count: int) -> List[List[int]]:
        """Parse a range of faces from the faces file.

        Scans through face entries quickly, only parsing vertex data for
        the requested [start, start+count) range.

        Binary ``faces`` is a ``faceCompactList``: an offsets list of length
        nFaces+1 followed by the flat vertex list, so a range slice is two
        buffer views instead of a scan.
        """
        raw = faces_path.read_bytes()
        info = MeshPreviewLoader._foam_header_info(raw)
        if info["format"] == "binary":
            lb = info["label_bytes"]
            ltype = {1: np.int8, 2: np.int16, 4: np.int32, 8: np.int64}[lb]
            pos = MeshPreviewLoader._payload_start(raw)
            n_off, off_start, _ = MeshPreviewLoader._binary_list_at(raw, pos)
            offsets = np.frombuffer(raw, dtype=ltype, count=n_off, offset=off_start)
            after_offsets = off_start + n_off * lb
            # skip the ')' that closes the offsets list
            while after_offsets < len(raw) and raw[after_offsets:after_offsets + 1] != b")":
                after_offsets += 1
            n_vert, vert_start, _ = MeshPreviewLoader._binary_list_at(raw, after_offsets + 1)
            verts = np.frombuffer(raw, dtype=ltype, count=n_vert, offset=vert_start)
            n_faces = n_off - 1
            lo = max(0, min(start, n_faces))
            hi = max(lo, min(start + count, n_faces))
            return [
                verts[offsets[i]:offsets[i + 1]].astype(int).tolist()
                for i in range(lo, hi)
            ]
        # OpenFOAM writes larger polygon lists as ``12\n(\n...\n)``.
        # Count parentheses-delimited faces rather than assuming one face per
        # line; that assumption loses cut-cell faces and shifts patch ranges.
        import re
        header = re.search(rb"(?m)^\s*(\d+)\s*\(", raw)
        if header is None:
            raise ValueError(f"No face list found in {faces_path}")
        n_faces = int(header.group(1))
        pos = header.end()
        lo = max(0, min(start, n_faces))
        hi = max(lo, min(start + count, n_faces))
        faces: List[List[int]] = []
        for face_idx in range(hi):
            opening = raw.find(b"(", pos)
            closing = raw.find(b")", opening + 1)
            if opening < 0 or closing < 0:
                raise ValueError(f"Incomplete face {face_idx} in {faces_path}")
            if face_idx >= lo:
                faces.append([int(value) for value in raw[opening + 1:closing].split()])
            pos = closing + 1
        return faces

    @staticmethod
    def _parse_faces_compact(faces_path: Path, start: int, count: int) -> Tuple[np.ndarray, np.ndarray]:
        """Faces [start, start+count) as (offsets, vertices) arrays.

        ``offsets`` has count+1 entries from 0; face i is
        ``vertices[offsets[i]:offsets[i + 1]]``. A binary ``faceCompactList``
        is sliced as two buffer views; an ASCII file goes through the
        per-face parser.
        """
        raw = faces_path.read_bytes()
        info = MeshPreviewLoader._foam_header_info(raw)
        if info["format"] == "binary":
            lb = info["label_bytes"]
            ltype = {1: np.int8, 2: np.int16, 4: np.int32, 8: np.int64}[lb]
            pos = MeshPreviewLoader._payload_start(raw)
            n_off, off_start, _ = MeshPreviewLoader._binary_list_at(raw, pos)
            offsets = np.frombuffer(raw, dtype=ltype, count=n_off, offset=off_start)
            after_offsets = off_start + n_off * lb
            while after_offsets < len(raw) and raw[after_offsets:after_offsets + 1] != b")":
                after_offsets += 1
            n_vert, vert_start, _ = MeshPreviewLoader._binary_list_at(raw, after_offsets + 1)
            verts = np.frombuffer(raw, dtype=ltype, count=n_vert, offset=vert_start)
            n_faces = n_off - 1
            lo = max(0, min(start, n_faces))
            hi = max(lo, min(start + count, n_faces))
            off = offsets[lo:hi + 1].astype(np.int64)
            return off - off[0], verts[off[0]:off[-1]].astype(np.int64)
        faces = MeshPreviewLoader._parse_faces_range(faces_path, start, count)
        sizes = np.fromiter((len(f) for f in faces), dtype=np.int64, count=len(faces))
        off = np.zeros(len(faces) + 1, dtype=np.int64)
        np.cumsum(sizes, out=off[1:])
        flat = np.fromiter((v for f in faces for v in f), dtype=np.int64, count=int(off[-1]))
        return off, flat

    @staticmethod
    def _patch_surface(all_points: np.ndarray, offsets: np.ndarray, verts: np.ndarray):
        """One patch's (points, triangles, edges), vectorized.

        Local point ids follow the sorted global ids. Edges are the unique
        polygon sides, sorted. Faces fan from their first vertex; a face of
        five or more vertices that is not convex (a cut-cell face joining two
        coplanar pieces into an L) is ear-clipped in its own plane instead,
        since a fan would draw outside it and leave part of it open.
        """
        uniq, local = np.unique(verts, return_inverse=True)
        local = local.astype(np.int64)
        pts = all_points[uniq]
        sizes = np.diff(offsets)
        n_faces = len(sizes)
        # Edges: each vertex to the next, the last of a face back to its first.
        nxt = np.arange(1, len(local) + 1, dtype=np.int64)
        if n_faces:
            nxt[offsets[1:] - 1] = offsets[:-1]
        a, b = local, local[nxt]
        key = (np.minimum(a, b).astype(np.uint64) << np.uint64(32)) | np.maximum(a, b).astype(np.uint64)
        key = np.unique(key)
        edges = np.stack([key >> np.uint64(32), key & np.uint64(0xFFFFFFFF)], axis=1).astype(np.uint32)
        # Fan triangles: face i gives (v0, vk, vk+1) for k = 1 .. n-2.
        ntri = np.maximum(sizes - 2, 0)
        tri_start = np.zeros(n_faces + 1, dtype=np.int64)
        np.cumsum(ntri, out=tri_start[1:])
        face_of = np.repeat(np.arange(n_faces, dtype=np.int64), ntri)
        k = np.arange(int(tri_start[-1]), dtype=np.int64) - tri_start[face_of] + 1
        base = offsets[face_of]
        tris = np.stack([local[base], local[base + k], local[base + k + 1]], axis=1)
        # Concave polygons (five or more vertices) are ear-clipped instead.
        replace = {}
        for n in np.unique(sizes[sizes >= 5]):
            ids = np.nonzero(sizes == n)[0]
            ring = local[offsets[ids][:, None] + np.arange(n)]
            P = pts[ring].astype(np.float64)
            Q = np.roll(P, -1, axis=1)
            normal = np.cross(P, Q).sum(axis=1)                    # Newell
            e = Q - P
            turn = np.einsum('fkj,fj->fk', np.cross(e, np.roll(e, -1, axis=1)), normal)
            scale = np.linalg.norm(normal, axis=1)[:, None] * np.abs(e).max(axis=(1, 2))[:, None] + 1e-300
            concave = (turn < -1e-9 * scale).any(axis=1)
            for f in ids[concave]:
                replace[int(f)] = _triangulate_polygon(pts, local[offsets[f]:offsets[f + 1]].tolist())
        if replace:
            pieces, cursor = [], 0
            for f in sorted(replace):
                pieces.append(tris[cursor:tri_start[f]])
                ear = np.asarray(replace[f], dtype=np.int64).reshape(-1, 3)
                pieces.append(ear)
                cursor = tri_start[f + 1]
            pieces.append(tris[cursor:])
            tris = np.concatenate(pieces)
        return pts, tris.astype(np.uint32), edges

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> Tuple[bytes, List[dict]]:
        """Read physical boundary surfaces from serial or decomposed polyMesh."""
        if self._binary is not None:
            return self._binary, self._meta
        pieces: Dict[str, List[dict]] = {}
        meshes = self._mesh_dirs()
        # One processor mesh per thread: the reads and the numpy work release
        # the GIL; the per-rank results are merged in rank order below.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(meshes)))) as pool:
            for patches in pool.map(self._load_poly, meshes):
                for patch in patches:
                    pieces.setdefault(patch["name"], []).append(patch)
        patches = []
        for name, parts in pieces.items():
            if len(parts) == 1:
                patches.append(parts[0])
                continue
            offset = 0
            points, indices, edges = [], [], []
            for part in parts:
                points.append(part["points"])
                indices.append(part["indices"] + offset)
                edges.append(part["edges"] + offset)
                offset += len(part["points"])
            patches.append({
                "name": name,
                "points": np.concatenate(points),
                "indices": np.concatenate(indices),
                "edges": np.concatenate(edges),
            })
        self._binary, self._meta = CachedPostProcessor.pack_mesh_binary(patches)
        for meta in self._meta:
            meta["defaultVisible"] = _patch_default_visible(meta["name"])
        return self._binary, self._meta

    def _load_poly(self, poly_dir: Path) -> List[dict]:
        boundary_path = poly_dir / "boundary"
        points_path = poly_dir / "points"
        faces_path = poly_dir / "faces"

        if not boundary_path.exists():
            return []

        t0 = time.time()

        # 1. Parse boundary patches
        boundary_patches = [
            patch for patch in self._parse_boundary(boundary_path)
            if patch.get("nFaces", 0) > 0 and _physical_patch_name(patch) is not None
        ]
        if not boundary_patches:
            return []

        # 2. Determine the global face range we need (min startFace to max end)
        min_start = min(p["startFace"] for p in boundary_patches)
        max_end = max(p["startFace"] + p["nFaces"] for p in boundary_patches)
        total_boundary_faces = max_end - min_start

        logger.info(
            f"[MeshPreview] {len(boundary_patches)} patches, "
            f"boundary faces {min_start}..{max_end} ({total_boundary_faces} faces)"
        )

        # 3. Read ALL boundary faces in one pass (faces are contiguous at end of file)
        face_offsets, face_verts = self._parse_faces_compact(faces_path, min_start, total_boundary_faces)
        t_faces = time.time()
        logger.info(f"[MeshPreview] Parsed {len(face_offsets) - 1} boundary faces in {t_faces - t0:.1f}s")

        # Read points file (boundary faces can reference any point).
        all_points = self._parse_points(points_path)
        t_points = time.time()
        logger.info(f"[MeshPreview] Parsed {all_points.shape[0]} points in {t_points - t_faces:.1f}s")

        # 6. Build per-patch geometry with local point indices
        result_patches = []
        for bp in boundary_patches:
            nf = bp["nFaces"]
            if nf == 0:
                continue
            f0 = bp["startFace"] - min_start
            off = face_offsets[f0:f0 + nf + 1]
            verts = face_verts[off[0]:off[-1]]
            if verts.size == 0:
                continue
            pts, indices, edge_pairs = self._patch_surface(all_points, off - off[0], verts)
            result_patches.append({
                "name": _physical_patch_name(bp),
                "points": np.ascontiguousarray(pts, dtype=np.float32),
                "indices": indices,
                "edges": edge_pairs,
            })

        t_done = time.time()
        logger.info(
            f"[MeshPreview] Built {len(result_patches)} patches "
            f"({sum(p['points'].shape[0] for p in result_patches)} pts, "
            f"{sum(p['indices'].shape[0] for p in result_patches)} tris) "
            f"in {t_done - t0:.1f}s total"
        )

        return result_patches


class MeshPreviewCache:
    """Cache wrapper for MeshPreviewLoader with mtime-based invalidation."""

    def __init__(self) -> None:
        self._loader: Optional[MeshPreviewLoader] = None
        self._mtime: Optional[tuple] = None
        self._mesh_dir_str: Optional[str] = None

    def get(self, mesh_dir: Path) -> MeshPreviewLoader:
        candidate = MeshPreviewLoader(mesh_dir)
        boundary_mtime = tuple(
            (str(path), stat.st_mtime_ns, stat.st_size)
            for mesh in candidate._mesh_dirs()
            for name in ("boundary", "points", "faces", "owner", "neighbour")
            if (path := mesh / name).is_file()
            for stat in (path.stat(),)
        )
        resolved = str(mesh_dir.resolve())
        if (self._loader is None
                or self._mesh_dir_str != resolved
                or self._mtime != boundary_mtime):
            self._loader = candidate
            self._mtime = boundary_mtime
            self._mesh_dir_str = resolved
        return self._loader

    def reset(self) -> None:
        self._loader = None
        self._mtime = None
        self._mesh_dir_str = None
