# OV 显示战役 — Stage 0 架构裁决(2026-09-29)

基线 `main@d7f0a704`;基线视频验证回执见 `docs/benchmarks/ov-display-baseline-2026-09-29/`。
用户裁决原文记录在 OneDrive `Research/DCTwin/OV_DISPLAY_CAMPAIGN_PLAN_2026-09-29.md` 文首。

## D1 — Kit 侧:复用并演进 `dc.cfd_bridge`,不新写扩展

Stage 0 实测:该扩展的 16 条命令面(loadScene/loadDataset/setVolume/setVolumeRange/
setStreamlines/setSeedSphere/setRoiBox/setRoiClip/setRoiVisible/extractRoi/
selectObject/focusObject/setObjectVisible/setObjectOpacity/syncFromViewport/resetScene)
覆盖了显示战役的大部分需求,且 GTC 场景、171 MB .vtu 数据集、IndeX 体渲染(cuda:0,
体素化 1.11 s)、流线+种子点球全部实机通过。新写扩展只会重复这 6582 行已验证代码。
**演进点**:新增 `setSlice`(表面切片,Stage 3)、液冷结果命令(Stage 4)、与主 React
`ovProtocol` 的 ACK 语义对齐(现桥为 fire-and-forget + 日志回传;主 React 期望
RenderAck{requestId,ok,view,revision})。

## D2 — 前端:主 React `renderer/` 为唯一 UI,cfd_demo web 退役为参考

`OvViewport.tsx`(AppStreamer direct 模式,与 cfd_demo stream.ts 同一库同一连法)接活
Kit 流;`RendererControl` 的 Three.js↔Omniverse 切换为无缝切换入口;新增 OV 后处理
面板(体渲染/切片/流线控件,命令走 ovProtocol)。**OV 仅显示**:不做拖拽建模,
`ovEditBridge` 保持冻结;OV 视口内唯一鼠标交互 = 流线种子球拖拽 + 切面预览圈拖拽
(nativeTouchEvents 已把指针事件转发给 Kit,种子球拖拽在 cfd_demo 已有先例,
`_resolve_seed_center` 仲裁前端数值 vs 视口拖拽)。

## D3 — GPU/端口/启动参数(用户裁决)

`start_server.sh` 新增:`--ov`(react 模式并启 Kit 桥)、`--ov-gpu N`(**默认 0**,
覆盖 cfd_demo"最后一张卡"默认)、`--ov-port`(默认 8301)。单卡纪律沿用:
CUDA_VISIBLE_DEVICES 掩码 + `--/renderer/activeGpu` 同卡 + multiGpu off +
`omni.cae.viz` persistent-device 覆写(start_demo.sh 注释中的血泪史全部继承)。
起 Kit 前 `nvidia-smi` 实测占用(避卡清单不可靠;Stage 0 实测用户 FVGN 训练
与 Kit 共享 GPU0,4999+1510 MiB,无冲突)。隔离端口对:React 8390 + Kit 8301
(+vite 参考 UI 8392 仅 Stage 0 用);8360/8361 禁触。

## D4 — 表面切片 = Stage 3 新开发(桥现无可视 slice)

`result_layers_usd.py` 的 'slice' 是主 React 结果层管线的 USD 层授权,不是可视切割;
桥命令面无 slice。实现候选(Stage 3 调研定案):a) omni.cae.viz/IndeX 的裁剪平面
(体渲染域内切割);b) VTK slice filter 重采样为 2D 场 + 纹理映射平面(对体/表面场
都通用,且与"表面切片"语义更贴近);c) 两者都做(体渲染用 a,表面场用 b)。
切面预览圈 = 视口内可拖圆环 gizmo(法向+中心+半径),拖拽→setSlice 参数,双通道
(面板数值 + 视口拖拽)循种子球仲裁先例。

## D5 — 液冷结果的 OV 表示(Stage 4 细化)

液冷是 1D 热工网络,无三维场:OV 表示 = 几何按帧温度着色(管道/冷板/CDU,与
Three.js 侧"芯片/管道温度着色"同语义同色标),时间滑条驱动重着色;**不**为液冷
造体渲染。体渲染主战场是风冷(OpenFOAM→foamToVTK→.vtu/.vtm,含本项目自产
run 的 VTK 目录)。诚实边界沿用:mock/合成数据如实标注,422 fail-closed 的
可信来源语义不被显示通道绕过(血缘回执方案 Stage 4 设计)。

## D6 — 已知问题清单(继承 + 本轮新发现)

1. ROI 裁剪清空体渲染(cfd_demo README 已知 bug)——Stage 3 修复或如实降级标注。
2. 长寿命 Kit 多次体重建后可崩(README)——E2E 前重启 Kit 的纪律写入 SOP。
3. `start_demo.sh:202` `CONDA_PREFIX: unbound variable`(set -u + AI 段)——Stage 1
   参数化改造时一并修。
4. scipy/NumPy 版本告警刷屏(系统 dist-packages)——良性,日志过滤,不动系统包。
5. WebRTC 丢帧率 ~9%(隧道+软解负载)——可接受;若影响验收截图质量再调
   (码率/分辨率参数)。
6. python3.10 CLI 无 vtk 不影响:数据集加载走 Kit 自带 omni.cae.data VTK 栈
   (Stage 0 实测 load_dataset OK)。

## D7 — 每阶段合 main 的验收形态

产品代码+测试+回执同提交;browser-use 证据(截图/像素探针/日志摘录)落
`docs/benchmarks/ov-display-*`;回归 = vitest 全量 + `tsc -b && vite build` +
受影响后端 pytest;提交带 Co-Authored-By trailer;推送后同步 40901(与液冷 demo
战役同纪律)。
