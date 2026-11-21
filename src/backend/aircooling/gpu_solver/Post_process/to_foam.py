"""Read and write OpenFOAM volFields on the global mesh of ``Parse_mesh.foamio.decomposed.read_case``.

* :func:`read_fields` reads ``<case>/<time>/<field>`` (serial) or ``<case>/processorN/<time>/<field>`` into global
  arrays: the internal field on the global cells, and each physical patch's ``value`` on its global faces.
* :func:`write_fields` writes cell fields back as ASCII volFields, serial (``<out>/<time>/``) or decomposed
  (``<out>/processorN/<time>/``), taking each patch's boundary-condition dictionary from a template time directory
  of the same layout (e.g. ``processorN/0/<field>``) and writing ``value`` explicitly where it is given:

  - ``boundary_values[field][patch]`` (global per-patch arrays) set the ``value`` of physical patches;
  - processor patches always get the neighbour-cell values (OpenFOAM's ``processorFvPatchField`` value is the
    neighbour internal field, and the dictionary constructor requires it);
  - every other entry of the template's patch dictionary is kept (values in full precision).

  For a subset mesh (``read_case(keep=...)``) the dropped cells keep their values from ``source_time`` (and so do
  the patch / processor faces of the dropped cells).

Numbers are written with ``repr`` (shortest round-trip), so a read -> write -> read cycle is exact;
``write_fields(precision=p)`` writes ``%.{p}g`` instead (smaller files, for a caller that only reads the result), and
``write_fields(workers=n)`` formats the (field, rank) files in ``n`` forked processes.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from Parse_mesh.foamio.foam_dict import DimensionSet, FoamDict, FoamString  # noqa: E402
from Parse_mesh.foamio.fields import expand_value, read_vol_field  # noqa: E402

__all__ = ["FieldData", "read_fields", "write_fields", "write_vol_field_ascii", "rank_dir"]

_CLASS = {1: "volScalarField", 3: "volVectorField", 6: "volSymmTensorField", 9: "volTensorField"}
_ELEM = {1: "scalar", 3: "vector", 6: "symmTensor", 9: "tensor"}
_NO_VALUE_TYPES = ("processor", "processorCyclic", "cyclic", "cyclicAMI", "cyclicSlip", "empty", "wedge",
                   "symmetry", "symmetryPlane")       # explicit=True leaves these patch dictionaries as they are
_FLOAT = repr                       # the float format of this process (write_fields(precision=) replaces it)
_JOB: dict = {}                     # write_fields' arguments, inherited by its forked workers


@dataclass
class FieldData:
    name: str
    internal: np.ndarray                         # (N,) or (N, ncomp) on the global cells
    boundary: dict = field(default_factory=dict)  # physical patch -> (patch faces,) or (patch faces, ncomp); NaN = no value
    dimensions: DimensionSet | None = None

    @property
    def ncomp(self) -> int:
        return 1 if self.internal.ndim == 1 else int(self.internal.shape[1])


def rank_dir(case, rm, root: Path | None = None) -> Path:
    """The directory of rank ``rm`` under ``root`` (default: the case): ``processorN`` or the case itself."""
    root = Path(root) if root is not None else Path(case.path)
    return root / f"processor{rm.rank}" if case.decomposed else root


def _shape(n: int, ncomp: int):
    return (n,) if ncomp == 1 else (n, ncomp)


# --------------------------------------------------------------------------------------------------------- read
def read_fields(case, time: str, names, root: Path | None = None) -> dict[str, FieldData]:
    """Global internal fields and physical-patch values of ``names`` at ``time``."""
    out = {}
    for name in names:
        data = None
        for rm in case.ranks:
            vf = read_vol_field(rank_dir(case, rm, root) / str(time) / name, rm.n_cells, patches=rm.patches)
            nc = vf.ncomp
            if data is None:
                data = FieldData(name, np.full(_shape(case.n_cells, nc), np.nan), {}, vf.dimensions)
                for p in case.patches:
                    data.boundary[p.name] = np.full(_shape(p.size, nc), np.nan)
            ok = rm.cell_global >= 0
            data.internal[rm.cell_global[ok]] = vf.internal[ok]
            for p in case.patches:
                lp = rm.patch(p.name)
                if lp.size == 0:
                    continue
                v = vf.patch_value(p.name, lp.size)
                if v is None:
                    continue
                g = rm.face_global[lp.slice]
                k = g >= 0
                data.boundary[p.name][g[k] - p.start] = v[k]
        out[name] = data
    return out


# -------------------------------------------------------------------------------------------------------- write
def _num(x) -> str:
    if isinstance(x, (bool, np.bool_)):
        return "true" if x else "false"
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    return _FLOAT(float(x))


def _list_body(a: np.ndarray) -> str:
    a = np.asarray(a)
    if a.ndim == 1:
        if a.dtype.kind in "iu":
            return "\n".join(map(str, a.tolist()))
        return "\n".join(map(_FLOAT, a.astype(np.float64).tolist()))
    if a.dtype.kind in "iu":
        return "\n".join("(" + " ".join(map(str, r)) + ")" for r in a.tolist())
    rows = a.astype(np.float64).tolist()
    return "\n".join("(" + " ".join(map(_FLOAT, r)) + ")" for r in rows)


def _field_value(a: np.ndarray, ncomp: int, uniform_ok: bool = True) -> str:
    """``uniform x`` or ``nonuniform List<T> N (...)``."""
    a = np.asarray(a, dtype=np.float64).reshape(_shape(a.shape[0], ncomp))
    n = a.shape[0]
    if uniform_ok and n > 0 and np.all(a == a[0]):
        v = a[0]
        return "uniform " + (_FLOAT(float(v)) if ncomp == 1 else "(" + " ".join(map(_FLOAT, v.tolist())) + ")")
    if n == 0:
        return f"nonuniform List<{_ELEM[ncomp]}> 0()"
    return f"nonuniform List<{_ELEM[ncomp]}>\n{n}\n(\n{_list_body(a)}\n)\n"


def _fmt(v, indent: str) -> str:
    if isinstance(v, FoamDict) or isinstance(v, dict):
        inner = indent + "    "
        body = "".join(_entry(k, x, inner) for k, x in v.items())
        return f"\n{indent}{{\n{body}{indent}}}"
    if isinstance(v, FoamString):
        return f'"{v}"'
    if isinstance(v, DimensionSet):
        return "[" + " ".join(_num(x) for x in v) + "]"
    if isinstance(v, np.ndarray):
        if v.ndim == 0:
            return _num(v.item())
        if v.size <= 9 and v.ndim == 1:
            return "(" + " ".join(_num(x) for x in v.tolist()) + ")"
        return f"\n{v.shape[0]}\n(\n{_list_body(v)}\n)\n"
    if isinstance(v, tuple):
        if len(v) >= 2 and v[0] == "nonuniform" and isinstance(v[-1], np.ndarray):
            elem = v[1] if isinstance(v[1], str) and v[1].startswith("List<") else "List<scalar>"
            arr = v[-1]
            if arr.size == 0:
                return f"nonuniform {elem} 0()"
            return f"nonuniform {elem}\n{arr.shape[0]}\n(\n{_list_body(arr)}\n)\n"
        return " ".join(_fmt(x, indent) for x in v)
    if isinstance(v, list):
        return "(" + " ".join(_fmt(x, indent) for x in v) + ")"
    if isinstance(v, (int, float, np.integer, np.floating, bool, np.bool_)):
        return _num(v)
    return str(v)


def _entry(k, v, indent: str) -> str:
    key = f'"{k}"' if isinstance(k, FoamString) else str(k)
    if isinstance(v, (FoamDict, dict)):
        return f"{indent}{key}{_fmt(v, indent)}\n"
    if isinstance(v, str) and v.startswith("__RAW__"):
        return f"{indent}{key:<15} {v[7:]};\n"
    return f"{indent}{key:<15} {_fmt(v, indent)};\n"


_BANNER = ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
           "  =========                 |\n"
           "  \\\\      /  F ield         | written by LPCNS Post_process/to_foam.py\n"
           "   \\\\    /   O peration     |\n"
           "    \\\\  /    A nd           |\n"
           "     \\\\/     M anipulation  |\n"
           "\\*---------------------------------------------------------------------------*/\n")


def write_vol_field_ascii(path: Path, name: str, ncomp: int, dimensions, internal: np.ndarray,
                          boundary: dict, location: str, extra: dict | None = None) -> None:
    """Write one volField; ``boundary`` maps patch -> FoamDict (a ``value`` that is an ndarray is written as a
    field value, uniform when all equal)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [_BANNER, "FoamFile\n{\n    version     2.0;\n    format      ascii;\n",
             '    arch        "LSB;label=32;scalar=64";\n', f"    class       {_CLASS[ncomp]};\n",
             f'    location    "{location}";\n', f"    object      {name};\n}}\n",
             "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n",
             f"dimensions      {_fmt(DimensionSet(dimensions or [0] * 7), '')};\n\n",
             f"internalField   {_field_value(internal, ncomp, uniform_ok=False)};\n\n"]
    for k, v in (extra or {}).items():
        parts.append(_entry(k, v, ""))
    parts.append("boundaryField\n{\n")
    for pname, d in boundary.items():
        d = FoamDict(d)
        if isinstance(d.get("value"), np.ndarray):
            d["value"] = "__RAW__" + _field_value(d["value"], ncomp)
        parts.append(f"    {pname}\n    {{\n")
        parts.append("".join(_entry(k, x, "        ") for k, x in d.items()))
        parts.append("    }\n")
    parts.append("}\n\n\n// ************************************************************************* //\n")
    path.write_text("".join(parts))


