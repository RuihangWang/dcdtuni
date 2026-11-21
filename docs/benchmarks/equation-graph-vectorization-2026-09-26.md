# EquationGraph 设备与张量化验证

`EquationGraph(graph, primary, device=None, dtype=None)` 默认继承图的设备和
精度，也可显式选择目标。浮点支持 `float32`、`float64`；索引保持整数，mask
保持布尔。构造使用 `CompiledGraph.to(device, dtype)`，
`PrimaryCircuit.to(device, dtype)` 同步转换一次侧输入，并保留外层梯度。
原图不被就地修改。

残差统一使用目标设备上的批量张量算子。管道热单元没有逐单元 Python 循环；
组件按类型整批计算。质量关联通过两次 `index_add` 组装，避免额外的稠密关联
矩阵乘法。拓扑遍历仅在构造阶段进行，允许一次性读取整批索引到 CPU。

有限性、固定阀门活动集和缺失入口温度检查通过 `validate_parameters`、
`validate_state` 显式执行，并绑定到 `DAEProblem` 的 admission/accepted-root
回调。它们不进入残差或 Jacobian 热路径。有效 admission 采用合并的设备侧
谓词和一次主机判断，异常细节才额外读取。压力边界缺少实际流入温度仍被拒绝，
没有把数值 gauge 偷换为物理热库。

**CPU 实测**：1 条管道／4 个热单元与 32 条管道／128 个热单元，单次残差
都记录到 **599 次 aten 算子调用、4 次 `index_add`**，完整算子调用计数逐项
一致。两种规模均未出现 `aten::_local_scalar_dense`、`aten::item`、
`aten::equal` 或 `aten::is_nonzero`。这证明本次测量的残差路径没有随组件
数量增长的标量主机取数或 Python 算子派发；不等于计算量与规模无关。

18 项定向测试通过，包含实际物理网络的 float64/float32 残差、输入梯度、
Newton 稳态及隐式一次回水梯度。float32 小图明确使用 `atol=rtol=5e-6`，
一次回水误差门槛 `2e-4 K`；float64 原物理测试门槛保持不变。参数转换和
`PrimaryCircuit.to` 的外层梯度也经过验证。

测试在40901执行，affinity限定一个CPU核、nice 10、BLAS/PyTorch单线程、
CUDA不可见。这是此次资源预算，求解器实现没有设置全局线程数或限制为CPU。
**尚未执行GPU数值验证**；设备迁移能力来自统一张量路径与构造契约，不能把
CPU测试表述为CUDA验证通过。

证据：[完整 profiler JSON](equation-graph-vectorization-2026-09-26.json)。
测试：[test_equation_graph_device.py](../../src/backend/tests/test_liquid/test_equation_graph_device.py)。
核心及Newton文件SHA-256见JSON，Mac与40901对应文件哈希已核对一致。
