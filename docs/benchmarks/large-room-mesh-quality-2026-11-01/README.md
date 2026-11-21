# 大机房网格质量与分区一致性回执（2026-11-01）

## 结论

- CMI-Guichou：16 进程的分区不一致已修好，8 进程和 16 进程的回执都由 rejected 变为 accepted / 0，两种进程数给出同一网格（各 866,311 个单元）。
- Alibaba-sige、Alibaba-hongchou：坏单元明显减少，回执仍为 rejected，下一轮继续。
- 其余 14 个算例回归全部 accepted / 0，单元数与修复前基本一致。

## 修了什么（`dcCartMesh.C`、`dcCartCutCells.C`）

1. **分区一致（`gatherBaffleCells`）**。合并之后，baffle 面两侧的单元要归到同一 rank。原做法由每个 rank 各自改 `cellRank`，一串 baffle 面能把单元交给并不完整持有它的 rank：原 rank 把它当作 ghost 丢掉，新 rank 又没有它，处理器边界就只剩一侧（Guichou 16 进程 rank 8/9：17338 对 17362）。现在由拥有者提出请求，新 rank 只在自己的副本面数一致时接收。修复前的代码同样复现了这个问题（见 10-31 回执），并非新引入。
2. **收尾合并（`mergeSmallCells` 末尾）**。仍不满足 checkMesh 限值的单元，可以和邻居合并，条件是合并后的单元按 checkMesh 的口径逐面满足面金字塔、偏斜度、插值权重、体积比和行列式。不要求凸，因为 checkMesh 对凹单元只警告。
3. **收尾清除（`killExtremeSlivers(closing)`）**。仍不满足限值、又无法合并的单元交给固体，沿用原清除的保护条件（只限纯墙、不交出偏斜面）。wallPairZones 两侧都是墙的 baffle 面视作墙；成串的缝隙单元作为集合一起判断。
4. **大单元分级（`gradeBesideSmallCells`）**。小单元旁的大单元若接近长方体，就沿共享面平行切开，让两侧尺寸相近；没有沿某方向内部面的盒形单元，也沿该方向切开。

## 大算例（8 进程，只出网格）

| 算例 | 修复前 | 修复后 |
|---|---|---|
| CMI-Guichou | rejected：小行列式 20，小体积比 98；16 进程分区不一致 | accepted / 0（8 与 16 进程） |
| Alibaba-hongchou | rejected：小行列式 29，小插值权重 1353，小体积比 110 | rejected：小行列式 11，小体积比 8 |
| Alibaba-sige | rejected：小行列式 7，face tets 1，小插值权重 107，小体积比 78 | rejected：face tets 4，小插值权重 1，小体积比 7 |

## 回归（只出网格，工程副本在 `_tmp/reg3/`）

| 算例 | 进程 | 耗时 | 回执 | 最小行列式 | 最大非正交度 |
|---|---|---|---|---|---|
| DAML-Testbed | 8 | 74 s | accepted 0 | 0.0177197 | 75.0737 |
| Base_coldAisleIso | 8 | 33 s | accepted 0 | 0.101716 | 75.588 |
| SingleRack | 8 | 18 s | accepted 0 | 0.125 | 69.6535 |
| Base_leakage | 8 | 28 s | accepted 0 | 0.0443044 | 73.1821 |
| Tri_angle | 8 | 18 s | accepted 0 | 0.00222464 | 75.566 |
| Polygon_Room | 8 | 24 s | accepted 0 | 0.0894824 | 71.9296 |
| Slab | 8 | 33 s | accepted 0 | 0.0152283 | 72.3297 |
| STDCT | 8 | 65 s | accepted 0 | 0.0312395 | 78.4504 |
| CMI-China | 8 | 38 s | accepted 0 | 0.0392222 | 76.8227 |
| Shinaide | 8 | 37 s | accepted 0 | 0.0225428 | 74.8568 |
| Shinaide_Liquid | 8 | 46 s | accepted 0 | 0.0131311 | 74.693 |
| GTC2026 | 8 | 162 s | accepted 0 | 0.0609717 | 71.0457 |
| DCTS | 8 | 186 s | accepted 0 | 0.00378245 | 59.9993 |
| Base2-9 | 16 | 204 s | accepted 0 | 0.0345039 | 73.5359 |
| Base604 | 16 | 237 s | accepted 0 | 0.0317706 | 76.2246 |
| CMI-Guichou | 8 | 537 s | accepted 0 | 0.00433844 | 77.3048 |
| CMI-Guichou | 16 | 437 s | accepted 0 | 0.00433844 | 77.3048 |

## 尚未解决

sige、hongchou 剩下的坏单元都贴着墙 baffle，是 1–3 mm 的缝隙单元：合并会使面金字塔翻转，清除又会交出偏斜面。下一步从切割阶段入手，避免产生这些单元。

## 求解收敛（2026-11-01 晚，当前代码 `12b8c567f`）

`case/` 里的三个工程原样运行，只指定进程数：8 进程出网格，24 进程求解，均匀初场。由 `python -m backend.api air` 依次跑完。

| 算例 | 网格回执 | 判定 | 步数 | 空调热量 / 声明热负荷 | 能量判据 | 网格 + 求解 + 其余 |
|---|---|---|---|---|---|---|
| Alibaba-sige | rejected / 3 | CONVERGED | 298 | 993.85 / 994.92 kW | 闭合（−0.11 %） | 8.7 + 30.7 + 1.4 min = 40.8 min |
| Alibaba-hongchou | rejected / 2 | CONVERGED | 276 | 1136.60 / 1136.28 kW | 平稳（有定温热表面，声明热负荷不完整） | 8.1 + 28.3 + 0.9 min = 37.5 min |
| CMI-Guichou | accepted / 0 | CONVERGED | 296 | 1518.76 / 1519.65 kW | 平稳（窗口漂移 0.68 %） | 8.0 + 32.3 + 0.9 min = 41.5 min |

三个算例都收敛。sige 与 Guichou 比 40 分钟多 1 到 2 分钟，耗时主要在求解，每步约 6 秒。sige、hongchou 的网格回执仍为 rejected，会在求解页显示提示，但不影响求解与收敛。
