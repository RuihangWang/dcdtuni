# dcCart × Base604 回执(2026-09-30)——"网格生成卡住"定位与修复

**结论:** Base604 在 16 进程下的"卡住"是两件事叠加:①切割阶段长时间无日志(旧 23.5 M 叶子网格 2555 s 无输出);
②dcCart 写出的分区网格在进程间不一致,并行 checkMesh 段错误。②已找到根因并修复,①已加进度日志。分支 `fix/dccart-base604`。

复现环境:40902(dcCart 隔离复验机),Base604 复制到 scratch,修复版在独立 worktree 编译(`deploy.sh --cart-only`),
40902 主 checkout 与其 case 目录未改动。基线代码与 main 在网格路径上完全一致(`75b13b1e`)。

## 现象与证据

| 运行 | 结果 |
|---|---|
| 16 进程(原版,浏览器界面) | dcCart 429 s 跑完,`checkMesh -parallel` 在 rank 9 段错误(`processorPolyPatch::updateMesh`),任务失败 |
| 同一网格的处理器边界配对检查 | 仅两处不一致:`procBoundary8to9` 20151 vs 20150、`procBoundary9to10` 17890 vs 17891 |
| 孤立面定位 | 同一几何面(x=7.63043,架空地板上方 4.2 cm 薄层)在 rank 8 与 rank 10 各有一份"对 rank 9 的处理器面",rank 9 两边都没有 |
| 8 进程(原版) | 成功,3,554,526 单元,Mesh 可加载(9 个面 pyramid 方向错误,与进程数无关) |
| 16 进程 + 调试框 | 确定性复现(面数完全相同);新加的配对检查给出一行报错,不再段错误 |
| **16 进程(修复版)** | **成功**,3,566,729 单元,配对 0 问题,checkMesh 与 8 进程结果相同(同样 9 个 pyramid 面) |

## 根因

`mergeSmallCells` 对合并做全局仲裁(列表各进程一致),但每个进程只执行"两侧单元都持有"的合并,只持有一侧的静默跳过
(`nHalfHeld`)。bandAbsorb(S9e)在地板上方把 rank 9 的薄板链式并入 rank 8 与 rank 10 的宿主;rank 9 的片区仅 0.64 m,
比合并后的单元还窄,于是 rank 8 持有 rank 10 那一组的薄板却没有其宿主(光晕第 5 层,halo=4),不执行合并、仍把薄板当 rank 9 的单元,
写出了 rank 9 并不存在的处理器面;rank 10 对称。

## 修复

- **dcCart**(`dcCartCutCells.C`):三个仲裁执行循环(phantom 重合并、sliver/quality 合并、bandAbsorb)先收集被接受的合并,
  任何进程只持有一侧的即全局否决(每轮一次 listCombine),其余在所有进程统一执行。代价:被否决的合并在所有进程都不做,
  Base604/16 进程多出 12,203 个未合并单元(+0.34%),网格不再与进程数严格无关(原设计目标);以"网格一定有效"优先。
- **dcCart 进度**:切割循环每 30 s 输出"shell k of N",单个壳超过 30 s 另报耗时与覆盖单元数。
  实测:Base604 的切割耗时几乎全在最后一个壳(684/685,317 s,主进程覆盖 29 万单元)——之前"逐壳全局同步导致串行"的推测不是主因。
- **runner**(`processor_patches.py`):并行 checkMesh 前检查处理器边界两两配对(普通 AtoB↔BtoA,processorCyclic master↔slave),
  不一致时报出进程号与面数,不再让 OpenFOAM 段错误。

## 回归

见 `receipt.json` 的 `regression`(Tri_angle / Shinaide / DAML-Testbed / Base2-9,原版 vs 修复版,同一份导出输入)与
`deploy.sh` 冒烟(串行 + 2 进程,单元数一致)。

## 完整求解(09-30 下午,40901,只求解,16 分区)

同一份修复后网格(3,566,729 单元,baseSize 0.34 m)跑满 1000 步 SIMPLE:**判定 CONVERGED(残差 PASS、传感器 PASS)**,
EXIT 0,墙钟约 49.5 min(约 3.0 s/步,`OMP_NUM_THREADS=2`、`nice 5`)。末步初始残差 Ux 3.8e-4 / Uy 7.1e-4 / Uz 4.1e-4 /
p_rgh 2.9e-2(门槛 5e-2)/ T 1.3e-4 / k 1.8e-4 / ε 7.8e-5;三个回风传感器由 22 °C 升至 42.4–43.3 °C 并平台化(末 20 个采样
峰峰值 ≤ 0.07 K)。判定文件原样存为 `full_solve_convergence.json`;scratch 目录(3.8 GB)随后删除。
结论:下方 9 个 face pyramid 等既有质量问题不阻塞求解,维持「E7 之后与切割提速一并处理」。

## 未决

- 9 个 face pyramid 方向错误:与进程数、本修复无关的既有质量问题(8 进程原版即有)。
- 单个大壳(房间/地板)切割耗时长:性能问题,未处理。
- `partition1` 的 seed 碰撞预警、13/55 个种子未落入流体单元:记录在案,未查。
