#!/usr/bin/env python3
"""Persistent AI worker for postprocess adaptive-region inference."""

from __future__ import annotations

import json
import re
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Optional

import h5py
import numpy as np
import torch
import yaml
from torch_geometric.utils import k_hop_subgraph


REPO_ROOT = Path(__file__).resolve().parents[2]
MYCODE_ROOT = REPO_ROOT.parent
GEN_FVGN_ROOT = MYCODE_ROOT / "Gen-FVGN-refine"
GEN_FVGN_SRC = GEN_FVGN_ROOT / "src"
DEFAULT_CONFIG = GEN_FVGN_ROOT / "cases/GTC/configs/config_repro_text_fixedemb_case103.yaml"
DEFAULT_DATASET_ROOT = GEN_FVGN_ROOT / "cases/GTC/dataset/processed_dataset"

sys.path.insert(0, str(GEN_FVGN_SRC))

from config import Config
from FVdomain.Graph_data import HomoGraph
from NNmodels.importer import Simulator
from Utils.key import Key
from Utils.normalization import StandardDeviationNormalizer
from Utils.text_conditioning import load_patch_list_from_h5, load_text_conditioning_from_h5
from Utils.utilities import FaceType


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _resolve_path(path_str: str, base: Path) -> Path:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    return path.resolve()


def _mk_norm(stats: dict, mean_key: str, std_key: str) -> StandardDeviationNormalizer:
    return StandardDeviationNormalizer(
        mean=torch.tensor(stats["mean"][mean_key], dtype=torch.float32),
        std=torch.tensor(stats["std"][std_key], dtype=torch.float32),
    )


def _build_key_sets(stats: dict) -> tuple[list[Key], list[Key], list[Key]]:
    input_keys = [
        Key(name="u_cpd_cell_init", dimension=1),
        Key(name="v_cpd_cell_init", dimension=1),
        Key(name="w_cpd_cell_init", dimension=1),
        Key(name="p_cpd_cell_init", dimension=1),
        Key(name="T_cpd_cell_init", dimension=1),
    ]
    output_keys = [
        Key(name="u_cpd_cell_pred", dimension=1, normalizer=_mk_norm(stats, "uvw_cpd_cell_u", "uvw_cpd_cell_u")),
        Key(name="v_cpd_cell_pred", dimension=1, normalizer=_mk_norm(stats, "uvw_cpd_cell_v", "uvw_cpd_cell_v")),
        Key(name="w_cpd_cell_pred", dimension=1, normalizer=_mk_norm(stats, "uvw_cpd_cell_w", "uvw_cpd_cell_w")),
        Key(name="p_cpd_cell_pred", dimension=1, normalizer=_mk_norm(stats, "press_cpd_cell", "press_cpd_cell")),
        Key(name="T_cpd_cell_pred", dimension=1, normalizer=_mk_norm(stats, "temp_cpd_cell", "temp_cpd_cell")),
    ]
    target_keys = [
        Key(name="u_cpd_cell_true", dimension=1, normalizer=_mk_norm(stats, "uvw_cpd_cell_u", "uvw_cpd_cell_u")),
        Key(name="v_cpd_cell_true", dimension=1, normalizer=_mk_norm(stats, "uvw_cpd_cell_v", "uvw_cpd_cell_v")),
        Key(name="w_cpd_cell_true", dimension=1, normalizer=_mk_norm(stats, "uvw_cpd_cell_w", "uvw_cpd_cell_w")),
        Key(name="p_cpd_cell_true", dimension=1, normalizer=_mk_norm(stats, "press_cpd_cell", "press_cpd_cell")),
        Key(name="T_cpd_cell_true", dimension=1, normalizer=_mk_norm(stats, "temp_cpd_cell", "temp_cpd_cell")),
    ]
    return input_keys, output_keys, target_keys


@dataclass
class CaseData:
    case_path: Path
    h5_path: Path
    case_name: str
    cpd_centroid: torch.Tensor
    neighbor_cpd_cell: torch.Tensor
    mesh_pos: torch.Tensor
    pv_cells_node: torch.Tensor
    pv_cells_type: torch.Tensor
    target_phi: torch.Tensor
    unv_cpd_cell: torch.Tensor
    mask_internal_full: torch.Tensor
    internal_centroids: torch.Tensor
    internal_global_indices: torch.Tensor
    internal_position_map: torch.Tensor
    boundary_pos: torch.Tensor
    boundary_unv: torch.Tensor
    boundary_batch: torch.Tensor
    boundary_patch_indices: torch.Tensor
    patch_ref_edge_index: torch.Tensor
    patch_ref_edge_type: torch.Tensor
    precomputed_patch_embeddings: torch.Tensor
    precomputed_global_embeddings: Optional[torch.Tensor]
    bounds_min: torch.Tensor
    bounds_max: torch.Tensor


