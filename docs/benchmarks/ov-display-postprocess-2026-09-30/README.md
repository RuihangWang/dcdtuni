# OV 显示战役 Stage 3 回执(2026-09-30)——后处理三件套 + 唯二视口交互

**结论:过门。** 表面切片、体渲染、流线三件套在主 React(非 cfd_demo UI)里经 OV 实机显示并联动;
两个视口交互(切面预览圈、流线种子球)"视口拖拽 → 回读/重积分"闭环在真实浏览器里走通。
过门途中定位并修复了 8 个真实缺陷(其中 2 个是历史遗留的根因),另有 1 个外观问题如实登记为未决(流线着色)。

环境:40901,`src/start_server.sh --app react --port 8390 --ov --ov-gpu 0`,Kit 单实例单监听 8301,仅 GPU0
(另一会话的 SCANO 训练占 GPU1–6,无冲突);Mac 隧道 8390/8391/8301。8360/8361 全程未触碰。

## 数据集与诚实边界

- **SyntheticAirDisplay**(用户指定主路径):500 单元显示夹具,**均匀场**(t=2:T=303 K、U=(0.1,0,0) m/s),
  不是 CFD 预测。用于验证传输/授权/ack/`.case` 转换路径;均匀场下切面与体渲染只能呈现单色,色标如实标注"均匀场,展宽仅为显示"。
- **Base_coldAisleIso / 2026-06-19 运行**(仓库内既有真实求解,已重建,t=60,105330 单元):用于体渲染/切面/流线的**视觉正确性**对照。
  只读引用既有结果,不产生新物理结论。

## 过门证据

| 项 | 结果 | 证据 |
|---|---|---|
| 顶栏切换 | Three.js/Omniverse 切换控件移入顶栏(用户 09-30 裁决),顶栏折叠时回落到视口浮层 | `evidence/s3-01-topbar-switch-scene-ack.jpg` |
| scene ack | 内置浏览器经 SSH 隧道 8.0–8.8 s;40901 本机 Chromium 6.2 s(修复前:**永不到达**) | 同上;Kit 中继探针日志见下 |
| 数据集载入 | 面板选运行 → 服务器 foamToEnsight(镜像目录,补夹具缺失的 `uniform/time`,不写源运行目录)→ 签发显示票据 → Kit 验签 → 桥 `.case` 转换载入;夹具 8.0 s、冷通道 9–11 s;面板自动把切面原点/种子中心放到数据包围盒中心 | Kit 日志 `_convert_case … load_dataset OK` |
| 体渲染 T | 色标由 ack 回读 IndeX 实际使用的 domain;三组域颜色明确不同(蓝 [0,2e5] / 红 [288.1,295.3] / 绿 [288,305])→ 域被采用、体素为 T 场 | `s3-03/04/05-coldaisle-volume-*` |
| 表面切片 | 冷通道中高度截面 4832 三角、按 T 着色;黄色预览圈 + Kit 变换 gizmo | `s3-06`、`s3-07-slice-before-drag.jpg` |
| 切面圈拖拽闭环 | 视口拖 gizmo 的 Y 轴 → "回读视口拖拽" → 面板原点 (5, 1.425, 3) → (5, **2.592**, 3),提示"已采用视口拖拽位置并在该处重新切割",重切 4724 三角;Kit:`slice from viewport -> origin=(5.000, 2.592, 3.000)` | `s3-08/09` |
| 流线 + 种子球拖拽闭环 | 追踪 32 种子 48951 点;视口拖种子球 → "按视口拖拽重新积分" → Kit:`seed from viewport -> center=(5.000, 2.592, 3.000)`,重积分 42957 点 | `s3-10/11/12` |

交互门在 40901 Xvfb 的有头 Chromium(仓库规定的 OV 验证方式)上以逐步鼠标拖拽完成:
内置浏览器窗格在隐藏状态下截图会给出陈旧视频层,拖拽取证不可靠;数据集/体渲染/切面的 ack 与解码像素则在内置窗格经隧道完成
(解码帧由 `<video>` 画到画布取证,不依赖截图)。

## 过门途中修复的缺陷(均已提交 feature 分支并测试)