def _write_one(task) -> Path:
    """One (field, rank) file of :func:`write_fields` (its arguments are in ``_JOB``)."""
    name, r = task
    case, out_dir, time, troot = _JOB["case"], _JOB["out_dir"], _JOB["time"], _JOB["troot"]
    template_time, source_time = _JOB["template_time"], _JOB["source_time"]
    arr, bvals = _JOB["fields"][name], _JOB["boundary_values"].get(name, {})
    owner, neighbour = case.owner, case.neighbour
    rm = case.ranks[r]
    tdir = rank_dir(case, rm, troot)
    tmpl = read_vol_field(tdir / str(template_time) / name, rm.n_cells, patches=rm.patches)
    nc = 1 if arr.ndim == 1 else arr.shape[1]
    if tmpl.ncomp != nc:
        raise ValueError(f"{tdir / str(template_time) / name}: {tmpl.ncomp} components, field has {nc}")
    ok = rm.cell_global >= 0
    src = None
    if not ok.all() or any((rm.face_global[p.slice] < 0).any() for p in rm.patches):
        if source_time is None:
            raise ValueError("the mesh is a subset: source_time is required for the dropped cells")
        src = (tmpl if str(source_time) == str(template_time)
               else read_vol_field(tdir / str(source_time) / name, rm.n_cells, patches=rm.patches))
    internal = (src.internal.copy() if src is not None else np.zeros(_shape(rm.n_cells, nc)))
    internal[ok] = arr[rm.cell_global[ok]]
    boundary = {}
    explicit = _JOB["explicit"]
    local_owner = None
    for lp in rm.patches:
        d = FoamDict(tmpl.boundary[lp.name])
        g = rm.face_global[lp.slice]
        k = g >= 0
        if lp.type == "processor":
            base = (src.patch_value(lp.name, lp.size) if src is not None
                    else tmpl.patch_value(lp.name, lp.size))
            vals = (np.array(base, dtype=np.float64) if base is not None
                    else np.zeros(_shape(lp.size, nc)))
            flip = rm.face_flip[lp.slice][k]
            other = np.where(flip, owner[g[k]], neighbour[g[k]])  # the cell on the other rank
            vals[k] = arr[other]
            d["value"] = vals
        elif explicit and lp.type in _NO_VALUE_TYPES:
            pass                                             # a coupled / constraint patch: its dictionary as it is
        elif explicit and lp.size and not (lp.name in bvals and k.all()):
            # every physical patch carries its values: the owner cell's (zero-gradient) on the faces the mesh
            # subset dropped and on patches no value was given for
            if local_owner is None:
                from Parse_mesh.foamio.foam_dict import read_foam_list
                local_owner = np.asarray(read_foam_list(Path(rm.path) / "constant" / "polyMesh" / "owner",
                                                        "label")[1], dtype=np.int64)
            vals = internal[local_owner[lp.slice]].copy()
            if lp.name in bvals:
                vals[k] = np.asarray(bvals[lp.name], dtype=np.float64)[g[k] - case.patch(lp.name).start]
            if np.isnan(vals).any():
                raise ValueError(f"{name}/{lp.name} on rank {rm.rank}: NaN in the explicit patch values")
            d["value"] = vals
        elif lp.name in bvals and lp.size:
            gp = case.patch(lp.name)
            base = src.patch_value(lp.name, lp.size) if src is not None else None
            if base is None:
                base = tmpl.patch_value(lp.name, lp.size)
            vals = np.array(base, dtype=np.float64) if base is not None else np.zeros(_shape(lp.size, nc))
            vals[k] = np.asarray(bvals[lp.name], dtype=np.float64)[g[k] - gp.start]
            if np.isnan(vals).any():
                raise ValueError(f"{name}/{lp.name} on rank {rm.rank}: NaN in the explicit patch values")
            d["value"] = vals
        boundary[lp.name] = d
    path = (out_dir / f"processor{rm.rank}" if case.decomposed else out_dir) / str(time) / name
    write_vol_field_ascii(path, name, nc, tmpl.dimensions, internal, boundary, str(time), tmpl.extra)
    return path


