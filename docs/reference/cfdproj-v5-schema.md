# .cfdproj v5 字段参考（相对 v4 的差异）

> **状态（截至 2026-10-21）：** React 编辑器保存的工程就是 v5。v5 沿用 v4 的全部结构，只在顶层多一个 `transient` 块：所有"可时变参数"（服务器功率、ACU 送风温度与风量、冷板热负载、泵转速、阀开度等）不再写在模型里，而是写成 `transient.tracks` 里的关键帧轨道，稳态工程就是每条轨道只有一个 t = 0 关键帧。v4 的字段见 [.cfdproj V4 字段参考](cfdproj-v4-schema.md)，本页只写 v5 新增和改变的部分。
>
> 权威实现（本页以它们为准）：
>
> - `src/frontend/shared/js/cfdproj_transient.js`：轨道求值、校验（`validateTransient`）、字段与轨道互转（`materializeAt` / `stripTrackedFields` / `commitEdit`）、v4 → v5 迁移（`migrateCfdprojV4toV5WithReport`）。Python 孪生 `src/frontend/scripts/cfdproj_transient.py`，两者由 `src/frontend/test/test_cfdproj_transient_parity.py` 对拍。
> - `src/frontend/shared/transient/transient_params.json`：可时变参数登记表，JS 与 Python 共读。
> - `src/frontend/shared/js/cfdproj_schema.js`：`validateDocument()` 接受 `schemaVersion` 4 或 5，v5 必须带 `transient` 块并逐项校验。
> - `src/frontend/react/src/io/projectFiles.ts`（`projectForSave`）与 `src/frontend/react/src/transient/model.ts`（`liveProject` / `projectAtStart`）：React 的保存与载入。
>
> 设计来源：[瞬态关键帧设计](../developer/transient_keyframes_design_2026-09-30.md)（2026-09-30，E7）；各阶段回执见 `docs/benchmarks/transient-keyframes-t1…t5-2026-09-30/`。

## 1. 与 v4 的关系

| 方面 | v4 | v5 |
|---|---|---|
| `schemaVersion` | `4` | `5` |
| 顶层键 | `schemaVersion, uid, name, meta, geometry, geometryTopology, models, constructions, instrumentation, runDefaults` | 同 v4，末尾加 `transient` |
| 可时变参数 | 写在各模型字段里（例如 `constructions.servers.<id>.cooling.power`） | **磁盘上**从模型中剥离，只存在于 `transient.tracks`；**内存里**编辑器按播放头时刻把它们写回原字段（物化），所以读模型字段的代码不用改 |
| 旧的逐模型时间曲线 `model.timeVarying` | 存在 | 迁移时转成关键帧后删除 |
| 液冷实验里的负载阶跃与供水计划 | `runDefaults.liquid.experiment.settings` 里 | 迁移成冷板热负载、一次侧进水温度的关键帧 |

`cfdproj_schema.js` 里的常量 `SCHEMA_VERSION` 仍然是 `4`：它是 v3 → v4 迁移写入的版本号，v4 → v5 在其后单独执行。

保存顺序（`SAVE_KEYS`）：

```text
CfdProjectV5
  ├── schemaVersion        # 5
  ├── uid
  ├── name
  ├── meta
  ├── geometry
  ├── geometryTopology     # 可选
  ├── models
  ├── constructions        # 可时变字段已剥离
  ├── instrumentation
  ├── runDefaults
  └── transient            # v5 新增
```

## 2. `transient` 块

