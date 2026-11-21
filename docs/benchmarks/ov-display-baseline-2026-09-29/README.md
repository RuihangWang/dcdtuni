# OV 显示战役 Stage 0 回执 — Kit→浏览器基线视频验证(2026-09-29)

**结论:历史阻断("Kit 实机无 decoded video/ACK")已打破。** cfd_demo 已知好路径在
40901 + Mac 隧道 + 真实 Chromium 下全链通过:WebRTC 视频解码、场景加载、数据集
加载、IndeX 体渲染、流线+种子点球,全部有浏览器侧像素/日志证据。

## 环境与执行块

- 机器:NTU-Litianyu-40901(`~/mycode/dcdtuni@4c95ed4e→d7f0a704` 期间未变,Stage 0 无产品代码改动);浏览器:Mac Playwright MCP(真实 Chromium)经 SSH 隧道 `-L 8392 -L 8301`。
- Kit:`kit-cae _build/linux-x86_64/release/omni.cae_streaming.kit.sh` + `dc.cfd_bridge`,启动参数 `DC_CFD_GPU=0 DC_CFD_GB300_COUNT=0 DC_CFD_AI=0 bash scripts/start_demo.sh`;Xvfb :99(既有);PID 1053307;日志 `/tmp/dc_cfd_demo.kit.log`。
- 启动时间线:`app ready` 114.5 s → `RTX ready` 124.7 s → 8301 监听;**GPU 纪律:Kit 仅在 GPU0**(3.4→7.1 GB 随体渲染增长)。
- 参考 UI:cfd_demo web(vite dev,8392)。主 React 接线属 Stage 1。
- 实测期间用户本人 FVGN-pt2.7 训练进程(PID 1077336-40)上线共享 GPU0-3(GPU0 上 1510 MiB);与 Kit 无冲突,如实记录。8360/8361(PID 3282138)未触碰。

## 验证项与证据

| 项 | 实测 | 证据 |
|---|---|---|
| WebRTC 视频 | track live 未 muted,1920×1080,readyState 4;**1.5 s 内解码 90 帧(60 fps)**;累计 13547 帧;丢帧 ~9%(隧道+软解,可接受) | 浏览器 `getVideoPlaybackQuality()` 探针 |
| 场景加载 | `loadScene GTC2025.json` → 50 racks/118 group prims/7 类;模型树 50 对象;画面 nonBlack 1.1%→**98.8%** | `evidence/ov-00-baseline-gtc-scene-video.png` |
| 数据集 | `loadDataset internal.vtu`(171 MB)→ **7 s OK**,识别 T/U/Umag(/World/internal/CellData/*) | 桥日志 |
| 体渲染 | `setVolume T` → 隐藏 Room 几何 → `setVolumeRange 292.1–309` → 体素化 **1.11 s** → `volume rebuilt cycles=30 path=/World/CAE/InternalVolume_1`,IndeX kernel on **cuda:0**(192 ms load);彩色像素占比 9.2% | `evidence/ov-01-volume-render-T.png` |
| 流线+种子球 | `Add Streamlines` → "Streamlines: ON";`setSeedSphere {center:[15.5,2.499,9.597],radius:1.25,nSeeds:27}` → 桥确认 visible=True clip=False;面板含 Seed sphere (m)/Seeds/Hide seed sphere 控件;**视口拖拽种子球先例存在**(nativeTouchEvents + `_resolve_seed_center` 仲裁) | `evidence/ov-02-streamlines-seedsphere.png` |
| 命令通道 | 浏览器→Kit 16 命令面枚举(见裁决文档 D1);Kit→浏览器日志/事件回流(LogPanel 实时) | 本回执 + D1 |

## 命令面清单(dc.cfd_bridge,Stage 0 实测枚举)

loadScene · loadDataset · setVolume · setVolumeRange · setStreamlines · setSeedSphere ·
setRoiBox · setRoiClip · setRoiVisible · extractRoi · selectObject · focusObject ·
setObjectVisible · setObjectOpacity · syncFromViewport · resetScene
(**无 slice 命令** → 表面切片为 Stage 3 新开发,裁决 D4)

## 边界与遗留

- 本回执验证的是 **cfd_demo 参考 UI**;主 React(OvViewport/ovProtocol)接线是 Stage 1 的门。
- 数值/结果解读边界:internal.vtu 为 GTC demo 预置结果,非本项目求解产物;不涉及任何物理结论。
- 已知问题继承清单见 `docs/archive/omniverse-2026-09-10/ov_display_decisions_2026-09-29.md` D6(ROI 裁剪 bug、长寿命 Kit 重建崩溃、start_demo.sh CONDA_PREFIX unbound、scipy 告警、丢帧率)。
- Stage 0 结束时 Kit(PID 1053307)/vite(8392)/Mac 隧道**保持运行**供 Stage 1 续用;战役结束或异常时按 SOP teardown(lsof 前后核对)。
