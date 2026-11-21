# dcCart：1 个 rank 与 8 个 rank 得到同一套网格（五个发布算例，2026-10-25）

DCCARTMESH.md 写明，切割单元在任意 rank 数下都相同。此前这一点已在五个算例上比对过，这份回执补上另外五个发布算例中的四个——Tri_angle、Slab、DAML-Testbed、Shinaide，并重新核对 Base_leakage。

**结论：五个算例的 1 rank 与 8 rank 网格逐单元一致，十套网格都通过严格 checkMesh。**

| 算例 | 单元数（np1 / np8） | 单元中心与体积 | 总体积 (m³) | 严格 checkMesh |
|---|---|---|---|---|
| Tri_angle | 99,958 / 99,958 | 全部一致 | 41.315043 | accepted / 0，两次均是 |
| Base_leakage | 321,479 / 321,479 | 全部一致 | 91.169112 | accepted / 0 |
| Slab | 659,934 / 659,934 | 全部一致 | 231.805507 | accepted / 0 |
| DAML-Testbed | 254,150 / 254,150 | 全部一致 | 49.183721 | accepted / 0 |
| Shinaide | 723,302 / 723,302 | 全部一致 | 251.514125 | accepted / 0 |

## 比对方法

- 网格回执里的 `meshSha256` 按各 rank 的拓扑文件计算，分区方式不同时哈希必然不同，所以不能直接拿来比对。这里改用与分区无关的签名（`signature.py`）。
- 每套网格先复制一份快照，再用 OpenFOAM 自带的 `writeCellCentres` 和 `writeCellVolumes` 写出每个单元的中心与体积（ASCII 输出，6 位有效数字）。
- 汇总各 rank 的单元后，按中心坐标（0.1 mm 分箱）加体积排序再比较。
- 结果：五个算例的单元集合完全相同，体积逐个相等，最大相对差为 0。

## Patch

- Base_leakage 与 DAML-Testbed 各物理 patch 的面数完全相同。
- Tri_angle、Slab、Shinaide 有差别，但只出现在循环挡板上，即多孔盲板和机柜门的 master/slave 面对。8 rank 时，跨分区边界的挡板面会归入 OpenFOAM 的 `procBoundary…through<挡板>` processor-cyclic patch。
- 对每个物理 patch 逐一核对：np1 的面数都等于 np8 的面数加上它的 processor-cyclic 面数。所以没有面丢失，也没有面重复。

## 复现

`run.sh`：每个算例经 `python -m backend.api air <cfdproj> --mesh-only --mesh-processes {1,8}` 生成网格（经私有服务器与 runner，dcCart 引擎），再做快照、写单元中心与体积，最后比较。

- 本机为 4 核，8 rank 是超额订阅运行。
- 每次网格加严格检查耗时 24–121 s，见 `runs.txt`。
- `summary.json` 记录每套网格的单元数与总体积。
