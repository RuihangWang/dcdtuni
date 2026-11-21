"""
HeteroData-based loader for FV domain.
Defines loaders and compatibility wrapper for existing FVGraph interface.
"""

import sys
import os
file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)

import logging
logger = logging.getLogger(__name__)

from typing import List
import numpy as np
import torch
from torch_geometric.data import Batch as HeteroBatch
from torch_geometric.data import HeteroData
from .Graph_dataset import HeteroGraphDataset, HeteroFVGraph


def graph_collate_fn(batch):
    """Custom collate function for heterogeneous graphs."""
    return HeteroBatch.from_data_list(batch)

from FVdomain.SetBC.FvPatch import fvPatchBatch, fvPatch
def fvpatch_collate_fn(fvpatch_list: List[fvPatch], graph_data_list: List[HeteroData]):
    # 这里list_list由每个case包含的fvpatch组成的list
    return fvPatchBatch.from_fvpatch_list(fvpatch_list, graph_data_list)

from FVdomain.SetBC.onthefly.BCbase import BoundaryConditionBatch
def bcpatch_collate_fn(bcpatch_dict_list: List[dict], fvpatch_list: List[fvPatch]):
    """Collate function for batching bcpatch dictionaries."""
    return BoundaryConditionBatch.from_bcpatch_list(bcpatch_dict_list, fvpatch_list)

