# G2 门回执 — NV mock 1604 态 'pgmres'(右预条件重启 GMRES,matrix-free 前向)

S2 的真实算例考试。攻击的实测缺口(`../liquid-dae-matrix-free-2026-09-27/`):
无预条件 GMRES 在本夹具首个稳态线性解即停(2.76077 @64it / 0.887391 @128it,目标 1.3673e-6),
dense 因此一直是产品默认。算法参考 Pro6000 `LearnedPreConditionNewtonSolver/src/SparseSolver/gmres.py`
(研读后独立实现,未复制源码):宽窗口 restart×cycles 预算纪律、每 cycle 新鲜真残差、host float64
Hessenberg/Givens(每迭代一次批量 transfer,避免 CUDA 单标量核发射风暴——此为对 S2 计划
"标量全设备张量化"措辞的**记录在案偏离**,依据是参考仓在 92 万未知数上的实测形态)。

## 递进记录(全部入库,失败不删)
| 运行 | 配置 | 结果 |
|---|---|---|
| nv_pgmres_cpu2.json | restart30/cycles8/deg8 | **failed**:首个稳态线性解 2240 matvecs 停在 4.04e-4(tol 1.37e-6)——已比无预条件 2.76 改善 ~6800× |
| nv_pgmres_c16d8_cpu2.json | cycles16 | **failed**:4472 matvecs 停在 1.118e-10(tol 1.0e-10,差 12%)——已深入 Newton 后段 |
| nv_pgmres_c20d8(首跑,cycles20) | 前向 matrix-free + 伴随 pgmres | **failed at adjoint**:前向 steady/step 全收敛(residual 8.13e-12 / 7.11e-11),但转置算子 J^T 停 0.0048@5588 matvecs |
| (cycles25 复跑) | 同上 | **failed at adjoint**:0.00438@6983 matvecs——5 cycles 仅降 9%,**结构性停滞平台,预算不是约束** |
| **nv_pgmres_c20d8_cpu2.json(终版)** | 前向 pgmres + **伴随 dense(M1 混合模式)** | **PASSED** |

## 终版结果(G2 门)
- steady:收敛, residual **8.13e-12**, 2 Newton 迭代, 2 线性解, 9223 matvecs, 124.3 s
- BE step:收敛, residual **7.11e-11**, 287 matvecs, 4.0 s
- adjoint(dense Jacobian,每步一次构建):**0.66 s**, adjoint_matvecs=0
- **heat_gradient = 0.19631667626512406,对黄金值 0.19631667626512342 rel diff 3.25e-15**(float64 舍入级;门 <1e-9)✓
- energy_balance_W = -9.78e-7(dense 基线 1.5e-8;线性求解器容差路径差异,同量级如实记录)
- input_sha256 = 460f7e24…(与 matrix-free/compiled 回执同锚);source_sha256 见 JSON

## 判读边界(不宣称的内容)
- **速度**:pgmres 在 1604 态比 dense 慢(steady 124 s vs dense 0.8 s)——M1 的价值是
  **matrix-free 前向在真实鞍点算例上收敛**这一能力证据(为 >4000 态稠密上限与 GPU 批量
  路径铺路),不是 1604 态提速;产品默认仍 dense,pgmres 永久 opt-in 直至大图证据(计划 D 决策)。
- **伴随是 dense 回退**(M1 混合模式):J^T 的 Krylov 空间在同族多项式下停滞(0.0044 平台,
  cycles 20→25 仅 9%),转置预条件属 M2 块三角/粗校正层;梯度与前向线性路径无关(根唯一),
  heat gradient 门即其证据。代码注释与本回执双记录,非静默。
- 多项式预条件 M1 的谱边界见 `test_dae_preconditioned_gmres.py`(双簇 cond≥1e6 与宽连续谱
  停滞,窄带+孤立尾收敛)——NV 鞍点算例恰在 M1 前向可破域内,伴随不可破。
- mock 夹具数值验证,非现场 PEK02 校准;GPU 执行是 S3/S4 episode。
