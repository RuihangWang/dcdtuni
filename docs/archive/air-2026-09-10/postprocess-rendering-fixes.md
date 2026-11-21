# 原生后处理与显示修复（2026-09-11）

使用现有 `case/DAML-Testbed/log/2026-09-11-06-40-53_DAML-Testbed` 验证：4 个分区、第 500 步、269625 个单元、170 个物理 patch。本轮没有重新划网格或求解。

## 读取与列表

后处理目录统一由 PyVista 原生读取 OpenFOAM，串行和分区都不自动调用 `foamToVTK`，已有 VTK 目录也不会覆盖原生结果。显式 VTM 文件导入保留。保留旧 root 时，后端选择最新完整结果；前端状态扫描也能发现所有分区共同写出的新时刻。

HTML 与 React 的 patch 分类和模型组默认全部折叠，勾选/可见性不变。同一结果刷新保留用户展开状态，切换结果重新折叠；筛选、分组勾选和从视口定位 patch 继续有效。

## 三角形与阴影

原显示路径从多边形第一个点做扇形剖分，遇到凹面可能产生越过凹口的三角形。现使用 `vtkTriangleFilter` 正确剖分，再用 `vtkPolyDataNormals` 在 45° 尖锐边缘拆分显示顶点，避免浏览器把 90° 转角两侧法线混在一起。平面着色稳定，设备棱角保留；显示点上的原场值随拆点复制。

此外，后处理模式关闭主题的装饰扫描线，避免条纹叠加到场颜色上。没有对温度或速度做模糊或额外数值平滑。

## 验证

- 后端相关测试 57 项通过，React 相关测试 99 项通过，HTML 折叠行为测试 3 项通过；TypeScript/生产构建通过。
- 均匀温度箱体：相同生产 shader、相机、色标和几何，旧法线最大偏离平面 63.67°，新法线为 0°；异常三角顶点 48 → 0。
- 实际 DAML：170 个 patch 的精确 float32 `(x,y,z,T)` 配对集合全部相同；仅增加 4644 个显示顶点，三角形数均为 196488。T 范围均为 288.14999–290.30801 K。
- 两端实际显示成功：36 个可折叠组均默认折叠、扫描线关闭、页面与 shader 错误均为 0。

[均匀场前后对比](../../../bench/dccart_partition_flow/results/render_fix_daml500/uniform_box_before_after.png)

| 客户端 | 原显示方式 | 修复后 |
| --- | --- | --- |
| HTML | [原效果](../../../bench/dccart_partition_flow/results/render_fix_daml500/html_DAML_before.png) | [新效果](../../../bench/dccart_partition_flow/results/render_fix_daml500/html_DAML_after.png) |
| React | [原效果](../../../bench/dccart_partition_flow/results/render_fix_daml500/react_DAML_before.png) | [新效果](../../../bench/dccart_partition_flow/results/render_fix_daml500/react_DAML_after.png) |

[字段保持证据](../../../bench/dccart_partition_flow/results/render_fix_daml500/field_preservation.json) · [浏览器证据](../../../bench/dccart_partition_flow/results/render_fix_daml500/actual_browser_evidence.json)

前后对比使用同一份第 500 步温度数据、相机和色标，旧显示方式由原扇形剖分/共享顶点重现。真实温度梯度保留，未将梯度当作渲染错误抹去。

使用时重启后端、刷新前端页面并重新开启后处理，以替换进程和浏览器中已经缓存的旧显示网格。已有求解结果可以直接使用。
