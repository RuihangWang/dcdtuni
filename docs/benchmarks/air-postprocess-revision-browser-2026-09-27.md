# 风冷后处理双标签版本门禁浏览器验收（CPU，2026-09-27）

在 Mac/40901 同一开发提交 `811378e5` 上，40901 以仓库 `src/start_server.sh --app react` 在独立端口 8372/8373 启动生产构建，CPU 亲和性 0–3、BLAS/OpenMP 各 2 线程、CUDA 不可见。本机 IAB 经独立 18472 转发打开两个标签；没有更改原 Shinaide 服务 8360 的 case，也没有使用 GPU、Kit 或 AI worker。浏览器实际加载 `index-CvsqvsMY.js`，Mac/40901 的 `dist/index.html` SHA-256 均为 `6e274cd33eceac24a37c0a491603f6307358f45479105bddce3eb88f9c6ef320`。

两个标签加载同一 `SyntheticAirDisplay.cfdproj`（SHA-256 `eff19281d602fdc37475c4311e8b68c63f52093e054a44328f1e2029eff16569`），选择相同制造显示运行 `2026-09-27-03-31-00_ManufacturedAirDisplay`。其 `PROVENANCE.json` SHA-256 为 `878c136ac4692249c4834d73a32ade68f48dbae36b2ab632928a08ae26c5d5a5`：500-cell 来源小盒严格 `checkMesh` 为 `Mesh OK`，第 1、2 帧 T/U 是手工制造的显示数组，**不是 Shinaide 或 NV 的风冷 CFD 预测**。

1. 标签 A 选择时间 1 并开启后处理，页面显示 3 patches、500 cells、800 triangles，T 色条 **26.9–30.9 °C**。
2. 标签 B 保持时间 2 并开启后处理，T 色条 **26.9–32.9 °C**；服务端当前快照随 B 切换。
3. 标签 A 未重新初始化就选择 `U (Magnitude)`。真实页面收到 `/api/pp/field/Umag -> 409`，立即清除旧表面场、色条与后处理控件，保留所选时间 1，不把 B 的数据渲染为 A 的结果。此时标签 B 仍显示时间 2、T **26.9–32.9 °C**。8372 服务日志也记录该 `Umag` 请求为 HTTP 409。
4. 标签 A 再次点击“开启后处理”，时间 1 的 T 色条恢复为 **26.9–30.9 °C**，无须改选 B 的时间或运行记录。页面的 AI 提示明确说明独立 H5 图预测没有以当前 CFD 时刻为输入。

这验证了同一进程内两个真实 React 标签对不同时间帧的请求级 fail-closed 行为，以及旧标签的恢复路径。后端真实 handler/PyVista 测试还覆盖跨运行记录、切 case、并发体渲染和 AI 晚返回；浏览器这次只执行了 T/Umag 与重新初始化，没有操作真实 AI worker、旧 HTML 双标签、Omniverse 或 Shinaide 风冷求解。服务 8372/8373 与本机转发仅供此次隔离验收，结束后关闭；用户原有 18360 标签继续指向 8360 的 Shinaide 工程。
