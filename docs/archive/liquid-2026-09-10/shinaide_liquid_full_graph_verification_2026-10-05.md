# Shinaide 完整液冷图：制造解、物理闭式与可微性核验（2026-10-05）

本次输入来自真实 React 浏览器的「准备实验」：`Shinaide_dae_cpu.cfdproj` 迁移为 cfdproj v5，
编译为 `.liquidrun` v4。存档清单是
`docs/benchmarks/fixtures/shinaide-dae-v4-react-20261005.liquidrun`；SHA-256 见回执。
拓扑包含 187 节点、242 边、56 冷板、1 换热器、455 管道水 cell，
共 1128 个 DAE 未知量，其中 513 个热储能状态。设备参数来源是 mock。
以上计数直接读取原始 receipt.graph，由工具从 EquationGraph 和热状态 layout 导出。
验证覆盖该完整拓扑的固定水力工况、连续热方程、时间积分与局部一阶导数。

可复算工具：`tools/shinaide_liquid_verification.py`；原始回执及三档制造输入：
`docs/benchmarks/shinaide-liquid-full-graph-2026-10-05/`。
细步阶跃窗口补验：`tools/shinaide_liquid_step_resolution.py`、
`docs/benchmarks/shinaide-liquid-step-resolution-2026-10-05/`。

## 1. 连续方程与各控制体

水力坐标 `from/to` 只定义流量符号。每条边实际入/出节点由已求得流量的符号决定。
令 `w_e=|m_e|`、`c_p` 为水比热；盲管的实际平流流量为零。
固定阀门活动集与恒定泵转速下，水力子系统不依赖温度，压力/流量保持定常。
质量约束为各节点有向流量之和为零；动量约束包含管道 Darcy 压降、设备损失与泵扬程。
本制造解保持这些水力状态，检查质量余额及完整 Newton 残差。

每个 pipe cell 的方程是：

    C_i dT_i/dt = w_e c_p (T_up,i - T_i) + q_i,
    C_i = rho c_p V_i.

顺流取前一 cell，逆流取后一 cell；入口 cell 使用实际上游节点温度。
455 个水 cell 全部属于 pipe 边，冷板/泵没有额外水储能状态。

冷板采用准稳态 NTU 水侧与集总固体：

    epsilon_j = 1 - exp(-UA_j / (w_j c_p)),
    G_j = w_j c_p epsilon_j,
    T_out,j = (1-epsilon_j) T_up,j + epsilon_j T_s,j,
    C_s,j dT_s,j/dt = Q_j - G_j (T_s,j - T_up,j).

零流时 epsilon 和换热量为零。各有入流节点按流量混合边出口温度。
没有入流的干节点使用数值温度规范值；它不提供外部热源。
本 Shinaide 输入的 CDU 回水端仅规定压力，没有规定温度。

换热器二次侧与一次回水各有一个完全混合容积：

    C_h dH/dt = w_h c_p (T_up,h - H) - UA_h (H - P),
    C_p dP/dt = m_p c_p,p (T_in,p - P) + UA_h (H - P).

一次流量/供水温度为输入，一次回水温度为求解状态。该模型的来源与解释随输入绑定保存。

## 2. 独立组装与基准初态

工具使用清单/编译图中的拓扑和属性数值，在 NumPy 中重新组装节点混合方程：

    N T_node = R x + q,   T_node = S x + g,
    S = solve(N,R),       g = solve(N,q),
    x = [所有水 cell, 所有固体, 二次侧容积, 一次侧容积].

再逐个控制体组装 `dx/dt = A x + b`。这些公式独立于生产残差。
生产残差仅在单向 JVP 审计与制造解连续残差核验中被调用。
初态取生产求解器的稳态根作为共同初始条件，并以独立组装检查
`A x0+b` 和节点混合；三个随机方向的 JVP 比较激励空间平流、混合与交换耦合。
这些初态/节点检查用于防止共享消元中间量造成自洽错误。

## 3. 完整拓扑非均匀制造解

取 `f(t)=0.2 sin^3(pi t / 120)`，对每个水 cell、固体和二次侧容积分配固定不同振幅
`v in [0.5,1.5]`（随机种子 20261005）。令

    T_i*(t)=T_i,0+v_i f(t),   T_s,j*(t)=T_s,j,0+v_s,j f(t),
    H_h*(t)=H_h,0+v_h f(t),   node amplitude a = S v.

不同的相邻 cell 振幅使平流项随时间变化；节点混合与 NTU 交换也持续被激励。
管道源按独立控制体能量方程计算：

    q_i*(t) = C_i v_i f'(t) - w_e c_p (v_up,i-v_i) f(t).

清单中的单位是 W/m，因此再除以该 cell 的弧长。
冷板输入为：

    Q_j*(t)=Q_j,0+C_s,j v_s,j f'(t)+G_j(v_s,j-a_up,j) f(t).

