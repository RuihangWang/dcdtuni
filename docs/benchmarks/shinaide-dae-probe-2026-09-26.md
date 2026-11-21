# Shinaide 新 DAE 单核数值 probe（2026-09-26）

状态：**passed**；总耗时 6.958 s；90 s 总预算。

仅数值 probe：不是 NV 校准、独立物性确认，也不代表 schema/runtime/UI/mock 平台已接入。

CPU 单核 affinity、PyTorch/BLAS 单线程、nice 10、CUDA 禁用；不涉及 NSCC。

输入：`/tmp/dcdtuni-shinaide-v2.liquidrun`，SHA-256 与关键实现 SHA-256 见同名 JSON。

一次侧 mock：每 HX 流量 1 kg/s、入口 293.15 K；cp/rho 取图中介质；一次/二次混合容积各 0.02 m³。所有值明确非 NV 标定。

水力旧解仅用于 pressure/flow 初猜；最终稳态与三个 5 s 瞬态步均由同一连续 F 的 Newton 求根完成。稳态保留盲端初始储能，求解后仍检验原始 F。

本 probe 使用初始热负载；稳态后施加 1.2 倍热负载，替代该派生输入原有事件，不修改原工程或输入文件。

复现脚本：[verify_shinaide_dae_probe.py](verify_shinaide_dae_probe.py)。在 dcdtuni 环境执行：

```sh
python docs/benchmarks/verify_shinaide_dae_probe.py --fixture /tmp/dcdtuni-shinaide-v2.liquidrun
```

已接受状态：

- steady t=0 s：一次回水 [298.17392344634254] K；二次供水 [308.6739234491984] K；质量残差 5.55e-17 kg/s；能量残差 -5.71e-06 W。
- transient t=5 s：一次回水 [298.1739234463455] K；二次供水 [308.6739234492321] K；质量残差 5.55e-17 kg/s；能量残差 1.5e-09 W。
- transient t=10 s：一次回水 [298.1739234463954] K；二次供水 [308.6739234497757] K；质量残差 5.55e-17 kg/s；能量残差 1.13e-10 W。
- transient t=15 s：一次回水 [298.1739234467224] K；二次供水 [308.67392345308514] K；质量残差 5.55e-17 kg/s；能量残差 8.15e-10 W。

阶段耗时：

- schema_parse: 0.0293 s。
- compile_graph: 0.0433 s。
- equation_graph: 0.0101 s。
- hydraulic_warm_start_only: 0.1268 s。
- unified_steady: 0.5072 s。
- backward_euler_step_1: 0.4715 s。
- backward_euler_step_2: 0.4977 s。
- backward_euler_step_3: 0.4721 s。

结果边界：此次 15 s 窗口内一次回水仅变化约 3.8×10⁻¹⁰ K，不能据此声称已验证可观测的一次侧动态响应。同期模型最大固体温度由 338.289 K 升至 343.570 K，新增约 4200 W 几乎全部进入模型储能。这个 probe 验证的是统一方程的稳态、初期储能响应、守恒与数值收敛；更长时段的输运与回水响应仍需单独验证。
