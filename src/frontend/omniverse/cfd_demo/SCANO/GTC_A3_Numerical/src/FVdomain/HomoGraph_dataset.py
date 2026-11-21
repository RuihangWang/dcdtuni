"""
HomoGraph Dataset for FV domain.
Defines the homogeneous graph data structure and dataset for data-driven mode.
"""
import sys
import os
file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)

import torch
from torch_geometric.data import Dataset
import h5py
from torch_geometric.utils import k_hop_subgraph
import logging
logger = logging.getLogger(__name__)
import json
import math
from pathlib import Path
from typing import Dict, List, Optional
from Utils.utilities import FaceType
from Utils.spatial_interpolation import find_nearest_cell_indices
from Parse_mesh.text_sidecar import load_patch_list_from_h5, load_text_conditioning_from_h5
from FVdomain.Graph_data import HomoGraph


# ============================================================================
# Field-set presets (generalizes the hardcoded 5-field [u,v,w,p,T] plumbing)
# ============================================================================
# Each spec is (h5_key, n_comp, transform). `transform` is applied at read time
# and inverted at the metric/VTU-export layer. Default preset "uvwpT" reproduces
# the original GTC/Slab behavior exactly; "airfrans" opts into 4 fields [u,v,p,nut]
# (2D, no w/T) with an asinh transform on the multi-scale turbulent viscosity.
FIELD_SET_PRESETS = {
    "uvwpT": {
        "specs": [("uvw_cpd_cell", 3, None), ("press_cpd_cell", 1, None), ("temp_cpd_cell", 1, None)],
        "names": ["u", "v", "w", "p", "T"],
    },
    # Buoyancy-pressure variant: reads p_rgh instead of total pressure. The without-init-condition
    # GTC baselines condition on and predict [u, v, w, p_rgh, T] per the physical (Boussinesq) BC set.
    # NOTE: the pressure channel is NAMED "p" (not "p_rgh") on purpose — the name is only the
    # per-field metric label, and the whole downstream stack (inferencer display, aggregate_sweep.py,
    # sweep_table columns) keys pressure by "p". The physical field is still p_rgh via the spec.
    "uvw_prgh_T": {
        "specs": [("uvw_cpd_cell", 3, None), ("p_rgh_cpd_cell", 1, None), ("temp_cpd_cell", 1, None)],
        "names": ["u", "v", "w", "p", "T"],
    },
    "airfrans": {
        "specs": [("uvw_cpd_cell", 2, None), ("press_cpd_cell", 1, None), ("nut_cpd_cell", 1, "asinh")],
        "names": ["u", "v", "p", "nut"],
    },
    "olbc": {
        # olbc Poisson: single scalar solution (stored in sol_cpd_cell). The PDE forcing f and the
        # per-boundary-node BC type/value are inputs (carried in data.x + boundary KV), not predicted.
        "specs": [("sol_cpd_cell", 1, None)],
        "names": ["sol"],
    },
}


def resolve_field_set(name):
    """Resolve a field_set preset name -> {specs, names, output_size}."""
    if name not in FIELD_SET_PRESETS:
        raise ValueError(
            f"Unknown field_set '{name}'; expected one of {sorted(FIELD_SET_PRESETS)}"
        )
    preset = FIELD_SET_PRESETS[name]
    specs = preset["specs"]
    return {
        "specs": specs,
        "names": list(preset["names"]),
        "output_size": sum(n_comp for _, n_comp, _ in specs),
    }


def read_field_stack(h5_handle, field_specs, prefix, time_index):
    """Read & concat the configured field arrays into a [N, output_size] tensor.

    prefix: an H5 group prefix (e.g. 'field_unconverged', 'field_target') whose members
            are stored 2-D [N, n_comp]; or None for the flat layout where each field is
            stored [T, N, n_comp] and `time_index` selects the slice (0=init, -1=target).
    Applies each spec's transform (e.g. asinh on nut) at read time. Raises ValueError if a
    stored field's component width does not match the preset (guards silent layout drift).
    """
    parts = []
    for key, n_comp, transform in field_specs:
        if prefix:
            arr = h5_handle[f"{prefix}/{key}"][:]
        else:
            arr = h5_handle[key][time_index, :, :]
        t = torch.from_numpy(arr).to(torch.float32)
        if t.ndim != 2 or t.shape[1] != n_comp:
            raise ValueError(
                f"field_set expects {key} with width {n_comp}, got shape {tuple(t.shape)} "
                f"in {getattr(h5_handle, 'filename', '<h5>')}"
            )
        if transform == "asinh":
            t = torch.asinh(t)
        elif transform is not None:
            raise ValueError(f"Unknown field transform '{transform}' for {key}")
        parts.append(t)
    return torch.cat(parts, dim=1)


# ============================================================================
# Helper Functions
# ============================================================================

def collate_fn_filter_none(batch):
    """
    自定义collate函数，过滤掉None样本

    当推理模式下pivot_pool被取空时，get()会返回None
    这个函数会过滤掉这些None，只返回有效样本
    """
    # 过滤掉None
    batch = [item for item in batch if item is not None]

    if len(batch) == 0:
        # 如果整个batch都是None，返回None（表示应该停止迭代）
        return None

    # 使用PyG的默认collate
    from torch_geometric.data import Batch
    return Batch.from_data_list(batch)


def _parse_pivot_roi() -> Dict:
    """Pivot-pool GEOMETRY selector for the contiguous-vs-scattered experiment.

    The model has only ever seen pivots drawn as a permutation slice over the whole domain
    (scattered). A user-dragged ROI is a contiguous box, which is therefore out of
    distribution and UNVALIDATED. This switch lets both arms run through the SAME sampler,
    so the two are not confounded by two different code paths.

        SCANO_PIVOT_POOL=full     (default, or unset) -> untouched baseline. Every branch
                                  below is skipped and the pool is torch.arange(num_cells)
                                  shuffled exactly as before, so the scattered baseline is
                                  bit-identical to the published run.
        SCANO_PIVOT_POOL=box      -> pool = the cpd ids whose centroid lies in the box.
        SCANO_PIVOT_POOL=scatter  -> pool = a scattered subset of the WHOLE domain with the
                                  SAME cardinality as that box, taken from the same shuffle.
                                  This is the paired control for `box`.

        SCANO_PIVOT_POOL=box+anchor -> pool = the box's cpd ids UNION a room-wide sprinkle
                                  of SCANO_PIVOT_ANCHOR anchors drawn from the whole domain.
                                  This is the measured mitigation for the box arm's failure:
                                  the box alone costs +0.405, the same node budget placed as
                                  one distant COMPACT block costs +0.391 (no help), but spread
                                  room-wide it costs +0.204 at 1,748 anchors and +0.049 at
                                  8,944. What heals is the SPREAD of the pivot cloud, not its
                                  size — because the query basis is pooled node-set-globally
                                  eight times per forward (PatchCrossAttentionEncoder.py:227-229
                                  plus seven AttnProcessors at GraphTransolver.py:94-98), so a
                                  spatially confined sample collapses the slice basis
                                  (entropy 2.699 -> 2.286, ~14.9 -> ~9.8 effective slices of
                                  32) and can no longer ADDRESS the complete boundary the
                                  cross-attention injects. A halo hugging the box is compact
                                  and therefore does NOT work; the anchors must be spread.
                                  NOTE the result is GREY, not PASS: +0.049 sits above the
                                  0.020 PASS threshold, and K is NON-MONOTONE (30,000 anchors
                                  score 0.2704, worse than 8,944's 0.2613), so K must be swept.

        SCANO_PIVOT_ANCHOR=<K>    number of room-wide anchors for box+anchor (default 8944,
                                  the best value measured so far).

        SCANO_PIVOT_BOX="xmin,ymin,zmin,xmax,ymax,zmax"   (metres, required by box/scatter/
                                  box+anchor)
        SCANO_PIVOT_BUDGET=<int>  optional pivots-per-draw override. Left unset, the budget
                                  becomes |box| in BOTH arms, i.e. exactly one draw whose
                                  pivots are the whole box — the drag-and-simulate case.

    Both arms therefore share the pivot count per draw AND the draw count
    (ceil(len(pool)/budget) == 1), and differ only in where the pivots sit.

    Two further knobs turn the SAME sampler into the coverage-matched contiguous arm,
    which is what separates "contiguous geometry is out of distribution" from "one draw
    instead of nine loses the draw-averaging":

        SCANO_PIVOT_ORDER=random  (default) -> pool order is the existing shuffle, so every
                                  budget-slice at _sample_from_pool is a SCATTERED set.
        SCANO_PIVOT_ORDER=spatial -> the pool is reordered by a recursive median (k-d) split
                                  into ceil(len(pool)/budget) compact clusters, so every
                                  budget-slice is an axis-aligned CONTIGUOUS block — a
                                  dragged ROI — at identical budget, identical draw count and
                                  identical whole-domain coverage.
        SCANO_PIVOT_REPEAT=<R>    (default 1) -> the pool is emitted R times, each rotated by
                                  r*budget//R, so each cpd id is a pivot exactly R times from
                                  R DIFFERENT block partitions. R>1 restores the draw
                                  redundancy that the 51-draw scattered baseline enjoys
                                  (9.368 draws per cell; R=6 gives 9.498), so nRMSE(spatial,R=6) vs
                                  nRMSE(random,R=1) isolates contiguity alone.
    """
    order = (os.environ.get("SCANO_PIVOT_ORDER") or "random").strip().lower()
    if order in ("", "0", "off", "none"):
        order = "random"
    if order not in ("random", "spatial"):
        raise ValueError(
            f"SCANO_PIVOT_ORDER must be random|spatial, got {order!r}"
        )
    repeat_env = (os.environ.get("SCANO_PIVOT_REPEAT") or "").strip()
    repeat = max(1, int(repeat_env)) if repeat_env else 1

    mode = (os.environ.get("SCANO_PIVOT_POOL") or "full").strip().lower()
    if mode in ("", "full", "0", "off", "none"):
        return {"mode": "full", "box": None, "budget": None,
                "order": order, "repeat": repeat}
    if mode not in ("box", "scatter", "box+anchor"):
        raise ValueError(
            f"SCANO_PIVOT_POOL must be one of full|box|scatter|box+anchor, got {mode!r}"
        )
    raw = (os.environ.get("SCANO_PIVOT_BOX") or "").strip()
    # Two accepted forms, and the DEFAULT of the bare one is load-bearing.
    #
    #   "xmin,ymin,zmin,xmax,ymax,zmax"          -> SOLVER frame, as it always meant.
    #   {"min":[...], "max":[...], "frame":"demo"|"solver"}
    #
    # The bare CSV keeps meaning solver coordinates because 93 recorded experiment arms
    # used it that way; re-interpreting it would silently change what those runs measured.
    # The JSON form exists because the demo authors its ROI in the DEMO frame (Y-up) and
    # the box must be converted before it is compared against cpd|centroid. Passing the
    # demo numbers through unconverted addresses a different region of the room and fails
    # silently — the anchors dominate the pool, so the field still looks plausible.
    frame = "solver"
    if raw.startswith("{"):
        try:
            _j = json.loads(raw)
            lo_j, hi_j = _j["min"], _j["max"]
            frame = str(_j.get("frame", "solver")).strip().lower()
        except (ValueError, KeyError, TypeError) as e:
            raise ValueError(f"SCANO_PIVOT_BOX JSON must carry min/max: {raw!r} ({e})")
        if frame not in ("demo", "solver"):
            raise ValueError(
                f"SCANO_PIVOT_BOX frame must be demo|solver, got {frame!r}")
        parts = list(lo_j) + list(hi_j)
    else:
        parts = [p for p in raw.replace(";", ",").split(",") if p.strip()]
    if len(parts) != 6:
        raise ValueError(
            f"SCANO_PIVOT_POOL={mode} requires SCANO_PIVOT_BOX="
            f"'xmin,ymin,zmin,xmax,ymax,zmax' or a min/max JSON object; got {raw!r}"
        )
    box = [float(p) for p in parts]
    if not all(box[i] < box[i + 3] for i in range(3)):
        raise ValueError(f"SCANO_PIVOT_BOX min must be < max per axis; got {box}")
    budget_env = (os.environ.get("SCANO_PIVOT_BUDGET") or "").strip()
    budget = int(budget_env) if budget_env else None
    anchor_env = (os.environ.get("SCANO_PIVOT_ANCHOR") or "").strip()
    anchors = max(0, int(anchor_env)) if anchor_env else 8944
    # How the anchors are placed. The measured mechanism is that SPREAD heals and
    # compactness does not, so placement should matter as much as count: a stratified
    # sprinkle (one anchor per occupied grid cell, round-robin) covers the room more
    # evenly than a uniform random draw of the same size, which clumps by chance.
    a_mode = (os.environ.get("SCANO_PIVOT_ANCHOR_MODE") or "random").strip().lower()
    if a_mode not in ("random", "stratified"):
        raise ValueError(
            f"SCANO_PIVOT_ANCHOR_MODE must be random|stratified, got {a_mode!r}")
    return {"mode": mode, "box": box, "box_frame": frame, "budget": budget,
            "order": order, "repeat": repeat, "anchors": anchors,
            "anchor_mode": a_mode}