def run_forked(function, tasks, workers: int) -> list:
    """``[function(t) for t in tasks]`` in ``workers`` forked processes (the module state is inherited, nothing of
    it is pickled); in this process for ``workers <= 1``."""
    tasks = list(tasks)
    if workers <= 1 or len(tasks) <= 1:
        return [function(t) for t in tasks]
    import multiprocessing as mp
    with mp.get_context("fork").Pool(min(workers, len(tasks))) as pool:
        return pool.map(function, tasks, chunksize=1)


def write_fields(case, out_dir, time: str, fields: dict, *, template_time: str = "0", template_root=None,
                 source_time: str | None = None, boundary_values: dict | None = None,
                 precision: int | None = None, workers: int = 1, explicit: bool = False) -> list[Path]:
    """Write global cell ``fields`` {name: (N,) or (N, ncomp)} as OpenFOAM volFields at ``time``.

    template_time / template_root: where the patch dictionaries come from (default: the case's own
    ``[processorN/]<template_time>/<field>``). source_time: time directory (under template_root) giving the values
    of cells / faces not in the global mesh (required when the mesh is a subset). boundary_values: {field: {patch:
    global per-patch array}} written as the patches' ``value``. precision: significant digits (default: ``repr``).
    workers: forked processes over the (field, rank) files. explicit: every physical patch gets a ``value`` (the
    given one, else the owner cell's -- also on the faces a subset dropped); cyclic and constraint patches keep
    their dictionary and take no ``boundary_values``.
    """
    global _FLOAT
    arrays = {}
    for name, arr in fields.items():
        arr = np.asarray(arr, dtype=np.float64)
        if arr.shape[0] != case.n_cells:
            raise ValueError(f"field {name} has {arr.shape[0]} values for {case.n_cells} cells")
        arrays[name] = arr
    previous = _FLOAT
    _JOB.update(case=case, out_dir=Path(out_dir), time=time, fields=arrays,
                troot=Path(template_root) if template_root is not None else Path(case.path),
                template_time=template_time, source_time=source_time, boundary_values=boundary_values or {},
                explicit=bool(explicit))
    try:
        if precision is not None:
            _FLOAT = f"%.{int(precision)}g".__mod__
        return run_forked(_write_one, [(name, r) for name in arrays for r in range(len(case.ranks))], workers)
    finally:
        _FLOAT = previous
        _JOB.clear()
