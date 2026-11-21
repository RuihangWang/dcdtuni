# Stage 2b 回执 — MMS 产品 case 解析验收(2026-09-28)

**结论:passed(10/10 门)**。产品链 `.cfdproj → .liquidrun v3 → 编译图 → PyTorch Newton DAE`
在解析可解的 1-CDU+1-rack fixture 上,瞬态响应与**独立手推闭式解**一致:BE 一阶收敛
(load 0.941→0.986,supply 0.878→0.970),误差在推导上界内(load 4.55e-3/1.15e-2 K,
supply 2.04e-2/5.93e-2 K @dt=0.25),稳态初始化等价 1.5e-10,组装审计 1.1e-16,
节点一致性 3.1e-11,Newton ≤2.1e-13,能量余额 ≤2.3e-9 W,流量全程恒定。

- 场景:S1 负载阶跃 100→200 W @t=30s(供水 290 K 固定);S2 供水温度阶跃 290→295 K
  @t=30s(负载 100 W 固定)——正是用户要求的"瞬时负载变化 / 供水温度变化正确响应"验证。
- 推导:`docs/developer/liquid_dae_mms_product_derivation.md`(49 维线性热 ODE、节点消元、
  增广矩阵指数闭式、BE 误差界、门限实测记录与两条方法论教训)。
- 独立性协议:拓扑/参数数值读自编译 manifest(数据),组装公式为手推重实现(numpy),
  生产残差从不生成解析轨迹,仅出现在单向 JVP 组装审计与节点一致性锚。
- fixture:`case/MMS_Product/`(input_sha256=7271f03e…,生成链 f73614bb,--check 可复现)。
- 复算:`PYTHONPATH=src python tools/mms_product_acceptance.py --output-dir docs/benchmarks/liquid-dae-mms-product-20260928`
  (CPU float64 2 线程,~1 min;source_sha256 在 receipt.json)。
- `analytic_curves.json`:S1/S2 在 dt=1 的解析+DAE 曲线(Stage 3 前端 overlay 唯一数据源,
  sha 绑定本回执)。
- 判读边界:mock fixture 的合成参数验证,非现场校准;49 维含 22 个影子/死区态(cp/pump
  cell 与零流盲支),真实反馈核心 27 维。

## Stage 3 补遗 — React overlay 同源回执(2026-09-28 夜,stage3b `fc6e5d0a` + 审查修复 `57da05ee`,已合 main)

- `tools/generate_mms_analytic_ts.py` 从本目录 `analytic_curves.json` 幂等再生成
  `src/frontend/react/src/liquid/mmsAnalytic.ts`:文件头记 `source_sha256=a6145883…`,
  无时间戳,未变更 JSON 的重跑零 diff;`--check` 于合并前复核通过(Stage 3 gate
  "UI 与验收同源"达成)。
- Monitor overlay 诚实门:仅 DAE 运行且帧时间轴与夹具轴逐样本匹配才绘制虚线解析参考
  (尾部对齐:夹具曲线覆盖 seq 1..60,重放帧序列含 seq 0 初始帧;offset∈{0,1},容差
  1e-6),不可比时显式说明原因。对齐逻辑抽为 `liquid/mmsOverlay.ts`,由
  `test/mmsOverlay.test.ts` 逐样本钉住——错位一格、等长错 dt、单样本扰动均判不可比。
- 合并前对抗审查(3 视角 17 agent,14 项原始发现,6 项确认)全部修复:2 项产品缺陷
  (overlay 门缺非 DAE 项,schedule 编辑器对 v2 实验渲染但编译静默丢弃)+ 4 项测试
  空洞/弱断言。前端 2994 项测试、`tsc -b` + vite production build 全绿。
- 判读边界不变:曲线为 mock fixture 闭式解,非现场校准。
