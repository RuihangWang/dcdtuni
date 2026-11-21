"""One case-loading API for all three method families.

Every family funnels through H5CFDdataset.__getitem__ -> CFDmesh; what differed
was the copy-pasted epilogue around it (pool choice, loader, one-graph pull,
Integrator registration). This module owns that epilogue once.

Stages:
  load_mesh_only(h5)        -> (mesh_dict, phi)      # operator-level MMS / GradTest
  load_case(stage="graph")  -> pool + loader          # GNN training (model owns Integrator)
  load_case(stage="solver") -> + fv_graph/meta/integrator (registered)  # ODIL / linear
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from FVdomain.Graph_pool import HeteroDataPool, MmapDataPool
from FVdomain.Graph_loader import HeteroGraphLoader
from FVdomain.Load_mesh import H5CFDdataset
from FVsolver.integrator import Integrator


@dataclass
class LoadedCase:
    params: Any
    pool: Any
    loader: Any
    fv_graph: Any = None
    meta_data: Any = None
    case_name: str | None = None
    graph_index: int | None = None
    integrator: Any = None


def load_mesh_only(h5_path, params=None):
    """GradTest-style direct load: no pool, no HeteroData, no Integrator.

    Minimal params contract measured in GradTest/_load_mesh_via_pipeline:
    only ``fourier_num_freqs`` is consumed.
    """
    if params is None:
        params = SimpleNamespace(fourier_num_freqs=0)
    dataset = H5CFDdataset(params=params, case_list=[str(h5_path)])
    return dataset[0]


def load_case(
    dataset_dir,
    params,
    *,
    stage="solver",
    pool="legacy",
    device=None,
    batch_size=1,
    shuffle=False,
    loader_device=None,
    resume=False,
    resume_from=None,
    split=None,
    loss_mode=None,
    total_dataset_size=None,
    world_size=None,
    param_overrides=None,
):
    if stage not in ("graph", "solver"):
        raise ValueError(f"unknown stage {stage!r}; use 'graph' or 'solver' "
                         "(or load_mesh_only for the raw-mesh stage)")
    if pool == "legacy":
        datasets = HeteroDataPool(
            params=params, device=device, state_save_dir=params.state_save_dir
        )
        params = datasets.load_mesh_to_cpu(dataset_dir=dataset_dir)
    elif pool == "mmap":
        datasets = MmapDataPool(
            params=params, device=device, state_save_dir=params.state_save_dir
        )
        # Forward the pool-shaping arguments.  Without them,
        # every caller of load_case silently got split="all": the FVM arms drew
        # boundary conditions from the WHOLE parametric range, including the
        # val/test cases the data-driven arms hold out, and no caller could
        # reach the rank-partitioning path at all.
        params = datasets.load_mesh_to_mmap(
            dataset_dir=dataset_dir, resume=resume, resume_from=resume_from,
            split=split if split is not None else getattr(params, "split", "all"),
            loss_mode=(loss_mode if loss_mode is not None
                       else getattr(params, "loss_mode", "fvm")),
            total_dataset_size=total_dataset_size,
            world_size=world_size if world_size is not None else 1,
            param_overrides=param_overrides,
        )
    else:
        raise ValueError(f"unknown pool strategy {pool!r}; use 'legacy' or 'mmap'")

    loader_kwargs = dict(
        base_dataset=datasets,
        batch_size=batch_size,
        num_workers=0,
        pin_memory=False,
        shuffle=shuffle,
    )
    if loader_device is not None:
        loader_kwargs["device"] = loader_device
    loader = HeteroGraphLoader(**loader_kwargs)

    case = LoadedCase(params=params, pool=datasets, loader=loader)
    if stage == "graph":
        return case

    fv_graph = next(iter(loader))
    graph_index = int(fv_graph.graph_Index.graph_index.cpu().item())
    meta_data = datasets.meta_pool[graph_index]
    case.case_name = str(meta_data["case_name"])
    case.graph_index = graph_index
    case.meta_data = meta_data
    case.fv_graph = fv_graph.to(device, exclude_keys=["global_idx", "graph_index"])

    integrator = Integrator(params)
    integrator.register_properties(case.fv_graph, force_register=True)
    case.integrator = integrator
    return case
