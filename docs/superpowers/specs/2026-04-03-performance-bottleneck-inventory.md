# STL Generator 性能瓶颈清单 — 长期优化计划

**Date**: 2026-04-03
**File**: `STLGeneratorForOpenFoam/web/STL_generate.html`
**Baseline cases**: CMI-Guichou (343 models, ~76K meshes), Alibaba-Hangchou (287 models, ~60K meshes)

---

## 已完成 ✅

| # | 问题 | Commit | 日期 |
|---|------|--------|------|
| ✅ | 12 个 pointermove 监听器全部执行（hover raycast 在 drag 期间不跳过） | `f0aa0e0` | 2026-04-03 |
| ✅ | `updatePatchEdgeHover` 对全部 76K mesh raycast（应只检测选中模型） | `2496df7` | 2026-04-03 |
| ✅ | Snap 计算遍历全部 renderRoots O(N)，无空间索引 | `5e155f4`, `29d8141` | 2026-04-03 |
| ✅ | Snap avgSize 每次调用重新计算 | `5e155f4` | 2026-04-03 |
| ✅ | Seed helper 每次 pointermove 都 destroy+recreate | `402f37c` | 2026-04-03 |
| ✅ | 点击选择 "穿透" 到远处高优先级对象 | `0f0b029` | 2026-04-03 |
| ✅ | 合并代理 mesh 导致 patch 需要双击 | `615b779` | 2026-04-03 |
| ✅ | 合并代理点击后无意义的 re-merge | `a58dd23` | 2026-04-03 |
| ✅ | 合并代理 re-raycast 遍历全部 renderMeshes | `59f25bc` | 2026-04-03 |
| ✅ | `updateHighlights` 循环内调用 `getSelectedRackSlotIndices` 和 `getSelectedModelIds` | `1bef2d3` | 2026-04-03 |
| ✅ | `updateHighlights` 不跳过非选中模型的 mesh | `1bef2d3` | 2026-04-03 |
| ✅ | `selectCategory` 多余调用 `updateUI` | `a799f9d` | 2026-04-03 |
| ✅ | 多选 model ID 查找用 Array.includes 而非 Set | `a799f9d` | 2026-04-03 |
| ✅ | 多选透明度滑块 `previewMultiOpacity` 调用 `doRebuildGeometry` 全场景重建 | `ecb4fb2` | 2026-04-03 |
| ✅ | 所有透明度预览 (model/face/wall) 用 `rebuildSingleModel` 而非直接改 `material.opacity` | `ecb4fb2` | 2026-04-03 |
| ✅ | 同模型内切换 patch/face 时 `setSelection` 重建整棵 model tree DOM | `b4b70c3` | 2026-04-03 |
| ✅ | `setSelection` 后无 `requestRender()` 导致高亮不立即可见 | `071225c` | 2026-04-03 |
| ✅ | `onPointerMove` 缺少 `updateViewportPointerState` 导致 orbit 后误触发选择 | `038f16b` | 2026-04-03 |

---

## CRITICAL — 大型 case 必卡

### C1: `getModelById()` 线性扫描
- **位置**: Line ~2928
- **代码**: `state.models.find(model => model.id === modelId)`
- **影响**: O(N) 遍历 `state.models` 数组。全代码库被调用 **135 次**，包括 hover/click/drag 热路径中每帧多次调用。343 模型 × 多次调用/帧 = 每帧几千次字符串比较
- **修复**: 维护 `_modelMap = new Map(id → model)`，在 model 增删改时同步更新。`getModelById` 改为 `_modelMap.get(modelId)`
- **难度**: 小
- **状态**: ✅ 已完成 (2026-04-03)

