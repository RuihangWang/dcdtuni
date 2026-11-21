# S3b GPU episode 回执 — NV mock 1604 态 GPU parity(dense,float64)

单卡 episode(排期授权内):GPU3 = RTX 4090,UUID `GPU-9cc41e7b-4c4a-d642-8992-842550a6c4f3`
(pre-flight 1 MiB 空闲核对,`CUDA_VISIBLE_DEVICES` UUID 钉死);窗口 **04:13:07Z–04:13:35Z(28 s)**;
结束 nvidia-smi 确认释放。避开 GPU0/1/2(用户进程)与 GPU7(OV 模板卡)。

## 方式(零产品改动)
`load_dae_input` 本就按 v3 manifest `execution.device` 装载(拓扑 host 编译后张量整体迁移);
本 benchmark 仅在内存中把副本的 `execution.device` 覆写为 `cuda`(夹具文件与 sha256 未动,
覆写记录在 JSON)。同脚本先跑 CPU 参照臂(锚定黄金值)再跑 GPU 臂,直接全状态对比。

## 结果(nv_gpu_parity.json,全部门通过)
| 量 | CPU 臂 | GPU 臂 | 门 |
|---|---|---|---|
| heat_gradient | 0.19631667626512342 | **0.19631667626512342** | vs 黄金值 rel diff **0.0** ✓ |
| steady residual | 2.2788e-12(1 iter) | 2.1799e-12(1 iter) | <1e-8 ✓ |
| BE step residual | 1.2631e-12 | 9.5752e-13 | <1e-8 ✓ |
| 全状态 max abs diff | — | steady/BE 均 **5.82e-11** | <1e-9 ✓ |
| primary_return_K | 301.2800818699916/936 | 301.2800818699887/854 | 1e-11 级一致 |
| energy_balance_W | -1.74e-8 | -1.22e-8 | 同量级 |
| steady/step/adjoint 秒 | 0.69/0.65/0.72 | **3.46/3.32/3.39** | 记录不宣称 |
| 峰值显存 | — | 135 MiB | — |

## 判读边界(不宣称的内容)
- **GPU 在 1604 态上比 CPU 慢 ~5×**——根因即 A3 假设(小图、每迭代主机同步、小核发射风暴;
  dense Jacobian 的 vmap 批构建同样受发射主导)。**GPU 的价值主张在大图(>4000 态稠密上限
  之外)、批量多设计评估与消同步后的稳态计时,全部属 S4 组合门,本回执不作任何加速宣称。**
- 09-26 一次性验收的 1.23e-13 是 **scaled** state 口径,与本回执的原始状态 5.82e-11(含
  ~1.5e5 Pa 压力分量)不可直接对比,两者同判"CPU/GPU 数值一致"。
- dense 路径;**pgmres 的 GPU 执行未测**(host-Givens 形态在 GPU 上的每迭代 transfer 成本
  待 S4 量化);mock 夹具,非现场校准。
