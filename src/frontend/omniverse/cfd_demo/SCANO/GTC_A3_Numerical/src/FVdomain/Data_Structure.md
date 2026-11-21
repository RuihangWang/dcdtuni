# FV Domain HeteroData 数据结构文档

## 概述

本文档详细描述了基于PyTorch Geometric HeteroData的有限体积(FV)域图数据结构。该结构将原始的多个独立图数据集（GraphNodeDataset, GraphFaceDataset, GraphCellDataset等）统一为一个异构图对象。

## 文件结构

```
src/FVdomain/Graph_dataset.py  # HeteroGraphBase和HeteroGraphDataset定义
src/FVdomain/Graph_pool.py   # 数据加载器和兼容性包装器
```

## 命名逻辑（前缀或尾缀）
`node`: 网格顶点数量

`face`: 网格面数量（包含所有面元，即：内部面元+边界面元）

`cell`: 网格单元数量 (仅包含内部单元)

`cpd_cell`: 复合单元数量 （包含所有内部单元+边界面元）

`graph`: 图级别属性

### 举例
`node_pos`: 网格顶点坐标

`face_pos`: 网格面中心坐标

`cell_volume`: 网格单元体积

`cpd_cell_pos`: 复合单元坐标

`uvwp_cpd_cell`: 复合单元的uvwp值 (包含内部单元+边界面元)

`uvwp_face`: 网格面元的uvwp值 (包含内部面元+边界面元)

...

## 核心概念：指针数组格式 (Pointer Array Format)

本框架使用**指针数组格式**来存储不规则的连接关系（如单元-节点、单元-面、面-节点等）。这种格式不同于传统的CSR (Compressed Sparse Row) 格式。

### 指针数组格式定义

对于任意的连接关系（如单元包含哪些节点），使用两个**维度相同**的1D数组：

1. **数据数组** (`cells_node`): 存储所有连接的索引（展平）
2. **指针数组** (`cells_node_ptr`): 存储每个元素所属的父对象索引

**示例:**
```python
cells_node = [0, 1, 2, 3, 4, 5, 6, 7]
cells_node_ptr = [0, 0, 0, 0, 1, 1, 1, 1]
# 解释：
# - Cell 0 包含 nodes [0, 1, 2, 3]
# - Cell 1 包含 nodes [4, 5, 6, 7]
```

### 与CSR格式的对比

**传统CSR格式:**
```python
# CSR (Compressed Sparse Row) 格式
data = [0, 1, 2, 3, 4, 5, 6, 7]
row_ptr = [0, 4, 8]  # 每行的起始位置
# Cell 0: data[row_ptr[0]:row_ptr[1]] = [0, 1, 2, 3]
# Cell 1: data[row_ptr[1]:row_ptr[2]] = [4, 5, 6, 7]
```

**本框架的指针数组格式:**
```python
# Pointer Array 格式
cells_node = [0, 1, 2, 3, 4, 5, 6, 7]
cells_node_ptr = [0, 0, 0, 0, 1, 1, 1, 1]
# Cell 0: cells_node[cells_node_ptr == 0] = [0, 1, 2, 3]
# Cell 1: cells_node[cells_node_ptr == 1] = [4, 5, 6, 7]
```

### 优势

1. **GPU友好**: 支持高效的scatter/gather操作
   ```python
   # 将单元值scatter到节点
   node_values = scatter_add(cell_values[cells_node_ptr], cells_node, dim=0)
   ```

2. **无需切片**: 不需要计算起始/结束位置
   ```python
   # CSR需要切片
   nodes_of_cell_i = data[row_ptr[i]:row_ptr[i+1]]

   # 指针数组直接索引
   nodes_of_cell_i = cells_node[cells_node_ptr == i]
   ```

3. **支持不规则连接**: 每个单元可以有不同数量的节点/面
4. **Batching友好**: 指针值直接累加offset即可

### 应用场景

本框架中所有不规则连接都使用此格式：
- `cells_node` + `cells_node_ptr`: 单元-节点连接
- `cells_face` + `cells_face_ptr`: 单元-面连接
- `face_node` + `face_node_ptr`: 面-节点连接
- `face_edge` + `face_edge_ptr`: 面-边连接

## HeteroData 数据结构

### 节点类型 (Node Types)

#### 1. `'node'` - 物理网格节点
存储网格的物理节点/顶点信息。

**属性:**
- `pos`: `[num_nodes, 3]` - 节点的3D坐标位置 (x, y, z)
- `num_nodes`: `int` - 节点总数

#### 2. `'face'` - 网格面
存储网格面的几何和类型信息。

**属性:**
- `pos`: `[num_faces, 3]` - 面中心的3D坐标位置
- `face_area`: `[num_faces, 1]` - 每个面的面积
- `face_type`: `[num_faces]` - 面的类型标识（边界类型等）
- `num_nodes`: `int` - 面的总数

#### 3. `'cell'` - 网格单元
存储网格单元的几何信息。