### C2: `getRenderRootByModelId()` 线性扫描
- **位置**: Line ~3740
- **代码**: `renderRoots.find(root => root.userData && root.userData.modelId === modelId)`
- **影响**: 同 C1，O(N) 线性查找。在 hover、drag、highlight、rebuildSingleModel 等热路径中频繁调用
- **修复**: 维护 `_renderRootMap = new Map(modelId → root)`，在 `buildGeometryData` / `rebuildSingleModel` 时更新
- **难度**: 小
- **状态**: ✅ 已完成 (2026-04-03)

### C3: Undo 深拷贝 `JSON.parse(JSON.stringify(state))`
- **位置**: Line ~2571 (`captureStateSnapshot`)
- **影响**: 每次 `pushUndoCheckpoint` 对整个 state 做深拷贝。CMI-Guichou state ~10MB × MAX_UNDO=200 = **~2GB 内存峰值**，造成 GC 卡顿
- **修复方案**:
  - 短期: 减小 MAX_UNDO（如 50），对大 case 自动降低
  - 长期: 改为 command/delta 模式，只存储变化的部分
- **难度**: 中（短期）/ 大（长期）
- **状态**: 🔲 待实现

### C4: `rebuildGeometry()` 被 30+ 个函数调用（全量重建）
- **位置**: 47 处调用点（见下方详表）
- **影响**: 每次调用 `clearRender()` 销毁全部几何体 → 重建全部模型 → merge 全部非选中模型。很多场景只改了 1 个模型却重建了 343 个
- **修复**: 逐步将只涉及单个模型的操作改为 `rebuildSingleModel(modelId)`
- **难度**: 中（逐个替换，需确认每处是否安全）
- **状态**: ✅ 8处已替换 (2026-04-03)，剩余23处需全量rebuild

**可替换为 `rebuildSingleModel` 的调用点**:

| 函数 | 位置 | 当前调用 | 可否改为单模型 |
|------|------|----------|---------------|
| `toggleModelEnabled()` | Line ~8998 | `rebuildGeometry()` | ✅ 可以 — 只影响 1 个模型 |
| `createDefaultBoxModel()` 等 6 个创建函数 | Lines ~9636-9713 | `rebuildGeometry()` | ✅ 可以 — 新增模型后只需 rebuild 该模型 |
| `removeModel()` | Line ~9731 | `rebuildGeometry()` | ✅ 可以 — 只需移除该模型的 render root |
| `duplicateModel()` | Line ~9743 | `rebuildGeometry()` | ✅ 可以 — 只需 rebuild 新复制的模型 |
| `togglePreviewModelVisible()` | Line ~8566 | `rebuildGeometry()` | ⚠️ 需验证 — STL preview 可能涉及多模型 |
| `importCustomSTL()` | Line ~10939 | `rebuildGeometry()` | ✅ 可以 — 只导入 1 个模型 |
| Bool diff 操作 | Lines ~8245-8354 | `rebuildGeometry()` | ⚠️ 需验证 — CSG 可能影响多个模型 |

---

## HIGH — 明显卡顿

### H1: `updateUI()` 每次重建整棵 model tree DOM
- **位置**: Line ~8760 (`tree.innerHTML = html`)
- **影响**: 343 个模型生成完整 HTML 字符串再一次性塞进 DOM。被 27 处调用。对于只需更新 inspector 的场景是巨大浪费
- **修复方案**:
  - 短期: 已实现 `_updateInspectorOnly()` 用于同模型内切换（✅ 已完成）
  - 中期: tree 节点增量更新 — 只修改变化的 `.selected` class，不重建 innerHTML
  - 长期: 虚拟 DOM 或 incremental DOM 方案
- **难度**: 中
- **状态**: ✅ 已完成 — `_refreshTreeHighlights()` + `_updateInspectorOnly()` (2026-04-03)

### H2: 4 处 `rebuildGeometry()` 后多余调用 `updateUI()`
- **状态**: ✅ 已完成 (2026-04-03)

### H3: 未缓存的 material 创建
- **状态**: ✅ anchor marker material cached (2026-04-03)。Edge/patch材质因参数可变或已被 `_skipEdgesForPerf` 跳过，影响小

