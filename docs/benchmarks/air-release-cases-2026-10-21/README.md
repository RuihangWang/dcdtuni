# 十个风冷发布算例：严格网格与回风收敛回执摘要（2026-10-21）

这份回执把十个发布算例最近一次的网格质量回执和稳态收敛判定收在一起，让状态文档里的结论能追溯到原始文件。原始文件在本机工作树里，被 `.gitignore` 排除（`case/**/mesh-quality-receipt.json` 与 `case/*/log/`），所以这里提交的是逐字段摘录：[summary.json](summary.json)。

## 结论

十个算例都经 React 编辑器的产品流程（dcCart 网格引擎、MPI 分区）生成网格，严格 `checkMesh` 全部通过（`status = accepted`、`failedChecks = 0`）；最近一次稳态求解都按所有者 2026-10-20 定的判据判为 `CONVERGED`：每个 ACU 回风温度先变化、后在最后一个窗口内平稳到 0.5 K 以内，残差和点探针只作诊断。每份网格回执绑定的 `.cfdrun` SHA-256 与当前磁盘上 `case/<name>/model/` 里的 `.cfdrun` 一致。

相关提交：`999e6143e`（五例严格网格，2026-10-18）、`34bb6b883`（Base604 通过，2026-10-19）、`a69b81e97`（十例经 React 重网格全部通过，2026-10-20）、`76f9925f7`（回风 0.5 K 判据，2026-10-20）、`31e65b349`（CMI-China、Shinaide 切到 fast 预设后重解收敛，2026-10-21）。

## 逐例

| 算例 | 网格回执 | 分区 | 绑定的 .cfdrun（前 12 位） | 最近一次求解 | 判定 | 回风监测通过 | 窗口内回风最大波动 (K) | 残差诊断 |
|---|---|---|---|---|---|---|---|---|
| Base_leakage | accepted / 0 | 8 | 475793cdd5fb | 2026-10-20-17-17-30 | CONVERGED | 2 / 2 | 0.0001 | 通过 |
| Tri_angle | accepted / 0 | 8 | c7a5f0902fa9 | 2026-10-20-18-13-06 | CONVERGED | 1 / 1 | 0.0004 | 通过 |
| Slab | accepted / 0 | 8 | 5c32640c5db1 | 2026-10-20-19-25-05 | CONVERGED | 2 / 2 | 0.0001 | 通过 |
| STDCT | accepted / 0 | 8 | 1820ac9fe1f6 | 2026-10-20-20-14-40 | CONVERGED | 3 / 3 | 0.0036 | 通过 |
| CMI-China | accepted / 0 | 8 | fc562635bd84 | 2026-10-21-04-39-56 | CONVERGED | 5 / 5 | 0.0139 | Uy 有散布（诊断） |
| Base604 | accepted / 0 | 16 | b3087ad1d7b0 | 2026-10-20-21-33-20 | CONVERGED | 7 / 7 | 0.1197 | k 上升、窗口内有 bounding（诊断） |
| Base2-9 | accepted / 0 | 16 | df9940b55014 | 2026-10-20-18-11-06 | CONVERGED | 8 / 8 | 0.4893 | 通过 |
| DAML-Testbed | accepted / 0 | 8 | 5d1590d96fab | 2026-10-20-19-54-07 | CONVERGED | 9 / 9 | 0.1941 | 通过 |
| Shinaide | accepted / 0 | 8 | e4d929436167 | 2026-10-21-04-39-58 | CONVERGED | 4 / 4 | 0.2235 | 通过 |
| Shinaide_Liquid | accepted / 0 | 8 | 4fa39a294bbd | 2026-10-20-17-03-35 | CONVERGED | 4 / 4 | 0.0018 | 通过 |

Shinaide 一行是 2026-10-21 的网格；那套网格的房间外壳因地板开孔错位而不闭合，2026-10-22 修复后重网格并重解，见 [shinaide-floor-cutouts-2026-10-22](../shinaide-floor-cutouts-2026-10-22/README.md)。

Shinaide_Liquid 这次是风液混合运行：液冷 DAE 会话的热量经 `/api/run-mesh` 的 `hybridHeat` 交接给风冷求解，同一运行目录里有热量交接账本。

## 下一步

- Base604 的 k 与 CMI-China 的 Uy 残差诊断：按 R29 先在网格里定位 k 被 bounding 的单元（是否为封闭空腔或薄切割单元），查清原因后决定是否需要修，结论补进本回执。
- Base2-9 在这次求解之后以 cfdproj v5 重新保存（`8010ff0ce`，几何不变，功率与 ACU 送风量迁到 t=0 关键帧）。下一步用 v5 工程重新导出 `.cfdrun`，与回执绑定的 `df9940b5…` 做规范化比对；一致就沿用本回执，不一致再重跑网格与求解。
- 风冷瞬态（PIMPLE）在发布算例上还没有回执：从已收敛的 Shinaide 稳态出发跑一段瞬态，留下 `TRANSIENT_COMPLETED` 回执。
- 十例之外的算例（GTC、Alibaba 两例、CMI-Guichou、Polygon_Room、Base_coldAisleIso 等，对应论文数据集的大算例）尚未用当前 dcCart 流程验收，下一步先补 GTC 与 Alibaba-sige。
