import torch
import tomlkit
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)

# 控制是否打印debug信息（只打印一次）
_FVPATCH_DEBUG_PRINTED = False

class fvPatch:
    """
    Finite Volume Patch class for handling boundary conditions.
    Each patch represents a boundary region with specific boundary conditions for different fields.
    """

    def __init__(self,):
        """
        Initialize an empty fvPatch.

        The patch information coming from mesh parsing is supplied later
        through register(patch_dict).
        """
        self.patch_idx = []
        self.start_idx_face = []
        self.end_idx_face = []
        self.start_idx_cpd_cell = []
        self.end_idx_cpd_cell = []
        self.neighbour_patch_idx = [] # 如果是非cyclic边界，那么这个值为-1
        
        self.num_patches = 0
        
        self.patch_dict = None
    
    def register(self, patch_dict: Dict[str, Dict[str, Any]]):

        self.patch_dict = patch_dict

        delay_neighbour_patch = []

        for source_patch_idx, (patch_name, patch_info) in enumerate(patch_dict.items()):
            self.patch_dict[patch_name]["patch_idx"] = source_patch_idx
            self.patch_idx.append(torch.tensor([source_patch_idx]))
            self.start_idx_face.append(torch.tensor(patch_info["start_idx_face"]))
            self.end_idx_face.append(torch.tensor(patch_info["end_idx_face"]))
            self.start_idx_cpd_cell.append(torch.tensor(patch_info["start_idx_cpd_cell"]))
            self.end_idx_cpd_cell.append(torch.tensor(patch_info["end_idx_cpd_cell"]))
            neighbour_patch = patch_info.get("neighbour_patch",None)

            if neighbour_patch is not None:
                delay_neighbour_patch.append((source_patch_idx, neighbour_patch)) # neighbor_patch is str or patch_name

            # 无论如何都插入-1来占位
            self.neighbour_patch_idx.append(torch.tensor([-1]))

            # 计数当前这个case一共有几个patch
            self.num_patches += 1

        # 处理循环边界
        for source_patch_idx, neighbour_patch in delay_neighbour_patch:
            self.neighbour_patch_idx[source_patch_idx] = torch.tensor([patch_dict[neighbour_patch]["patch_idx"]])

        self.patch_idx = torch.stack(self.patch_idx).long()
        self.start_idx_face = torch.stack(self.start_idx_face).long()
        self.end_idx_face = torch.stack(self.end_idx_face).long()
        self.start_idx_cpd_cell = torch.stack(self.start_idx_cpd_cell).long()
        self.end_idx_cpd_cell = torch.stack(self.end_idx_cpd_cell).long()
        self.neighbour_patch_idx = torch.stack(self.neighbour_patch_idx).long()

        return self
    
from FVdomain.Graph_data import HeteroData
from typing_extensions import Self

