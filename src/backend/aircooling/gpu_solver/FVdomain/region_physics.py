"""Per-query physics of a multi-region case (mesh schema v2, docs/development/mesh-schema-v2.md sections 4-5).

The mesh h5 says WHICH cells form which region / zone (``Utils.mesh_schema``); fvconfig says what they are made of
and what heats them, as parametric query values:

    [regions.<name>]  kind = "fluid" | "solid"; fluid: rho, cp (only needed to convert kappa / power);
                      solid: kappa (W/m/K) or alpha_ratio (k / k_fluid)
    [zones.<name>]    kappa or alpha_ratio: a material inside a region, overriding the region's value
    [sources.<name>]  zone = "<zone>" or region = "<region>", field = "T", and one of
                      rate (K/s, the energy equation's source per unit volume), power_density (W/m^3), power (W)

``apply`` turns them into, per case and per query (it runs inside ``CFDmesh.__call__``, so every reset re-samples):

    cell|material_id  [n_cells] int32   material of each cell (row of theta_material)
    cell|source_id    [n_cells] int32   heat source of each cell (row of theta_source; 0 = none)
    theta_material    [n_mat, 2] float64  (diffusivity factor relative to the fluid's theta_PDE alpha, kind code)
    theta_source      [n_src + 1, 1] float64  source rate in K/s (row 0 = 0)

A trivial case (one fluid region, nothing configured) gets none of these; ``attach`` gives its graph the neutral
tables, so v1 and v2 cases batch together and a v1 case's numbers do not change.
"""
from __future__ import annotations

import numpy as np
import torch

KIND_CODE = {"fluid": 0, "solid": 1}
# theta_PDE column of the fluid's thermal diffusivity, per application that reads the region physics (keys lower
# case: fvconfig spells solver types in either case, the application registry matches them case-insensitively)
ALPHA_INDEX = {"buoyantboussinesqpicoext": 7}
MESH_KEYS = ("cell|material_id", "cell|source_id", "theta_material", "theta_source")


