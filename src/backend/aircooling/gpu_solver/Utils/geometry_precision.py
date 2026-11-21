"""Opt-in float64 storage of the mesh geometry in the parsed h5 (docs/research/2026-11-01-geometry-float64.md).

The parser computes every geometry quantity in float64 and by default stores it as float32; the loader then casts the
geometry to float32.  A file parsed with the float64 option stores the geometry arrays of ``GEOMETRY_DATASETS`` as
float64 and carries the root attribute ``geometry_dtype = "float64"``; the loader keeps float64 for the geometry (and
the weighted-least-squares matrices built from it) when, and only when, the file carries that attribute.  A file
without the attribute loads as before.

* Parser: ``parse_openfoam_refactored.py --geometry_dtype float64`` or the environment variable
  ``LPCNS_GEOMETRY_DTYPE=float64`` (read by ``BaseMeshManager.save_to_hdf5``; the batch converter's worker processes
  inherit it).
* Loader: ``H5CFDdataset`` sets ``mesh[MESH_KEY] = "float64"`` for such a file; ``torch_dtype(mesh)`` is the floating
  type of the geometry tensors in the mesh dictionary and in the graph built from it.
"""
from __future__ import annotations

import os

import torch

ATTR = "geometry_dtype"
ENV = "LPCNS_GEOMETRY_DTYPE"
MESH_KEY = "geometry_dtype"
CHOICES = ("float32", "float64")

# the floating-point geometry datasets of the mesh h5 (``BaseMeshManager.save_to_hdf5``)
GEOMETRY_DATASETS = (
    "node|node_pos", "face|face_pos", "cell|cell_pos", "face|face_area", "face|face_normal", "cell|cell_volume",
    "cpd|cell_pos", "cells_face_area", "cells_face_normal",
)


def checked(name) -> str:
    """``name`` as one of ``CHOICES`` (case and surrounding blanks ignored)."""
    value = str(name).strip().lower()
    if value not in CHOICES:
        raise ValueError(f"geometry dtype must be one of {CHOICES}, got {name!r}")
    return value


def requested() -> str:
    """The storage type the parser writes: ``LPCNS_GEOMETRY_DTYPE`` if set, else ``"float32"``."""
    return checked(os.environ.get(ENV, "float32"))


def of_file(hf) -> str:
    """``"float64"`` for an open h5 file whose root attribute ``geometry_dtype`` is ``"float64"``, else ``"float32"``."""
    if ATTR not in hf.attrs:
        return "float32"
    value = hf.attrs[ATTR]
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return checked(value)


def torch_dtype(mesh) -> torch.dtype:
    """The floating type of the geometry of a loaded mesh dictionary: float64 iff ``mesh[MESH_KEY] == "float64"``."""
    return torch.float64 if mesh.get(MESH_KEY) == "float64" else torch.float32
