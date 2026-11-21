# 瞬态关键帧(E7)设计 —— cfdproj v5 / .liquidrun v4 / cfdrun v3(2026-09-30)

需求:`docs/archive/transient-keyframes-2026-09-30/transient_keyframes_requirements_2026-09-30.md`(用户原始要求 + §1b 裁决)。
本设计经用户逐节确认(§1–§4,2026-09-30 SGT),§5 交付顺序由用户授权自行把握。

## 0. 用户裁决汇总

| 议题 | 裁决 |
|---|---|
| 关键帧之间 | 默认**阶跃**;每个关键帧前有可拖的"过渡起点"手柄,拖开即在 `[t−rampS, t]` 线性过渡,拖到前一关键帧即整段线性 |
| 旧「⏱ 瞬态」页 / `model.timeVarying` | **替换并迁移**;左栏「求解」tab 只保留求解器参数 |
| 进度条 | 顶栏下主进度条 + 可展开的「物体·参数」轨道区(类 PR),与模型树/渲染区选中联动 |
| 主条与轨道 | **轨道为真源,主条是所有轨道关键帧时刻的汇总**;拖主条关键帧 = 平移该时刻全部参数关键帧 |
| 架构 | **C:轨道即全部状态**——可时变参数的值(含起点)只存在轨道里,模型对象只留静态参数 |
| 液冷格式 | `.liquidrun` **v4**(用户所说 liquidrun_V2);`cfdproj` v5、`cfdrun` v3 |
| 液冷首版参数 | 现有输入(冷板热负载、泵转速、阀开度、一次侧进水温度/流量)**加** 四种端点边界:二次侧供水温度设定、泵定流量、回水端压力、冷板温升模式 |

## 1. 数据模型(cfdproj v5)

### 1.1 顶层 `transient`

```json
"transient": {
  "mode": "steady | transient",
  "durationS": 600,
  "gridS": 1,
  "start": {"kind": "steadyConverged | uniform", "uniform": {"airT_C": 24, "waterT_C": 30, "solidT_C": 40}},
  "tracks": [
    {"modelUid": "m_12", "param": "server.power", "target": "servers/s3",
     "keyframes": [{"t": 0, "value": 800}, {"t": 120, "value": 1200, "rampS": 30}]}
  ]
}
```

- `gridS`:时间分辨率;关键帧时刻与过渡起点都落在网格上(UI 吸附,校验拒绝离网格值);液冷 DAE 的 `dt` 必须整除 `gridS`。
- 每条轨道由 `(modelUid, param, target)` 唯一确定;`target` 是参数在该模型内的定位(服务器 id、patch id、阀 id、冷板对下标),无则空串。
- **每条轨道必须有 t=0 关键帧**(其 `rampS` 恒为 0);稳态工程 = 所有轨道只有 t=0 关键帧,`mode=steady`。
- 关键帧按 t 严格递增;`0 ≤ rampS ≤ t_k − t_{k−1}`;值受登记表上下限约束。

### 1.2 求值语义(阶跃 + 可选斜坡)

对关键帧 `(t_k, v_k, r_k)`:`t < t_k − r_k` 时取 `v_{k−1}`;`t_k − r_k ≤ t < t_k` 时线性插值 `v_{k−1}→v_k`;`t ≥ t_k` 取 `v_k`
(右连续:恰在关键帧时刻即新值)。`t > 最后关键帧` 保持末值。编译用的**角点表**:每个关键帧贡献
`(t_k−r_k, v_{k−1})` 与 `(t_k, v_k)` 两点(`r_k=0` 时两点同一时刻 = 跳变)。

### 1.3 可时变参数登记表

单一 JSON 数据文件 `src/frontend/shared/transient/transient_params.json`,JS 与 Python 共读。每项:
`key, mode(air|liquid), modelKinds, targetKind, path(模型内路径模板), unit, min, max, default, compile(目标字段)`。首版:

| key | 模式 | 适用模型 | 模型内路径(相对该模型) | 单位 |
|---|---|---|---|---|
| `server.power` | air | rack | `constructions.servers.<id>.cooling.power` | W |
| `box.heat` | air | box(热源) | `cooling.totalPower` | W |
| `acu.supplyTemp` | air | acu | `geometry.faces.*.patches[id=<pid>].physics.temperature` | °C |
| `acu.airflow` | air | acu | `…physics.volumeFlowRate`(0 = 关) | m³/s |
| `acu.supplyAngle` | air | acu(壁挂) | `…physics.supplyAngleDeg` | ° |
| `cp.heat` | liquid | rack | `constructions.servers.<id>.liquidCooling.cpCuPairs.<i>.Q_W` | W |
| `cp.dT` | liquid | rack | `….cpCuPairs.<i>.dT_K`(新,`heatMode=temperature_rise` 时生效) | K |
| `pump.rpm` | liquid | cdu | `constructions.pump.rpmRatio`(新,默认 1) | – |
| `pump.flow` | liquid | cdu | `cooling.totalFlowRate`(`pumpMode=flow` 时生效) | L/min |
| `valve.opening` | liquid | pipe_segment | `cooling.valves[id=<vid>].opening`(`flow_control`/`isolation` 阀) | 0–1 |
| `cdu.primaryInletT` | liquid | cdu | `cooling.primaryInletTemperatureC`(新) | °C |
| `cdu.primaryFlow` | liquid | cdu | `cooling.primaryFlowKgS`(新) | kg/s |
| `cdu.secondarySupplyT` | liquid | cdu | `cooling.supplyTemperatureC`(`hxMode=setpoint` 时生效) | °C |
| `cdu.returnPressure` | liquid | cdu | `cooling.returnPressureKpa`(新,默认 150) | kPa |

静态"形式"字段(不可打关键帧):`cpCuPairs.<i>.heatMode (power|temperature_rise)`、`cdu.cooling.pumpMode (curve|flow)`、
`cdu.cooling.hxMode (physics|setpoint)`。

### 1.4 读路径:投影

`projectAt(project, t)`(纯函数):深拷贝项目,按登记表把每条轨道在 t 的值写回旧字段位置。现有属性面板、Three.js 场景、
稳态编译器全部读投影。**磁盘上只有轨道**;内存中 store 持有的项目是"轨道在播放头时刻的物化投影"——登记表字段只是由轨道
重算出的缓存,每次编辑、撤销/重做、移动播放头之后都从轨道重新物化(不变式:字段 = 轨道(播放头)),保存时剥除。
这样现有读者无需逐个改读法,真源仍只有轨道。

### 1.5 写路径:编辑拦截

`store.edit(fn)` 在(物化于播放头时刻的)项目副本上执行 `fn`,然后:

1. 对比投影前后的登记表字段 → 在播放头时刻 `t` 写/改该轨道关键帧(无则新建,`rampS=0`);
2. 非登记表字段的变化:`t = 0` 时写入模型;`t ≠ 0`(瞬态)时整次编辑拒绝并提示"关键帧时刻不可修改几何/静态参数";
   "静态"只指场景状态——顶层 `geometry` 与 `constructions`(剥除轨道字段后)。工程设置 `meta`(含 casePath)、`runDefaults`(求解器参数)、
   `instrumentation`、`models` 与时间无关,任何播放头时刻都可改、不产生关键帧(2026-09-30 T1 浏览器冒烟发现:改 case 目录在 t≠0 被误拒)。
   **用户裁决 2026-09-30**:确认此范围;另外纯显示字段(模型/服务器 `meta.display`、面 `opacity`/`wallOpacities`、`moveTool`/`rotateTool`)
   列入登记表 `displayOnly` 白名单,不参与冻结、不产生关键帧。冻结对所有写路径一致生效:`edit` 整次拒绝;`notifyLiveEdit`(就地修改的旧路径)
   在 t≠0 时与进入该时刻时的场景快照比对,静态场景有变则整体还原并记录拒绝;拖拽在起点即被禁止(`store.sceneFrozen()`)。
   混合编辑:冷板改 `Q_W` 不再顺带把 `preset` 改成 custom(仅非轨道数值变化才改);ACU 改送风风量时,回风 patch 的风量只在 t = 0 同步,
   t≠0 不另设回风轨道(T4 实测回风是压力出口、流量由质量守恒决定,见 §6 T4 第 6 条)。