def apply(mesh, resolve):
    """Add the region physics tensors to ``mesh`` (module docstring). ``resolve(mesh, value, key=...)`` is
    ``CFDmesh._resolve_param_value``."""
    for k in MESH_KEYS:
        mesh.pop(k, None)
    schema = mesh.get("region_schema")
    fvconfig = mesh["fvconfig"]
    cfg_regions = dict(fvconfig.get("regions") or {})
    cfg_zones = dict(fvconfig.get("zones") or {})
    cfg_sources = dict(fvconfig.get("sources") or {})
    if schema is None or (schema.is_trivial and not (cfg_regions or cfg_zones or cfg_sources)):
        return mesh
    where = f"case {mesh.get('case_name')!r}"
    solver_type = str(fvconfig.get("solver", {}).get("type", "")).lower()
    if solver_type not in ALPHA_INDEX:
        raise ValueError(f"{where}: regions / zones / sources are implemented for {sorted(ALPHA_INDEX)}, "
                         f"not for solver {solver_type!r} (it would ignore them)")
    if fvconfig.get("fvSchemes", {}).get("chtRegions"):
        raise ValueError(f"{where}: fvSchemes.chtRegions and schema-v2 regions both set; use one")

    names = [r["name"] for r in schema.regions]
    for table, known, label in ((cfg_regions, names, "regions"), (cfg_zones, [z["name"] for z in schema.zones],
                                                                   "zones")):
        unknown = sorted(set(table) - set(known))
        if unknown:
            raise ValueError(f"{where}: fvconfig [{label}] names {unknown}, the mesh has {known}")

    def val(cfg, key, label):
        return float(resolve(mesh, cfg[key], key=f"{label}.{key}")) if key in cfg else None

    fluids = [r for r in schema.regions if r["kind"] == "fluid"]
    rho_cp = None
    if len(fluids) == 1:
        fc = cfg_regions.get(fluids[0]["name"], {})
        rho, cp = val(fc, "rho", f"regions.{fluids[0]['name']}"), val(fc, "cp", f"regions.{fluids[0]['name']}")
        rho_cp = rho * cp if rho is not None and cp is not None else None
    alpha_f = float(mesh["theta_PDE"][0, ALPHA_INDEX[solver_type]])

    def need_rho_cp(what):
        if rho_cp is None:
            raise ValueError(f"{where}: {what} needs rho and cp of the (single) fluid region in fvconfig "
                             f"[regions.<fluid>]")
        return rho_cp

    def factor(cfg, label):
        """Diffusivity of a material relative to the fluid's, or None if ``cfg`` sets none."""
        r = val(cfg, "alpha_ratio", label)
        if r is not None:
            return r
        k = val(cfg, "kappa", label)
        if k is not None:
            return k / need_rho_cp(f"{label}.kappa") / alpha_f
        return None

    rows = []                                   # (factor, kind code)
    for r in schema.regions:
        cfg = cfg_regions.get(r["name"], {})
        if "kind" in cfg and cfg["kind"] != r["kind"]:
            raise ValueError(f"{where}: fvconfig says region {r['name']!r} is {cfg['kind']!r}, the mesh {r['kind']!r}")
        f = factor(cfg, f"regions.{r['name']}")
        if f is None:
            if r["kind"] != "fluid":
                raise ValueError(f"{where}: solid region {r['name']!r} needs kappa or alpha_ratio in fvconfig")
            f = 1.0
        rows.append((f, KIND_CODE[r["kind"]]))
    region_id = schema.region_ids().astype(np.int64)
    material = region_id.copy()
    for zname, zcfg in cfg_zones.items():
        f = factor(zcfg, f"zones.{zname}")
        if f is None:
            continue
        cells = schema.zone_members(zname)
        kinds = np.unique(region_id[cells])
        if kinds.size != 1:
            raise ValueError(f"{where}: material zone {zname!r} spans several regions")
        material[cells] = len(rows)
        rows.append((f, rows[int(kinds[0])][1]))

    volume = mesh["cell|cell_volume"].reshape(-1).double().numpy()
    source_id = np.zeros(schema.num_cells, np.int64)
    rates = [0.0]
    for sname, scfg in cfg_sources.items():
        label = f"sources.{sname}"
        if scfg.get("field", "T") != "T":
            raise NotImplementedError(f"{where}: {label}: only energy sources (field = 'T') are implemented")
        if "zone" in scfg:
            cells = schema.zone_members(scfg["zone"])
        elif "region" in scfg:
            cells = np.flatnonzero(region_id == schema.region_index(scfg["region"]))
        else:
            raise ValueError(f"{where}: {label} needs zone = ... or region = ...")
        if cells.size == 0:
            raise ValueError(f"{where}: {label} covers no cell")
        rate, density, power = val(scfg, "rate", label), val(scfg, "power_density", label), val(scfg, "power", label)
        if sum(v is not None for v in (rate, density, power)) != 1:
            raise ValueError(f"{where}: {label} needs exactly one of rate, power_density, power")
        if rate is None:
            density = density if density is not None else power / float(volume[cells].sum())
            rate = density / need_rho_cp(label)
        if np.any(source_id[cells] != 0):
            raise ValueError(f"{where}: {label} overlaps another source")
        source_id[cells] = len(rates)
        rates.append(rate)

    mesh["cell|material_id"] = torch.from_numpy(material.astype(np.int32))
    mesh["cell|source_id"] = torch.from_numpy(source_id.astype(np.int32))
    mesh["theta_material"] = torch.tensor(rows, dtype=torch.float64).reshape(-1, 2)
    mesh["theta_source"] = torch.tensor(rates, dtype=torch.float64).reshape(-1, 1)
    return mesh


def attach(hetero_graph, get, num_cells: int) -> None:
    """Put the region physics on a graph; ``get(key)`` returns the mesh tensor or None. Neutral tables for a case
    without region physics (material 0 = the fluid, factor 1; no source)."""
    mid, sid = get("cell|material_id"), get("cell|source_id")
    tm, ts = get("theta_material"), get("theta_source")
    if mid is None:
        mid = torch.zeros(num_cells, dtype=torch.int32)
        sid = torch.zeros(num_cells, dtype=torch.int32)
        tm = torch.tensor([[1.0, 0.0]], dtype=torch.float64)
        ts = torch.zeros(1, 1, dtype=torch.float64)
    hetero_graph['cell'].material_id = mid.to(torch.int32)
    hetero_graph['cell'].source_id = sid.to(torch.int32)
    hetero_graph['graph'].theta_material = tm.to(torch.float64)
    hetero_graph['graph'].theta_source = ts.to(torch.float64)
    hetero_graph._num_materials = int(tm.shape[0])
    hetero_graph._num_sources = int(ts.shape[0])
