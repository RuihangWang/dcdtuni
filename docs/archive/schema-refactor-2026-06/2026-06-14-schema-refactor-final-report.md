# Schema 重构最终总结报告（.cfdproj V4 / .cfdrun V2）

> 日期：2026-06-14　分支：`refactor/cfdproj-v4-cfdrun-v2`（已完成，待合入 main）
> 配套权威字段参考：[.cfdproj V4 Schema](../../reference/cfdproj-v4-schema.md)、[.cfdrun V2 Schema](../../reference/cfdrun-v2-schema.md)
> 设计规格：`docs/superpowers/specs/2026-06-12-cfdproj-v4-cfdrun-v2-design.md`
> 验收记录：`docs/superpowers/specs/baselines/2026-06-12-v4-refactor/p4-acceptance/ACCEPTANCE.md`

---

## 0. 一句话总览

把项目的两套文件格式做彻底重构：编辑器存档 `.cfdproj` 升到 **V4**，运行清单 `.cfdrun` 升到 **V2**。工作拆成 4 个顺序 plan，**全部完成并通过验收**。5 个验证 case 端到端跑通 §9.4 验收门（GPU WebGL 真实路径），registry 全等、网格全部在基线 0.13% 内、求解收敛健康；20 个 tracked 项目已批量转 V4；参考文档已对齐实现。

---

## 1. 两套新 schema 的结构

### 1.1 cfdproj V4 — 编辑态领域模型（9 个顶层键）

```
schemaVersion(=4) | uid | name | meta | geometry | models | constructions | instrumentation | runDefaults
```

核心思想：**领域模型与运行配置分离**，参考 dclib `Room` 的分层组织。

- `meta` / `geometry`：房间元信息与主体几何（高度、平面轮廓、tileSize、温度、bounds）。
- `models`：可复用模型库（curve/geometry/cooling/power/control/material 各类）。
- `constructions`：实例层，按类别分桶（`racks/acus/baffles/floors/walls/partitionWalls/boxes/customMeshes/surfaces/rows/servers/cdus/pipeSegments/pipeJunctions/sensors/liquidFlowNetworks/heatGains` + `raisedFloor/falseCeiling`），每桶是 `uid → object` 字典。
- 每个 model 的规范形状：`geometry.{location, size, quaternion, scale, faces, cutouts}`；物理参数挂 `geometry.faces[*].patches[*].physics`。
- `instrumentation`：probes（测点）+ measurements（实测）。
- `runDefaults`：可编辑的运行默认（`meshConfig` / `simulationConfig` / `liquid`）。

权威结构源：`js/cfdproj_schema.js`（顶层键 + `BUCKETS` + 校验）、`js/cfdproj_migrate.{js,py}`（迁移）、`js/cfdproj_state_v4.js`（内存态机）。

### 1.2 cfdrun V2 — 一次运行的冻结 manifest（14 个顶层键）

```
schemaVersion(=2) | runId | name | sourceProject | inputs | geometryExport
| boundaryBindings | sourceTerms | porousBindings | meshConfig
| simulationConfig | postprocessConfig | artifacts | meshGeometry
```

核心思想：**显式化边界条件**。把旧 runner 内 697 行 `_build_bridge_bc` 的 BC 推导搬到浏览器 JS translator，导出**显式** `boundaryBindings`/`porousBindings`/`sourceTerms`（uid 键字典），后端只物化、不再推导。

- 严格 Pydantic（`cfdrun_schema.py`，所有嵌套模型 `extra="forbid"`），任何未声明字段都校验失败。
- `schemaVersion` 必须严格等于 2，否则 `_load()` 报错并提示 `re-export`（A12 硬切换，无双格式兼容）。
- `meshGeometry`：**过渡块**，承载 v1 几何 blob 供网格管线读取（runner `CFDExport.model_validate(meshGeometry)`），待网格管线迁纯 V2 后删除。求解的 `simulationConfig` 实际取自 `meshGeometry.simulationConfig`。

权威结构源：`cfdrun_schema.py`（Pydantic 真值）、`js/cfdrun_export.js`（`buildCfdrunJsonV2()` 生成器）、`cfdrun_reader.py`（`resolve_run()` 物化）。

### 1.3 文件角色契约

- **`.cfdproj` = 用户显式保存的文档。** 只有点保存才写盘；runner 永不读 `.cfdproj`。
- **`.cfdrun` = 触发运行时由前端从实时 UI 状态生成的冻结快照。** 未保存的修改（例如刚改的 server 功率）也进 `.cfdrun`；每次导出/网格/求解重新生成。

---

## 2. 为什么要做（设计动机）

1. **BC 推导该在前端做。** `.cfdrun` 要快照"实时未保存"的 UI 状态；BC 由 UI 状态推导，理应在浏览器内完成、显式落盘，而不是在后端从几何反推。
2. **消除未声明字段。** 旧 cfdrun schema 用 `extra="allow"`，大量 rider 字段隐式流过，难以审计。V2 全字段声明 + `extra="forbid"`。
3. **几何即 STL。** V4 把几何域模型交给 STL 文件，`.cfdproj` 只描述可编辑领域模型，`.cfdrun` 只记录运行输入 + STL artifact + patch 绑定。
4. **领域模型分层。** V4 参考 dclib，把房间/设备/模板分层，便于引用、合并、diff 和跨版本迁移。

---

## 3. 各 Plan 交付