class _TextEncoderStub(torch.nn.Module):
    def forward(self, *args, **kwargs):
        raise RuntimeError("Text encoder was replaced after fixedEmbedding precomputation")


class FixedEmbeddingRuntime:
    def __init__(self) -> None:
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.config: Optional[Config] = None
        self.simulator: Optional[Simulator] = None
        self.case_data: Optional[CaseData] = None
        self.model_path: Optional[Path] = None
        self.case_path: Optional[Path] = None
        self.khop = 2
        self.ready = False
        self.last_error: Optional[str] = None
        self.last_result: Optional[dict] = None

    def status(self) -> dict:
        bounds = None
        if self.case_data is not None:
            bounds = {
                "min": self.case_data.bounds_min.tolist(),
                "max": self.case_data.bounds_max.tolist(),
            }
        return {
            "ready": self.ready,
            "device": self.device,
            "modelPath": str(self.model_path) if self.model_path else "",
            "casePath": str(self.case_path) if self.case_path else "",
            "caseName": self.case_data.case_name if self.case_data else "",
            "bounds": bounds,
            "lastError": self.last_error,
            "lastResult": self.last_result,
        }

    def _resolve_checkpoint(self, checkpoint_path: str) -> Path:
        return _resolve_path(checkpoint_path, GEN_FVGN_ROOT)

    def _find_case_h5(self, case_path: Path) -> Path:
        h5_dir = case_path / "h5"
        if h5_dir.is_dir():
            matches = sorted(h5_dir.glob("*.h5"))
            if matches:
                return matches[0]

        match = re.search(r"(case\d+)", case_path.name)
        if match:
            case_name = match.group(1)
            fallback = DEFAULT_DATASET_ROOT / case_name / "h5" / f"{case_name}.h5"
            if fallback.exists():
                return fallback

        raise FileNotFoundError(
            f"Could not locate an H5 graph for case path {case_path}. "
            "Expected case/h5/*.h5 or a path name containing caseNNN."
        )

    def _load_config(self, checkpoint_path: Path) -> Config:
        config_dict = yaml.safe_load(DEFAULT_CONFIG.read_text())
        dataset_path = str(DEFAULT_DATASET_ROOT)
        config_dict["training_config"]["dataset_path"] = dataset_path
        config_dict["inference_config"]["dataset_path"] = dataset_path
        config_dict["training_config"]["model_name"] = "TextCondTransFVGN_v1"
        config_dict["inference_config"]["model_name"] = "TextCondTransFVGN_v1"
        config_dict["inference_config"]["checkpoint_path"] = str(checkpoint_path)
        return Config.from_dict(config_dict)

    def _load_stats(self) -> dict:
        with open(DEFAULT_DATASET_ROOT / "meta.json", "r") as f:
            return json.load(f)["dataset_statistics"]

    def _load_simulator(self, checkpoint_path: Path) -> None:
        self.config = self._load_config(checkpoint_path)
        stats = self._load_stats()
        input_keys, output_keys, target_keys = _build_key_sets(stats)
        self.simulator = Simulator(
            config=self.config,
            input_keys=input_keys,
            output_keys=output_keys,
            target_keys=target_keys,
            mode="inference",
        ).to(self.device)
        self.simulator.load_model(str(checkpoint_path), strict=False)
        self.simulator.eval()
        self.khop = int(self.config.inference_config.get("tta_khop", 2))

    def _precompute_embeddings(self, sidecar: dict) -> tuple[torch.Tensor, Optional[torch.Tensor]]:
        assert self.simulator is not None
        with torch.no_grad():
            unique_ids = sidecar["patch_unique_input_ids"].long().to(self.device)
            unique_mask = sidecar["patch_unique_attention_mask"].long().to(self.device)
            unique_embeddings = self.simulator.model.text_encoder(unique_ids, unique_mask)
            inverse_idx = sidecar["patch_text_inverse_indices"].long().to(self.device)
            patch_embeddings = unique_embeddings.index_select(0, inverse_idx).cpu()

            global_embeddings = None
            if sidecar.get("global_input_ids") is not None:
                g_ids = sidecar["global_input_ids"].long().to(self.device)
                g_mask = sidecar["global_attention_mask"].long().to(self.device)
                global_embeddings = self.simulator.model.text_encoder(g_ids, g_mask).cpu()

        return patch_embeddings, global_embeddings

    def _load_case_data(self, case_path: Path) -> CaseData:
        h5_path = self._find_case_h5(case_path)
        case_name = h5_path.stem
        sidecar = load_text_conditioning_from_h5(str(h5_path))
        patch_list = load_patch_list_from_h5(str(h5_path))

        with h5py.File(h5_path, "r") as f:
            cpd_centroid = torch.from_numpy(f["cpd|centroid"][()]).float()
            neighbor_cpd_cell = torch.from_numpy(f["cpd|neighbor_cell"][()]).long()
            mesh_pos = torch.from_numpy(f["node|pos"][()]).float()
            pv_cells_node = torch.from_numpy(f["pv_cells_node"][()]).long()
            pv_cells_type = torch.from_numpy(f["pv_cells_type"][()]).long()
            target_uvw = torch.from_numpy(f["uvw_cpd_cell"][-1]).float()
            target_p = torch.from_numpy(f["press_cpd_cell"][-1]).float()
            target_t = torch.from_numpy(f["temp_cpd_cell"][-1]).float()
            cells_face_unv = torch.from_numpy(f["cells_face_normal"][()]).float()
            cell_face = torch.from_numpy(f["cells_face"][()]).long()
            cell_type = torch.from_numpy(f["cpd|cell_type"][()]).long().squeeze()
            face_type = torch.from_numpy(f["face|face_type"][()]).long().squeeze()

        num_cpd_cell = int(cpd_centroid.shape[0])
        num_face = int(neighbor_cpd_cell.shape[1])
        target_phi = torch.cat((target_uvw, target_p, target_t), dim=1)

        unv_face = torch.zeros((num_face, 3), dtype=torch.float32)
        unv_face[cell_face] = cells_face_unv
        mask_boundary_cell = cell_type != FaceType.NORMAL
        mask_boundary_face = face_type != FaceType.NORMAL
        mask_internal_full = cell_type == FaceType.NORMAL
        unv_cpd_cell = torch.zeros((num_cpd_cell, 3), dtype=torch.float32)
        unv_cpd_cell[mask_boundary_cell] = unv_face[mask_boundary_face]

        boundary_pos_list = []
        boundary_unv_list = []
        boundary_patch_index_list = []
        for patch_idx, patch_name in enumerate(sidecar["patch_order"]):
            patch_meta = patch_list.get(patch_name)
            if patch_meta is None:
                raise KeyError(f"Patch {patch_name} missing from patch_list in {h5_path}")
            start_idx = int(patch_meta["start_idx_cpd_cell"])
            end_idx = int(patch_meta["end_idx_cpd_cell"])
            patch_centroids = cpd_centroid[start_idx:end_idx]
            patch_unvs = unv_cpd_cell[start_idx:end_idx]
            if patch_centroids.numel() == 0:
                continue
            boundary_pos_list.append(patch_centroids)
            boundary_unv_list.append(patch_unvs)
            boundary_patch_index_list.append(
                torch.full((patch_centroids.size(0),), patch_idx, dtype=torch.long)
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

        patch_embeddings, global_embeddings = self._precompute_embeddings(sidecar)
        internal_global_indices = torch.nonzero(mask_internal_full, as_tuple=False).view(-1)
        internal_position_map = torch.full((num_cpd_cell,), -1, dtype=torch.long)
        internal_position_map[internal_global_indices] = torch.arange(
            internal_global_indices.numel(), dtype=torch.long
        )
        internal_centroids = cpd_centroid[mask_internal_full]

        return CaseData(
            case_path=case_path,
            h5_path=h5_path,
            case_name=case_name,
            cpd_centroid=cpd_centroid,
            neighbor_cpd_cell=neighbor_cpd_cell,
            mesh_pos=mesh_pos,
            pv_cells_node=pv_cells_node,
            pv_cells_type=pv_cells_type,
            target_phi=target_phi,
            unv_cpd_cell=unv_cpd_cell,
            mask_internal_full=mask_internal_full,
            internal_centroids=internal_centroids,
            internal_global_indices=internal_global_indices,
            internal_position_map=internal_position_map,
            boundary_pos=boundary_pos,
            boundary_unv=boundary_unv,
            boundary_batch=boundary_batch,
            boundary_patch_indices=boundary_patch_indices,
            patch_ref_edge_index=sidecar["patch_ref_edge_index"].clone().long(),
            patch_ref_edge_type=sidecar["patch_ref_edge_type"].clone().long(),
            precomputed_patch_embeddings=patch_embeddings,
            precomputed_global_embeddings=global_embeddings,
            bounds_min=internal_centroids.min(dim=0).values,
            bounds_max=internal_centroids.max(dim=0).values,
        )

    def _make_default_warmup_box(self) -> dict:
        assert self.case_data is not None
        bounds_min = self.case_data.bounds_min
        bounds_max = self.case_data.bounds_max
        center = 0.5 * (bounds_min + bounds_max)
        size = (bounds_max - bounds_min) * 0.12
        box_min = center - 0.5 * size
        box_max = center + 0.5 * size
        return {"min": box_min.tolist(), "max": box_max.tolist()}

    def start(self, model_path: str, case_path: str) -> dict:
        checkpoint_path = self._resolve_checkpoint(model_path)
        resolved_case_path = Path(case_path).expanduser().resolve()
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")
        if not resolved_case_path.exists():
            raise FileNotFoundError(f"Case path not found: {resolved_case_path}")

        t0 = time.time()
        self.model_path = checkpoint_path
        self.case_path = resolved_case_path
        self.ready = False
        self.last_error = None
        self.last_result = None

        _log(f"[pp-ai-worker] loading simulator from {checkpoint_path}")
        self._load_simulator(checkpoint_path)
        _log(f"[pp-ai-worker] loading case graph from {resolved_case_path}")
        self.case_data = self._load_case_data(resolved_case_path)

        assert self.simulator is not None
        self.simulator.model.text_encoder = _TextEncoderStub()

        warmup_box = self._make_default_warmup_box()
        warmup_result = self.infer_box(warmup_box, warmup=True)
        self.ready = True

        return {
            "device": self.device,
            "caseName": self.case_data.case_name,
            "bounds": {
                "min": self.case_data.bounds_min.tolist(),
                "max": self.case_data.bounds_max.tolist(),
            },
            "warmup": warmup_result,
            "startupSeconds": round(time.time() - t0, 3),
        }

    def _build_graph(self, subgraph_nodes: torch.Tensor, subgraph_edge_index: torch.Tensor) -> HomoGraph:
        assert self.case_data is not None
        graph = HomoGraph(
            x=torch.cat(
                [
                    self.case_data.cpd_centroid[subgraph_nodes],
                    self.case_data.unv_cpd_cell[subgraph_nodes],
                ],
                dim=1,
            ),
            y=self.case_data.target_phi[subgraph_nodes],
            edge_index=subgraph_edge_index,
            edge_attr=None,
            cpd_centroid=self.case_data.cpd_centroid[subgraph_nodes],
            unv_cpd_cell=self.case_data.unv_cpd_cell[subgraph_nodes],
            mesh_pos=self.case_data.mesh_pos,
            pv_cells_node=self.case_data.pv_cells_node,
            pv_cells_type=self.case_data.pv_cells_type,
            mask_internal_cell=self.case_data.mask_internal_full[subgraph_nodes],
            boundary_pos=self.case_data.boundary_pos,
            boundary_unv=self.case_data.boundary_unv,
            boundary_batch=self.case_data.boundary_batch,
            boundary_patch_indices=self.case_data.boundary_patch_indices,
            patch_input_ids=None,
            patch_attention_mask=None,
            patch_unique_input_ids=None,
            patch_unique_attention_mask=None,
            patch_text_inverse_indices=None,
            patch_ref_edge_index=self.case_data.patch_ref_edge_index,
            patch_ref_edge_type=self.case_data.patch_ref_edge_type,
            global_input_ids=None,
            global_attention_mask=None,
            precomputed_patch_embeddings=self.case_data.precomputed_patch_embeddings,
            precomputed_global_embeddings=self.case_data.precomputed_global_embeddings,
            case_name=self.case_data.case_name,
        )
        graph.batch = torch.zeros((graph.x.shape[0],), dtype=torch.long)
        return graph

    def infer_box(self, box: dict, warmup: bool = False) -> dict:
        if not self.simulator or not self.case_data:
            raise RuntimeError("Worker is not initialized")

        t0 = time.time()
        box_min = torch.tensor(box["min"], dtype=torch.float32)
        box_max = torch.tensor(box["max"], dtype=torch.float32)
        internal_centroids = self.case_data.internal_centroids
        selected_mask = ((internal_centroids >= box_min) & (internal_centroids <= box_max)).all(dim=1)
        pivot_nodes = self.case_data.internal_global_indices[selected_mask]
        if pivot_nodes.numel() == 0:
            raise ValueError("No internal cells are inside the current AI box")

        subgraph_nodes, subgraph_edge_index, _, _ = k_hop_subgraph(
            pivot_nodes,
            self.khop,
            self.case_data.neighbor_cpd_cell,
            relabel_nodes=True,
        )
        graph = self._build_graph(subgraph_nodes, subgraph_edge_index).to(self.device)

        local_lookup = {int(global_idx.item()): local_idx for local_idx, global_idx in enumerate(subgraph_nodes)}
        local_pivots = torch.tensor(
            [local_lookup[int(global_idx.item())] for global_idx in pivot_nodes],
            dtype=torch.long,
        )

        with torch.no_grad():
            prediction = self.simulator.inference(graph).prediction.detach().cpu()
        pivot_prediction = prediction[local_pivots]

        result = {
            "selectedCount": int(pivot_nodes.numel()),
            "subgraphNodeCount": int(subgraph_nodes.numel()),
            "subgraphEdgeCount": int(subgraph_edge_index.shape[1]),
            "ranges": {
                "AI_T": [
                    float(pivot_prediction[:, 4].min()),
                    float(pivot_prediction[:, 4].max()),
                ],
                "AI_p": [
                    float(pivot_prediction[:, 3].min()),
                    float(pivot_prediction[:, 3].max()),
                ],
                "AI_Umag": [
                    float(torch.linalg.norm(pivot_prediction[:, :3], dim=1).min()),
                    float(torch.linalg.norm(pivot_prediction[:, :3], dim=1).max()),
                ],
            },
            "box": {"min": box_min.tolist(), "max": box_max.tolist()},
            "inferSeconds": round(time.time() - t0, 3),
        }

        if warmup:
            result["warmup"] = True
            return result

        internal_count = int(self.case_data.internal_global_indices.numel())
        ai_u = np.zeros((internal_count, 3), dtype=np.float32)
        ai_p = np.zeros((internal_count,), dtype=np.float32)
        ai_t = np.zeros((internal_count,), dtype=np.float32)
        ai_mask = np.zeros((internal_count,), dtype=np.float32)

        selected_internal_positions = self.case_data.internal_position_map[pivot_nodes]
        selected_np = selected_internal_positions.numpy()
        pivot_np = pivot_prediction.numpy()
        ai_u[selected_np] = pivot_np[:, :3]
        ai_p[selected_np] = pivot_np[:, 3]
        ai_t[selected_np] = pivot_np[:, 4]
        ai_mask[selected_np] = 1.0

        output_dir = self.case_data.case_path / "ai_inference"
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            output_dir = Path("/tmp") / "dcdtuni_pp_ai" / self.case_data.case_name
            output_dir.mkdir(parents=True, exist_ok=True)
        stem = time.strftime("%Y%m%d-%H%M%S")
        output_path = output_dir / f"adaptive_ai_{stem}.npz"
        np.savez_compressed(
            output_path,
            AI_U=ai_u,
            AI_p=ai_p,
            AI_T=ai_t,
            AI_region_mask=ai_mask,
            box_min=np.asarray(box_min, dtype=np.float32),
            box_max=np.asarray(box_max, dtype=np.float32),
            selected_internal_positions=selected_np,
        )

        result["warmup"] = False
        result["outputPath"] = str(output_path)
        self.last_result = result
        return result


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> int:
    runtime = FixedEmbeddingRuntime()
    _log(f"[pp-ai-worker] ready on device={runtime.device}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = {}
        try:
            request = json.loads(line)
            action = request.get("action")
            if action == "status":
                _emit({"ok": True, "status": runtime.status()})
            elif action == "start":
                result = runtime.start(
                    model_path=str(request.get("modelPath", "")).strip(),
                    case_path=str(request.get("casePath", "")).strip(),
                )
                _emit({"ok": True, "result": result, "status": runtime.status()})
            elif action == "infer":
                result = runtime.infer_box(request.get("box") or {}, warmup=False)
                _emit({"ok": True, "result": result, "status": runtime.status()})
            elif action == "shutdown":
                _emit({"ok": True})
                return 0
            else:
                raise ValueError(f"Unsupported action: {action}")
        except Exception as exc:
            if request.get("action") == "start":
                runtime.ready = False
            runtime.last_error = str(exc)
            _log("[pp-ai-worker] error:\n" + traceback.format_exc())
            _emit({"ok": False, "error": str(exc), "status": runtime.status()})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