class fvPatchBatch:
    
    """
    Batch of fvPatch objects.
    """
    def __init__(self,):
        
        self.patch_idx = []
        self.start_idx_face = []
        self.end_idx_face = []
        self.start_idx_cpd_cell = []
        self.end_idx_cpd_cell = []
        self.neighbor_patch_idx = [] # 如果是非cyclic边界，那么这个值为-1
        
        self.num_cases = 0

    @classmethod
    def from_fvpatch_list(
        cls,
        fvpatch_list: List[fvPatch], # 这里list里面存储了多个case的fvpatch对象
        graph_data_list: List[HeteroData], # 这里list里面存储了多个case的graph_data对象，且主要用来获取num_faces,num_cpd_cells,num_patches等信息
        debug: bool = False,  # 是否打印debug信息
    ) -> Self:
        global _FVPATCH_DEBUG_PRINTED

        assert len(fvpatch_list)==len(graph_data_list), "fvpatch_list and graph_data_list must have the same length"

        # Create a new instance
        batch = cls()

        last_num_faces=0
        last_num_cpd_cells = 0
        last_num_patches = 0

        # DEBUG: 是否打印详细信息
        should_debug = debug and not _FVPATCH_DEBUG_PRINTED and len(fvpatch_list) > 1
        
        if should_debug:
            _FVPATCH_DEBUG_PRINTED = True
            logger.info("=" * 80)
            logger.info("DEBUG: fvPatchBatch.from_fvpatch_list 详细信息")
            logger.info("=" * 80)
            logger.info(f"Number of cases to batch: {len(fvpatch_list)}")

        for case_i, fvpatch in enumerate(fvpatch_list):
            graph_data_i = graph_data_list[case_i]
            
            if should_debug:
                logger.info("-" * 80)
                logger.info(f"Case {case_i} 原始信息 (offset前):")
                logger.info(f"  num_patches: {fvpatch.num_patches}")
                logger.info(f"  patch_idx (原始): {fvpatch.patch_idx.squeeze().tolist()}")
                logger.info(f"  neighbour_patch_idx (原始): {fvpatch.neighbour_patch_idx.squeeze().tolist()}")
                logger.info(f"  start_idx_face (原始): {fvpatch.start_idx_face.squeeze().tolist()}")
                logger.info(f"  end_idx_face (原始): {fvpatch.end_idx_face.squeeze().tolist()}")
                logger.info(f"  start_idx_cpd_cell (原始): {fvpatch.start_idx_cpd_cell.squeeze().tolist()}")
                logger.info(f"  end_idx_cpd_cell (原始): {fvpatch.end_idx_cpd_cell.squeeze().tolist()}")
                logger.info(f"  ")
                logger.info(f"  Offsets to apply:")
                logger.info(f"    last_num_patches: {last_num_patches}")
                logger.info(f"    last_num_faces: {last_num_faces}")
                logger.info(f"    last_num_cpd_cells: {last_num_cpd_cells}")
                logger.info(f"  ")
                logger.info(f"  Graph data info:")
                logger.info(f"    graph_data._num_faces: {graph_data_i._num_faces}")
                logger.info(f"    graph_data._num_cpd_cells: {graph_data_i._num_cpd_cells}")
                
                # 打印每个patch的名称和类型
                if fvpatch.patch_dict is not None:
                    logger.info(f"  Patch details:")
                    for patch_name, patch_info in fvpatch.patch_dict.items():
                        geom_type = patch_info.get("geom_type", "unknown")
                        neighbour = patch_info.get("neighbour_patch", None)
                        patch_idx = patch_info.get("patch_idx", "?")
                        logger.info(f"    [{patch_idx}] {patch_name}: type={geom_type}, neighbour={neighbour}")

            batch.patch_idx.append(fvpatch.patch_idx + last_num_patches)
            batch.start_idx_face.append(fvpatch.start_idx_face + last_num_faces)
            batch.end_idx_face.append(fvpatch.end_idx_face + last_num_faces)
            batch.start_idx_cpd_cell.append(fvpatch.start_idx_cpd_cell + last_num_cpd_cells)
            batch.end_idx_cpd_cell.append(fvpatch.end_idx_cpd_cell + last_num_cpd_cells)
            
            # 只对有效的neighbor_patch_idx (>=0) 添加offset
            # -1表示非cyclic边界，不应该添加offset
            neighbor_idx_with_offset = fvpatch.neighbour_patch_idx.clone()
            valid_mask = neighbor_idx_with_offset >= 0
            neighbor_idx_with_offset[valid_mask] += last_num_patches
            batch.neighbor_patch_idx.append(neighbor_idx_with_offset)

            if should_debug:
                logger.info(f"  ")
                logger.info(f"  Case {case_i} offset后的值:")
                logger.info(f"    patch_idx: {batch.patch_idx[-1].squeeze().tolist()}")
                logger.info(f"    neighbor_patch_idx: {batch.neighbor_patch_idx[-1].squeeze().tolist()}")
                logger.info(f"    start_idx_face: {batch.start_idx_face[-1].squeeze().tolist()}")
                logger.info(f"    end_idx_face: {batch.end_idx_face[-1].squeeze().tolist()}")
                logger.info(f"    start_idx_cpd_cell: {batch.start_idx_cpd_cell[-1].squeeze().tolist()}")
                logger.info(f"    end_idx_cpd_cell: {batch.end_idx_cpd_cell[-1].squeeze().tolist()}")
                
                # 验证cyclic边界配对
                cyclic_patches = torch.where(neighbor_idx_with_offset.squeeze() >= 0)[0]
                if len(cyclic_patches) > 0:
                    logger.info(f"  ")
                    logger.info(f"  Cyclic patches in Case {case_i}:")
                    for cp_local_idx in cyclic_patches:
                        cp_global_idx = (fvpatch.patch_idx[cp_local_idx] + last_num_patches).item()
                        nb_global_idx = neighbor_idx_with_offset[cp_local_idx].item()
                        logger.info(f"    Global patch {cp_global_idx} -> neighbor {nb_global_idx}")

            # 更新offset (这里必须用+=)
            last_num_faces += graph_data_i._num_faces
            last_num_cpd_cells += graph_data_i._num_cpd_cells
            last_num_patches += fvpatch.num_patches

        batch.patch_idx = torch.cat(batch.patch_idx)
        batch.start_idx_face = torch.cat(batch.start_idx_face)
        batch.end_idx_face = torch.cat(batch.end_idx_face)
        batch.start_idx_cpd_cell = torch.cat(batch.start_idx_cpd_cell)
        batch.end_idx_cpd_cell = torch.cat(batch.end_idx_cpd_cell)
        batch.neighbor_patch_idx = torch.cat(batch.neighbor_patch_idx)

        # Store number of cases for validation
        batch.num_cases = len(fvpatch_list)

        if should_debug:
            logger.info("=" * 80)
            logger.info("DEBUG: Batched fvPatchBatch 最终结果")
            logger.info("=" * 80)
            logger.info(f"num_cases: {batch.num_cases}")
            logger.info(f"Total patches: {len(batch.patch_idx)}")
            logger.info(f"patch_idx: {batch.patch_idx.squeeze().tolist()}")
            logger.info(f"neighbor_patch_idx: {batch.neighbor_patch_idx.squeeze().tolist()}")
            logger.info(f"start_idx_face: {batch.start_idx_face.squeeze().tolist()}")
            logger.info(f"end_idx_face: {batch.end_idx_face.squeeze().tolist()}")
            logger.info(f"start_idx_cpd_cell: {batch.start_idx_cpd_cell.squeeze().tolist()}")
            logger.info(f"end_idx_cpd_cell: {batch.end_idx_cpd_cell.squeeze().tolist()}")
            
            # 验证所有cyclic边界配对的双向性
            logger.info("-" * 80)
            logger.info("DEBUG: 验证Cyclic边界配对双向性")
            cyclic_mask = batch.neighbor_patch_idx.squeeze() >= 0
            cyclic_indices = torch.where(cyclic_mask)[0]
            all_valid = True
            for idx in cyclic_indices:
                patch_idx = batch.patch_idx[idx].item()
                nb_idx = batch.neighbor_patch_idx[idx].item()
                # 检查反向配对
                reverse_nb = batch.neighbor_patch_idx[nb_idx].item()
                if reverse_nb == patch_idx:
                    logger.info(f"  ✅ Patch {patch_idx} <-> {nb_idx} 双向验证通过")
                else:
                    logger.info(f"  ❌ Patch {patch_idx} -> {nb_idx}, 但反向是 {nb_idx} -> {reverse_nb} (期望 {patch_idx})")
                    all_valid = False
            
            # 验证非cyclic边界保持-1
            non_cyclic_mask = batch.neighbor_patch_idx.squeeze() < 0
            non_cyclic_indices = torch.where(non_cyclic_mask)[0]
            logger.info("-" * 80)
            logger.info(f"DEBUG: 非Cyclic边界验证 (共{len(non_cyclic_indices)}个)")
            non_cyclic_valid = True
            for idx in non_cyclic_indices:
                nb_val = batch.neighbor_patch_idx[idx].item()
                if nb_val != -1:
                    logger.info(f"  ❌ Patch {idx} 的 neighbor_patch_idx = {nb_val}, 应该是 -1")
                    non_cyclic_valid = False
            if non_cyclic_valid:
                logger.info(f"  ✅ 所有非Cyclic边界的neighbor_patch_idx都是-1")
            
            # 验证face和cpd_cell索引的连续性和正确性
            logger.info("-" * 80)
            logger.info("DEBUG: 验证face索引范围")
            for i in range(len(batch.start_idx_face)):
                start_f = batch.start_idx_face[i].item()
                end_f = batch.end_idx_face[i].item()
                num_faces = end_f - start_f
                logger.info(f"  Patch {i}: face_range=[{start_f}, {end_f}), num_faces={num_faces}")
                if num_faces <= 0:
                    logger.info(f"    ❌ ERROR: num_faces <= 0!")
            
            logger.info("-" * 80)
            logger.info("DEBUG: 验证cpd_cell索引范围")
            for i in range(len(batch.start_idx_cpd_cell)):
                start_c = batch.start_idx_cpd_cell[i].item()
                end_c = batch.end_idx_cpd_cell[i].item()
                num_cells = end_c - start_c
                logger.info(f"  Patch {i}: cpd_cell_range=[{start_c}, {end_c}), num_cpd_cells={num_cells}")
                if num_cells <= 0:
                    logger.info(f"    ❌ ERROR: num_cpd_cells <= 0!")
            
            logger.info("=" * 80)
            if all_valid and non_cyclic_valid:
                logger.info("✅ fvPatchBatch 所有验证通过!")
            else:
                logger.info("❌ fvPatchBatch 存在验证错误!")
            logger.info("=" * 80)

        return batch
    