3. 按新轨道在播放头时刻重新物化登记表字段(保存时剥除,它们在磁盘上只在轨道里);
4. 结构变更(新增/复制/删除模型、服务器、patch、阀、冷板对)在 `t = 0` 同步新建/复制/删除对应轨道(新建取登记表默认值或模型当前值)。

撤销/重做沿用整项目快照,轨道自然纳入。

### 1.6 迁移 v4 → v5

JS `cfdproj_migrate.js` 新增 `migrateCfdprojV4toV5`,Python 孪生 `cfdproj_migrate.py`,奇偶测试。

- 登记表字段从模型移入 t=0 关键帧;缺省值按登记表 `default`;
- `model.timeVarying` 各点无损转换:线性段 → 关键帧 `rampS=段长`;旧的 0.1 s 伪阶跃(相邻两点相距 ≤0.1 s)→ 真阶跃;
- 液冷实验 `runDefaults.liquid.experiment`:`duration_s → transient.durationS`,`loadStep/stepTime/loadFactor → cp.heat` 各轨道阶跃,
  `supplySchedule`(fixed/step 精确;ramp 精确为斜坡;sinusoid 按 `gridS` 采样成折线并在迁移报告中注明)→ `cdu.primaryInletT`;
  `analysis.mode` 与 `simulationConfig.solverType` 合并为 `transient.mode`(任一为瞬态即瞬态);
- 保存写 v5(`SAVE_KEYS` 加 `transient`);载入拒绝 >5;旧版稳态风冷工程打开结果不变(投影与 v4 字段一致,回归测试)。
- 旧 HTML 页面保持 v4-only:打开 v5 提示改用 React 编辑器;服务器无头载入与 `hybrid_air_heat_handoff` 改读 v5(Python 投影)。

## 2. UI

- **顶栏**:渲染器切换旁加「稳态 / 瞬态」切换(写 `transient.mode`)。稳态不显示进度条。
- **进度条**(瞬态时顶栏下方):左端起点控件(稳态收敛场 / 均匀初始场 + 均匀场温度);主条显示全部轨道关键帧时刻的并集(菱形)
  与可拖播放头;单击菱形选时刻;拖菱形 = 平移该时刻全部参数关键帧;双击精确输入时间;右端时长输入(缩短到已有关键帧之内时确认后裁剪)。
- **展开轨道区**:折叠按钮;显示当前模型树/渲染区选中物体的登记表参数轨道(双向联动,无长下拉);每行显示关键帧与数值折线,
  关键帧可拖时间,关键帧前的过渡起点手柄可拖出斜坡;单击关键帧弹出精确输入(时间 / 值 / 斜坡时长);可增删(t=0 不可删)。
  另一模式(风冷/液冷)的参数隐藏但保留。
- **几何与属性 tab**:显示播放头时刻的值;有关键帧/插值中两种状态标识;改登记表参数 = 在该时刻写关键帧;播放头不在起点时几何与静态字段只读。
- **移除/迁移**:删除「⏱ 瞬态」tab 与 `TransientPanel`/`TimeVaryingEditor`;「求解」tab 仅求解器参数(风冷 deltaT/写出/格式/PIMPLE;
  液冷 dt/设备/Newton);液冷实验面板去掉时长、负载阶跃、供水计划。
- **结果回放**(最后阶段):瞬态运行后,同一播放头可擦洗结果(风冷最近写出时刻、液冷最近采样)。

## 3. 液冷:.liquidrun v4 与 DAE

- `format:"liquidrun"`, `schemaVersion:4`, `version:"4.0"`, `source_project.schema_version:5`;v3 文件继续可读、行为不变。
- 时间:`dt` 来自求解设置,`N_t = durationS/dt + 1`;所有被轨道驱动的输入写成 `list[N_t]`,采样 i 取轨道在 `t_i` 的值
  (阶跃在关键帧时刻起的第一个采样生效);编译器不再产生 events(events 仅为读 v3 保留)。
- `transient` 溯源块:所用轨道 + 起点。`analysis.initialization`:稳态收敛场 → `steady`;均匀初始场 → `consistent`,
  `initial_conditions.T_fluid_K/T_solid_K` 取自起点控件。
