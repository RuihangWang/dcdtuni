# Shinaide CPU 梯度验证（2026-09-26）

状态：**passed**；本次耗时 5.676 s；另有 3.552 s 输入检查，累计预算 90 s（本次设限 80 s）。

范围：对前端真实导出的 Shinaide 图及当前 mock 参数进行软件一致性与方向导数验证；不代表现场精度或已校准。

CPU affinity=[0]，nice=10，PyTorch/BLAS 限制单线程，CUDA_VISIBLE_DEVICES 为空。

输入为 /tmp/dcdtuni-shinaide-v2.liquidrun；输入与关键源文件 SHA-256、Git HEAD 见同名 JSON。

网络：187 节点、242 边、56 冷板、423 管道热单元、14 机柜。dt=1.0 s，11 个样本覆盖 0–10 s；manual，保留原始 56 个负载事件（t=[5] s）。

初值：{"T_fluid_K": 308.15, "T_solid_K": 340.0, "P_uniform_Pa": 175000.0}。

方向：全部冷板热负载乘以可求导标量 alpha（基线 1）；目标为 t=10 s 的平均冷板固体温度。中心差分 alpha±1e-4；水力保持原输入，验证的是热负载梯度。

目标=332.047605476 K；autograd=19.5079566656 K/alpha；中心差分=19.5079566652 K/alpha；绝对误差=4.46e-10，相对误差=2.29e-11；通过=True。

Tensor rollout 与同输入 JSON runtime 在所有 11 个样本比较温度、压力、流量和边出口温度（atol=1e-6，rtol=1e-8）：

- pressure: max |差值|=3.351e-06，通过=True。
- flow: max |差值|=2.941e-10，通过=True。
- node_temperature: max |差值|=8.94e-09，通过=True。
- solid_temperature: max |差值|=1.937e-09，通过=True。
- edge_outlet: max |差值|=8.94e-09，通过=True。

单次阶段耗时：

- schema_parse: 0.0428 s。
- compile_graph: 0.0541 s。
- tensor_forward: 0.7660 s。
- autograd_backward: 0.3065 s。
- finite_difference_plus: 0.4064 s。
- finite_difference_minus: 0.3907 s。
- json_runtime_forward: 0.5745 s。

边界：固定拓扑、当前流向与未饱和的负载方向；不检验跨流向/阀门关闭切换梯度，不检验泵速梯度或 agent 参数后训练。初始高温产生的过渡响应不等于已验证负载响应。

## 泵速方向补充验证

状态：**passed**；耗时 21.669 s；单核、单线程、nice 10、CUDA 禁用。

可复现脚本：[verify_shinaide_cpu_gradient.py](verify_shinaide_cpu_gradient.py)。输入：`/tmp/dcdtuni-shinaide-v2.liquidrun`（SHA-256 与原热负载证据一致）。

在仓库的 dcdtuni Python 环境执行：

```sh
python docs/benchmarks/verify_shinaide_cpu_gradient.py --fixture /tmp/dcdtuni-shinaide-v2.liquidrun --direction pump
```

保持原有 10 s / 11 个样本、初值和负载事件不变；全部泵速轨迹乘 beta，基线 1，中心差分 beta±1e-4；目标仍是末时刻平均冷板固体温度。

目标 332.047605476 K；autograd -1.45645154475，中心差分 -1.45645042608；绝对误差 1.12e-06，相对误差 7.68e-07。

- schema_parse: 0.0454 s。
- compile_graph: 0.0617 s。
- tensor_forward: 0.8562 s。
- autograd_backward: 1.0341 s。
- finite_difference_plus: 0.5655 s。
- finite_difference_minus: 0.5205 s。

范围仅为该工况的平滑方向一阶导数；不代表现场精度、跨流向/阀门关闭切换导数或 agent 后训练已经完成。
