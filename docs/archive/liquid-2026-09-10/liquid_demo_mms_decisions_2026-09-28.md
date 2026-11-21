# 液冷 demo + MMS 产品化 — Stage 0 决策记录(2026-09-28)

依据:liquid-demo-research 调研(4 路只读 survey + 综合计划;计划全文归档于 OneDrive
`Research/DCTwin/LIQUID_DEMO_MMS_PLAN_2026-09-28.md`)。分支
`feat/liquid-supply-temp-mms-demo` 自 `main@75b13b1e`。目标(用户指令):
`.cfdproj → .liquidrun v3 → 编译图 → PyTorch 可微求解` 全链 Shinaide_Liquid 可视化 demo、
产品级 MMS case(瞬时负载/供水温度变化的解析验证,一次侧供水温度为固定边界)、
React 可编辑设置完善;browser-use 在 React+Three.js 验收;OV 后置。

## 调查矛盾裁决(5 条)

1. **MMS 产品链 vs 编译器能力**:编译器 `compileDynamicLiquidGraph` 只能从 CDU+rack 物理
   对象建拓扑(dynamicLiquidGraph.ts:91-135),发不出测试 adapter() 的最小 3 节点环。
   **裁决**:生成式 mock cfdproj 路线——按 `tools/nv_mock_project.ts` 模式新建
   `tools/mms_product_project.ts`,产出 1 CDU + 1 服务器机架工程,parameter_pack 配到 MMS
   目标(UA_cp=100、C_solid=1000、UA_hx=500、volumes 0.002/0.001),经
   `tools/compile_cfdproj_liquidrun_v3.mjs --check` 确定性编译;解析闭式按**编译后 manifest
   参数**参数化实例并 sha 锁定;若编译值无法精确命中 adapter 原值,以编译值重算解析
   (**不调物理去凑数**)。
2. **schedule 的 schema 容量**:无矛盾——`Q_W` 与 `inlet_K`/`flow_kg_s` 均支持
   标量|list[N_t](长度精确=N_t)双形态(liquidrun_dae_schema.py:87-88,166-169;正反测试
   test_liquidrun_dae_schema.py:27,50,69)。events 仅限 rpm_ratio/opening/Q_W →
   **供水温度变化一律走 inlet_K 列表(计划态)或 Stage 1 在线命令,不新增事件类型**。
3. **checkpoint 版本**:升 **v3**(携带在线供水温度接受轨迹);v2 replay 拒携带新字段,
   循 v1→v2 拒 actuator 事件先例(dae_runtime.py:116,137-138)。
4. **口径修正**:旧文档"56 servers × 750 W"与实测不符——实测为电功率 1000/2000 W 交替、
   冷板合计 21 kW、液冷占比 25% 为显式假设。**所有新文档禁用旧口径。**
5. **证据路径口径**:浏览器验收先例 = `docs/benchmarks/react-browser-interaction-cpu-2026-09-27.md`、
   `shinaide-browser-evidence-2026-09-26.json`、`accepted-input-browser-2026-09-26.json`
   (调查所引 `docs/BROWSER_CPU_VALIDATION_2026-09-27.md` 在仓内不存在,系 OneDrive
   LiquidCooling-DC 子树文件)。Stage 5 证据按 `docs/benchmarks/<topic>-<date>/` 目录制。

## Open question 默认值(记录在案,用户可随时否决)

1. **Demo 场景可视性**:09-27 验收自警 ×1.5@60s 负载阶跃下一次回水仅变 ~0.0005 °C(不可分辨)。
   默认走**调参路线**(降一次流量/加大阶跃/提 HX UA,目标回水温升 0.1–1 °C 量级),
   具体数值 Stage 4 落定并写入 case provenance;分轴放大仅作辅助且显式标注。
2. **固定供水温度边界**:**293.15 K(20 °C)**,取典型设施水 15–25 °C 中值,mock 标签;
   Shinaide 与 NV mock 统一口径。
3. **MMS 产品链方案**:按裁决 1 执行(生成式 mock + 编译值参数化解析)。
4. **checkpoint**:v3(裁决 3)。
5. **Shinaide 合成负载**:等差 Q_W 100→650 W 保留,标注 "synthetic demo pattern, not a
   recorded NV load event";对外口径按裁决 4 修正。

## 资源与纪律

- 隔离端口对 **8390/8391**(启用前 lsof 实测,记录于提交时输出);**8360/8361 用户服务全程禁触**。
- **数值边界冻结**:`solver/dae_newton.py`、`solver/compile_support.py`、`solver/equation_graph.py`
  数学零改动;每阶段以 `git diff --stat` 自证。
- RED 先行(后端 `src/backend/tests/test_liquid/`,前端 `src/frontend/react/test`,vitest);
  回执 `docs/benchmarks/<topic>-<date>/`(input/source sha256 + execution block,模板
  `liquid-health-2026-09-28/README.md`);提交带 Co-Authored-By trailer。
- 浏览器验收登录:repo dev account(首次启动 bootstrap 的 admin,`src/server/auth_manager.py:151-163`;
  证据文档只引行号,不复述口令)。
- NV 真值仅可引用资产计数(2 CDU / 34 机柜能量阀 / 68 隔离阀)与规格计数;**CDU 70 彩页曲线
  禁用**(液—空气型与点表液—液三泵 CDU 型号不一致,为记录在案的首要核对项)。
