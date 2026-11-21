# Shinaide 真实 React v4：CUDA float64 短轨迹回执，2026-10-05

输入为真实浏览器导出的
[Shinaide v4 清单](../fixtures/shinaide-dae-v4-react-20261005.liquidrun)，
SHA-256 `79717967fa59d4c4ad4095b38a61ede037a5431a158e9dd6dc9c646d0271778e`。
原设备参数为 mock；此处检验实现的一致性，不提供现场校准证据。

完整图统计直接来自清单编译后的 EquationGraph：
187 节点、242 边、455 管道水 cell、
56 冷板、1 换热器、
1128 DAE 未知量、513 热储能态。
工具从 graph/state_layout/differential_mask 导出统计，不硬编码缩减图。

## 验证范围和输入

CPU eager、GPU eager、GPU routed-compiled 三臂均用 float64。
仅测试原 600 s 清单的 0–30 s，dt=10 s，三次接受步及原稳态初始帧。
每臂同时记录原有输入轨迹，以及在原初态后通过 DAEExperiment 在线命令
施加热倍率 1.2、全部曲线泵 RPM 倍率 0.9 的扰动轨迹。
一阶导数的目标是最后时刻全部冷板固体温度均值。
所有状态、theta 和梯度均核查执行设备；全部状态原始数组保存为 NPZ，并附 SHA-256。

第一次使用原清单 Newton `atol=rtol=1e-10`：
最大全温度差 1.55e-8 K，预设全态绝对差 <1e-8 门略未达。
[原始 receipt](receipt.json) 保留 `gates_not_met`，没有改门。
第二次仅在验证副本中将 Newton 收紧为 `atol=1e-12,rtol=0`；
产品默认和输入存档未修改；[严格 receipt](tight-newton/receipt.json) 全部门通过。
严格复核支持首轮差异由 Newton 停止容差引入这一解释，属于受控对照推断，
不是对所有运行的数学误差上界。`original` 标记指原有边界/负载轨迹；
严容差臂的执行配置和 Newton 覆盖明确写在 receipt。

| Newton 配置 | 对照 | 最大全态绝对差（各分量自身单位） | 最大温度差 K | 最大梯度相对差 |
|---|---|---:|---:|---:|
| 原输入 Newton | gpu_eager_vs_cpu_eager | 1.11880922304e-08 | 1.11880922304e-08 | 3.4644619673e-12 |
| 原输入 Newton | gpu_compiled_vs_gpu_eager | 1.54521444529e-08 | 1.54521444529e-08 | 1.95095486001e-12 |
| 严格 Newton | gpu_eager_vs_cpu_eager | 5.82076609135e-11 | 3.41060513165e-13 | 2.12663878403e-16 |
| 严格 Newton | gpu_compiled_vs_gpu_eager | 5.82076609135e-11 | 5.11590769747e-13 | 1.31401564169e-15 |

严格容差对照的最大全态差主导分量是 pressure_free（Pa）；温度差另列，避免混淆单位。

## 真实编译和路由

严格臂实测：2 个 Dynamo 图，
604 个 captured calls，
34 个 Inductor 生成内核，
2 次 FX graph cache hit。
因此编译调用具有后端编译图/内核证据。
路由计数为 29 次 compiled 值调用、
15 次 intentional eager-grad、
0 次 eager-transform、
0 次异常 fallback，broken_latch=False。
Jacobian 与隐式伴随使用路由器明确支持的 eager autograd 路径；
GPU tensor residency 持续成立。此处不声称梯度自身由 compiled backward 执行。

## 设备与释放

只允许 GPU0 UUID `GPU-dd97ad37-4a36-19df-d789-0ee503ba4d93`，
CUDA_VISIBLE_DEVICES 仅该 UUID，CPU affinity 24–27、2 线程。
预检实际空闲约 19 GB；Kit 同卡持续运行。
退出后的 [GPU 内存快照](gpu-release-memory.txt) 和
[GPU 进程快照](gpu-release-processes.txt) 显示只有原 Kit，无验证 Python 进程。
运行期 receipt 中 postflight 仍含该进程 CUDA context；释放快照是退出后另行核查。

## 复算

`tools/shinaide_liquid_cuda_verification.py` 是入口。
所有 TMPDIR/XDG_CACHE_HOME/TORCHINDUCTOR_CACHE_DIR/TRITON_CACHE_DIR
必须是仓库内存在的绝对目录。LIBRARY_PATH 需含
`/usr/local/cuda-12.8/lib64/stubs`，供 Triton 构建期 -lcuda 链接。

在 dcdtuni 根目录、取得同一 GPU 授权后运行：

```bash
mkdir -p .codex-tmp/cuda-verify/{tmp,xdg,inductor,triton}
export PYTHONPATH=src
export CUDA_VISIBLE_DEVICES=GPU-dd97ad37-4a36-19df-d789-0ee503ba4d93
export TMPDIR="$PWD/.codex-tmp/cuda-verify/tmp"
export XDG_CACHE_HOME="$PWD/.codex-tmp/cuda-verify/xdg"
export TORCHINDUCTOR_CACHE_DIR="$PWD/.codex-tmp/cuda-verify/inductor"
export TRITON_CACHE_DIR="$PWD/.codex-tmp/cuda-verify/triton"
export LIBRARY_PATH=/usr/local/cuda-12.8/lib64/stubs
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
taskset -c 24-27 python tools/shinaide_liquid_cuda_verification.py \
  --fixture docs/benchmarks/fixtures/shinaide-dae-v4-react-20261005.liquidrun \
  --output-dir .codex-tmp/cuda-verify/original --steps 3
taskset -c 24-27 python tools/shinaide_liquid_cuda_verification.py \
  --fixture docs/benchmarks/fixtures/shinaide-dae-v4-react-20261005.liquidrun \
  --output-dir .codex-tmp/cuda-verify/tight --steps 3 \
  --newton-atol 1e-12 --newton-rtol 0
```

首轮工具的逐字源码保存在 `original-episode-verification-source.py.gz`，
解压字节的 SHA 与首轮 receipt 一致，作为来源存档；严容差轮逐字源码在
`tight-newton/observed-verification-source.py.gz`，解压 SHA 与其 receipt 一致。
当前入口只清理了严格轮源码末尾空白，AST 完全一致；严格轮入口补了严格路径前检、
显式 Newton 覆盖与 Dynamo/Inductor 计数。新入口默认仍使用原输入 Newton。
原始 stdout 是 [首轮](verification-output.txt)、
[严格轮](tight-newton/verification-output.txt)。
[artifact-integrity.json](artifact-integrity.json) 核查全部 NPZ 的 hash、
形状、float64 和有限性。

此次通过的范围是该完整图的短时一阶梯度 CPU/GPU parity 与安全编译路由。
完整 600 s GPU、二阶导数、浏览器 CUDA 模式、任意拓扑变化、性能提升均未在此验收。
完整 600 s 的 CPU 时间误差、MMS 阶与科学边界见
[完整图推导](../../archive/liquid-2026-09-10/shinaide_liquid_full_graph_verification_2026-10-05.md)。
