# Shinaide DAE 600 s 功率阶跃（2026-09-26）

状态：**passed**；实际完成 **600 / 600 s**；墙钟 86.063 s，硬预算 90 s。

仅数值验证，非 NV 现场标定，非 schema/UI/mock 平台交付。CPU 单核 affinity、PyTorch/BLAS 单线程、nice 10、CUDA 禁用；未使用 NSCC。

一次侧保持短 probe 的明确 mock 值：1 kg/s、入口 293.15 K、两侧容积各 0.02 m³；cp/rho 来自原图。没有修改容量、流量或 UA 以加快响应。

从原图初始 21 kW 热负载稳态开始，t=0 后施加 1.2 倍功率，dt=5 s。原导出文件的事件由此次显式 theta 阶跃替代，输入文件与原工程只读。

输入：`/tmp/dcdtuni-shinaide-v2.liquidrun`；逐步数据：[CSV](shinaide-dae-load-step-2026-09-26.csv)、[JSON](shinaide-dae-load-step-2026-09-26.json)。

复现：[verify_shinaide_dae_load_step.py](verify_shinaide_dae_load_step.py)。

```sh
python docs/benchmarks/verify_shinaide_dae_load_step.py --fixture /tmp/dcdtuni-shinaide-v2.liquidrun
```

一次回水：298.173923446 → 298.302880040 K，变化 0.128956594 K。

二次供水：308.673923449 → 309.089993605 K；二次回水：318.872173943 → 319.619869746 K。

末时刻输入功率 25200.000000 W，储能率 3660.961431 W，一次侧带走 21539.038569 W。

最大绝对能量残差 5.712e-06 W；最大质量残差 5.551e-17 kg/s。

稳态 primaryReturn 对总热负载 scale 的梯度检查：not_run。

数值温度变化不等于传感器可检测性；该结论仍依赖未校准的阻力、热容与换热参数。
