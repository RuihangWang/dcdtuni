# Stage 4 回执 — Shinaide demo 打磨(2026-09-28 夜)

**结论:通过。** demo 场景按用户裁决重定参数后,负载阶跃下的一次回水温升
**ΔT_pr = 0.227 K**(目标带 0.1–1 °C,单轴可读,不依赖分轴放大或图面标注);
在线供水温度 +2 K 阶跃 20 s 内 +0.95 K、终值 +1.84 K,展示 Stage 1 在线通道。
能量平衡残差 ≤ 3.4e-6 W。

## 用户裁决(2026-09-28 深夜,原话要义)

1. 负载参数授权实施者代调,**改变温升**使其可见;不采用"分轴放大 + 显式 mock 标注"的展示方案。
2. 供水温度边界 293.15 K(20 °C)**保持为可设置项**的默认值,不定死。
3. 总原则:前端支持任意设置;demo 场景只是默认参数的一组选择。

数据层 provenance(source:"mock" 标注)按仓库纪律保留——它与图面展示是两回事。

## demo 场景(fixture_revision `shinaide-demo-2`)

| 项 | 值 | 依据 |
|---|---|---|
| duration / dt | 600 s / 10 s(N_t=61) | 曲线平滑;BE 一阶,dt 减半与预探测(dt=20,ΔT=0.238 K)一致 |
| 负载阶跃 | ×1.5 @ 60 s(56 冷板事件,150→975 W) | 实测 ΔT_pr=0.227 K 落在裁决带内;ΔT_chip≈14.8 K 视觉显著 |
| 供水温度默认 | 293.15 K 标量(fixed) | 创建期编辑器 + 运行期在线命令全程可改 |
| 在线演示脚本 | `set_inputs(primary_inlet_K=+2 K)` @300 s | 冒烟实测:T_pr 298.19→300.01 K |

## 证据链

- `receipt.json`:sha256(case 源文件 / project content / 编译 manifest)、全部实测数字、门判定、execution block。
- `smoke_load.json` / `smoke_supply.json`:40901 headless 冒烟的逐帧原始输出(61 帧)。
- `probe_dae.py`:探测/冒烟脚本(事件注入与前端 `buildExperiment` 同构;manifest 自带事件时不重复注入)。
- 复算:`PYTHONPATH=src python probe_dae.py <manifest> 1.5 60 [2.0 300]`(40901,conda env `dcdtuni`,torch 2.5.1 CPU,float64,OMP 2 线程)。

## 门判定

1. **重编译过 `load_dae_input`** ✓(probe 先过 schema 门再建 DAESimulation)。
2. **grep 门** ✓:`case/Shinaide_Liquid` 内 Vertiv/CDU70 仅命中 parameterPack 免责声明
   "These are not NVIDIA or Vertiv specifications"(否定性提及,非曲线/寄存器数据)。
3. **provenance 全块** ✓:`meta.provenance`(topology/geometry/thermal/display_only/missing,
   source=mock);等差 Q_W 100→650 W 标 "synthetic demo pattern, not a recorded NV load event";
   IC 340 K/175 kPa(graphgen 硬编码)与 totalFlowRate display-only 均已入块。
4. **温升达标** ✓:0.227 K ∈ [0.1, 1] °C,直接实测,非推算。

## 边界

- 合成 mock 夹具验证,**非现场校准**;不代表 NV 设备或真实机房行为。
- CDU `totalFlowRate=60` 仍为零消费点 display-only 字段(未接线,已在 provenance 标注);
  一次侧流量由 `primary_circuits.flow_kg_s` 决定。
- MMS 解析 overlay 与本 case **不可比**(夹具/时间轴不同),Monitor 诚实门会自动拒绝并说明。
- 本回执不含浏览器验收——E2E 终验归 Stage 5(browser-use,隔离端口 8390/8391)。
