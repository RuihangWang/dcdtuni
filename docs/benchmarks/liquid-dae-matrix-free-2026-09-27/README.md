# 液冷 DAE 矩阵自由 Newton/伴随：CPU 数值与性能边界

2026-09-27，40901 指定自有仓库。新增显式 `NewtonOptions(linear_solver='gmres')` 路径，使用 PyTorch JVP/VJP 和 GMRES 线性步，不构造完整 DAE 雅可比；原有 `dense` 仍是默认。新路径保持同一个非因果残差 `F(t,y,ydot,theta)`、稳态根、Backward Euler 与隐式伴随语义，线性残差不达标即抛错，不返回未收敛状态或梯度。它目前是小图数值研究入口，**不是** NV mock 的加速方案，也未接入 `.liquidrun` 产品运行选项。

定向测试先 RED 后实现；在仓库当前源码上，2 状态解析根/参数与历史梯度、20 状态 CP/HX/pipe/pump/valve 的稠密等价与能量守恒、热/泵/阀跨步梯度与中心差分、物理 MMS 一阶时间收敛、float32 设备/精度继承和失败拒绝均通过。CPU 定向复核 `test_dae_matrix_free.py`、`test_dae_newton.py`、`test_dae_physical_mms.py` 为 14 通过；没有运行 GPU 数值验证。

同一输入 [`nv-pek02-mock-v3.liquidrun`](../fixtures/nv-pek02-mock-v3.liquidrun) SHA-256 `460f7e24498adb8c581b8e58034fbacf69a814352515c11f7e7a81511b2659b2`，1604 状态、CPU float64、同一 hydraulic predictor 初值：

- 默认 dense 在 CPU 1 核的稳态/一步瞬态/伴随分别为 **0.816/0.822/0.755 s**；CPU 4 核为 **0.727/0.664/1.035 s**。两者热负载梯度同为 **0.196316676265**，一步能量余额量级 `1e-8 W`，一次侧回水约 `301.28008 K`。这不是现场 PEK02 时序校准结果。
- 无预条件 GMRES 在 CPU 1/4 核的首个稳态线性步均未于 64 次内收敛：线性残差 **2.76077/2.78687**，目标 **1.3673e-6**；CPU 1 核增加到 128 次仅降至 **0.887391**，仍失败。因此没有完整大图轨迹、梯度或可比较的 GMRES 总耗时，更不能宣称加速。

可复算的 [CPU 脚本](benchmark_nv.py)与各次 [JSON 回执](nv_dense_cpu1.json)在本目录；每个回执记录输入和源码 SHA、线程数、阶段耗时与失败位置。下一步应利用图结构构造块预条件或稀疏策略，再在 NV mock 和 Shinaide 上同时过残差、守恒、有限差分梯度及 1/4 核耗时门禁。GPU 测试必须遵守同一时刻只使用一张物理卡的约定；当前 Kit 占用 GPU 7 时不并行启动 PyTorch GPU 求解。