二次侧方程决定一次回水的制造轨迹。定义

    a_p,h = v_h - (w_h c_p/UA_h)(a_up,h-v_h),
    b_p,h = C_h v_h/UA_h,
    P_h*(t)=P_h,0+a_p,h f(t)+b_p,h f'(t),
    delta_exchange_h = w_h c_p(a_up,h-v_h)f(t)-C_h v_h f'(t).

一次供水的制造输入为：

    T_in,p*(t)=T_in,p,0+a_p,h f+b_p,h f'
              +[C_p(a_p,h f'+b_p,h f'')-delta_exchange_h]/(m_p c_p,p).

`f(0)=f'(0)=f''(0)=0`，所以制造轨迹与原稳态初值完全对齐。
制造源从上述手推式生成，随后经严格 v4 schema、图编译和 DAEExperiment 求解。
每个采样点先检查整个连续残差在制造状态/导数处为零，再检查后向欧拉的时间误差。
这些专用验证清单的分布热源用于代码验证；原浏览器导出的物理清单另行验证。
本试验检查离散网络的时间阶。管道连续空间阶另由现有 pipe 空间 MMS 测试验证。

## 4. 原物理 600 s 轨迹的独立闭式

固定流量下热方程是线性的，盲管使 A 可以奇异。使用增广矩阵指数，无需 A 的逆：

    [x(t_i);1] = exp([[A,b_i],[0,0]] dt) [x(t_(i-1));1].

输入语义与生产积分器一致：sample i 的输入作用于 `(t_(i-1),t_i]`。
因此闭式比较验证的是该采样保持输入的积分误差。关键帧的连续角点语义需同时参考时间轴测试。
同一几何/拓扑和设备参数分别运行完整 0–600 s、dt=10/5/2.5 s。
半步重采样保留负载阶跃和原初态，误差比较覆盖全部 513 热态及观测分组。
Newton 收敛、守恒余额与时间步精度是三个不同的指标。

## 5. 可微性与 v4 端点边界

真实导出的 v4 清单另生成 30 s/3 步验证副本，以末时刻全部冷板平均固体温度为目标。
在线全局热倍率与泵转速倍率在初态之后改变，Autograd 经过每步隐式 Newton/伴随。
中心差分步长为 1e-4。JSON/React 编图属于离散建模操作，连续梯度定义在固定编译图的张量参数上。

`test_dae_v4_gradients.py` 还验证定流量泵、温升冷板、HX 二次侧温控和压力边界的隐式一阶导数。
阀门开关活动集与流向切换需要独立分析；当前证据适用于固定局部工况。
定流量泵的在线 rpm 命令现明确拒绝，并通过 `pump_rpm_control_edge_ids` 告知前端可用控制。

## 6. 门限与观察结果

制造解的末两档时间阶要求在 0.85–1.15；连续/非线性 scaled residual 要求小于 1e-8。
全图能量闭合要求 `|balance|/(1+|heat_input|)<1e-9`，同时报告 W 绝对值。
该尺度用于区分约 21–31.5 kW 完整图与 100 W 小夹具的舍入误差，
不用于替代独立轨迹误差。闭式物理结果报告观测误差并检查细化减小误差，
没有以小 Newton 残差宣称某个任意时间步的高精度。

输入 SHA-256：`79717967fa59d4c4ad4095b38a61ede037a5431a158e9dd6dc9c646d0271778e`。独立节点一致性 3.94e-09 K、稳态热右端 3.38e-10 K/s、最大 JVP 相对差 2.32e-16、独立质量余额 5.55e-17 kg/s。

| 制造解 dt/s | 全热态最大误差/K | 最大 solid 误差/K | 连续 scaled defect |
|---|---:|---:|---:|
| 10 | 0.046969 | 0.044714 | 5.15e-11 |
| 5 | 0.023838 | 0.023059 | 5.15e-11 |
| 2.5 | 0.012152 | 0.011754 | 5.15e-11 |

制造解时间阶：0.9785, 0.9720。

| 物理 600 s dt/s | water 最大/K | chip 最大/K | 二次供水最大/K | 一次回水最大/K | 末时全态最大/K |
|---|---:|---:|---:|---:|---:|
| 10 | 0.914226 | 2.985611 | 0.017515 | 0.005859 | 0.053167 |
| 5 | 0.539623 | 2.118137 | 0.009016 | 0.003019 | 0.027002 |
| 2.5 | 0.292891 | 1.231919 | 0.004578 | 0.001534 | 0.013606 |

粗步物理峰值误差阶：0.4952, 0.7819。这些时间步接近冷板热时间尺度；细步阶跃窗口回执继续检查渐近区。

原 dt=10 s 完整运行最大 Newton scaled residual 5.15e-11，绝对能量余额 4.42e-06 W，相对余额 2.11e-10。chip 的近 3 K 阶跃误差要求时间步分辨率判断，CDU 温度观测的误差明显更小。

| 梯度方向 | Autograd | 中心差分 | 相对误差 |
|---|---:|---:|---:|
| heat_multiplier | 16.7057692425 | 16.7057692423 | 1.21e-11 |
| pump_rpm_multiplier | -1.85879876667 | -1.85879880831 | 2.24e-08 |

## 7. 复算

在 dcdtuni Python 环境中执行（GPU 隐藏；所有临时目录/结果在仓库内）：

```sh
mkdir -p .codex-tmp/tmp
CUDA_VISIBLE_DEVICES= TMPDIR="$PWD/.codex-tmp/tmp" PYTHONPATH=src \
  OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  python tools/shinaide_liquid_verification.py \
  --fixture docs/benchmarks/fixtures/shinaide-dae-v4-react-20261005.liquidrun \
  --output-dir .codex-tmp/shinaide-full-verification
CUDA_VISIBLE_DEVICES= TMPDIR="$PWD/.codex-tmp/tmp" PYTHONPATH=src \
  OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  python tools/shinaide_liquid_step_resolution.py \
  --fixture docs/benchmarks/fixtures/shinaide-dae-v4-react-20261005.liquidrun \
  --output-dir .codex-tmp/shinaide-step-resolution
```

CPU 亲和按现场资源分工配置。本次完整图验收起始亲和 16–19，运行中按协调迁移到 24–27；
后续阶跃窗口使用 24–27。运行耗时包括并行任务的现场竞争，不用作求解器性能承诺。

## 8. 细步阶跃窗口收口

冷板有效局部热时间常数范围 4.5562–4.5654 s，直接取自原始阶跃回执的 `cp_time_constant_s`，由 `C_s/G` 计算，其中 `G=|m_dot| c_p [1-exp(-UA/(|m_dot| c_p))]` 与生产 NTU 冷板热平衡一致。真实 v4 输入的全部冷板均为 `C_s=100 J/K, UA=33.333333 W/K`；其 `C_s/UA=3 s` 是大流量下 G 趋近 UA 的简化近似，不能当作当前有限流量的有效时间常数。以上为固定入口温度时的局部冷板松弛尺度，不是完整管道/冷板/HX 耦合图的特征时间。原 0–50 s 输入恒定且状态为同一平衡点，因此从原 t=50 s 初始化 40 s 窗口保留了全部水力与热状态。

| 窗口 dt/s | chip 最大误差/K | water 最大误差/K | 原时间的峰值位置/s |
|---|---:|---:|---:|
| 2.5 | 1.231919 | 0.292891 | 62.5000 |
| 1.25 | 0.672892 | 0.152845 | 63.7500 |
| 0.625 | 0.353152 | 0.078224 | 64.3750 |
| 0.3125 | 0.181664 | 0.039575 | 64.3750 |

峰值误差时间阶：0.8725, 0.9301, 0.9590。较细步进入一阶渐近区；完整 600 s 的 dt10/5/2.5 粗步阶数偏低与阶跃及约 4.56 s 的有效局部冷板热时间尺度分辨率有关，当前独立轨迹/非均匀制造解未发现时间积分实现缺陷。

窗口仅用于阶跃峰值分辨率补验；完整 600 s 轨迹仍以另份原始回执为准。约 0.31 s 步长的 chip 峰值误差仍有 0.18 K，使用者应按所需观测精度选步长。

## 9. v4 稳态查询修复

验收发现温控 HX 在关键帧后查询 equilibrium 时仍带入上一瞬态区间的温变率。专项测试首先复现“equilibrium”里 166778.16 W 的储能；该查询应冻结当前边界值并使其时间导数为零。现在只在独立稳态查询的参数副本里将 `hx_T_set_rate` 置零，瞬态接受轨迹和 checkpoint 均不改变。43 项 v4/experiment/runtime/梯度回归通过，稳态一次侧排热与冷板输入热量相等。

## 10. 真实 v4 CUDA float64 短轨迹复核

追加 CPU eager、GPU eager、GPU routed-compiled 的原输入 0–30 s 对照，
以及热倍率 1.2、泵 RPM 倍率 0.9 的在线扰动一阶梯度对照。
完整图为同一 455 水 cell/1128 未知量，
没有删减拓扑。原输入 Newton 容差下全温度差最高 1.55e-8 K，
预设 1e-8 全态门略未达，原始失败回执保留。
只在验证副本收紧 atol=1e-12、rtol=0 后三臂全过：
CPU/GPU 最大温度差 3.41e-13 K，GPU compiled/eager 最大温度差 5.12e-13 K；
梯度最大相对差 1.32e-15。产品默认未改。

Dynamo/Inductor 证据显示真实后端编译：2 图、
34 内核；
compiled 值调用 29、intentional eager-grad 15、
fallback 0，无 broken latch。
梯度和 Jacobian 使用显式 eager autograd 路由，始终在同一 CUDA 设备。
该证据覆盖短时一阶导数与路由，不覆盖完整 600 s GPU、二阶导数或加速主张。
严格容差改善支持 Newton 停止容差解释，属于受控对照推断。

设备为授权的 GPU0 UUID，CPU 24–27；Kit 共卡运行，验证退出后只剩 Kit。
工具、全部原始状态/原始 stdout、首轮失败与严容差回执、释放快照：
[shinaide-liquid-cuda-2026-10-05](../../benchmarks/shinaide-liquid-cuda-2026-10-05/README.md)。
