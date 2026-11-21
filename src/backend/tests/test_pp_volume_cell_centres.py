"""Large meshes sample the volume from cell centres; the picture matches the probe."""
import struct
import json

import numpy as np
import pytest

pv = pytest.importorskip("pyvista")
pytest.importorskip("scipy")

from backend.aircooling.runner.postprocess import CachedPostProcessor


def _unpack(payload):
    n = struct.unpack("<I", payload[:4])[0]
    header = json.loads(payload[4:4 + n].rstrip(b"\x00"))
    N = header["dimensions"][0] ** 3
    body = payload[4 + n:]
    return header, np.frombuffer(body[:4 * N], dtype=np.float32), np.frombuffer(body[4 * N:], dtype=np.uint8)


def _pp_with_box(monkeypatch, limit):
    grid = pv.ImageData(dimensions=(41, 21, 21), spacing=(0.05, 0.05, 0.05)).cast_to_unstructured_grid()
    # a solid block in the middle is not fluid
    centres = grid.cell_centers().points
    keep = ~((np.abs(centres[:, 0] - 1.0) < 0.3) & (np.abs(centres[:, 1] - 0.5) < 0.25))
    grid = grid.extract_cells(np.where(keep)[0])
    grid.cell_data["T"] = 290.0 + 5.0 * grid.cell_centers().points[:, 0]
    pp = CachedPostProcessor.__new__(CachedPostProcessor)
    pp._internal = grid
    monkeypatch.setattr(CachedPostProcessor, "_VOLUME_PROBE_MAX_CELLS", limit)
    monkeypatch.setattr(pp, "_resolve_field_name", lambda f: (f, False, 0), raising=False)
    return pp, grid


def test_cell_centre_volume_matches_the_probe(monkeypatch):
    pp, grid = _pp_with_box(monkeypatch, 10)
    fast = pp._volume_by_cell_centres("T", 32, grid.bounds)
    assert fast is not None and fast[1]["sampling"] == "cellCentres"
    _, vals, mask = _unpack(fast[0])
    probe = pv.ImageData(dimensions=(32, 32, 32), origin=(grid.bounds[0], grid.bounds[2], grid.bounds[4]),
                         spacing=[(grid.bounds[2 * a + 1] - grid.bounds[2 * a]) / 31 for a in range(3)]
                         ).sample(grid.cell_data_to_point_data())
    ok = np.asarray(probe["vtkValidPointMask"]).astype(bool)
    agree = (mask.astype(bool) == ok).mean()
    assert agree > 0.95
    both = mask.astype(bool) & ok
    assert np.abs(vals[both] - np.asarray(probe["T"])[both]).max() < 0.2
    assert np.all(vals[~mask.astype(bool)] == -9999.0)


def test_small_meshes_keep_the_probe(monkeypatch):
    pp, grid = _pp_with_box(monkeypatch, 10**9)
    assert pp._volume_by_cell_centres("T", 32, grid.bounds) is None