class HeteroGraphLoader:
    """Compatibility loader that returns HeteroFVGraph objects with threaded prefetch."""

    def __init__(self, base_dataset, batch_size=1, num_workers=0,
                 pin_memory=True, shuffle=True, prefetch_factor=0, device=None):
        self.base_dataset = base_dataset
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.pin_memory = pin_memory
        self.prefetch_factor = prefetch_factor

        # --- Static-graph cache (perf, batch_size==1 mmap path) -- STRICTLY OPT-IN ---
        # When enabled, the assembled+collated graph for each slot is built (and moved
        # to `device`) ONCE; subsequent steps reuse it and only refresh the evolving
        # fields (phi=x, time_step, data-driven GT). This removes the ~24 ms/step static
        # clone+collate and the ~19 ms/step H2D, but it makes the graph DEVICE-resident
        # (~148 MiB/slot, measured) which can OOM memory-tight runs, so it is OFF by
        # default: every Adam/Adam-ddp run uses the original verified per-step rebuild +
        # explicit .to(device) path UNLESS the user exports ENABLE_GRAPH_CACHE=1.
        # NOTE the trainers move the graph to device by testing ACTUAL tensor residency
        # (not this flag), so the cache off/on and batch_size>1 are all handled safely.
        self.device = device if os.getenv("ENABLE_GRAPH_CACHE") == "1" else None
        self._graph_cache = {}

        self.hetero_dataset = HeteroGraphDataset(base_dataset)

        self.dataset_size = len(self.base_dataset.meta_pool)
        self.indices = list(range(self.dataset_size))
        logger.info(
            f"✅ Loader initialized: {self.dataset_size} cases, "
            f"batch_size={batch_size}, shuffle={shuffle}, prefetch={prefetch_factor}"
        )

    def _build_one_batch(self, batch_indices):
        # Fast path: static-graph cache for the common batch_size==1 mmap case.
        # The assembled+collated graph for each slot is built ONCE (skipping the
        # ~24 ms/step re-clone of ~175 MB static mesh tensors from the mmap + the
        # re-collate); thereafter only the evolving fields (phi=x, time_step,
        # data-driven GT) are refreshed on the cached graph. NOTE that
        # HeteroGraph.to() (Graph_data.py) moves tensors IN PLACE and returns self,
        # so the .to(device) below migrates the cached graph itself: from the second
        # step on, every cached slot stays device-resident (~148 MiB/slot measured),
        # which is exactly why the cache is opt-in rather than always on (see the
        # __init__ note). Synchronous with the default prefetch_factor=0 (no producer
        # thread) -> no race with refresh_pool's phi write-back.
        if (self.device is not None and self.batch_size == 1
                and hasattr(self.base_dataset, 'slots')):
            idx = batch_indices[0]
            slot = self.base_dataset.slots[idx]
            cached = self._graph_cache.get(idx)
            # Invalidate when reset_env regenerated this slot's case: the FVM-loss
            # reset_env path (Graph_pool.reset_env) calls transform() to re-sample
            # parametric BCs and REPLACES slot.meta_objects with a new dict, which would
            # make the cached fvpatch_batch/bcpatch_batch (and init_x/theta) stale. We
            # detect the meta-object swap by identity (`is`) and rebuild. Holding the
            # reference also pins the dict, avoiding id()-reuse false matches.
            if cached is None or cached._cache_meta_ref is not slot.meta_objects:
                cached = self._build_one_batch_cpu(batch_indices)
                cached._cache_meta_ref = slot.meta_objects
                self._graph_cache[idx] = cached
            else:
                self._refresh_dynamic_fields(cached, idx)
            # HeteroGraph.to() migrates the cached graph in place and returns it,
            # so this is idempotent once the slot is already resident. The excluded
            # host-side index keys stay on CPU (refresh_pool / VTK consume them on
            # host).
            device_hd = cached._hetero_data.to(
                self.device, exclude_keys=['slot_idx', 'graph_index', 'global_idx']
            )
            return HeteroFVGraph(device_hd, cached.fvpatch_batch, cached.bcpatch_batch)
        return self._build_one_batch_cpu(batch_indices)

    def _build_one_batch_cpu(self, batch_indices):
        graph_data_list = []
        fvpatch_list_list = []
        bcpatch_list_list = []
        for idx in batch_indices:
            graph_data, fvpatch_list, bcpatch_list = self.hetero_dataset[idx]
            graph_data_list.append(graph_data)
            fvpatch_list_list.append(fvpatch_list)
            bcpatch_list_list.append(bcpatch_list)

        graph_batch = graph_collate_fn(graph_data_list)
        fvpatch_batch = fvpatch_collate_fn(fvpatch_list_list, graph_data_list)
        bcpatch_batch = bcpatch_collate_fn(bcpatch_list_list, fvpatch_list_list)
        return HeteroFVGraph(graph_batch, fvpatch_batch, bcpatch_batch)

    def _refresh_dynamic_fields(self, fv_graph, idx):
        """Refresh ONLY the per-step-evolving fields on the cached graph.

        Everything else (static mesh geometry/connectivity, BC patch objects) is
        case-static and reused. Mirrors the dynamic reads in
        HeteroGraphDataset._get_mmap (phi=slot.phi_tensor, time_step, data-driven GT).
        The values are written as CPU tensors; the .to(device) call in
        _build_one_batch does the H2D for them.
        """
        slot = self.base_dataset.slots[idx]
        hd = fv_graph._hetero_data
        # Autoregressive phi state. Clone so in-place BC ghost-cell writes during the
        # forward cannot corrupt the slot's mmap buffer (matches _get_mmap's clone).
        hd['cpd_cell'].x = slot.phi_tensor.clone()
        # Scalar time step (advanced by refresh_pool.increment_time_step()).
        hd['graph'].time_step = torch.tensor(float(slot.time_step), dtype=torch.float32)
        # Data-driven rollout: GT label at the current timestep (FVM-loss has none).
        pool_loss_mode = getattr(self.base_dataset, "loss_mode", "fvm")
        if pool_loss_mode == "data_driven" and getattr(slot, "has_labels", False):
            t_target = min(slot.time_step, slot.n_timesteps - 1)
            phi_GT_np = np.asarray(slot.get_label_at(t_target)).copy()
            t_offset = getattr(slot, '_label_T_offset', None)
            if t_offset is not None:
                phi_GT_np[:, -1] += t_offset
            hd['cpd_cell'].phi_GT_cpd_cell = torch.from_numpy(phi_GT_np).to(torch.float32)

    def __iter__(self):
        import random as _random
        from queue import Queue
        from threading import Thread

        if self.shuffle:
            indices = self.indices.copy()
            _random.shuffle(indices)
        else:
            indices = self.indices

        all_batches = [
            indices[i:i + self.batch_size]
            for i in range(0, len(indices), self.batch_size)
        ]

        if self.prefetch_factor <= 0:
            for batch_indices in all_batches:
                yield self._build_one_batch(batch_indices)
            return

        q: Queue = Queue(maxsize=self.prefetch_factor)
        _sentinel = None

        def _producer():
            for batch_indices in all_batches:
                item = self._build_one_batch(batch_indices)
                q.put(item)
            q.put(_sentinel)

        t = Thread(target=_producer, daemon=True)
        t.start()

        while True:
            item = q.get()
            if item is None:
                break
            yield item

        t.join()

    def __len__(self):
        """返回batch数量"""
        return (self.dataset_size + self.batch_size - 1) // self.batch_size

    def __getitem__(self, indices):
        """Support direct indexing: loader[indices] - returns HeteroFVGraph"""
        hetero_graph = self.hetero_dataset[indices]
        return HeteroFVGraph(hetero_graph)

