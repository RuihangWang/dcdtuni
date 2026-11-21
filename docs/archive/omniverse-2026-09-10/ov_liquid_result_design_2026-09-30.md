# OV 显示战役 Stage 4 设计 — 液冷结果着色 + 服务器签发血缘(2026-09-30)

输入:Stage 3 过门后的 main(`c30b492a`)、只读调研(液冷着色/结果模型/结果层三道闸门)、用户裁决
(液冷可微 DAE 接入,工程 `case/Shinaide_Liquid/Shinaide_liquid.cfdproj`;着色不止管道,rack manifold、三通等全部件)。

## 1. 现状要点(调研实测)

- Three.js 只给**管道**(按 binding 边的 cell 温度均值)和**服务器**(冷板芯片温度最大值)上色;机柜/CDU/阀的均值算了但丢弃;
  manifold、CDU、三通/阀门零着色。项目里**没有"三通阀"类型**:阀类型为 `tee_junction`(三通接头)/`flow_control`/`isolation`。
- 时间滑条每动一次 → `resultRenderRevision` 变 → OV 场景同步键变 → **整场景重传**(Shinaide 1930 图元约 86 s)。
- 液冷结果到 OV 无通道:结果层 schema 无"按 id 重着色"层;浏览器侧 owner 只认 air/mesh-preview;Kit `_trusted_result_context` 永不赋值。
- 可微 DAE 结果 `result.json`(`solver: differentiable_newton_dae`)含 `object_bindings`、`cell_edge_ids`、`cell_temp_K[cell][t]`、
  `node_temperature_K`、`edge_outlet_temp_K`、`chip_temp_K`、`cp_details`;`input.json.liquidrun.source_project = {project_uid, sha256}`,
  其 sha256 与 React `projectContentHashSync`(Kit 已确认场景的 `projectHash`)同口径。旧 hybrid 结果无 bindings,不支持。

## 2. 着色语义(Three.js 与 OV 同一规则,同一 coolwarm 色表)

| 部件 | 场景标识 | 温度量 |
|---|---|---|
| 管段 | `modelUid=管段 uid`,无 equipment | binding 边的 cell 温度均值(现有 Three.js 规则) |
| 供水 manifold | `modelUid=机柜`,`liquidPart=supply-manifold` | 该机柜各服务器 `*_branch_in` 边 cell 温度均值 |
| 回水 manifold | `modelUid=机柜`,`liquidPart=return-manifold` | 该机柜各服务器 `*_branch_out` 边 cell 温度均值 |
| 三通接头 | `modelUid=管段`,`equipmentUid=tee id` | 节点 `<管段>_tee_<id>` 温度 |
| 流量控制阀 | `modelUid=管段`,`equipmentUid=valve id` | binding `<管段>/<valve id>` 的控制阀边温度 |
| CDU | `modelUid=CDU` | 二次侧供水节点 `<cdu>_supply` 温度(图例注明) |
| 服务器 | `modelUid=机柜`,`equipmentUid=服务器` | 该服务器冷板芯片温度最大值(现有 Three.js 规则) |

冷板/泵/换热器在图中只是边,无网格,不单独着色(如实)。色域:网络部件与芯片**分两套**,默认按当前帧 min/max(与现 Three.js 一致),
面板可设固定域(动画时不跳色)。温度计算放纯函数(TS 与 Python 各一份),**同一 JSON 夹具**对两边做逐值一致性测试。

## 3. 血缘(Stage 4.2 的"422 → 服务器签发")

- 服务器 `POST /api/ov/liquid-result-grant {runId}`:只认当前工程 `Liquid/log/run-<32hex>/result.json`;
  无 bindings/cell 映射的旧结果 → 422(列出缺项);签发 `kind=liquidResult` 票据:`runId, resultSha256(文件字节), configHash, solver,
  status, samples, projectUid, projectContentSha256(来自 source_project)`,HMAC 同 Stage 3 显示票据(启动器每次启动生成的密钥)。
- Kit `loadLiquidResult {grant}`:验签 + 根目录白名单 → 读文件并**复算 sha256 必须等于票据** → `projectUid` 必须等于已确认场景 →
  成为唯一的液冷可信上下文;内容哈希不等于已确认场景时照常显示但 ack 回报 `projectContentMatches:false`(面板提示"工程在求解后已修改")。
- `setLiquidFrame {sample, domain}` 只在可信上下文存在时可用;颜色由 **Kit 从已验证文件自算**,浏览器只给帧号——浏览器无法伪造颜色。
- 空气结果层 `/api/pp/render-source` 仍 422(其缺项回执确实不存在),不被本通道绕过;反例测试:伪造/篡改票据、结果文件被改、
  工程不符、未载入即设帧、浏览器自报 expectedSource —— 全部拒绝。

## 4. OV 重着色(不重传场景)

Kit 按 `dcdtuni:modelUid / equipmentUid / liquidPart` 找到图元,覆写其独占材质的 `diffuseColor`(断开的纹理/顶点色连接先存档,
`clearLiquidColours` 还原);场景重载后若可信上下文仍在则自动重放当前帧。React:OV 液冷着色开启时,`sample/timeSeconds/visualRevision`
退出场景同步键,滑条改发 `setLiquidFrame`(acked,毫秒级)。快照新增可选图元字段 `liquidPart`(schema + validate + USD customData)。

## 5. 可微 DAE 接入与门槛

DAE 作业路径(v3 `.liquidrun` → `/api/liquid/runs` 线程作业 → 帧轮询)在 `Shinaide_liquid.cfdproj` 上浏览器实跑;
运行中按新帧数重签票据,完成后全时域滑条。门:Three.js 与 OV 同帧同部件着色一致(像素 + ack 数值)、滑条联动毫秒级、
反例全拒;回归:vitest、cfd_demo/server pytest、液冷后端相关 pytest;合 main。