| Plan | 内容 | 状态 |
|---|---|---|
| **P1** | 测试基建 + 5 例预重构基线 + `js/cfdproj_schema.js` + v3→v4 迁移器（JS+Python 值对等）+ 批量 CLI | ✅ 完成（`4b9b532` + 评审修复 `c84668a`） |
| **P2** | 编辑器内存态全面 V4 化：3 层别名层 → 分阶段 sweep → 加载链切换 → `ui`→localStorage → 别名层删除 + 结构测试 | ✅ 完成（T16 `8d485d9`），编辑器端到端原生 V4 |
| **P3** | cfdrun V2 工具链：`cfdrun_schema.js` + `cfdrun_export.js`（translator）+ `cfdrun_schema.py` + `cfdrun_reader.py` + runner 切换（删 `_build_bridge_bc`） | ✅ 完成，registry 全等 5/5（52/372/157/1613/40）；T7 sign-off |
| **P4** | 5 例 §9.4 验收（GPU WebGL）+ 批量转换 + 参考文档更新 | ✅ 完成（2026-06-14） |

---

## 4. Plan 4 验收结果（§9.4 门，5/5 通过）

| Case | 场景(存/读 V4) | 导出 V2 | 网格 vs 基线 | registry | 求解/传感器 |
|---|---|---|---|---|---|
| Base_leakage | ✅ 16，一致 | 52 绑定，18 STL | 0.009% | ✅ 52 | rc=0，max 0.034 °C |
| Shinaide | ✅ 29，一致 | 372 绑定 | 0.067% | ✅ 372 | rc=0，2/3 <0.5（1 capped WARN） |
| CMI_China | ✅ 37，一致 | 157 绑定 | 0.127% | ✅ 157 | rc=0，7/8 <0.5（1 capped WARN） |
| STDCT | ✅ 44，一致 | 1613 绑定 | 0.036% | ✅ 1613 | rc=0，max 0.015 °C |
| Liquid | ✅ 13，一致 | 40 绑定 | 完全相等 | ✅ 40 | 仅网格基线 + liquidrun 语义 ✅ |

- **场景同一性**：每个 case 的 V3→V4 存盘/重读 scene 指纹结构一致、sv=4，GPU 真实渲染逐张多模态确认。
- **求解深度**：按用户决定 capped（约 200 步）做收敛/合理性抽检；Base（1000 步）与 STDCT（200 步）即满足严格 0.5 °C；Shinaide/CMI 各 1 个传感器在 capped 深度未完全收敛（WARN，符合预期）。
- 验收 harness：`scripts/p4_acceptance/`（已提交，可复现）。

---

## 5. 关键技术决策与发现

- **D1–D7（设计期锁定）**：现在实现格式 / 仅文档化模块化；验收 = 全网格 + 并行求解 5 例；加载即迁移、只存 V4；旧 `.cfdrun` 硬切换；`.cfdrun` 生成器在浏览器内（快照实时状态）；全内存 V4 采用；每个 schema 独立成文件。
- **D8 值对等而非字节对等**：浏览器 JSON 与 Python `json` 在科学计数法上无法字节一致，但 round-trip 等值；解析测试归一化数字表示。
- **V4 旋转统一（Plan 2 架构修复，由 T7 暴露）**：交互/渲染/导出/网格全部统一到 `geometry.quaternion`（曾误读 vestigial 的 `model.transform`，导致旋转模型未旋转 → 网格域错 +38%）。修复后网格回到基线 0.009%。
- **`meshGeometry` 求解配置陷阱**：runner `_load()` 校验/`model_dump` 的对象是 `meshGeometry`，所以求解读的是 `meshGeometry.simulationConfig`，不是根级 `simulationConfig`。capped solve 必须 patch `meshGeometry.simulationConfig.endTime`。
- **Plan 4 harness 暴露并修复 6 个集成问题**：`/api/read-file` 是 GET、浏览器会话需登录、`prepare-case` body 形状、capped 目标块、sensorResults 落在 `log/<ts>/`、基线键名 `_`/`-`。详见 `ACCEPTANCE.md`。

---

## 6. 最终门禁

- **node**：126/126 通过。
- **后端 pytest**：7 failed / 709 passed / 67 skipped —— 与重构前基线逐条一致（7 个均为既有失败：4 个 liquid/visualize + 2 个 parallel_mesh + 1 个 blanking_panel，均与 case 文件无关），**零新增回归**。
  - 批量转换一度打破 `test_floor_room_boundary.py`（读 v3 `.construction`）→ 改读 V4（`load_cfdproj_v4` + `constructions.floors[uid].cooling.*`），8→7 恢复基线。
- **GPU WebGL**：5 例真实渲染确认（RTX 4090 Vulkan）。

---

## 7. 收尾与后续

- **批量转换**：20 个 tracked 顶层 `.cfdproj` 已转 V4（`9f96af9`）；`case/Slab_dataset/*` 按设计保留 v2（其 v1 `.cfdrun` 需要时在编辑器重新导出才能跑 V2 runner）。
- **参考文档**：[.cfdproj V4 Schema](../../reference/cfdproj-v4-schema.md)、[.cfdrun V2 Schema](../../reference/cfdrun-v2-schema.md) 已升级为实现版全字段参考；旧 v3/v1 文档加 superseded 横幅；user-guide 更新。
- **待办**：
  - 合入 `main`（四 plan 已全部落地，由用户决定时机）。
  - 合入后 `/sync-dcdtuni-dctwin` 同步到 dctwin。
  - 延期项：`meshGeometry` 过渡块在网格管线迁纯 V2 后删除；液冷 `inputs.liquid.inheritResults` 耦合路径修复（已知失效，V2 范围外）；RDHX 复活代码处置（无基线含 RDHX）。