**属性:**
- `cell_volume`: `[num_cells, 1]` - 每个单元的体积
- `cells_face_unv`: `[num_cells, max_faces_per_cell, 3]` - 单元面的单位法向量
- `num_nodes`: `int` - 单元总数

#### 4. `'cpd_cell'` - 复合单元 (用于WLSQ)
存储用于加权最小二乘(WLSQ)计算的复合单元信息。

**属性:**
- `pos`: `[num_cpd_cells, 3]` - 复合单元的质心坐标
- `x`: `[num_cpd_cells, 4+]` - 特征向量，包含uvwp（速度和压力）+ theta_PDE参数
- `y`: `[num_cpd_cells, 4]` - 目标值，包含目标uvwp
- `cell_type`: `[num_cpd_cells, 1]` - 单元类型标识
- `global_idx`: `[num_cpd_cells]` - 全局索引，用于数据池更新
- `A_cell_to_cell`: `[num_cpd_cells, num_cpd_cells]` - WLSQ系数矩阵A
- `single_B_cell_to_cell`: `[num_cpd_cells, num_cpd_cells]` - WLSQ系数矩阵B
- `num_nodes`: `int` - 复合单元总数

#### 5. `'graph'` - 图级别属性
存储整个图的全局参数和元数据。

**属性:**
- `theta_PDE`: `[5]` - PDE参数向量
- `case_name`: `[case_name_length]` - 案例名称（字符编码）
- `graph_index`: `[1]` - 图索引
- `num_nodes`: `int` - 固定为1

### 边类型 (Edge Types)

#### 1. `('face', 'connects_to', 'node')` - 面-节点连接
描述面由哪些节点组成。使用**指针数组格式**存储。

**属性:**
- `face_node`: `[total_face_node]` - 所有面的节点索引（展平的1D数组）
- `face_node_ptr`: `[total_face_node]` - 每个节点所属的面索引（与face_node维度相同）

**示例:**
```python
face_node = [0, 1, 2, 3, 4, 5, 6]
face_node_ptr = [0, 0, 0, 0, 1, 1, 1]
# 表示：
# - Face 0 由 nodes [0, 1, 2, 3] 组成（四边形）
# - Face 1 由 nodes [4, 5, 6] 组成（三角形）
```

#### 2. `('cell', 'contains', 'node')` - 单元-节点连接
描述单元包含哪些节点。使用**指针数组格式**存储。

**属性:**
- `cells_node`: `[total_cells_node]` - 所有单元的节点索引（展平的1D数组）
- `cells_node_ptr`: `[total_cells_node]` - 每个节点所属的单元索引（与cells_node维度相同）

**示例:**
```python
cells_node = [0, 1, 2, 3, 4, 5, 6, 7]
cells_node_ptr = [0, 0, 0, 0, 1, 1, 1, 1]
# 表示：
# - Cell 0 包含 nodes [0, 1, 2, 3]
# - Cell 1 包含 nodes [4, 5, 6, 7]
```

**数据格式说明:**
这种格式允许高效地：
1. 查询某个单元包含哪些节点：`cells_node[cells_node_ptr == cell_idx]`
2. 在GPU上进行scatter/gather操作
3. 支持不规则的单元-节点连接（每个单元可以有不同数量的节点）

#### 3. `('cell', 'bounded_by', 'face')` - 单元-面连接
描述单元由哪些面围成。使用**指针数组格式**存储。

**属性:**
- `cells_face`: `[total_cells_face]` - 所有单元的面索引（展平的1D数组）
- `cells_face_ptr`: `[total_cells_face]` - 每个面所属的单元索引（与cells_face维度相同）

**示例:**
```python
cells_face = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
cells_face_ptr = [0, 0, 0, 0, 0, 0, 1, 1, 1, 1]
# 表示：
# - Cell 0 由 faces [0, 1, 2, 3, 4, 5] 围成（六面体）
# - Cell 1 由 faces [6, 7, 8, 9] 围成（四面体）
```

#### 4. `('cpd_cell', 'neighbors', 'cpd_cell')` - 单元邻居关系
描述复合单元之间的邻接关系（1-hop neighbors）。

**属性:**
- `neighbor_cpd_cell`: `[2, num_neighbor_edges]` - 邻居单元的边索引（COO格式）

**说明:**
这是标准的COO (Coordinate) 格式边索引，用于表示图的邻接关系：
```python
neighbor_cpd_cell = [[0, 0, 1, 1, 2],    # source nodes
                     [1, 2, 0, 2, 1]]    # target nodes
# 表示边: 0→1, 0→2, 1→0, 1→2, 2→1
```

#### 5. `('cpd_cell', 'wlsq_neighbors', 'cpd_cell')` - WLSQ扩展连接
描述用于WLSQ计算的扩展邻居关系（k-hop neighbors）。

**属性:**
- `neighbor_cpd_cell_x`: `[2, num_wlsq_edges]` - WLSQ邻居的边索引（COO格式）