def _pivot_box_to_solver(roi, depth: float):
    """Pivot box -> (solver_lo, solver_hi). Solver-frame boxes pass through unchanged.

    The conversion is DELEGATED to Post_process.export_roi rather than reimplemented. The
    depth axis flips, and a sign error there mirrors the selection about the room's
    mid-depth — which reads as a plausible field in the wrong place, not as a bug. One
    implementation, used by both the export path and this one, is the only way that error
    cannot be introduced on one side alone.
    """
    box = roi["box"]
    lo, hi = [float(v) for v in box[:3]], [float(v) for v in box[3:]]
    if roi.get("box_frame", "solver") == "solver":
        return lo, hi
    from Post_process.export_roi import demo_box_to_solver
    return demo_box_to_solver(lo, hi, float(depth))


def _kd_block_order(centroids, num_blocks: int):
    """Reorder ids so that each consecutive 1/num_blocks slice is a COMPACT spatial cluster.

    Recursive median split on the longest axis, with an exact target count per side, so the
    concatenated leaves partition the ids into `num_blocks` axis-aligned clusters of equal
    size (+/-1). Slicing that order in budget-sized chunks — which is exactly what
    _sample_from_pool already does — therefore hands each draw a contiguous block instead of
    a scattered set, with nothing else about the draw changed.

    Args:
        centroids: numpy [M, 3] float array, positions of the ids in pool order.
        num_blocks: how many compact clusters to produce.
    Returns:
        numpy int64 [M] permutation of range(M).
    """
    import numpy as _np

    order = _np.arange(len(centroids), dtype=_np.int64)
    if num_blocks <= 1 or len(order) <= 1:
        return order

    out = []
    # (ids, k) — split `ids` into `k` compact groups. Iterative to avoid deep recursion.
    stack = [(order, int(num_blocks))]
    while stack:
        ids, k = stack.pop()
        if k <= 1 or len(ids) <= 1:
            out.append(ids)
            continue
        c = centroids[ids]
        ax = int(_np.argmax(c.max(axis=0) - c.min(axis=0)))
        ka = k // 2
        m = max(1, min(len(ids) - 1, (len(ids) * ka) // k))
        p = _np.argpartition(c[:, ax], m)
        # push right first so the left group is emitted first (stack is LIFO)
        stack.append((ids[p[m:]], k - ka))
        stack.append((ids[p[:m]], ka))
    return _np.concatenate(out)


# ============================================================================
# HomoGraphDataset
# ============================================================================

class HomoGraphDataset(Dataset):
    """
    同质图数据集 - 支持训练和TTA推理两种模式

    采样策略：
        - 预先生成torch.arange(num_cpd_cell)的pivot_node索引数组
        - 执行不放回采样（从打乱后的数组中依次取出）
        - 与batchsize和epoch无关，只要调用get()就会采样

    训练模式 (split='train'):
        - 从torch.arange(num_cpd_cell)打乱后的数组中采样
        - 池空时自动重新生成并打乱
        - len() 返回一个很大的数（因为可以无限重新生成）

    推理模式 (split='test'):
        - 单GPU: 从完整的pivot_node数组中采样
        - 多GPU: 由master进程提前分组，每个GPU从sub_pivot_node数组中采样
        - 池空时返回None，触发dataloader退出
        - len() 返回预估的采样次数
        - 支持TTA（Test-Time Augmentation）推理
    """

    def __init__(
        self,
        root: str,
        split: str = "train",
        subsampling: bool = True,
        khop: int = 2,
        sample_ratio: float = 0.5,
        fixed_subgraph_size: Optional[int] = None,
        fixed_subgraph_size_mode: Optional[str] = None,
        fixed_subgraph_size_scale: float = 1.0,
        case_names: List[str] = None,
        rank: int = 0,
        world_size: int = 1,
        sensor_positions: Optional[Dict[str, Dict]] = None,
        sensor_only: bool = False,
        use_text_conditioning: bool = False,
        text_sidecar_name: str = "text_inputs.pt",
        global_cond_only: bool = False,
        patch_only_numerical: bool = False,
        patch_numerical_per_face: bool = False,
        include_t1_fields: bool = False,
        field_source: str = None,
        embedding_key: str = "pretrained_patch_embeddings",
        baseline_full_boundary: bool = False,
        field_set: str = "uvwpT",
        olbc_cond_mode: str = "numeric",
        olbc_type_embeddings_path: str = None,
    ):
        """
        Args:
            root: 数据集根目录
            split: 数据集划分 ('train', 'test')
            subsampling: 是否进行子图采样
            khop: K-hop邻居采样
            sample_ratio: 采样比例
            fixed_subgraph_size: 运行时扫描得到的固定子图节点数（内部使用）
            fixed_subgraph_size_mode: 固定子图模式（内部使用，目前支持 none / scan_split_min）
            case_names: 指定要加载的case名称列表（可选）
            rank: 当前进程的rank（用于多GPU推理）
            world_size: 总进程数（用于多GPU推理）
            sensor_positions: Sensor位置配置（可选）
                e.g., {"Sensor_1": {"x": 7.4, "y": 3.0, "z": 2.6, "aisle": "cold"}, ...}
            sensor_only: 是否仅推理sensor位置
                - True: 以sensor对应的最近网格节点为pivot构建k-hop子图
                - False: 正常采样（如果定义了sensor，推理后插值）
            global_cond_only: 是否仅使用全局文本条件（Method D），跳过边界几何和per-patch tokens
        """
        self.subsampling = subsampling
        self.khop = khop
        self.sample_ratio = sample_ratio
        self.split = split
        # Which epoch's training subgraph to draw. The training loop advances this via
        # set_epoch() before each epoch; the draw is a pure function of (case_idx, epoch), so it
        # is reproducible and — unlike the old mutable per-worker cursor — independent of the
        # DataLoader worker lifecycle. See _sample_from_pool for why that mattered.
        self._current_epoch = 0
        self.rank = rank
        self.world_size = world_size
        self.sensor_positions = sensor_positions
        self.sensor_only = sensor_only and sensor_positions is not None
        self.use_text_conditioning = bool(use_text_conditioning)
        self.global_cond_only = bool(global_cond_only)
        self.patch_only_numerical = bool(patch_only_numerical)
        self.patch_numerical_per_face = bool(patch_numerical_per_face)
        self.baseline_full_boundary = bool(baseline_full_boundary)
        self.include_t1_fields = bool(include_t1_fields)
        self.field_source = field_source
        self._embedding_key = str(embedding_key)
        self.field_set = str(field_set)
        _fs = resolve_field_set(self.field_set)
        self._field_specs = _fs["specs"]
        self._field_names = _fs["names"]
        self._node_output_size = _fs["output_size"]
        # olbc BC-type conditioning mode: how a boundary node's BC TYPE (D/N/R) is encoded into
        # the cross-attention KV. All modes share the same numeric value channel [g, alpha];
        # they differ ONLY in the appended type encoding (so the experiment isolates type info):
        #   "numeric" -> [g, alpha]                (type-blind; should lose on mixed-type bc4)
        #   "onehot"  -> [g, alpha, onehot3]       (explicit type)
        #   "text"    -> [g, alpha, type_emb(384)] (MiniLM embedding of "Dirichlet/Neumann/Robin")
        self.olbc_cond_mode = str(olbc_cond_mode)
        self._olbc_type_embeddings = None
        if self.field_set == "olbc" and self.olbc_cond_mode == "text":
            if not olbc_type_embeddings_path or not os.path.exists(olbc_type_embeddings_path):
                raise FileNotFoundError(
                    f"olbc_cond_mode='text' requires olbc_type_embeddings_path to a precomputed "
                    f"[3, D] tensor (rows = Dirichlet/Neumann/Robin); got {olbc_type_embeddings_path!r}"
                )
            _te = torch.load(olbc_type_embeddings_path, map_location="cpu")
            self._olbc_type_embeddings = _te.to(torch.float32)  # [3, D_text]
        self.text_sidecar_name = text_sidecar_name
        self._precomputed_text_embeddings = None  # Set via set_precomputed_text_embeddings()
        self.fixed_subgraph_size = fixed_subgraph_size
        self.fixed_subgraph_size_mode = fixed_subgraph_size_mode or "none"
        self.fixed_subgraph_size_scale = float(fixed_subgraph_size_scale)
        self._fixed_pivot_warning_emitted = False
        self.scan_debug_enabled = bool(int(os.environ.get("GTC_MEMORY_DEBUG", "0") or "0"))

        if self.fixed_subgraph_size_mode not in ("none", "scan_split_min"):
            raise ValueError(
                f"Unsupported fixed_subgraph_size_mode: {self.fixed_subgraph_size_mode}"
            )
        if self.fixed_subgraph_size_mode == "none" and self.fixed_subgraph_size is not None:
            raise ValueError(
                "Manual fixed_subgraph_size is no longer supported. "
                "Use batch_size>1 and let runtime scan derive the fixed size."
            )
        if self.fixed_subgraph_size is not None and self.fixed_subgraph_size < 1:
            raise ValueError("fixed_subgraph_size must be >= 1")
        if self.fixed_subgraph_size_scale <= 0.0 or self.fixed_subgraph_size_scale > 1.0:
            raise ValueError("fixed_subgraph_size_scale must be within (0, 1]")

        super().__init__(root=root)

        # 加载case列表
        meta_path = os.path.join(self.root, "meta.json")

        if case_names is not None:
            # 手动指定case
            self.cases = case_names
            self.h5_list = [self._resolve_h5_path(c) for c in self.cases]
        else:
            # 从meta.json读取
            logger.info(f"📋 Loading meta.json from {meta_path}")
            with open(meta_path, "r") as f:
                meta = json.load(f)

            cases_from_meta = meta["case_splits"][f"{split}_cases"]
            self.cases = cases_from_meta
            # GTC_MAX_CASES caps the split, for diagnostic runs that must not read the whole
            # test set off disk while a training job depends on the page cache. It is LOUD on
            # purpose: a silently truncated evaluation would produce a table that looks like a
            # full one, and a mean over 5 cases is not a mean over 100.
            _cap = int(os.environ.get("GTC_MAX_CASES", "0") or "0")
            if _cap > 0 and _cap < len(self.cases):
                logger.warning(
                    f"⚠️  GTC_MAX_CASES={_cap}: using only the FIRST {_cap} of "
                    f"{len(self.cases)} '{split}' cases. Every metric from this run is a mean "
                    f"over {_cap} cases and is NOT comparable to the published table."
                )
                self.cases = self.cases[:_cap]
            logger.info(f"📦 Using {len(self.cases)} cases from split '{split}'")

            # 构造h5文件路径列表
            self.h5_list = [self._resolve_h5_path(c) for c in self.cases]

        if len(self.h5_list) == 0:
            raise ValueError(f"No cases available")

        # Drop corrupt meshes from TRAINING only. A handful of Slab cases have degenerate,
        # collapsed geometry (all cell centroids coincident -> zero bbox extent). They carry no
        # thermal field to learn, and on an edge-consuming trunk (AeroGTO) their k-hop subgraph is
        # pathologically dense (case_0999: 13.5k nodes but ~958k edges) -> the edge encoder OOMs
        # a 24 GB card. The un-frozen sampler now draws that configuration, so it must be excluded.
        # Test split is left intact BY DEFAULT so evaluation stays comparable to previously-reported
        # arms. Opt in with GTC_FILTER_DEGENERATE_TEST=1 for AeroGTO-style edge trunks (whose test-
        # time inference would otherwise OOM on a degenerate case) and for the knockout guardrail,
        # which needs a few clean cases. When enabled it is LOUD in the log so a filtered evaluation
        # is never mistaken for a full one.
        _filter_test = (os.environ.get("GTC_FILTER_DEGENERATE_TEST") or "").strip().lower() \
            not in ("", "0", "off", "none", "false")
        if self.split == "train" or _filter_test:
            self._filter_degenerate_cases()

        # Detect H5 layout version from first file
        self._layout_version = 1
        if self.h5_list:
            import h5py
            with h5py.File(self.h5_list[0], "r") as _hf:
                self._layout_version = int(_hf.attrs.get("layout_version", 1))

        logger.info(f"📊 Dataset with {len(self.h5_list)} cases")

        # 初始化采样逻辑
        if self.subsampling:
            self._init_sampling()
        else:
            self._pivot_pools = None
            self._sample_budgets = None
            self._train_pool_seeds = None

    def _filter_degenerate_cases(self) -> None:
        """Remove train cases whose geometry is degenerate (zero bbox extent).

        Detected from a strided subsample of cpd|centroid (a few thousand rows), so the check
        costs a tiny read per case, not the full centroid array. A real mesh has a bbox diagonal
        of order metres; a collapsed/all-zero mesh has ~0. On a read error the case is KEPT — we
        never silently drop data on an IO hiccup, only on a proven-degenerate geometry.
        """
        import h5py

        keep_cases, keep_h5, dropped = [], [], []
        for case, h5_path in zip(self.cases, self.h5_list):
            try:
                with h5py.File(h5_path, "r") as f:
                    c = f["cpd|centroid"]
                    n = int(c.shape[0])
                    step = max(1, n // 2000)
                    sub = torch.from_numpy(c[::step]).float()
                    ext = float((sub.max(dim=0).values - sub.min(dim=0).values).norm())
            except Exception:
                ext = 1.0  # keep on error
            if ext <= 1e-9:
                dropped.append(case)
            else:
                keep_cases.append(case)
                keep_h5.append(h5_path)

        if dropped:
            _short = [d.split("/")[-1] for d in dropped]
            logger.warning(
                f"⚠️  Excluding {len(dropped)} DEGENERATE (zero-geometry) {self.split} case(s): {_short}. "
                f"Corrupt collapsed meshes — no field to learn, and they OOM edge-based trunks. "
                f"{self.split} set is now {len(keep_cases)} cases"
                + ("." if self.split == "train" else " — NOTE: a FILTERED evaluation set, NOT the "
                   "full test set; metrics are not directly comparable to arms scored on all cases.")
            )
            self.cases = keep_cases
            self.h5_list = keep_h5

    def _resolve_h5_path(self, case: str) -> str:
        """Resolve H5 file path for a case name, handling both flat and nested layouts.

        Flat (GTC):   {root}/{case}/h5/{case}.h5
        Nested (Slab): {root}/case_XXXX/log/{case}/h5/...  (filename may be log.h5 or {case}.h5)
        """
        case_name = os.path.dirname(case) if '/' in case else case
        h5_dir = os.path.join(self.root, case, "h5")
        exact_path = os.path.join(h5_dir, f"{case_name}.h5")
        if os.path.exists(exact_path):
            return exact_path
        if os.path.isdir(h5_dir):
            h5_files = sorted(Path(h5_dir).rglob("*.h5"))
            if h5_files:
                return str(h5_files[0])
        # Nested layout fallback: build index once keyed by timestamp directory name
        if not hasattr(self, '_h5_index'):
            self._h5_index = {}
            for p in Path(self.root).rglob("*.h5"):
                # Index by stem (works for flat layouts where stem == case name)
                self._h5_index.setdefault(p.stem, str(p))
                # Index by timestamp ancestor dir (works for nested layouts)
                for part in p.relative_to(self.root).parts:
                    if part.startswith("20") and "_case_" in part:
                        self._h5_index.setdefault(part, str(p))
                        break
            logger.info(f"Built H5 index: {len(self._h5_index)} entries under {self.root}")
        if case_name in self._h5_index:
            return self._h5_index[case_name]
        raise FileNotFoundError(
            f"Cannot find H5 for case '{case}' under {self.root}. "
            f"Tried: {exact_path} and index ({len(self._h5_index)} entries)."
        )

    def get_sampling_summary(self) -> Dict[str, float]:
        """Return a compact summary of the current sampling setup for logging."""
        summary = {
            "num_cases": len(self.h5_list),
            "subsampling": bool(self.subsampling),
            "sample_ratio": float(self.sample_ratio),
            "khop": int(self.khop),
            "fixed_subgraph_size": (
                int(self.fixed_subgraph_size) if self.fixed_subgraph_size is not None else None
            ),
            "fixed_subgraph_size_scale": float(self.fixed_subgraph_size_scale),
        }

        if not self.subsampling or not self._sample_budgets:
            return summary

        budgets = [int(v) for v in self._sample_budgets]
        num_cells = [int(v) for v in self._case_num_cells]
        max_idx = max(range(len(num_cells)), key=num_cells.__getitem__)
        min_idx = min(range(len(num_cells)), key=num_cells.__getitem__)
        summary.update(
            {
                "num_cells_min": min(num_cells),
                "num_cells_mean": float(sum(num_cells) / len(num_cells)),
                "num_cells_max": max(num_cells),
                "num_cells_min_case": self.cases[min_idx],
                "num_cells_max_case": self.cases[max_idx],
                "budget_min": min(budgets),
                "budget_mean": float(sum(budgets) / len(budgets)),
                "budget_max": max(budgets),
                "budget_min_case": self.cases[min_idx],
                "budget_max_case": self.cases[max_idx],
            }
        )
        return summary

    def _init_sampling(self):
        """
        初始化采样逻辑

        训练模式：为每个case创建可重新生成的pivot_node池
        推理模式：为每个case创建固定的pivot_node池（单GPU或多GPU分配）
        Sensor-only模式：为每个case预计算sensor对应的最近网格节点
        """
        # 加载每个case的元信息
        self._case_num_cells = []
        self._sample_budgets = []
        self._case_cpd_centroids = [] if self.sensor_only else None  # 仅sensor模式才缓存完整centroid

        # Pivot-pool geometry experiment. Default is "full" -> every branch guarded by it is
        # skipped and this method behaves exactly as before.
        self._roi = _parse_pivot_roi()
        self._roi_pools = []  # per-case cpd ids inside the box (None when mode == "full")
        self._roi_centroids = []  # per-case cpd|centroid, only when a knob needs geometry
        _roi_needs_geom = (self._roi["mode"] != "full") or (self._roi["order"] == "spatial")
        if (_roi_needs_geom or self._roi["repeat"] > 1) and self.world_size > 1:
            # A box split across ranks is no longer a box: the rank chunking at the bottom of
            # this method would hand each rank an interleaved third of it, and `scatter` would
            # be truncated per-rank. Refuse rather than silently measure something else.
            raise ValueError(
                "SCANO_PIVOT_* requires world_size == 1 "
                f"(got {self.world_size}); run the experiment on a single GPU."
            )

        for h5_path in self.h5_list:
            with h5py.File(h5_path, 'r') as f:
                if 'cpd|centroid' in f:
                    num_cells = len(f['cpd|centroid'])
                else:
                    raise ValueError(f"Cannot find cpd|centroid in {h5_path}")

                self._case_num_cells.append(num_cells)
                if self.sensor_only:
                    cpd_centroid = torch.from_numpy(f['cpd|centroid'][()]).to(torch.float32)
                    self._case_cpd_centroids.append(cpd_centroid)

                roi_ids = None
                # Read cpd|centroid ONCE per case when any pivot knob needs geometry (box
                # selection and/or the k-d contiguous ordering). One extra [N,3] float32 read
                # (5.4 MB at N=447,205) inside the `with` block this loop already opens; no
                # new file handle, and nothing at all when every knob is at its default.
                self._roi_centroids.append(
                    f['cpd|centroid'][()].astype('float32') if _roi_needs_geom else None
                )
                if self._roi["mode"] != "full":
                    # The centroid array is ALREADY the only thing this loop opens the H5 for,
                    # so the box test costs one extra read of an [N,3] float32 (5 MB at
                    # N=447k) and no extra file handle. It is indexed by cpd id, and cpd
                    # covers interior cells AND boundary faces (cell i -> cpd i; boundary face
                    # f -> cpd nCells + (f - nInternalFaces)), so an in-box test on it picks up
                    # the boundary rows inside the box too — which is what a dragged ROI must
                    # contain for the model's cross-attention to see any boundary at all.
                    c = torch.from_numpy(self._roi_centroids[-1])
                    # cpd|centroid is SOLVER-frame, so a demo-frame box must be converted
                    # first. depth is the FULL mesh's solver y_max, read the same way the
                    # exporter reads it (inferencer.py: mesh_pos[:,1].max()) — deriving it
                    # from anything smaller places the box at the wrong depth, and the
                    # anchors would hide that.
                    _depth = (float(f['node|pos'][()][:, 1].max())
                              if self._roi.get("box_frame") == "demo" else 0.0)
                    _slo, _shi = _pivot_box_to_solver(self._roi, _depth)
                    lo = torch.tensor(_slo, dtype=torch.float32)
                    hi = torch.tensor(_shi, dtype=torch.float32)
                    roi_ids = torch.nonzero(
                        ((c >= lo) & (c <= hi)).all(dim=1), as_tuple=False
                    ).reshape(-1)
                    if self._roi.get("box_frame") == "demo":
                        logger.info(
                            f"  Case {len(self._roi_pools)}: pivot box demo"
                            f"{self._roi['box']} -> solver {_slo}-{_shi} (depth={_depth:.3f})")
                    if roi_ids.numel() == 0:
                        raise ValueError(
                            f"SCANO_PIVOT_BOX {self._roi['box']} "
                            f"(frame={self._roi.get('box_frame')}, solver {_slo}-{_shi}) "
                            f"selects 0 of {num_cells} cpd cells in {h5_path}"
                        )
                if self._roi["mode"] == "box+anchor" and roi_ids is not None:
                    # Union the box with a room-wide sprinkle. Drawn from a SEPARATE, fixed
                    # generator so the anchor set does not shift when the box does — two ROIs
                    # in one session must see the same anchors, or the difference between
                    # them would confound the anchors with the region.
                    _g = torch.Generator()
                    # The enclosing loop is `for h5_path in self.h5_list` with no index;
                    # len(self._roi_pools) is the index of the case about to be appended.
                    _g.manual_seed(20260810 + len(self._roi_pools))
                    _k = min(int(self._roi["anchors"]), int(num_cells))
                    if self._roi["anchor_mode"] == "stratified" and _k > 0:
                        # One anchor per occupied grid cell, round-robin over the cells, so
                        # the sprinkle is spread by CONSTRUCTION instead of by luck. A
                        # uniform random draw of K clumps; if spread is the active
                        # ingredient, stratified should reach the same accuracy at smaller K.
                        import numpy as _np
                        _c = self._roi_centroids[-1]
                        _lo, _hi = _c.min(axis=0), _c.max(axis=0)
                        _nb = max(1, int(round(_k ** (1.0 / 3.0))))
                        _idx = _np.clip(
                            ((_c - _lo) / _np.maximum(_hi - _lo, 1e-9) * _nb).astype(int),
                            0, _nb - 1)
                        _key = (_idx[:, 0] * _nb + _idx[:, 1]) * _nb + _idx[:, 2]
                        _perm = torch.randperm(int(num_cells), generator=_g).numpy()
                        _key_p = _key[_perm]
                        # First occurrence of each grid cell in a shuffled order = one
                        # random member per occupied cell.
                        _seen, _first = _np.unique(_key_p, return_index=True)
                        _pick = _perm[_np.sort(_first)]
                        if _pick.size < _k:   # top up randomly if the grid is coarser than K
                            _rest = _np.setdiff1d(_perm, _pick, assume_unique=False)
                            _pick = _np.concatenate([_pick, _rest[: _k - _pick.size]])
                        _anchor = torch.from_numpy(_pick[:_k].copy())
                    else:
                        _anchor = torch.randperm(int(num_cells), generator=_g)[:_k]
                    roi_ids = torch.unique(torch.cat([roi_ids, _anchor]))
                    logger.info(
                        f"  Case {len(self._roi_pools)}: box+anchor pool = "
                        f"{roi_ids.numel()} (box + {_k} room-wide anchors)"
                    )
                self._roi_pools.append(roi_ids)

                if self._roi["budget"] is not None:
                    # Explicit override pins BOTH arms to the same pivots-per-draw.
                    budget = max(1, int(self._roi["budget"]))
                elif roi_ids is not None:
                    # One draw == the whole box (and, in the scatter arm, an equally sized
                    # scattered set), so the two arms also share the DRAW count.
                    budget = max(1, int(roi_ids.numel()))
                else:
                    budget = max(1, int(num_cells * self.sample_ratio))
                self._sample_budgets.append(budget)

        # =====================================================
        # Sensor-only 模式：预计算sensor对应的最近网格节点
        # =====================================================
        if self.sensor_only:
            self._sensor_pivot_indices = []  # 每个case的sensor对应节点索引
            self._sensor_names = list(self.sensor_positions.keys())
            
            for case_idx, cpd_centroid in enumerate(self._case_cpd_centroids):
                # 找到每个sensor最近的cpd_cell节点
                indices, _, distances = find_nearest_cell_indices(
                    self.sensor_positions, cpd_centroid, return_distances=True
                )
                self._sensor_pivot_indices.append(indices)
                
                if case_idx == 0:  # 只打印第一个case的信息
                    logger.info(f"🎯 Sensor-only mode: {len(self._sensor_names)} sensors")
                    for i, name in enumerate(self._sensor_names):
                        dist = distances[i].item() if distances is not None else 0
                        logger.info(f"   {name}: nearest cell idx={indices[i].item()}, distance={dist:.4f}m")
            
            # Sensor-only模式不需要pivot_pool
            self._pivot_pools = None
            self._pool_positions = None
            logger.info(f"✅ Sensor-only mode initialized for {len(self.h5_list)} cases")
            return

        # =====================================================
        # 正常采样模式：初始化pivot_node池
        # =====================================================
        self._pivot_pools = []  # 推理模式下为每个case保存一个显式池
        self._pool_positions = []  # 每个case当前取到的位置
        self._train_pool_seeds = [] if self.split == "train" else None
        self._train_pool_offsets = [] if self.split == "train" else None
        self._train_pool_strides = [] if self.split == "train" else None

        for case_idx, num_cells in enumerate(self._case_num_cells):
            if self.split == "train":
                # 训练模式：只保存一个可复现的置换参数，避免为每个case常驻完整pivot池
                seed = 12345 + case_idx
                self._train_pool_seeds.append(seed)
                offset, stride = self._generate_train_pool_params(num_cells, seed)
                self._train_pool_offsets.append(offset)
                self._train_pool_strides.append(stride)
                self._pivot_pools.append(None)
                self._pool_positions.append(0)
            else:
                # 生成完整的pivot_node索引数组
                full_pool = torch.arange(num_cells)
                # Contiguous-ROI arm: restrict the pool to the cpd ids inside the dragged box
                # BEFORE the shuffle/rank-split below, which is then left untouched. The
                # `scatter` control keeps the whole domain here and is truncated to the same
                # cardinality after the shuffle, so both arms come out of the same generator.
                if self._roi["mode"] in ("box", "box+anchor"):
                    full_pool = self._roi_pools[case_idx]
                # 推理模式：根据rank分配子数组

                if self.world_size > 1:
                    # 🔧 CRITICAL FIX: 多GPU场景下使用interleaved分配
                    # 所有GPU必须使用相同的随机种子来打乱，确保pivot nodes不重叠

                    # 🔧 所有GPU必须使用相同的随机种子来打乱
                    generator = torch.Generator()
                    generator.manual_seed(42 + case_idx)
                    # full_pool.numel() == num_cells unless a ROI restriction is active, so
                    # this is a no-op rewrite for the baseline.
                    perm = torch.randperm(full_pool.numel(), generator=generator)
                    shuffled_pool = full_pool[perm]

                    # 🔧 FIX: 使用连续分配(Contiguous)而不是交错分配(Interleaved)
                    # 这样可以保证多GPU(ratio=1.0)的分块与单GPU(ratio=0.5)的分块一致
                    
                    total_size = len(shuffled_pool)
                    chunk_size = total_size // self.world_size
                    remainder = total_size % self.world_size
                    
                    start = self.rank * chunk_size + min(self.rank, remainder)
                    end = start + chunk_size + (1 if self.rank < remainder else 0)
                    
                    sub_pool = shuffled_pool[start:end]

                    logger.info(f"  Case {case_idx}: Rank {self.rank} assigned {len(sub_pool)}/{num_cells} nodes (contiguous: {start}-{end})")
                else:
                    # 单GPU：使用完整数组并打乱
                    # 🔧 FIX: Ensure deterministic shuffling for Single GPU as well
                    generator = torch.Generator()
                    generator.manual_seed(42 + case_idx)
                    perm = torch.randperm(full_pool.numel(), generator=generator)
                    sub_pool = full_pool[perm]

                if self._roi["mode"] == "scatter":
                    # Paired control: identical cardinality to the box arm, from the identical
                    # shuffle, so the ONLY difference between the two arms is the geometry of
                    # the pivots — not their count, not the draw count, not the RNG.
                    sub_pool = sub_pool[: int(self._roi_pools[case_idx].numel())]

                _budget = self._sample_budgets[case_idx]

                if self._roi["order"] == "spatial":
                    # CONTIGUOUS arm. Reorder the (already selected) pool so that consecutive
                    # budget-slices are compact axis-aligned clusters. Budget, draw count,
                    # pool membership and whole-domain coverage are all unchanged — only the
                    # GEOMETRY of each draw changes, which is precisely the variable under
                    # test. Applied after the shuffle so the pool contents are identical to
                    # the scattered arm's.
                    import numpy as _np
                    _cen = self._roi_centroids[case_idx]
                    _nb = max(1, math.ceil(len(sub_pool) / _budget))
                    _ord = _kd_block_order(_cen[sub_pool.numpy()], _nb)
                    sub_pool = sub_pool[torch.from_numpy(_ord)]

                if self._roi["repeat"] > 1:
                    # Emit the pool R times, each rotated by a fraction of the budget, so the
                    # block boundaries land elsewhere on every pass. Each cpd id becomes a
                    # pivot exactly R times from R different partitions, restoring the draw
                    # redundancy the 51-draw scattered baseline gets for free (~9.4 draws per
                    # cell). Without this, a contiguous arm is confounded: it would be both
                    # contiguous AND averaged over ~1 draw instead of ~9.
                    _R = int(self._roi["repeat"])
                    sub_pool = torch.cat([
                        torch.roll(sub_pool, shifts=-((r * _budget) // _R), dims=0)
                        for r in range(_R)
                    ])

                if (self._roi["mode"] != "full" or self._roi["order"] != "random"
                        or self._roi["repeat"] > 1):
                    logger.info(
                        f"  Case {case_idx}: PIVOT-EXP mode={self._roi['mode']} "
                        f"order={self._roi['order']} repeat={self._roi['repeat']} "
                        f"box={self._roi['box']} -> pool {len(sub_pool)} ids "
                        f"({num_cells} cpd cells), budget={_budget}, "
                        f"draws={math.ceil(len(sub_pool) / _budget)}"
                    )

                self._pivot_pools.append(sub_pool)
                self._pool_positions.append(0)

                # 🔧 DEBUG: Print first 10 pivot nodes for verification
                if self.split == "test" and len(sub_pool) > 0:
                    logger.info(f"  Case {case_idx}: Rank {self.rank} first 10 pivot nodes: {sub_pool[:10].tolist()}")

        if self.fixed_subgraph_size_mode == "scan_split_min":
            self.fixed_subgraph_size = self._scan_split_min_subgraph_size()
            logger.info(
                f"📏 Fixed subgraph size enabled from current split scan ({self.split}): "
                f"{self.fixed_subgraph_size}"
            )
        elif self.fixed_subgraph_size is not None:
            logger.info(f"📏 Fixed subgraph size enabled: {self.fixed_subgraph_size}")

        if self.split == "train":
            logger.info(f"✅ Training mode: {len(self.cases)} cases, sample_ratio={self.sample_ratio}")
            logger.info("   Train sampling initialized with lazy permutation pools (no eager full pivot tensors)")
        else:
            total_nodes = sum(len(pool) for pool in self._pivot_pools)
            logger.info(f"✅ Inference mode: Rank {self.rank}/{self.world_size}")
            logger.info(f"   Total nodes to process: {total_nodes}")
            logger.info(f"   Estimated samples: ~{sum(math.ceil(len(pool) / budget) for pool, budget in zip(self._pivot_pools, self._sample_budgets))}")

    def set_epoch(self, epoch: int) -> None:
        """Select which epoch's training subgraph to draw for every case.

        Called once per epoch by the training loop (Runner._run_train_loop), BEFORE the epoch's
        DataLoader iteration begins. With non-persistent workers the workers are re-forked at that
        point, so they inherit the updated value; the pivot draw then walks to a fresh, non-
        overlapping slice of each case's permutation (start = (epoch * budget) % num_cells).

        This replaces the previous scheme, in which a mutable cursor `_pool_positions[case_idx]`
        was advanced inside __getitem__ (i.e. inside the worker) and thrown away when the worker
        died at the end of each epoch — so every epoch replayed slice 0 and every case saw one
        frozen subgraph for the entire run. set_epoch has no such dependence on worker lifetime.
        """
        self._current_epoch = int(epoch)

    def _generate_train_pool_params(self, num_cells: int, seed: int):
        """为训练模式生成可复现的置换参数，惰性表示完整不放回序列。"""
        if num_cells <= 1:
            return 0, 1

        generator = torch.Generator()
        generator.manual_seed(int(seed))
        offset = int(torch.randint(0, num_cells, (1,), generator=generator).item())

        # 选择与 num_cells 互素的 stride，使 (offset + stride * i) % num_cells 构成完整置换。
        while True:
            stride = int(torch.randint(1, num_cells, (1,), generator=generator).item())
            if math.gcd(stride, num_cells) == 1:
                break

        return offset, stride

    def _sample_train_chunk(self, case_idx: int, start: int, count: int) -> torch.Tensor:
        """从训练模式的惰性置换池中取出连续 count 个 pivot 节点。"""
        if count <= 0:
            return torch.empty((0,), dtype=torch.long)

        num_cells = int(self._case_num_cells[case_idx])
        offset = int(self._train_pool_offsets[case_idx])
        stride = int(self._train_pool_strides[case_idx])

        # Wrap the permutation index modulo num_cells so a slice starting near the end of the
        # permutation cycles back to the front instead of repeating the tail. Because
        # (offset + p*stride) % num_cells is a bijection over p in [0, num_cells), `count`
        # DISTINCT wrapped positions (count <= num_cells) yield `count` DISTINCT pivots — a clean
        # no-replacement chunk. For the original call site (start + count <= num_cells) the
        # modulo is a no-op, so that path is unchanged.
        positions = (torch.arange(start, start + count, dtype=torch.long)) % num_cells
        return (offset + positions * stride) % num_cells

    def _scan_split_min_subgraph_size(self) -> int:
        """扫描当前 split 的 pivot 池，统计 k-hop 子图最小节点数。"""
        global_min_size = None
        global_max_budget = max(self._sample_budgets) if self._sample_budgets else 0
        total_samples_scanned = 0

        logger.info("🔍 Scanning subgraphs to determine fixed node count...")

        for case_idx, h5_path in enumerate(self.h5_list):
            with h5py.File(h5_path, 'r') as f:
                neighbor_cpd_cell = torch.from_numpy(f["cpd|neighbor_cell"][()]).long()

            if self.scan_debug_enabled and case_idx == 0:
                logger.info(
                    "🧪 Scan debug: neighbor_cpd_cell device=%s shape=%s fixed_size_mode=%s",
                    neighbor_cpd_cell.device,
                    tuple(neighbor_cpd_cell.shape),
                    self.fixed_subgraph_size_mode,
                )

            budget = self._sample_budgets[case_idx]
            pool_length = self._case_num_cells[case_idx] if self.split == "train" else len(self._pivot_pools[case_idx])
            case_min_size = None
            case_num_samples = 0

            for start in range(0, pool_length, budget):
                if start + budget > pool_length:
                    break

                if self.split == "train":
                    sampled_nodes = self._sample_train_chunk(case_idx, start, budget)
                else:
                    sampled_nodes = self._pivot_pools[case_idx][start:start + budget]
                if sampled_nodes.numel() == 0:
                    continue

                subgraph_nodes, _, _, _ = k_hop_subgraph(
                    sampled_nodes,
                    self.khop,
                    neighbor_cpd_cell,
                    relabel_nodes=False,
                    num_nodes=self._case_num_cells[case_idx],
                )
                subgraph_size = int(subgraph_nodes.numel())

                case_min_size = subgraph_size if case_min_size is None else min(case_min_size, subgraph_size)
                global_min_size = subgraph_size if global_min_size is None else min(global_min_size, subgraph_size)
                case_num_samples += 1
                total_samples_scanned += 1

            logger.info(
                f"   Case {self.cases[case_idx]}: scanned {case_num_samples} subgraphs, "
                f"budget={budget}, min_nodes={case_min_size}"
            )

        if global_min_size is None:
            raise ValueError("Failed to scan training subgraphs for fixed size")

        if global_min_size < global_max_budget:
            logger.warning(
                "⚠️  Scanned min subgraph size is smaller than some pivot budgets; "
                "truncation will prioritize pivot nodes, but some pivots may still be dropped."
            )

        scaled_fixed_size = max(1, int(math.floor(global_min_size * self.fixed_subgraph_size_scale)))
        if scaled_fixed_size != global_min_size:
            logger.info(
                "🔧 Applying fixed-size scale: raw_min=%s scale=%.4f -> fixed_size=%s",
                global_min_size,
                self.fixed_subgraph_size_scale,
                scaled_fixed_size,
            )

        if scaled_fixed_size < global_max_budget:
            logger.warning(
                "⚠️  Effective fixed size (%s) is smaller than pivot budget max (%s); "
                "some pivots may be dropped during truncation.",
                scaled_fixed_size,
                global_max_budget,
            )

        logger.info(
            f"✅ Subgraph scan complete: fixed_size={scaled_fixed_size}, "
            f"raw_min={global_min_size}, max_budget={global_max_budget}, scanned_samples={total_samples_scanned}"
        )
        return scaled_fixed_size

    def _select_fixed_size_nodes(
        self,
        subgraph_nodes: torch.Tensor,
        pivot_nodes: torch.Tensor,
        num_cpd_cell: int,
    ) -> torch.Tensor:
        """优先保留pivot节点，再用子图中其余节点补足固定大小。"""
        fixed_size = int(self.fixed_subgraph_size)

        if subgraph_nodes.numel() <= fixed_size:
            return subgraph_nodes

        subgraph_mask = torch.zeros(num_cpd_cell, dtype=torch.bool)
        subgraph_mask[subgraph_nodes] = True
        prioritized_pivots = pivot_nodes[subgraph_mask[pivot_nodes]][:fixed_size]

        selected_mask = torch.zeros(num_cpd_cell, dtype=torch.bool)
        if prioritized_pivots.numel() > 0:
            selected_mask[prioritized_pivots] = True

        remaining_slots = fixed_size - prioritized_pivots.numel()
        if remaining_slots <= 0:
            return prioritized_pivots

        remaining_nodes = subgraph_nodes[~selected_mask[subgraph_nodes]]
        return torch.cat((prioritized_pivots, remaining_nodes[:remaining_slots]), dim=0)

    def _induce_subgraph_edges(
        self,
        subgraph_nodes: torch.Tensor,
        neighbor_cpd_cell: torch.Tensor,
        num_cpd_cell: int,
    ) -> torch.Tensor:
        """基于保留的全局节点索引重新诱导子图边，并重映射到局部索引。"""
        global_to_local = torch.full((num_cpd_cell,), -1, dtype=torch.long)
        global_to_local[subgraph_nodes] = torch.arange(subgraph_nodes.numel(), dtype=torch.long)

        senders = neighbor_cpd_cell[0]
        receivers = neighbor_cpd_cell[1]
        local_senders = global_to_local[senders]
        local_receivers = global_to_local[receivers]
        edge_mask = (local_senders >= 0) & (local_receivers >= 0)

        return torch.stack((local_senders[edge_mask], local_receivers[edge_mask]), dim=0)

    def _truncate_subgraph(
        self,
        subgraph_nodes: torch.Tensor,
        subgraph_neighbor_cpd_cell: torch.Tensor,
        pivot_nodes: torch.Tensor,
        neighbor_cpd_cell: torch.Tensor,
        num_cpd_cell: int,
    ):
        """将子图截断到固定节点数，并同步更新边与pivot记录。"""
        if self.fixed_subgraph_size is None or subgraph_nodes.numel() <= self.fixed_subgraph_size:
            return subgraph_nodes, subgraph_neighbor_cpd_cell, pivot_nodes

        kept_nodes = self._select_fixed_size_nodes(subgraph_nodes, pivot_nodes, num_cpd_cell)
        kept_edges = self._induce_subgraph_edges(kept_nodes, neighbor_cpd_cell, num_cpd_cell)

        kept_mask = torch.zeros(num_cpd_cell, dtype=torch.bool)
        kept_mask[kept_nodes] = True
        kept_pivot_nodes = pivot_nodes[kept_mask[pivot_nodes]]

        if kept_pivot_nodes.numel() < pivot_nodes.numel() and not self._fixed_pivot_warning_emitted:
            logger.warning(
                "⚠️  Fixed subgraph truncation dropped part of the sampled pivots. "
                "This happens when pivot budget exceeds scanned fixed size."
            )
            self._fixed_pivot_warning_emitted = True

        return kept_nodes, kept_edges, kept_pivot_nodes

    def _sample_from_pool(self, case_idx: int) -> torch.Tensor:
        """
        从pivot_node池中不放回采样

        训练模式：
            - 从torch.arange(num_cpd_cell)打乱后的数组中采样
            - 池空时自动重新生成并打乱
            - 与batchsize和epoch无关

        推理模式：
            - 单GPU：从完整的pivot_node数组中采样
            - 多GPU：从预先分配的sub_pivot_node数组中采样
            - 池空时抛出StopIteration，表示该case已完成

        Args:
            case_idx: case索引

        Returns:
            采样的pivot_node索引

        Raises:
            StopIteration: 推理模式下池已空
        """
        pos = self._pool_positions[case_idx]
        budget = self._sample_budgets[case_idx]

        if self.split == "train":
            # Draw the slice for THIS epoch. The start is a pure function of the epoch
            # (set via set_epoch before the epoch's iteration) rather than a mutable cursor, so
            # the draw is reproducible and independent of the DataLoader worker lifecycle. Over
            # ceil(num_cells / budget) epochs the non-overlapping slices tile the whole permutation.
            #
            # Previously the start came from self._pool_positions[case_idx], which was advanced
            # here inside the worker and lost when the (non-persistent) worker died each epoch —
            # so it reset to 0 every epoch and every case saw ONE frozen subgraph for the entire
            # run. Verified: the frozen behaviour, and that this fix un-freezes it.
            pool_length = int(self._case_num_cells[case_idx])
            budget = min(int(budget), pool_length)
            start = (int(self._current_epoch) * budget) % max(1, pool_length)
            return self._sample_train_chunk(case_idx, start, budget)

        pool = self._pivot_pools[case_idx]

        # 检查池是否已空
        if pos >= len(pool):
            if self.split == "train":
                self._pool_positions[case_idx] = 0
                pos = 0
            else:
                # 推理模式：池已空，退出dataloader循环
                raise StopIteration(f"Case {case_idx}: Pivot pool exhausted")

        # 从池中取出budget个节点（不放回采样）
        end_pos = min(pos + budget, len(pool))
        sampled_nodes = pool[pos:end_pos]

        # 更新位置
        self._pool_positions[case_idx] = end_pos

        return sampled_nodes

    def len(self):
        """返回数据集长度"""
        # Sensor-only模式：每个case只推理一次（包含所有sensor）
        if self.sensor_only:
            return len(self.h5_list)
        
        if not self.subsampling:
            # 不采样：返回case数量
            return len(self.h5_list)

        if self.split == "train":
            # 训练模式：返回case数量
            # 每个epoch遍历所有case一次，每个case从pivot_pool中采样一个subgraph
            return len(self.h5_list)
        else:
            # 推理模式：返回预估的采样次数
            # 🔧 FIX: 检查_pivot_pools是否已初始化
            if self._pivot_pools is None or self._sample_budgets is None:
                return len(self.h5_list)

            total_samples = 0
            for pool, budget in zip(self._pivot_pools, self._sample_budgets):
                total_samples += math.ceil(len(pool) / budget)
            return total_samples

    def set_precomputed_text_embeddings(self, embeddings_dict):
        """Set pre-computed text embeddings to avoid running text encoder during training.

        Args:
            embeddings_dict: dict mapping case_name -> {
                "patch_embeddings": Tensor [N_patches, hidden_size],
                "global_embeddings": Tensor [1, hidden_size] or None,
                "patch_ref_edge_index": Tensor [2, E] or None,
                "patch_ref_edge_type": Tensor [E] or None,
            }
        """
        self._precomputed_text_embeddings = embeddings_dict

    def get(self, idx):
        """
        获取一个样本

        训练模式：idx % len(cases) 得到case_idx，然后从池中采样
        推理模式：遍历所有case，依次从池中采样直到池空
        Sensor-only模式：使用预计算的sensor节点索引作为pivot
        """
        # 确定case_idx
        if self.sensor_only:
            # Sensor-only模式：直接使用idx作为case_idx
            case_idx = idx % len(self.h5_list)
        elif self.split == "train":
            # 训练模式：循环使用case
            case_idx = idx % len(self.h5_list)
        else:
            # 推理模式：需要映射到case_idx
            case_idx = self._map_idx_to_case(idx)

        # 加载H5文件
        h5_file = self.h5_list[case_idx]
        h5_file_handle = h5py.File(h5_file, "r")
        case_name = self.cases[case_idx]

        # 加载基础数据
        cpd_centroid = torch.from_numpy(h5_file_handle["cpd|centroid"][()]).to(torch.float32)
        mesh_pos = torch.from_numpy(h5_file_handle["node|pos"][()]).to(torch.float32)
        neighbor_cpd_cell = torch.from_numpy(h5_file_handle["cpd|neighbor_cell"][()]).long()
        # PyVista cell connectivity is only used for optional VTU export. A node-cloud H5
        # (AirfRANS) may store per-vertex cells or none; GTC/Slab always provide them.
        if "pv_cells_node" in h5_file_handle:
            pv_cells_node = torch.from_numpy(h5_file_handle["pv_cells_node"][()]).long()
            pv_cells_type = torch.from_numpy(h5_file_handle["pv_cells_type"][()]).long()
        else:
            pv_cells_node = torch.zeros((0,), dtype=torch.long)
            pv_cells_type = torch.zeros((0,), dtype=torch.long)

        num_cpd_cell = int(cpd_centroid.shape[0])
        num_face = int(neighbor_cpd_cell.shape[1])

        # Validate neighbor_cpd_cell to prevent silent k_hop_subgraph failures
        if neighbor_cpd_cell.shape[0] != 2 or num_face == 0:
            h5_file_handle.close()
            raise RuntimeError(
                f"Invalid cpd|neighbor_cell shape {tuple(neighbor_cpd_cell.shape)} "
                f"in {h5_file} (case={case_name}). Expected [2, num_edges>0]."
            )
        nc_max = neighbor_cpd_cell.max().item()
        if nc_max >= num_cpd_cell:
            h5_file_handle.close()
            raise RuntimeError(
                f"cpd|neighbor_cell max index {nc_max} >= num_cpd_cell {num_cpd_cell} "
                f"in {h5_file} (case={case_name}). H5 may be corrupted."
            )

        # 加载初始场和目标场
        _needs_t1 = (not self.use_text_conditioning) or self.include_t1_fields or self.patch_only_numerical
        if _needs_t1:
            if self._layout_version >= 2 and self.field_source:
                _grp = f"field_{self.field_source}"
                if _grp not in h5_file_handle:
                    raise KeyError(
                        f"H5 layout v2 but group '{_grp}' not found in {h5_file_handle.filename}. "
                        f"Run precompute_features.py --unconverged-t1 to generate it."
                    )
                init_phi = read_field_stack(h5_file_handle, self._field_specs, _grp, 0)
            else:
                init_phi = read_field_stack(h5_file_handle, self._field_specs, None, 0)
        else:
            init_phi = torch.zeros((num_cpd_cell, self._node_output_size), dtype=torch.float32)

        # 加载面法矢
        cells_face_unv = torch.from_numpy(h5_file_handle["cells_face_normal"][()]).to(torch.float32)
        cell_face = torch.from_numpy(h5_file_handle["cells_face"][()]).long()
        unv_face = torch.zeros(size=(num_face, 3), dtype=torch.float32)
        unv_face[cell_face] = cells_face_unv

        # 加载unsigned distance function (旧格式h5可能没有，用零填充)
        if "cpd|cell_udf" in h5_file_handle:
            udf_cpd_cell = torch.from_numpy(h5_file_handle["cpd|cell_udf"][()]).to(torch.float32)
        else:
            # 旧格式h5缺少UDF，用零张量代替（shape: [num_cpd_cell, 1]）
            udf_cpd_cell = torch.zeros((num_cpd_cell, 1), dtype=torch.float32)
        
        # 生成边界mask
        cell_type = torch.from_numpy(h5_file_handle["cpd|cell_type"][()]).long().squeeze()
        face_type = torch.from_numpy(h5_file_handle["face|face_type"][()]).long().squeeze()
        mask_boundary_cell = cell_type != FaceType.NORMAL
        mask_boundary_face = face_type != FaceType.NORMAL
        mask_internal_cell = cell_type == FaceType.NORMAL

        # 将face维度的unv变换到cpd_cell维度
        unv_cpd_cell = torch.zeros((num_cpd_cell, 3), dtype=torch.float32)
        if self.field_set in ("airfrans", "olbc") and "unv_cpd_cell" in h5_file_handle:
            # Node-cloud layout (AirfRANS / olbc): per-node normals are stored directly (z=0).
            # AirfRANS = airfoil-surface normals; olbc = ∇sdf (outward normal proxy). The OpenFOAM
            # face-scatter below assumes a face structure a k-NN point cloud lacks (face|face_type
            # is length N, not num_edges), so read the per-node normals straight from H5. GTC/Slab
            # H5 have no `unv_cpd_cell` dataset, so this branch never fires for them.
            unv_cpd_cell = torch.from_numpy(h5_file_handle["unv_cpd_cell"][()]).to(torch.float32)
        else:
            unv_cpd_cell[mask_boundary_cell] = unv_face[mask_boundary_face]
        cpd_centroid_full = cpd_centroid.clone()
        unv_cpd_cell_full = unv_cpd_cell.clone()

        init_patch_phi_cpd_cell = None
        init_patch_phi_batch_cpd_cell = None
        boundary_pos = None
        boundary_unv = None
        boundary_batch = None
        boundary_patch_indices = None
        patch_input_ids = None
        patch_attention_mask = None
        patch_unique_input_ids = None
        patch_unique_attention_mask = None
        patch_text_inverse_indices = None
        patch_ref_edge_index = None
        patch_ref_edge_type = None
        global_input_ids = None
        global_attention_mask = None
        patch_numerical_values = None

        if self.field_set == "airfrans" and (self.patch_only_numerical or self.use_text_conditioning):
            # AirfRANS dual-stream conditioning (numerical-SCA and TE-SCA share this).
            # The boundary KV is the FULL set of airfoil-surface (WALL) nodes, selected by the
            # cell-type mask: surface nodes are NOT contiguous in the point cloud, so a patch
            # start:end slice would wrongly include interior nodes. The full boundary is kept
            # regardless of which pivots the prediction stream samples (the SCA recovery).
            # All surface nodes belong to the single "airfoil" patch (index 0).
            boundary_pos = cpd_centroid_full[mask_boundary_cell]
            boundary_unv = unv_cpd_cell_full[mask_boundary_cell]
            n_wall = int(boundary_pos.shape[0])
            boundary_patch_indices = torch.zeros((n_wall,), dtype=torch.long)
            boundary_batch = torch.zeros((n_wall,), dtype=torch.long)
            if self.patch_only_numerical:
                # numerical-SCA: each surface node carries the per-case freestream [u_inf, v_inf].
                fs = json.loads(h5_file_handle.attrs["freestream"])
                freestream_vec = torch.tensor(
                    [float(fs["u_inf"]), float(fs["v_inf"])], dtype=torch.float32
                )
                patch_numerical_values = freestream_vec.unsqueeze(0).repeat(n_wall, 1)  # [N_wall, 2]
            # TE-SCA (use_text_conditioning): the per-case descriptor embedding is attached
            # below via precomputed_patch_embeddings ([1, hidden]); boundary_patch_indices
            # (all 0) expands it to every surface node inside the bridge.

        elif self.field_set == "olbc" and (self.patch_only_numerical or self.use_text_conditioning):
            # olbc per-boundary-node BC-TYPE conditioning. The boundary KV is the FULL set of
            # |sdf|=0 boundary nodes (cell-type mask), each carrying its own (type, g, alpha).
            # Every arm builds patch_numerical_values per-face as [g, alpha, <type-encoding>];
            # ONLY the type-encoding differs (numeric=none / onehot3 / type_emb384), so the
            # experiment isolates the value of conveying BC TYPE while holding g,alpha constant.
            boundary_pos = cpd_centroid_full[mask_boundary_cell]
            boundary_unv = unv_cpd_cell_full[mask_boundary_cell]
            n_wall = int(boundary_pos.shape[0])
            boundary_patch_indices = torch.zeros((n_wall,), dtype=torch.long)
            boundary_batch = torch.zeros((n_wall,), dtype=torch.long)
            bc_all = torch.from_numpy(h5_file_handle["olbc|bc"][()]).to(torch.float32)  # [N,3]=(type,g,alpha)
            bc_wall = bc_all[mask_boundary_cell]                                         # [n_wall, 3]
            type_code = bc_wall[:, 0].long().clamp(0, 2)                                 # 0=D,1=N,2=R
            g_alpha = bc_wall[:, 1:3]                                                    # [n_wall, 2]
            if self.olbc_cond_mode == "numeric":
                patch_numerical_values = g_alpha                                         # [n_wall, 2]
            elif self.olbc_cond_mode == "onehot":
                onehot = torch.zeros((n_wall, 3), dtype=torch.float32)
                onehot[torch.arange(n_wall), type_code] = 1.0
                patch_numerical_values = torch.cat([g_alpha, onehot], dim=1)             # [n_wall, 5]
            elif self.olbc_cond_mode == "text":
                type_emb = self._olbc_type_embeddings.index_select(0, type_code)         # [n_wall, D_text]
                patch_numerical_values = torch.cat([g_alpha, type_emb], dim=1)           # [n_wall, 2+D_text]
            elif self.olbc_cond_mode == "text_sidecar":
                # Faithful Method-C: per-case [3,384] table precomputed offline from the OF BC
                # text (TOML-style serialize -> frozen MiniLM), baked into the H5. Index it by the
                # per-node type_code (same machinery as the global "text" mode, but the table is
                # PER CASE and value-aware). Absent-type rows are zero but never selected.
                text_table = torch.from_numpy(
                    h5_file_handle["olbc|text_emb_table"][()]).to(torch.float32)          # [3,384]
                text_emb = text_table.index_select(0, type_code)                          # [n_wall,384]
                patch_numerical_values = torch.cat([g_alpha, text_emb], dim=1)           # [n_wall, 2+384]
            else:
                raise ValueError(
                    f"Unknown olbc_cond_mode '{self.olbc_cond_mode}'; expected numeric/onehot/text/text_sidecar"
                )

        elif self.patch_only_numerical:
            # A2: use the same text_conditioning H5 group as C1/C2 for patch_order
            # and patch_ref_edge_index, but replace text tokens with per-patch
            # mean of (u,v,w,p,T) numerical values.
            try:
                sidecar = load_text_conditioning_from_h5(h5_file)
            except KeyError:
                h5_file_handle.close()
                raise FileNotFoundError(
                    f"Missing text_conditioning H5 group for case '{case_name}' — "
                    "patch_only_numerical requires the same dataset layout as C1/C2"
                )
            patch_list = load_patch_list_from_h5(h5_file)
            patch_order = sidecar["patch_order"]

            boundary_pos_list = []
            boundary_unv_list = []
            boundary_patch_index_list = []
            patch_numerical_list = []
            for patch_idx, patch_name in enumerate(patch_order):
                patch_meta = patch_list.get(patch_name)
                if patch_meta is None:
                    h5_file_handle.close()
                    raise KeyError(
                        f"Patch '{patch_name}' from text sidecar not found in patch_list for {h5_file}"
                    )
                start_idx = int(patch_meta["start_idx_cpd_cell"])
                end_idx = int(patch_meta["end_idx_cpd_cell"])
                patch_centroids = cpd_centroid_full[start_idx:end_idx]
                patch_unvs = unv_cpd_cell_full[start_idx:end_idx]
                if patch_centroids.numel() == 0:
                    continue
                boundary_pos_list.append(patch_centroids)
                boundary_unv_list.append(patch_unvs)
                boundary_patch_index_list.append(
                    torch.full((patch_centroids.size(0),), int(patch_idx), dtype=torch.long)
                )
                patch_uvwpT = init_phi[start_idx:end_idx]  # [N_patch_cells, 5]
                if self.patch_numerical_per_face:
                    # v2 input-aligned mode: raw per-FACE values, row-aligned with boundary_pos
                    # (same start:end slice) — matches the baseline's per-face boundary bc exactly.
                    patch_numerical_list.append(patch_uvwpT)  # [N_patch_cells, 5]
                else:
                    # Per-patch mean of (u, v, w, p, T)
                    patch_numerical_list.append(patch_uvwpT.mean(dim=0, keepdim=True))  # [1, 5]

            if boundary_pos_list:
                boundary_pos = torch.cat(boundary_pos_list, dim=0)
                boundary_unv = torch.cat(boundary_unv_list, dim=0)
                boundary_patch_indices = torch.cat(boundary_patch_index_list, dim=0)
                boundary_batch = torch.zeros((boundary_pos.size(0),), dtype=torch.long)
                patch_numerical_values = torch.cat(patch_numerical_list, dim=0)  # [N_patches, 5]
            else:
                boundary_pos = torch.zeros((0, 3), dtype=torch.float32)
                boundary_unv = torch.zeros((0, 3), dtype=torch.float32)
                boundary_patch_indices = torch.zeros((0,), dtype=torch.long)
                boundary_batch = torch.zeros((0,), dtype=torch.long)
                patch_numerical_values = torch.zeros((0, 5), dtype=torch.float32)

            patch_ref_edge_index = sidecar["patch_ref_edge_index"].clone().to(torch.long)
            patch_ref_edge_type = sidecar["patch_ref_edge_type"].clone().to(torch.long)

        elif self.use_text_conditioning:
            try:
                sidecar = load_text_conditioning_from_h5(h5_file)
            except KeyError:
                text_sidecar_path = Path(h5_file).parent / self.text_sidecar_name
                if not text_sidecar_path.exists():
                    h5_file_handle.close()
                    raise FileNotFoundError(
                        f"Missing text conditioning data for case '{case_name}': "
                        f"neither H5 group 'text_conditioning' nor legacy sidecar {text_sidecar_path} exists"
                    )
                sidecar = torch.load(text_sidecar_path, map_location="cpu")

            if self.global_cond_only:
                # Method D: only need global text, skip boundary geometry
                if self._precomputed_text_embeddings is not None:
                    case_embeds = self._precomputed_text_embeddings.get(case_name, {})
                    precomputed_global_embeddings = case_embeds.get("global_embeddings")
                    global_input_ids = None
                    global_attention_mask = None
                else:
                    precomputed_global_embeddings = None
                    global_input_ids = sidecar["global_input_ids"].clone()
                    global_attention_mask = sidecar["global_attention_mask"].clone()
                # Empty boundary tensors (GlobalCond skips per-patch geometry)
                boundary_pos = torch.zeros((0, 3), dtype=torch.float32)
                boundary_unv = torch.zeros((0, 3), dtype=torch.float32)
                boundary_patch_indices = torch.zeros(0, dtype=torch.long)
                boundary_batch = torch.zeros(0, dtype=torch.long)
                # Skip per-patch tokens
                patch_input_ids = None
                patch_attention_mask = None
                patch_unique_input_ids = None
                patch_unique_attention_mask = None
                patch_text_inverse_indices = None
                patch_ref_edge_index = None
                patch_ref_edge_type = None
                precomputed_patch_embeddings = None
            else:
                patch_list = load_patch_list_from_h5(h5_file)
                patch_order = sidecar["patch_order"]

                boundary_pos_list = []
                boundary_unv_list = []
                boundary_patch_index_list = []
                for patch_idx, patch_name in enumerate(patch_order):
                    patch_meta = patch_list.get(patch_name)
                    if patch_meta is None:
                        h5_file_handle.close()
                        raise KeyError(
                            f"Patch '{patch_name}' from text sidecar not found in patch_list for {h5_file}"
                        )

                    start_idx = int(patch_meta["start_idx_cpd_cell"])
                    end_idx = int(patch_meta["end_idx_cpd_cell"])
                    patch_centroids = cpd_centroid_full[start_idx:end_idx]
                    patch_unvs = unv_cpd_cell_full[start_idx:end_idx]
                    if patch_centroids.numel() == 0:
                        continue

                    boundary_pos_list.append(patch_centroids)
                    boundary_unv_list.append(patch_unvs)
                    boundary_patch_index_list.append(
                        torch.full((patch_centroids.size(0),), int(patch_idx), dtype=torch.long)
                    )

                if boundary_pos_list:
                    boundary_pos = torch.cat(boundary_pos_list, dim=0)
                    boundary_unv = torch.cat(boundary_unv_list, dim=0)
                    boundary_patch_indices = torch.cat(boundary_patch_index_list, dim=0)
                    boundary_batch = torch.zeros((boundary_pos.size(0),), dtype=torch.long)
                else:
                    boundary_pos = torch.zeros((0, 3), dtype=torch.float32)
                    boundary_unv = torch.zeros((0, 3), dtype=torch.float32)
                    boundary_patch_indices = torch.zeros((0,), dtype=torch.long)
                    boundary_batch = torch.zeros((0,), dtype=torch.long)

                # Check for pre-computed text embeddings (avoids running text encoder during training)
                _precomp = self._precomputed_text_embeddings
                if _precomp is not None and case_name in _precomp:
                    # Use pre-computed embeddings: no token IDs needed
                    patch_input_ids = None
                    patch_attention_mask = None
                    patch_unique_input_ids = None
                    patch_unique_attention_mask = None
                    patch_text_inverse_indices = None
                    global_input_ids = None
                    global_attention_mask = None
                    # ref graph data is still needed for trainable PatchReferenceGraphEncoder
                    patch_ref_edge_index = sidecar["patch_ref_edge_index"].clone().to(torch.long)
                    patch_ref_edge_type = sidecar["patch_ref_edge_type"].clone().to(torch.long)
                else:
                    # Load token IDs for on-the-fly text encoding (inference / legacy)
                    patch_input_ids = sidecar["patch_input_ids"].clone().to(torch.long)
                    patch_attention_mask = sidecar["patch_attention_mask"].clone().to(torch.long)
                    patch_unique_input_ids = sidecar["patch_unique_input_ids"].clone().to(torch.long)
                    patch_unique_attention_mask = sidecar["patch_unique_attention_mask"].clone().to(torch.long)
                    patch_text_inverse_indices = sidecar["patch_text_inverse_indices"].clone().to(torch.long)
                    patch_ref_edge_index = sidecar["patch_ref_edge_index"].clone().to(torch.long)
                    patch_ref_edge_type = sidecar["patch_ref_edge_type"].clone().to(torch.long)
                    global_input_ids = sidecar["global_input_ids"].clone().to(torch.long)
                    global_attention_mask = sidecar["global_attention_mask"].clone().to(torch.long)
        else:
            # 构造边界条件编码输入
            # 注意：这里包含了完整的边界信息（所有patch节点）
            init_patch_phi_cpd_cell = torch.cat((
                init_phi[mask_boundary_cell],
                unv_cpd_cell[mask_boundary_cell],
                cpd_centroid[mask_boundary_cell]
            ), dim=1)
            # 🔧 CRITICAL: 在多GPU推理时，每个sample都应该携带完整的边界信息
            # batch索引初始化为0，在collate时会被正确更新
            init_patch_phi_batch_cpd_cell = torch.zeros((init_patch_phi_cpd_cell.shape[0], 1), dtype=torch.float32)

        # 加载目标场
        if self._layout_version >= 2 and "field_target" in h5_file_handle:
            target_phi = read_field_stack(h5_file_handle, self._field_specs, "field_target", 0)
        else:
            target_phi = read_field_stack(h5_file_handle, self._field_specs, None, -1)

        # 执行采样
        if self.sensor_only:
            # =====================================================
            # Sensor-only模式：使用预计算的sensor节点索引作为pivot
            # =====================================================
            sampled_cpd_cell_ids = self._sensor_pivot_indices[case_idx]
            
            # K-hop采样：以sensor对应节点为中心构建子图
            subgraph_nodes, subgraph_neighbor_cpd_cell, _, _ = k_hop_subgraph(
                sampled_cpd_cell_ids, self.khop, neighbor_cpd_cell,
                relabel_nodes=True, num_nodes=num_cpd_cell,
            )
            
            # 记录sensor节点在子图中的索引（用于后续插值）
            # sampled_cpd_cell_ids是全局索引，需要找到它们在subgraph_nodes中的位置
            sensor_local_indices = torch.zeros_like(sampled_cpd_cell_ids)
            for i, global_idx in enumerate(sampled_cpd_cell_ids):
                # 找到global_idx在subgraph_nodes中的位置
                local_idx = (subgraph_nodes == global_idx).nonzero(as_tuple=True)[0]
                if len(local_idx) > 0:
                    sensor_local_indices[i] = local_idx[0]
            
            # 提取子图数据
            cpd_centroid_full = cpd_centroid.clone()  # 保留完整的centroid用于插值
            init_phi = init_phi[subgraph_nodes]
            target_phi = target_phi[subgraph_nodes] if target_phi is not None else None
            cpd_centroid = cpd_centroid[subgraph_nodes]
            udf_cpd_cell = udf_cpd_cell[subgraph_nodes]
            unv_cpd_cell = unv_cpd_cell[subgraph_nodes]
            mask_internal_cell = mask_internal_cell[subgraph_nodes]
            
        elif self.subsampling:
            # 从pivot_node池中采样（训练和推理都使用此逻辑）
            try:
                sampled_cpd_cell_ids = self._sample_from_pool(case_idx)
            except StopIteration:
                # 推理模式下池已空，返回None表示该case已完成
                h5_file_handle.close()
                return None

            # K-hop采样 (explicitly pass num_nodes to avoid edge_index inference errors)
            subgraph_nodes, subgraph_neighbor_cpd_cell, _, _ = k_hop_subgraph(
                sampled_cpd_cell_ids, self.khop, neighbor_cpd_cell,
                relabel_nodes=True, num_nodes=num_cpd_cell,
            )
            subgraph_nodes_before_trunc = int(subgraph_nodes.numel())

            subgraph_nodes, subgraph_neighbor_cpd_cell, sampled_cpd_cell_ids = self._truncate_subgraph(
                subgraph_nodes=subgraph_nodes,
                subgraph_neighbor_cpd_cell=subgraph_neighbor_cpd_cell,
                pivot_nodes=sampled_cpd_cell_ids,
                neighbor_cpd_cell=neighbor_cpd_cell,
                num_cpd_cell=num_cpd_cell,
            )
            subgraph_nodes_after_trunc = int(subgraph_nodes.numel())
            sample_was_truncated = int(subgraph_nodes_after_trunc < subgraph_nodes_before_trunc)

            if self.baseline_full_boundary:
                # Baseline mode (cpd_cell style): keep the sampled INTERNAL cells, drop any
                # sampled boundary, and re-append ALL boundary faces. Edges are re-induced over
                # the combined node set. Uses the still-global mask_internal_cell / neighbor_cpd_cell.
                from FVdomain.baseline_sampling import build_full_boundary_subgraph
                subgraph_nodes, subgraph_neighbor_cpd_cell = build_full_boundary_subgraph(
                    sampled_nodes=subgraph_nodes,
                    mask_internal_cell=mask_internal_cell,
                    neighbor_cpd_cell=neighbor_cpd_cell,
                    num_cpd_cell=num_cpd_cell,
                )

            init_phi = init_phi[subgraph_nodes]
            target_phi = target_phi[subgraph_nodes] if target_phi is not None else None
            cpd_centroid = cpd_centroid[subgraph_nodes]
            udf_cpd_cell = udf_cpd_cell[subgraph_nodes]
            unv_cpd_cell = unv_cpd_cell[subgraph_nodes]
            mask_internal_cell = mask_internal_cell[subgraph_nodes]
        else:
            subgraph_nodes = torch.arange(num_cpd_cell)
            subgraph_neighbor_cpd_cell = neighbor_cpd_cell
            sampled_cpd_cell_ids = subgraph_nodes
            subgraph_nodes_before_trunc = int(subgraph_nodes.numel())
            subgraph_nodes_after_trunc = int(subgraph_nodes.numel())
            sample_was_truncated = 0

        # 拼接节点特征
        if self.field_set == "olbc":
            # olbc: 5-D prediction stream = [cpd_centroid(3), SDF(1), source_f(1)]. The scalar
            # solution u is the TARGET (steady Poisson — no time evolution), so it never enters the
            # input. The PDE forcing f is a node feature; the per-boundary-node BC (type+g+alpha)
            # enters ONLY via the cross-attention KV. cpd_centroid/udf are already subsampled, so
            # source is read full from H5 and indexed by subgraph_nodes to stay row-aligned.
            source_full = torch.from_numpy(h5_file_handle["source_cpd_cell"][()]).to(torch.float32)
            source_f = source_full[subgraph_nodes]
            init_phi_with_unv = torch.cat([cpd_centroid, udf_cpd_cell, source_f], dim=1)  # 5D
        elif self.field_set == "airfrans" and not self.include_t1_fields:
            # AirfRANS: 7-D geometry-only prediction stream = [cpd_centroid(3), SDF(1), unv(3)]
            # for EVERY arm (numerical SCA, TE-SCA, and the single-stream baseline). The field
            # [u,v,p,nut] is the TARGET (steady RANS — no time evolution), so it must never enter
            # the input. The freestream condition is supplied per-arm: boundary KV (numerical SCA),
            # text embedding (TE-SCA), or a per-node freestream broadcast appended in the importer
            # baseline branch (single-stream). SDF (signed distance to the airfoil) is the key cue.
            init_phi_with_unv = torch.cat([cpd_centroid, udf_cpd_cell, unv_cpd_cell], dim=1)
        elif self.use_text_conditioning or self.patch_only_numerical:
            if self.include_t1_fields:
                # D1/D2: 9 维 = [init_phi(5), unv(3), udf(1)]；同时通过 text cross-attn 注入 BC 信息
                init_phi_with_unv = torch.cat([init_phi, unv_cpd_cell, udf_cpd_cell], dim=1)
            else:
                # C1/C2/A2: 6 维 = [cpd_centroid (xyz), unv (3)]；条件信号通过 cross-attn 从 patch 注入
                init_phi_with_unv = torch.cat([cpd_centroid, unv_cpd_cell], dim=1)
        else:
            init_phi_with_unv = torch.cat([init_phi, unv_cpd_cell, udf_cpd_cell], dim=1)

        # Attach pre-computed text embeddings if available
        # These are per-PATCH embeddings [N_patches, hidden], NOT per-boundary-cell.
        # The model's patch_reference_encoder + boundary expansion happen in forward().
        # For global_cond_only, precomputed_patch/global_embeddings are already set above.
        if not self.global_cond_only:
            precomputed_patch_embeddings = None
            precomputed_global_embeddings = None
            _precomp = self._precomputed_text_embeddings
            if _precomp is not None and case_name in _precomp:
                case_emb = _precomp[case_name]
                precomputed_patch_embeddings = case_emb["patch_embeddings"]  # [N_patches, hidden]
                if "global_embeddings" in case_emb and case_emb["global_embeddings"] is not None:
                    precomputed_global_embeddings = case_emb["global_embeddings"]
            elif self._layout_version >= 2 and not self.patch_only_numerical:
                # Direct embedding loading from H5 (layout v2 warm-start)
                _h5_emb_key = self._embedding_key
                if f"text_conditioning/{_h5_emb_key}" in h5_file_handle:
                    _emb_from_h5 = torch.from_numpy(
                        h5_file_handle[f"text_conditioning/{_h5_emb_key}"][:]
                    ).to(torch.float32)
                    precomputed_patch_embeddings = _emb_from_h5

        # AirfRANS single-stream baseline: the per-case freestream [u_inf, v_inf] is a graph-level
        # global (the official AirfRANS inlet-velocity input). Stored as [1,2] and broadcast to
        # every node in the importer baseline branch. SCA/TE-SCA ignore it (condition enters via KV).
        freestream_vec = None
        if self.field_set == "airfrans":
            fs = json.loads(h5_file_handle.attrs["freestream"])
            freestream_vec = torch.tensor(
                [[float(fs["u_inf"]), float(fs["v_inf"])]], dtype=torch.float32
            )  # [1, 2]

        # 构造HomoGraph
        homograph = HomoGraph(
            x=init_phi_with_unv,
            init_patch_phi_cpd_cell=init_patch_phi_cpd_cell,
            init_patch_phi_batch_cpd_cell=init_patch_phi_batch_cpd_cell,
            boundary_pos=boundary_pos,
            boundary_unv=boundary_unv,
            boundary_batch=boundary_batch,
            boundary_patch_indices=boundary_patch_indices,
            patch_input_ids=patch_input_ids,
            patch_attention_mask=patch_attention_mask,
            patch_unique_input_ids=patch_unique_input_ids,
            patch_unique_attention_mask=patch_unique_attention_mask,
            patch_text_inverse_indices=patch_text_inverse_indices,
            patch_ref_edge_index=patch_ref_edge_index,
            patch_ref_edge_type=patch_ref_edge_type,
            global_input_ids=global_input_ids,
            global_attention_mask=global_attention_mask,
            precomputed_patch_embeddings=precomputed_patch_embeddings,
            precomputed_global_embeddings=precomputed_global_embeddings,
            patch_numerical_values=patch_numerical_values,
            freestream_vec=freestream_vec,
            y=target_phi,
            edge_index=subgraph_neighbor_cpd_cell,
            edge_attr=None,
            cpd_centroid=cpd_centroid,
            unv_cpd_cell=unv_cpd_cell,
            mesh_pos=mesh_pos,
            pv_cells_node=pv_cells_node,
            pv_cells_type=pv_cells_type,
            mask_internal_cell=mask_internal_cell,
            case_name=case_name
        )

        # 添加TTA推理所需的元信息
        # 🔧 FIX: 推理模式下总是添加这些属性,无论是否采样
        if self.split == "test":
            homograph.original_node_indices = subgraph_nodes
            homograph.case_idx = torch.tensor([case_idx], dtype=torch.long)
            homograph.num_cpd_cells_total = torch.tensor([num_cpd_cell], dtype=torch.long)
            if self.subsampling:
                homograph.sampled_pivot_nodes = sampled_cpd_cell_ids  # 记录本次采样的pivot节点

        homograph.sampled_pivot_count = torch.tensor(
            [int(sampled_cpd_cell_ids.numel())], dtype=torch.long
        )
        homograph.sampled_nodes_before_trunc = torch.tensor(
            [int(subgraph_nodes_before_trunc)], dtype=torch.long
        )
        homograph.sampled_nodes_after_trunc = torch.tensor(
            [int(subgraph_nodes_after_trunc)], dtype=torch.long
        )
        homograph.sampled_edge_count = torch.tensor(
            [int(subgraph_neighbor_cpd_cell.shape[1])], dtype=torch.long
        )
        homograph.sample_was_truncated = torch.tensor(
            [int(sample_was_truncated)], dtype=torch.long
        )
        homograph.num_cpd_cells_total = torch.tensor([num_cpd_cell], dtype=torch.long)
        homograph.num_cpd_cells_total = torch.tensor([num_cpd_cell], dtype=torch.long)

        # =====================================================
        # Sensor-only模式：添加sensor相关元信息用于后续插值
        # =====================================================
        if self.sensor_only:
            homograph.sensor_mode = True
            homograph.sensor_names = self._sensor_names  # List of sensor names
            homograph.sensor_local_indices = sensor_local_indices  # [num_sensors] 子图中的索引
            homograph.sensor_positions = self.sensor_positions  # 原始sensor配置
            homograph.cpd_centroid_full = cpd_centroid_full  # 完整的centroid用于插值验证

        h5_file_handle.close()
        return homograph

    def _map_idx_to_case(self, idx: int) -> int:
        """
        将全局索引映射到case_idx

        推理模式下，我们依次遍历每个case，直到其pivot_pool被取空
        这个方法根据idx找到应该处理哪个case
        """
        # 🔧 FIX: 如果不采样(sample_ratio=1.0)，直接返回idx对应的case
        if not self.subsampling or self._pivot_pools is None or self._sample_budgets is None:
            return idx % len(self.h5_list)

        # 计算每个case预估的采样次数
        cumulative = 0
        for case_idx, (pool, budget) in enumerate(zip(self._pivot_pools, self._sample_budgets)):
            num_samples = math.ceil(len(pool) / budget)
            if idx < cumulative + num_samples:
                return case_idx
            cumulative += num_samples

        # 如果idx超出范围，返回最后一个case（会触发StopIteration）
        return len(self._pivot_pools) - 1

    def reset_pools(self):
        """
        重置所有pivot_node池（用于推理时重新开始）
        """
        if not self.subsampling:
            return

        for case_idx in range(len(self._pivot_pools)):
            self._pool_positions[case_idx] = 0

            if self.split != "train":
                # 推理模式：重新打乱池
                pool = self._pivot_pools[case_idx]
                perm = torch.randperm(len(pool))
                self._pivot_pools[case_idx] = pool[perm]

        logger.info("✅ Pivot pools reset")
