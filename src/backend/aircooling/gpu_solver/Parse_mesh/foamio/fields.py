"""volField reading and writing in OpenFOAM format."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .foam_dict import DimensionSet, FoamDict, read_foam_file, write_foam_file

__all__ = ["VolField", "read_vol_field", "write_vol_field", "expand_value"]

_CLASS = {1: "volScalarField", 3: "volVectorField", 6: "volSymmTensorField", 9: "volTensorField"}


def expand_value(v, n: int, ncomp: int) -> np.ndarray:
    """Expand an OpenFOAM field value entry (``uniform x`` / ``nonuniform List<T> N(...)`` / bare) to (n[, ncomp])."""
    if isinstance(v, tuple):
        kind = v[0]
        payload = v[-1]
        if kind == "uniform":
            a = np.asarray(payload, dtype=np.float64)
            return np.full(n, float(a)) if ncomp == 1 else np.tile(a.reshape(1, ncomp), (n, 1))
        if kind == "nonuniform":
            a = np.asarray(payload, dtype=np.float64)
            if a.size == 0:
                return np.zeros((n,) if ncomp == 1 else (n, ncomp))
            return a.reshape(n) if ncomp == 1 else a.reshape(n, ncomp)
    a = np.asarray(v, dtype=np.float64)
    if a.ndim == 0:
        return np.full(n, float(a)) if ncomp == 1 else np.tile(np.full(ncomp, float(a)), (n, 1))
    return np.tile(a.reshape(1, ncomp), (n, 1)) if a.size == ncomp else a.reshape((n,) if ncomp == 1 else (n, ncomp))


@dataclass
class VolField:
    name: str
    internal: np.ndarray                       # (N,) or (N, ncomp)
    boundary: dict[str, FoamDict]              # per-patch BC dictionaries as read
    dimensions: DimensionSet = field(default_factory=lambda: DimensionSet([0] * 7))
    extra: dict = field(default_factory=dict)  # other top-level entries (e.g. referenceLevel)

    @property
    def ncomp(self) -> int:
        return 1 if self.internal.ndim == 1 else self.internal.shape[1]

    def patch_value(self, patch: str, n: int) -> np.ndarray | None:
        d = self.boundary.get(patch)
        if d is None or "value" not in d:
            return None
        return expand_value(d["value"], n, self.ncomp)


def read_vol_field(path: str | Path, n_cells: int, patches=None) -> VolField:
    path = Path(path)
    header, d = read_foam_file(path)
    ncomp = {"volScalarField": 1, "volVectorField": 3, "volSymmTensorField": 6, "volTensorField": 9}.get(
        header.cls if header else "volScalarField", 1)
    internal = expand_value(d["internalField"], n_cells, ncomp)
    bf = d.get("boundaryField", FoamDict())
    boundary: dict[str, FoamDict] = {}
    if patches is not None:
        for p in patches:
            entry = bf.lookup(p.name, None)
            if entry is None:
                raise KeyError(f"{path}: no boundary condition for patch {p.name}")
            boundary[p.name] = FoamDict(entry)
    else:
        boundary = {k: FoamDict(v) for k, v in bf.items()}
    extra = {k: v for k, v in d.items() if k not in ("internalField", "boundaryField", "dimensions")}
    return VolField(path.name, internal, boundary, DimensionSet(d.get("dimensions", [0] * 7)), extra)


def _as_value(a: np.ndarray, ncomp: int):
    a = np.asarray(a, dtype=np.float64)
    elem = {1: "scalar", 3: "vector", 6: "symmTensor", 9: "tensor"}[ncomp]
    if a.size and np.all(a == a.reshape(a.shape[0], -1)[0]):
        first = a.reshape(a.shape[0], -1)[0]
        return ("uniform", float(first[0]) if ncomp == 1 else first)
    return ("nonuniform", f"List<{elem}>", a)


def write_vol_field(path: str | Path, f: VolField, patch_values: dict[str, np.ndarray] | None = None,
                    location: str | None = None) -> None:
    """Write ``f``; ``patch_values`` replaces/sets the ``value`` entry of each listed patch."""
    ncomp = f.ncomp
    bf = FoamDict()
    for name, d in f.boundary.items():
        d = FoamDict(d)
        if patch_values and name in patch_values and d.get("type") not in ("empty",):
            d["value"] = _as_value(patch_values[name], ncomp)
        bf[name] = d
    body = FoamDict()
    body["dimensions"] = f.dimensions
    body["internalField"] = _as_value(f.internal, ncomp)
    for k, v in f.extra.items():
        body[k] = v
    body["boundaryField"] = bf
    write_foam_file(path, body, cls=_CLASS[ncomp], obj=f.name, location=location)