```json
"transient": {
  "mode": "steady",
  "airMode": "steady",
  "liquidMode": "steady",
  "durationS": 600,
  "gridS": 1,
  "start": {"kind": "uniform", "uniform": {"airT_C": 22, "waterT_C": 30, "solidT_C": 40}},
  "tracks": [
    {"modelUid": "m_12", "param": "server.power", "target": "s3",
     "keyframes": [{"t": 0, "value": 800}, {"t": 120, "value": 1200, "rampS": 30}]}
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `mode` | `steady \| transient` | 工程的总工况。由两个分域工况推出：任一域为瞬态即为 `transient`。 |
| `airMode` | `steady \| transient` | 风冷工况，与 `runDefaults.simulationConfig.solverType` 同步。缺省时按 `solverType` 推出。 |
| `liquidMode` | `steady \| transient` | 液冷工况，与液冷 DAE 的 `analysis.mode` 同步。缺省时：时间轴为瞬态且 DAE 不是稳态，就是 `transient`。 |
| `durationS` | `number > 0` | 时间轴总长，秒。 |
| `gridS` | `number > 0` | 时间分辨率，秒。关键帧时刻和过渡起点都必须落在这个网格上。 |
| `start.kind` | `steadyConverged \| uniform` | 瞬态起点：从稳态收敛场出发，或从均匀初场出发。 |
| `start.uniform` | `{airT_C, waterT_C, solidT_C}` | 均匀初场温度（°C）。 |
| `tracks` | `Track[]` | 全部可时变参数的轨道。 |

新工程或缺这个块的 v5 文件使用 `defaultTransient()`：三个工况都是 `steady`，`durationS = 600`，`gridS = 1`，起点 `uniform`，`airT_C` 取房间温度 `geometry.temperature`（缺省 22），`waterT_C = 30`，`solidT_C = 40`，`tracks = []`。

### 2.1 轨道 `Track`

| 字段 | 类型 | 说明 |
|---|---|---|
| `modelUid` | `string` | 所属模型的 uid（`constructions.<bucket>.<uid>`）。 |
| `param` | `string` | 登记表里的参数键，例如 `server.power`（见 §3）。 |
| `target` | `string` | 参数在模型内的定位，例如服务器 id、patch id、阀 id、冷板对下标；多级定位用 `/` 连接；没有则为空串。 |
| `keyframes` | `Keyframe[]` | 按时间严格递增。 |

`(modelUid, param, target)` 唯一确定一条轨道。

`Keyframe`：`{t, value, rampS?}`。`t` 秒，`value` 用登记表的单位，`rampS` 是过渡时长（秒，缺省 0）。

### 2.2 求值语义

对第 k 个关键帧 `(t_k, v_k, r_k)`：

- `t < t_k − r_k`：取前一个值 `v_{k−1}`；
- `t_k − r_k ≤ t < t_k`：在 `v_{k−1}` 与 `v_k` 之间线性插值；
- `t ≥ t_k`：取 `v_k`（右连续：恰在关键帧时刻就是新值）；
- 最后一个关键帧之后保持末值。

`rampS = 0` 是阶跃。编译器（OpenFOAM 时间表、液冷 DAE 采样）使用 `cornerTable()` 给出的角点表 `[[t, v], …]`：每个关键帧贡献 `(t_k − r_k, v_{k−1})` 与 `(t_k, v_k)` 两行，阶跃即同一时刻两行。

### 2.3 校验规则（`validateTransient`）

- `mode`、`airMode`、`liquidMode` 只能是 `steady` 或 `transient`；三者同时给出时，`mode` 必须等于由两个分域推出的值。
- `durationS > 0`，`gridS > 0`，`start.kind` 为 `steadyConverged` 或 `uniform`。
- 每条轨道的 `param` 必须在登记表里；同一 `(modelUid, param, target)` 不得重复。
- 每条轨道必须有 `t = 0` 且不带过渡的第一个关键帧。
- 每个值都在登记表的 `[min, max]` 之内。
- 关键帧时刻严格递增；`0 ≤ rampS ≤ t_k − t_{k−1}`；`t_k` 与 `t_k − rampS` 都落在 `gridS` 网格上；关键帧不晚于 `durationS`。

`cfdproj_schema.js` 的 `validateDocument()` 对 v5 文件逐条执行这些检查；React 保存前也执行一次，有错就拒绝保存。

## 3. 可时变参数登记表

`src/frontend/shared/transient/transient_params.json`（`format: dcdtuni.transient-params/v1`）。`path` 是相对模型对象的路径模板：`{var}` 绑定对象键或数组下标，`seg[id={var}]` 选数组里 `id` 匹配的元素，`*` 遍历对象键但不绑定；`where` 在最后一个绑定选中的对象上检查。

| 键 | 桶 | 模型内路径 | 单位 | 范围 | 说明 |
|---|---|---|---|---|---|
| `server.power` | `racks` | `constructions.servers.{server}.cooling.power` | W | 0 – 1e6 | 服务器功率 |
| `box.heat` | `boxes` | `cooling.totalPower` | W | 0 – 1e7 | 热源盒体功率 |
| `acu.supplyTemp` | `acus` | `geometry.faces.*.patches[id={patch}].physics.temperature`（`physics.role = supply`） | °C | −20 – 80 | ACU 送风温度 |
| `acu.airflow` | `acus` | 同上 `.physics.volumeFlowRate`（送风 patch） | m³/s | 0 – 100 | ACU 送风量（0 = 关） |
| `acu.supplyAngle` | `acus` | 同上 `.physics.supplyAngleDeg`（送风 patch） | ° | −60 – 60 | 壁挂 ACU 送风角 |
| `cp.heat` | `racks` | `constructions.servers.{server}.liquidCooling.cpCuPairs.{pair}.Q_W` | W | 0 – 1e5 | 冷板热负载 |
| `cp.dT` | `racks` | `constructions.servers.{server}.liquidCooling.cpCuPairs.{pair}.dT_K` | K | 0 – 100 | 冷板温升（温升模式时生效） |
| `pump.rpm` | `cdus` | `constructions.pump.rpmRatio` | – | 0 – 2，缺省 1 | 泵相对转速 |
| `pump.flow` | `cdus` | `cooling.totalFlowRate` | L/min | 0 – 1e5 | 泵定流量（流量模式时生效） |
| `valve.opening` | `pipeSegments` | `cooling.valves[id={valve}].opening`（`type` 为 `flow_control` 或 `isolation`） | – | 0.001 – 1 | 阀开度 |
| `cdu.primaryInletT` | `cdus` | `cooling.primaryInletTemperatureC` | °C | 0 – 80，缺省 20 | 一次侧进水温度 |
| `cdu.primaryFlow` | `cdus` | `cooling.primaryFlowKgS` | kg/s | 0 – 1000，缺省 1 | 一次侧流量 |
| `cdu.secondarySupplyT` | `cdus` | `cooling.supplyTemperatureC` | °C | 0 – 80 | 二次侧供水温度设定 |
| `cdu.returnPressure` | `cdus` | `cooling.returnPressureKpa` | kPa | 1 – 2000，缺省 150 | 回水端压力 |

带缺省值的四项（`pump.rpm`、`cdu.primaryInletT`、`cdu.primaryFlow`、`cdu.returnPressure`）在模型里没有该字段时也会建出一条取缺省值的轨道。

**只影响显示的字段**（登记表 `displayOnly`）不参与时间冻结，也不产生关键帧：`meta.display`、`moveTool`、`rotateTool`，以及面上的 `opacity`、`wallOpacities`。

## 4. 读写路径

- **磁盘上**只有轨道：保存时 `stripTrackedFields()` 把登记表字段从模型里删掉（`projectForSave`）。
- **内存里**编辑器持有"物化在播放头时刻"的工程：`liveProject()` 先把旧版本迁移到 v5，再 `materializeAt(doc, t)` 把每条轨道在 t 的值写回原字段。属性面板、Three.js 场景和编译器都读这些字段。
- **编辑**：`commitEdit()` 比较编辑前后的登记表字段，把变化写成播放头时刻的关键帧。`t = 0` 时允许结构变化（增删服务器、patch、阀、冷板对），轨道随之增删；`t ≠ 0` 时场景几何与静态参数冻结，结构变化或静态字段变化会让整次编辑被拒绝。`meta`、`runDefaults`、`instrumentation`、`models` 与时间无关，任何时刻都可改。
- **编译**：稳态编译读 `projectAtStart()`，即物化在 t = 0 的副本。风冷瞬态把轨道采样成 `.cfdrun` 边界绑定上的 `schedules`，并带上 `transient` 时间轴块（见 [.cfdrun 字段参考](cfdrun-v2-schema.md) 的 v3/v4 一节）；液冷瞬态编译成 `.liquidrun` v4。

## 5. v4 → v5 迁移规则（`migrateCfdprojV4toV5WithReport`）

迁移在载入时进行，不写盘；返回迁移后的文档和一份人可读的报告。`schemaVersion ≥ 5` 的文档原样返回；`< 4` 的先走 v3 → v4 迁移（`migrateCfdprojToV4`）。

1. **建轨道**：对每个模型，按登记表找出所有已有值（或带缺省值）的字段，各建一条只有 t = 0 关键帧的轨道。
2. **一次侧回路**：液冷 DAE 设置里 `primary_circuits[*]` 的标量 `inlet_K`、`flow_kg_s` 写成对应 CDU 的 `cdu.primaryInletT`（K 换成 °C）与 `cdu.primaryFlow` 的起点值。
3. **旧时间曲线 `model.timeVarying`**（各点之间原本线性插值，旧编辑器用相隔 0.1 s 的两点假装阶跃），无损换算为关键帧：值不变的保持点丢弃；相距 ≤ 0.1 s 的一对点变成前一时刻的阶跃；其余变化变成在后一点、过渡时长等于间隔的关键帧。适用对象：
   - ACU 的 `airflow` / `supplyTemp` → 每个送风 patch 的 `acu.airflow` / `acu.supplyTemp`；
   - 盒体的 `heatOutput` → `box.heat`；
   - 机柜的 `heatOutput` → 按各服务器 t = 0 功率的比例分到每台服务器的 `server.power`；
   - 机柜的 `power:<服务器 id>` → 该服务器的 `server.power`。

   稳态工程里，若曲线起点与字段值不同，t = 0 保留字段值（稳态边界就是字段），并写进报告；瞬态工程则以曲线起点为准。没有可对应参数的曲线丢弃并写进报告。处理完删除 `timeVarying`。
4. **液冷实验设置**：`settings.loadStep`（`stepTime`、`loadFactor`）变成所有 `cp.heat` 轨道在 `stepTime` 的阶跃关键帧（值乘以倍率），`loadStep` 置为 `false`；`settings.supplySchedule` 的 `step` / `ramp` 精确换成 `cdu.primaryInletT` 关键帧，`sinusoid` 按实验 `dt_s` 采样成折线，然后删除 `supplySchedule`。只要有这类时间计划，工程就转为瞬态。
5. **时间轴**：`durationS` 取实验 `duration_s`，否则取 `simulationConfig.endTimeSeconds`，否则 600；比最后一个关键帧短就延长到它。`gridS` 取 `[1, 0.5, 0.1, 0.05, 0.01, 0.001]` 中能容纳全部时刻的最粗一档；液冷为瞬态且实验 `dt_s` 能容纳全部时刻与时长时，`gridS` 取 `dt_s`。`start.kind` 在 DAE `analysis.initialization = steady` 时为 `steadyConverged`，否则为 `uniform`。`airMode` 由 `solverType` 决定，`liquidMode` 由 DAE 分析模式与上面的时间计划决定。
6. **收尾**：剥离已成为轨道的字段，写 `schemaVersion = 5`。

迁移对已有稳态风冷工程不改变求解输入：物化在 t = 0 的字段与 v4 原值一致（Base2-9 以 v5 重存后，319 条 `server.power` 轨道合计与旧版逐台相同，见 [十例发布回执](../benchmarks/air-release-cases-2026-10-21/README.md) 的下一步一节）。

## 6. 版本兼容

| 读取方 | 行为 |
|---|---|
| React 编辑器 | 读 v1–v5（先检查格式标记，再迁移到 v5）；`schemaVersion > 5` 拒绝；保存总是写 v5。 |
| 旧 HTML 编辑器（`--app html`） | 只读写 v4；打开 v5 会提示改用 React 编辑器并拒绝载入，避免丢掉轨道里的边界条件。 |
| 无头编译器（`src/frontend/react/src/headless/`，脚本化 API 使用） | 复用 React 的同一套迁移与物化，行为与编辑器一致。 |
| 后端风液热量交接（`hybrid_air_heat_handoff.py`） | 接受 4 与 5；只读取不随时间变化的结构（服务器类型、`runDefaults`），按保存的文档计算内容哈希。 |
| Python 工具与测试 | 用孪生模块 `src/frontend/scripts/cfdproj_transient.py` 迁移与物化。 |

## 修订记录

| 日期 | 修订 |
|---|---|
| 2026-10-21 | 初稿。按 `cfdproj_transient.js`、`transient_params.json`、`cfdproj_schema.js` 与 React 保存/载入代码整理 v5 相对 v4 的差异、字段表、校验规则与迁移规则。 |
