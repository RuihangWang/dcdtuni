# S4 GPU episode 回执 — NV 1604 GPU+compile 组合门与同步解剖(2026-09-28)

单卡 episode:GPU3 RTX 4090(UUID `GPU-9cc41e7b-…` 钉死,pre-flight 1 MiB 空闲核对,
窗口 **04:28:54Z–04:34:07Z(313 s)**,结束释放确认;避 GPU0/1/2 用户训练与 GPU7 OV 模板卡)。

## 环境要求(重要,后续所有 GPU compile episode 适用)
`export LIBRARY_PATH=/usr/local/cuda-12.8/lib64/stubs:$LIBRARY_PATH` —— 主机只有驱动版
`libcuda.so.1`,triton launcher 的 gcc 编译期 `-lcuda` 链接需要 stubs(收养 harness
`test_dae_compiled_cuda.py` docstring 预言了此坑)。首跑未修时 B 臂编译失败,路由 v3
**显式降级闩锁正确工作**(n_eager_fallback=8、broken_latch=true、数值仍全对)——降级
机制本身获得了 GPU 实战验证;修复后重跑全绿。

## 三臂结果(nv_gpu_s4.json,gates 全过,status=passed)
| 臂 | steady s | step s | adjoint s | heat_gradient | 对黄金值 |
|---|---|---|---|---|---|
| A GPU eager(+profiler) | 28.55* | 5.87 | 5.24 | 0.19631667626512342 | rel 0.0 |
| B GPU compiled | 7.90* | 5.08 | 5.07 | 0.19631667626512342 | rel 0.0 |
| C GPU pgmres(matrix-free 前向 + dense 伴随) | 5.19 | 5.08 | 5.08 | 0.19631667626512342 | rel 0.0 |

\* A 臂 steady 含 profiler 开销,不与 B/C 直接比。B 路由:**8 compiled / 4 eager-grad /
0 fallback / 无闩锁**;B vs A:steady diff 1.7e-13、BE diff 5.8e-11(门 <1e-9);
C vs A steady diff **0.0**(bit-exact)。B 首次编译 4.27 s(inductor FX 缓存命中,单列)。

## A3 同步解剖(profile_steady,A 臂)
CUDA kernel 时间合计 **1.894 s** vs wall **28.55 s** → **launch/同步开销占 93.4%**。
Top ops:35288× `aten::min`、35291× `aten::max`、70576× 头部 op(isfinite/收敛检查类
小 reduction)——GPU 慢的根因是**小核发射风暴与逐迭代主机同步**,不是 fp64 吞吐
(A2:matmul fp32/fp64=41.7×,但 1604 态矩阵运算占比极小)。

## 判读边界(不宣称)
- **1604 态无 compiled 加速宣称**:同 run 内 B step 5.08 vs A step 5.87(-13%),
  steady 由 jacobian/jvp 的 eager functorch 路径主导,compiled 值调用面薄;
  跨 run 计时受主机争用影响(用户 FVGN 训练在 GPU0-2),不可比。
- **消同步路线图(登记 S5 深水区)**:收敛检查张量化/固定预算 Krylov/CUDA-graph 捕获
  评估——涉及契约"外层 Newton 标量收敛检查是唯一显式例外"的重裁,须单独设计与授权,
  本轮未动产品代码。
- pgmres GPU 5.19 s 不宣称优于 dense(S3b GPU dense steady 3.46 s);pgmres 价值在
  >4000 态稠密上限之外与内存,1604 态两路径都可用且梯度逐位一致。
- mock 夹具,非现场校准;OV 线(S7)未在本 episode 范围。