### H4: `isModelEnabled(stringId)` 在循环中触发线性查找
- **状态**: ✅ 随 C1 自动解决 — `getModelById` 现在 O(1) Map 查找 (2026-04-03)

---

## MEDIUM — 可感知的延迟

### M1: 项目加载串行解析 STL
- **位置**: Lines ~2916-2925 (`restoreGeometryCache`)
- **影响**: 逐个 base64 decode + TextDecoder + STL parse。有 34+ custom 模型时可能需要数秒
- **修复**: Web Worker 并行解析多个 STL 文件
- **难度**: 大
- **状态**: 🔲 待实现

### M2: Undo 恢复触发全量 rebuild
- **位置**: Line ~2593 (`restoreStateFromSnapshot` → `rebuildGeometry()`)
- **影响**: 每次 Ctrl+Z/Y 重建全部几何体，大 case 需要 500ms+
- **修复**: 对比 snapshot diff，只重建变化的模型
- **难度**: 大
- **状态**: 🔲 待实现

### M3: 创建/删除/复制模型用全量 rebuild
- **位置**: Lines ~9636-9747
- **影响**: 创建一个新 box 就重建全部 343 个模型的几何体
- **修复**: 创建 → 只添加新模型的 render root；删除 → 只移除该 root；复制 → 只 rebuild 新模型
- **难度**: 中
- **状态**: 🔲 待实现（属于 C4 的子项）

### M4: `handleViewportSelection` 初始 raycast 遍历全部 renderMeshes
- **位置**: Line ~12148
- **影响**: 点击时 `raycaster.intersectObjects(renderMeshes, false)` 遍历所有 mesh。merged 状态下约几百个 proxy mesh，可接受。但 unmerged 状态下可能更多
- **修复**: 可先做 frustum 预筛选，或使用 BVH（three-mesh-bvh）
- **难度**: 大（BVH）/ 中（frustum 筛选）
- **状态**: 🔲 待实现（Phase 2 — 当前 merged proxy 优化已大幅缓解）

### M5: `_mergeModelGroup` 在 `buildGeometryData` 中对每个非选中模型执行
- **位置**: Lines ~6181-6187
- **影响**: 每个模型的所有 mesh 都被 clone geometry → merge → 创建 proxy。N 个模型 × M 个 mesh/模型
- **修复**: 随 C4 一起解决 — 减少调用 `buildGeometryData` 的频率
- **难度**: 中
- **状态**: 🔲 待实现

---

## LOW — 小优化

### L1: `new THREE.Vector3()` 在 snap 循环内分配
- **位置**: 已部分修复（`_snapScratchVec`），但其他热路径中仍有
- **状态**: ✅ 部分完成

### L2: Seed helper material 未缓存
- **位置**: Lines ~5796, ~5920, ~5929, ~5974, ~5998
- **影响**: 每次 helper 重建创建新 material，但 helper 重建频率低（已优化为 reposition）
- **状态**: 🔲 低优先级

### L3: operationLog 无限增长后截断
- **位置**: Line ~2640 — `operationLog.splice(0, operationLog.length - 8000)`
- **影响**: 达到 8000 条后每次 log 都触发 splice
- **状态**: 🔲 低优先级

---

## 实施顺序建议

**Phase 1 — 快速高收益（1-2 小时）:**
1. C1 + C2: Map 查找替换 — 全局最大单点优化
2. H2: 去掉 4 处多余 updateUI
3. H3: material 缓存

**Phase 2 — 中等投入（半天）:**
4. C4/M3: 创建/删除/复制/toggle 改为 rebuildSingleModel
5. H1: tree 增量更新（只更新 selected class）

**Phase 3 — 较大投入（1-2 天）:**
6. C3: Undo 系统减少 MAX_UNDO + 大 case 自适应
7. M1: 项目加载并行 STL 解析
8. M2: Undo 恢复增量 rebuild