- 新增边界形式:
  - **换热器** `mode: physics | setpoint`;setpoint 时残差 `T_sec − T_set(t) = 0` 取代二次侧能量方程,换热量与一次侧回水由能量平衡给出(理想控制);
  - **泵** `mode: curve | flow`;flow 时残差 `ṁ − ṁ_set(t) = 0` 取代扬程曲线,压升由管网阻力决定;
  - **压力边界节点** `P_Pa` 可为序列(`pressure_bc` 与温度边界同样按采样取值);
  - **冷板** `heat_mode: power | temperature_rise`;温升时 `Q = ṁ·cp·ΔT_set(t)`(无流即无热)。
- 阀门维持 v3 规则(不可在开/关之间切换),最小开度由登记表下限保证。
- 运行时:`run_manager`、独立 `runner`、已接受输入清单(`accepted_run_input`)、热审计与 OV 结果票据接受 v4;在线输入维持现状。
- 验证:每种新形式各一条 MMS、能量守恒审计与短时阶跃响应;v3 全部既有测试保持绿色。

## 4. 风冷:cfdrun v3 与 OpenFOAM

- `schemaVersion:3`;`simulationConfig` 只保留一份(消除根与 `meshGeometry` 两份不一致);新增 `transient` 块(起点、时长);
  每个 boundary binding 的时变参数为 `schedules: {param: [[t, v], …]}`(角点表,见 §1.2);删除 `parameters.timeVarying`;
  v2 文件仍按稳态可跑。
- 从轨道直接编译(修掉 ACU/服务器时序从未进入清单、拆分服务器面丢时序等缺陷):
  - ACU 风量:`flowRateInletVelocity` 流量表(0 = 关);回风为压力出口,自然跟随;
  - ACU 送风温度:`uniformFixedValue` 表;
  - 送风角度(壁挂):矢量表 `U(t) = Q(t)/A · dir(angle(t))`,在两轨道角点并集上取值(角点处精确;两者同时斜坡时中间线性近似,文档注明);
  - 风扇服务器:出口温度 = 入口平均 + `P(t)/(ρ·cp·Q(t))`,改用时变流量;功率或流量为 0 时温升为 0;
  - 热流服务器与热源盒:`q(t) = W(t)/A` 表。
- OpenFOAM 表:线性插值 + `outOfBounds clamp`;阶跃写成 `(t_k − ε, v_old) (t_k, v_new)`,`ε ≪ deltaT`。
  实施第一步在 40901 做语法探针(v2412:同时刻两行是否接受;不接受则用 ε)。
- 起点:稳态收敛场 → 先用 t=0 值跑 SIMPLE 至收敛,收敛场拷为初始场(时间归零),再跑 PIMPLE 全时长;均匀初始场 → 按左端温度、U=0 直接 PIMPLE。
- 时间:`endTime = durationS`;deltaT、写出间隔、maxCo 来自「求解」tab;写出的所有时刻可在后处理选择。

## 5. 交付阶段与门槛(用户授权自行把握)

| 阶段 | 内容 | 门槛 |
|---|---|---|
| T1 核心模型 | 登记表、求值器、投影、编辑拦截、结构同步、v4→v5 迁移(JS+Py)、v5 载入/保存、Python 投影 | vitest/pytest 全绿;旧工程投影与 v4 字段逐值一致(全部 `case/*.cfdproj`) |
| T2 界面 | 顶栏切换、进度条、轨道区、属性面板时刻语义、移除旧瞬态页、求解 tab 收敛 | 浏览器门:打关键帧/拖斜坡/精确输入/联动/只读/撤销;保存再打开一致 |
| T3 液冷 | v4 schema + 编译 + DAE 四种新形式 + 运行时接受 v4 | MMS/守恒/阶跃测试;浏览器门:Shinaide 瞬态(二次侧设定阶跃 + 泵流量斜坡 + 回水压力变化)跑通并着色 |
| T4 风冷 | 语法探针;cfdrun v3 + 编译 + 表 + 稳态起点链 | 单测;小算例实跑,写出时刻的 patch 平均值对照轨道;浏览器门 |
| T5 结果回放 + 终验 | 播放头擦洗结果;E2E 回执 | 浏览器 E2E 回执 |

每阶段过门合 main;回执 `docs/benchmarks/transient-keyframes-<topic>-<date>/`。

## 6. 风险与如实边界

