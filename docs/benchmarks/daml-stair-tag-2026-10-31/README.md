# DAML-Testbed 网格质量修复回执（2026-10-31）

## 结论

`case/DAML-Testbed` 的严格 checkMesh 回执由 rejected（1 项失败）变为 accepted（0 项失败），
行列式最小值由 0 升到 0.0177，新网格上稳态求解 399 步判 CONVERGED，求解页不再出现网格质量提示。

## 原因

`stripOffPlaneStairTags`（`dcCartMesh.C`）判断“面在 sheet 平面上”时用通用 ON 容差
（0.1 倍最细单元，这里 3 mm）。`merged_wall_baffle` 是精确外形区域，切割时的平面容差只有
30 µm，切割面落在 x = 3.07954；而最近的网格面 x = 3.07672 离它 2.82 mm，落在 3 mm 以内，
builder 留下的 stair-step 标记因此没有被清除。这层面（0.98 m²）与真实切割面之间夹出
516 个 2.8 mm 厚、行列式为 0 的单元，合并守卫又不允许跨 baffle 面合并。

10-20 的网格没有这个现象：当时的 thin-gap 加密（`rackThinGapRegion_*`）让网格面落在别处。
提交 `5b12c5c65`（10-26）移除这些加密盒后，网格面正好落进容差。

## 修法

`dcCartCutCells::baffleSheetPlaneTolerance` 成为切割、in-plane 标记和清理共用的容差来源，
`stripOffPlaneStairTags` 按每个 sheet 自己的容差判断。三处精确外形区域的判定合并为
`exactOutlineZone` 一处。

## 验证

| 项 | 修复前 | 修复后 |
|---|---|---|
| 回执 | rejected / 1 | accepted / 0 |
| 行列式过小的单元 | 516 | 0 |
| 行列式最小值 | 0 | 0.0177 |
| 非正交度过高的面（只报告） | 273，最大 75° | 273，最大 75.07° |
| 单元数 | 216,169 | 215,653 |
| x = 3.0767 上的 baffle 面积 | 0.983 m² | 0 |
| x = 3.0795 上的 baffle 面积 | 1.948 m² | 1.948 m² |

修复后的完整运行（各 8 进程）：

| 算例 | 回执 | 判定 | 步数 | 空调带走 / 声明热负荷 |
|---|---|---|---|---|
| DAML-Testbed | accepted / 0 | CONVERGED | 399 | 3010 W / 3000 W（有定温热表面，走退化判定：窗口漂移 0.22%） |
| Tri_angle | accepted / 0 | CONVERGED | 429 | 6245 W / 6250 W |
| Slab | accepted / 0 | CONVERGED | 262 | 8253 W / 8250 W |

React 求解页经 GPU WebGL 浏览器核对：`meshReady` 为 true，`solve-mesh-quality-advisory` 不存在，
顶栏显示“网格 就绪”。

## 回归（只出网格，工程副本在 `_tmp/reg/`）

| 算例 | 进程 | 耗时 | 运行 | 回执 | 行列式最小值 | 最大非正交度 |
|---|---|---|---|---|---|---|
| Base_coldAisleIso | 8 | 22 s | succeeded | accepted 0 | 0.101716 | 75.588 |
| SingleRack | 8 | 15 s | succeeded | accepted 0 | 0.125 | 69.6535 |
| Base_leakage | 8 | 22 s | succeeded | accepted 0 | 0.0443044 | 71.334 |
| Tri_angle | 8 | 18 s | succeeded | accepted 0 | 0.00222464 | 75.566 |
| Polygon_Room | 8 | 22 s | succeeded | accepted 0 | 0.0894824 | 71.9296 |
| Slab | 8 | 31 s | succeeded | accepted 0 | 0.0152283 | 72.3297 |
| STDCT | 8 | 66 s | succeeded | accepted 0 | 0.0312395 | 78.4504 |
| CMI-China | 8 | 45 s | succeeded | accepted 0 | 0.0392222 | 76.8227 |
| Shinaide | 8 | 34 s | succeeded | accepted 0 | 0.0225428 | 74.8568 |
| Shinaide_Liquid | 8 | 44 s | succeeded | accepted 0 | 0.0131311 | 74.693 |
| GTC2026 | 8 | 138 s | succeeded | accepted 0 | 0.0609717 | 71.0457 |
| DCTS | 8 | 150 s | succeeded | accepted 0 | 0.00378245 | 59.9993 |
| Base2-9 | 16 | 164 s | succeeded | accepted 0 | 0.0345039 | 73.5359 |
| Base604 | 16 | 229 s | succeeded | accepted 0 | 0.0317706 | 76.2246 |
| CMI-Guichou | 16 | 233 s | failed | — | — | — |
| Alibaba-sige | 16 | 74 s | failed | — | — | — |
| Alibaba-hongchou | 16 | 77 s | failed | — | — | — |
| Alibaba-sige | 8 | 449 s | succeeded | rejected 4 | 0 | 89.2176 |
| Alibaba-hongchou | 8 | 405 s | succeeded | rejected 3 | 0 | 89.8499 |
| CMI-Guichou | 8 | 448 s | succeeded | rejected 2 | 0 | 77.3048 |

- 回执里的 “Failed 1 mesh checks” 是凹单元警告，不计入失败项。
- 表里 Alibaba-sige、Alibaba-hongchou 的 16 进程运行失败是内存护栏：当时本机可用内存只有约 93 GB。
- Alibaba-sige、Alibaba-hongchou 的 rejected 与修复前完全相同（sige：det 7、tets 1、vr 78、wgt 107；
  hongchou：det 29、vr 110、wgt 1353），这次修复没有改变它们。
- CMI-Guichou 16 进程的分区不一致不是这次引入的：临时换回修复前的代码，同一运行给出相同的
  17338 对 17362。8 进程可以出网格，回执 rejected / 2。

## 尚未解决

- CMI-Guichou 16 进程下 rank 8 与 9 的处理器边界面数不一致。
- Alibaba-sige、Alibaba-hongchou、CMI-Guichou 的少量小行列式单元与小体积比面。
