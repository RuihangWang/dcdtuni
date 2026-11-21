# 新二次曲率液冷 MMS：真实 GUI 四档回执（2026-10-06）

已实际完成四档 React GUI Prepare → Ready(frame0) → Start → Completed，分别 5/9/17/33 帧，共 64 帧；八类数组经公开 checkpoint v3 重放逐位一致。最终时刻温度 EOC 在原 dt=2/1/.5/.25 s、8 s 终点和原 [0.85,1.15] 门内全部通过。AD/FD 的证明范围仅为**固定 MMS forcing、1 s 窗口、56 个固体平均温度对 heat multiplier**；普通无 forcing 的 20 s 控制验证仍是独立证据。

本目录是拟提交的研究证据与复现快照，不改正常 solver/UI 源码、不合源码、不改变旧夹具身份。其 checkpoint 不代表 OV 像素、风冷 strict 或整体 goal 已完成。Root 的五张真实 MMS GUI 原图与原始像素回执已纳入下述证据，操作/像素观察与落盘、身份、数学核验分别说明；不能从私人脚本 PASS 推导更大的显示验收。

## 结果与实际身份

|dt (s)|帧数|真实 run_id|实际 source_project SHA|实际 config hash|
|---:|---:|---|---|---|
|2.0|5|4d889da52d9a4bab8e8db9af3bd85be3|dff583448db648dd7c0763e396fe81076a7eda5a5a66bb664184fb2f956b1ab7|bb29ae1cec121c0d93afe93d0ebd9cf248b0708209eeadb7546c4b4975848cf2|
|1.0|9|41621a7e12e7475683a456d7746cd738|a46aa2c719876874f0670a58d4f343e9ae0d469d49003103865e98ffbe57a611|094a58df8a43a41b3b3da3ed834c9d00c2313a5413aee75974d0e15131036fd5|
|0.5|17|f4d396ea4eda4113a3616899ffec61d0|09e78afbfc56f8e0160cf592de756d69fdf0886594d4b0733724e7909bb115f7|8cba5bac053ea3097d1a4e7ee681479504284f04bfb4d533609224bf58638f51|
|0.25|33|d9532b2059df4eada2aa98fdbb92cdac|b43680e15d9b725962f3fee48427d974a42440f4e3a5a549036630e7ad377d00|a7fb2e871f58cd95bf091673cdc229547e25db0c551b09a282279da3b43a4b88|

各 run 的 input.json 是包装层，须比较其中 liquidrun；它与对应 input.liquidrun、checkpoint 内原输入相同。相对 frozen 编译输出，存在数值 int→float 的严格 schema 规范化及 118 默认字段；相对 frozen **已 admission** 输入，唯一差异是实际 source_project.sha256。不能声称原字节同一或移植旧 config hash。

GUI 保存工程经真实 shared JS projectContentHashSync（UTF-8、递归 key-sort、对 126 条 tracks 做 stripTrackedFields）精确核到上表实际 SHA；原始文件 byte SHA 单独保存。保存工程相对 frozen normalized 工程的 302 处变化包括 floor polygon 路径迁移、server 端口/display 默认补全，以及 air 默认 nOuterCorrectors 1→20/transientResidualControl 补全。这些是实际全文内容变化，液冷 typed 物理与 forcing 不变；完整差异见 [项目差异](evidence/gui/all-four-saved-project-differences.json)。

[ALL_FOUR_REVIEW.json](evidence/gui/ALL_FOUR_REVIEW.json) 给出逐 run 的原输入/accepted hash、八数组比较和 EOC。每个 evidence/gui/run-* 包含真实六 artifact、原件复制 SHA、公开重放结果、完整输入差异和 review。全状态覆盖 1693 unknowns、950 storage；独立温度/出口预测与真实数组最大差约 4e-13 K。final EOC 五族原门全部保留，能量门仍为 max(1e-7,256*eps)*(1+abs(total heat input))。

## 真实 GUI 原图的范围

[evidence/gui-pixels](evidence/gui-pixels) 保留 dt2 Ready 与四档 Completed 的五张原始 JPEG，并逐字复制 [ROOT-actual-mms4-ov-pixel-observations.json](evidence/gui-pixels/ROOT-actual-mms4-ov-pixel-observations.json)。该原回执是混合记录，另含 OV-beforefail 与 header 修复阶段；其中 OV numeric surface pixels accepted=false，不能据此声称 MMS 的 OV 像素验收。回执提及的非 MMS 截图与含 base64 description 的载体 cfdproj 未纳入本包，只收五张 MMS raw JPEG。截图、回执原字节 SHA 及来源 mtime held 记录见 [root-pixel-copy-receipt.json](provenance/root-pixel-copy-receipt.json)。