- 架构 C 牵动面广:所有读登记表字段的代码必须改读投影。缓解:投影把值写回旧位置,读路径改动集中在"拿到投影"这一处;
  T1 用全部既有 `case/*.cfdproj` 做"投影 = v4 原字段"逐值回归。
- 液冷新边界形式是物理/数值改动:以 MMS 与守恒审计为准,不以"跑得通"为准;理想设定点无容量上限,UI 注明。
- 送风角度与流量同时斜坡时的矢量表为线性近似。
- sinusoid 供水计划迁移为折线(按 `gridS`),迁移报告列出。
- **T4 实施中的取舍(2026-09-30;第 1 条用户已确认,第 2 条待定;6、7 条为浏览器门发现)**:
  1. **版本号(用户 09-30 确认:cfdrun 使用 V3)**:仓库里已有一个叫 cfdrun V3 的「前后端薄契约」提案(7 月,只落地了第一阶段影子推导,代码不检查 `schemaVersion == 3`)。E7 按用户命名占用 3(= v2 + `transient` 块 + 各边界 `parameters.schedules`),那项迁移以后顺延编号;备选是保留 v2 加扩展字段、把 3 留给它。
  2. 「simulationConfig 只保留一份」:去掉 meshGeometry 里的派生副本要改 runner 读配置的方式,正是薄契约迁移的核心、且牵动日常稳态求解;T4 不做,瞬态的时长等字段继续镜像并由现有一致性检查守住。
  3. OpenFOAM v2412 拒绝同一时刻两行,阶跃写成 `(t−ε, 旧)(t, 新)`,ε = max(1 µs, |t|·1e-8);默认表线性且端点钳制;带表的运行以 double(10 位)写出,防止写回时间目录后 t−ε 与 t 相撞。
  4. 风扇服务器出口温度沿用 λ = 0.5 的阻尼反馈(稳态松弛);瞬态下它对功率阶跃有数个时间步的滞后,验收在阶跃后 ≥ 5 步读数。
  5. 稳态起点在同一个运行目录里先跑 SIMPLE(所有表去掉、取 t = 0 值,SIMPLE 里 time() 是迭代数)再把内场写入瞬态 0/,保证分区一致。
  6. **更正 §1 的「回风风量 t≠0 由 T4 按送风轨道推导」**:runner 里空调回风是压力出口(`p_rgh` 固定 0 + `pressureInletOutletVelocity`),
     流量由质量守恒决定,清单里回风的 `flowRateM3s` 只记录、不进边界条件,所以不需要回风调度。浏览器门实测:PCU_1 送风 4→3 m³/s 斜坡后,
     两台空调的回风平分总送风(PCU_1 回风 4.0→3.5),无质量亏空。
  7. 收敛判定(`convergence.json`)仍是稳态规则(残差平台 + 回风传感器先变后平),对瞬态运行不适用,会报 NOT_CONVERGED;瞬态的判定
     (跑到终止时刻 + 每步末残差)归 T5。
     **T5 已落地**:PIMPLE 运行判 `TRANSIENT_COMPLETED`/`TRANSIENT_INCOMPLETE`(到达 endTime、打印 End、无致命错误与非有限残差),其余只作诊断;只评估作业日志最后一个求解器阶段。
- **回水端压力的含义(T3a,2026-09-30)**:动态 CDU 拓扑里回水端是唯一的压力参考点,液体不可压、物性与压力无关,所以回水端压力随时间变化只会让全回路压力整体平移,流量与温度不变(测试 `test_pressure_boundary_series_shifts_every_pressure_and_leaves_the_flows`)。若需要供/回水压差控制,要引入第二个压力边界节点,属于拓扑改动,不在 T3。
- **待裁决(T2.5 发现,2026-09-30)**:一个全局 `transient.mode` 表达不了「风冷稳态 + 液冷瞬态」的混合工程。
  现行规则:液冷在工程为 `transient` 且自身时间推进(v2 恒是;DAE 看 `analysis.mode`)时按关键帧采样;风冷按 `solverType`
  (由顶栏切换镜像)。迁移来的 NV_PEK02 / Shinaide_dae 为 `transient` + `solverType=steady`。是否拆成风/液两个模式,待用户定。