**说明:**
包含k-hop邻居（k由fvconfig.toml中的khops参数指定），用于高阶WLSQ梯度重构。

### 全局属性

**HeteroGraphBase级别的属性:**
- `_num_nodes`: `int` - 物理节点总数，用于batching时的offset计算
- `_num_faces`: `int` - 面总数，用于batching时的offset计算
- `_num_cells`: `int` - 单元总数，用于batching时的offset计算
- `_num_cpd_cells`: `int` - 复合单元总数，用于batching时的offset计算

## Batching 行为

### Offset 规则 (`__inc__` 方法)

不同属性在batching时使用不同的offset规则：

```python
offset_rules = {
    # 连接性属性 - 索引值需要offset
    "face_node": _num_nodes,           # 面-节点连接：节点索引需要累加节点数
    "cells_node": _num_nodes,          # 单元-节点连接：节点索引需要累加节点数
    "cells_face": _num_faces,          # 单元-面连接：面索引需要累加面数
    "neighbor_cpd_cell": _num_cpd_cells,  # 单元邻居：单元索引需要累加复合单元数
    "neighbor_cpd_cell_x": _num_cpd_cells,    # WLSQ邻居：单元索引需要累加复合单元数

    # 指针属性 - 指向的单元/面索引需要offset
    "cells_node_ptr": _num_cells,      # 单元指针：指向的单元索引需要累加单元数
    "cells_face_ptr": _num_cells,      # 单元面指针：指向的单元索引需要累加单元数
    "face_node_ptr": _num_faces,       # 面节点指针：指向的面索引需要累加面数

    # 数据属性 - 不需要offset
    "pos": 0,                          # 位置坐标
    "x": 0,                           # 特征向量
    "y": 0,                           # 目标值
    "face_area": 0,                   # 面积
    "cell_volume": 0,                # 体积
    "global_idx": 0,                  # 全局索引（已经是全局的）
    "A_cell_to_cell": 0,              # WLSQ矩阵
    "single_B_cell_to_cell": 0,       # WLSQ矩阵
    "theta_PDE": 0,                   # PDE参数
    # ... 其他数据属性
}
```

**Batching示例:**

假设有两个图：
- Graph 0: 3个单元，每个单元有4个节点
- Graph 1: 2个单元，每个单元有3个节点

```python
# Graph 0
cells_node_0 = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
cells_node_ptr_0 = [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2]

# Graph 1
cells_node_1 = [0, 1, 2, 3, 4, 5]
cells_node_ptr_1 = [0, 0, 0, 1, 1, 1]

# Batching后
cells_node_batch = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11,  # Graph 0
                    12, 13, 14, 15, 16, 17]                # Graph 1 (节点索引+12)
cells_node_ptr_batch = [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2,  # Graph 0
                        3, 3, 3, 4, 4, 4]                    # Graph 1 (单元索引+3)
```

### Concatenation 维度 (`__cat_dim__` 方法)

不同属性在batching时沿不同维度concatenate：

```python
cat_dim_rules = {
    # 沿第0维concatenate（增加样本数）
    "pos": 0,                         # [N, 3] -> [N_total, 3]
    "x": 0,                          # [N, F] -> [N_total, F]
    "y": 0,                          # [N, F] -> [N_total, F]
    "face_area": 0,                  # [N, 1] -> [N_total, 1]
    "cell_volume": 0,               # [N, 1] -> [N_total, 1]
    "cells_node": 0,                 # [M] -> [M_total]
    "cells_face": 0,                 # [M] -> [M_total]
    "neighbor_cpd_cell": 0,          # [M] -> [M_total] (展平的边)
    "neighbor_cpd_cell_x": 0,            # [M] -> [M_total] (展平的边)

    # 沿第1维concatenate（增加边数）
    "face_node": 1,                  # [2, E] -> [2, E_total]

    # 其他属性使用默认规则
}
```

## 兼容性接口

为了保持与原始代码的兼容性，提供了`HeteroFVGraph`包装器，它将HeteroData映射到原始的接口：

```python
fv_graph.graph_node     # -> 访问 hetero_data['node']
fv_graph.graph_face     # -> 访问 hetero_data['face']
fv_graph.graph_cell     # -> 访问 hetero_data['cpd_cell']
fv_graph.graph_cell_x   # -> 访问 WLSQ相关属性
fv_graph.graph_Index    # -> 访问 hetero_data['graph']
```

## 数据流

1. **数据加载**: `HeteroGraphDataset.get()` 从原始数据创建`HeteroGraph`对象
2. **Batching**: PyG的`HeteroBatch.from_data_list()` 使用自定义的`__inc__`和`__cat_dim__`规则
3. **兼容性包装**: `HeteroFVGraph` 提供原始接口访问
4. **模型计算**: 模型使用兼容性接口进行前向传播
5. **数据更新**: 通过`global_idx`更新数据池