## 方程、参数与 AD 边界

新 fixture 为 full-ring-positive-curvature-lowcycle-v1，源参考 independent-quadratic-lowcycle-public-laws-20261006。非均匀显式初值/导数保持，storage 轨迹 T*=b+v*t+a*t²、a>0；全节点 mass balance 与 ideal mixing 无人为质量/节点热源。水、固体、HX 双侧 W 源及 momentum Pa 源由独立公共方程生成，未用产品 residual F/Newton 制造 forcing。参数/图布局抽取仍共享产品 compiler；独立性覆盖 forcing、守恒式与 BE 误差递推，不冒称另写了图编译器。

原参数是 liquid-demo-equipment / mock-1-dae-v3 加保存工程显式覆盖，均为 synthetic/mock。完整系数、单位、边界、IDs 和 polynomial/cycle/pressure 数据在 reference-data。终点总输入热约 **280.26 kW = 21 kW 物理 mock 负载 + 259.26 kW 人工 MMS 热源**；闭合的是带 forcing 的数学方程，不能当作未加 forcing 的真实机房热平衡或设备参数验收。没有额外实测/厂商标定结论。

固定 forcing 的 heat AD=3.1635843691760144 K/multiplier，与独立线性守恒敏感度一致；三档 heat centered FD 保留原 relative<=1e-4 OR absolute<=1e-8 门。其他三个 control 仅有限 AD 值，未作新夹具 FD acceptance；首阀 opening=1 不做越界 centered FD/clipping。对固定 artificial target 重新生成 S(theta) 是不同 dependency tape/source identity，未在本包冒充普通控制证据。source-family sum 梯度烟测与 constant sampled-tensor 绑定源码共同支持固定源依赖；未把 sum 烟测写成全 Jacobian 数值验收。

**原正弦 MMS coarse 失败保持失败。** 本包是新公式/新身份；没有换粗网格、降低 EOC 或 init/energy/FD 门、把终点误差换成 whole-trajectory 误差、steady rebase 初值，或重新定性旧失败。旧原包与原因包身份可追溯于 [preservation-and-freeze-receipt.json](evidence/product/preservation-and-freeze-receipt.json)；为避免重复大 baseline 结果，未复制其旧 raw frames/checkpoints。

## 证据与复现

- evidence/gui-pixels：五张真实 MMS GUI 原始 JPEG 与混合原回执，MMS 不声称 OV 验收。
- evidence/gui：四实际 GUI 运行的原始六文件、64 帧八数组重放、review、实际保存 cfdproj、真实 canonical JS。
- evidence/math：独立数学/身份审查原件；evidence/product：原产品报告、compiler/AD/负门及小型回执。
- derivation/original-design：原推导与 forcing 生成代码；reference-data：显式物理/轨迹/周期数据。
- code/product-source：仅供 PRIVATE 复现的冻结快照，不是对正常 src 的补丁。
- scripts：可迁移的独立方程、精确 forcing SHA 再生、canonicalhash、公开 checkpoint/原 EOC核验、固定源 AD/FD、四档 compiler 准备入口。
- provenance：三权威 manifest、逐项 copy SHA/mtime held 收据、装配复现回执；HANDOFF_PROPOSED.md 只是拟追加文本。

先读 [REPRO.md](REPRO.md) 与 [GUI_RUNBOOK.md](GUI_RUNBOOK.md)。原审查/生成脚本的绝对路径保持原件供审计，不能直接在 frozen/正常目录运行；新 scripts 的写出目录须在仓库 PRIVATE 内且在本证据目录之外。未带入真实凭据、浏览器会话或 base64 截屏描述。原权威逐项 SHA 与 source mtime 在复制前后均 held；包内 adapter 初次 namespace 设置错误作为 harness attempt 保留，不计产品 RED。

本装配执行了四源 SHA精确再生、四真实 JS hash、原 EOC/能量核验、装配后 dt2 公开重放八数组逐位比较、固定 forcing heat AD/三档FD/四负门，以及对四份实际 GUI 保存工程的真实 reader→save/reopen→compiler 4/4 复现。后者实际 source SHA/benchmark SHA 全部吻合；回执在 provenance/package-repro。没有重复运行新的服务/浏览器验收。