1. **renderAck 丢失(Stage 2 "ack 中继回归"的真正根因)**:Kit 侧逐跳探针证明 ack 已进入
   `omni.kit.livestream.send_message`;浏览器原始数据通道探针显示 WebRTC 插件(9.0.2)在会话初期丢弃 Kit→浏览器的
   app 消息(本机约 1 帧、经隧道约 2 s),而入站命令已可达。修复两层:Kit 侧 ack 经"入站静默 6 帧"队列按序发出;
   浏览器侧**出站就绪握手**(短超时重试幂等 `getViewState`,拿到任意 ack 才开始场景同步)。`527a8890`、`8d1d19aa`
2. `setSlice/getSliceState` 被渲染路径校验却**从未被路由**到渲染路径(落入旧通道成"未知命令")。ast 级回归测试。`527a8890`
3. `handlers.handle_set_slice` 缺失(WIP 调用了不存在的函数)。`7b7a42c8`
4. `read_grid` 从错误模块导入 `vtkXMLPolyDataReader`。读盘测试 + Kit 自带 Python 导入核验。`ea94c44c`
5. **体渲染纯红块根因**:`CaeVizRescaleRangeAPI("colors")` 在 IndeX 执行时把色图 domain 改写为场原始 min/max,
   均匀场写成零宽 (303,303);改为 rescale `disable` + 体就绪后触碰 domain 让 IndeX 重读传递函数;ack 等 `nvindex:composite` 置位。`d4441ebb`、`cb74a42a`
6. 派生 Umag 场缺 `fieldAssociation`,|U| 体被 IndeX 拒绝。`d4441ebb`
7. **切面/预览圈是无类型 prim**(`DefinePrim` 后包 `UsdGeom.Mesh`),渲染器不画;改 `UsdGeom.Mesh.Define` + extent,测试断言 typeName。`cb74a42a`
8. **视口鼠标输入从未转发给 Kit**:流库在视频父元素 `focusin` 时才 `captureUserInput(true)`;参考实现 `<video tabIndex=-1>`,
   主 React 缺失。jsdom 测试。`5230d28a`

另:切面切割只保留完整三角形(边界面切成线段会错位索引流)、切割在执行器线程里跑并缓存网格、`slice_geometry` 纯函数化;
服务器数据集授权(`/api/ov/result-dataset`,HMAC 票据,Kit 验签 + 根目录白名单,浏览器永不传路径)。

## 未决 / 如实登记

- **流线着色**:流线几何、追踪、重积分、种子球拖拽均正确,但线条恒为单一品红(kit-cae `ScalarColor`/basic.mdl 在 LUT 1.0 端)。
  已显式写入 domain(日志 288.15..295.31)并开启 `enable_coloring`,颜色仍不变 → MDL 取值链路待查(Stage 5 前处理或另立项)。
- **体渲染默认色域**:按单元数统计的 p1..p99 在加密网格上偏向小单元区(冷通道),热回风大单元区会越上限显示为红;面板可手动设范围。
  改为按体积加权的百分位是后续候选。
- **OV 后处理面板遮挡视口左部**(可折叠,默认展开)——后续改为贴右/默认折叠。
- IndeX 合成比体重建返回晚约 10 s;ack 现在等待合成并回报 `composited`。

## 液冷 Shinaide 场景同步复验(ack 改动的回归)

ack 延迟队列与出站就绪握手改动了所有场景同步路径,因此复验 Stage 2 的大场景分块路径:
`Shinaide_Liquid/Shinaide_dae_cpu.cfdproj` → OV,3585 分块、progress-ack 节拍,scene ack 回报 **primitives=1930**。
静默帧 6 时首同步 120 s(每个 progress ack 叠加等待);握手已覆盖会话初期丢包窗口,静默帧降为 2 后 **86.3 s**,
回到 Stage 1 基线(~85 s);经隧道的小场景仍 11.0 s ack。液冷结果着色是 Stage 4 的内容。

## 回归

cfd_demo + server pytest 215 passed(40901);vitest 3034/3034、`tsc -b` 干净、`vite build` 成功(Mac)。

## 复现

```bash
# 40901
CONDA_EXE=$HOME/miniconda3/bin/conda PATH=$HOME/miniconda3/bin:$PATH CUDA_VISIBLE_DEVICES="" OMP_NUM_THREADS=2 \
  src/start_server.sh --app react --port 8390 --ov --ov-gpu 0 --case-dir <repo>/case/Base_coldAisleIso
# 交互门(有头 Chromium on Xvfb :99):.codex-tmp/ov-stage3/drag_probe.py(临时脚本,不入库)
```
