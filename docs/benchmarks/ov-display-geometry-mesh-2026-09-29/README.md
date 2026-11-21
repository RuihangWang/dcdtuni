# OV 显示战役 Stage 2 中期回执(2026-09-29 夜)——几何/网格通道已证,ack 中继回归在查

**状态:未过门,不合 main。** 几何与网格显示通道在 07:39 Kit 实例上完整验证通过;
随后发现 **08:15 之后启动的所有 Kit 实例不再向浏览器中继 `dcdtuni:renderAck` 自定义事件**
(视频/命令通道正常),隔离链已完成到 webrtc 插件出站前一级;最后一跳取证被共享浏览器
竞争阻断(见下)。本回执记录已通过部分、隔离链与续跑 runbook。

## 已通过(07:39 Kit 实例,40901:8390 后端,GPU0)

1. **液冷几何**:Shinaide_dae_cpu 快照 → Kit `/World/RenderScene` **1930 primitives 全量授权**
   (begin=authored,两轮连接一致)。
2. **模型树开关→OV 重渲染**:取消 Rack_A_01 可见性 → 第三次 load **1814 primitives**
   (−116=该子树);恢复 → 第四次 **1930**。ack 往返正常,工程无损。`evidence/ov-20/21`。
3. **相机回读**:OV→Three.js 切换执行 returnToThree 的 view 回读,Three 视图恢复同机位。
   `evidence/ov-22`。
4. **风冷网格 overlay**:SyntheticAirDisplay(纯网格结果 case,工程模型数 0 忠实反映)
   网格预览 3 patches/500 cells/800 triangles → OV `/World/RenderOverlay` **6 primitives**
   (3 patches × triangles+lines),视口内可见半透明网格体。`evidence/ov-23`。
   (422 fail-closed 的 `/api/mesh-preview/render-source` 血缘端点按设计拒绝,属 Stage 4。)
5. **widths primvar 修复**:hydra 对单值 widths 的 vertex 默认插值告警,改为显式 constant
   (`_set_constant_widths`,Primvar(attr) 包装;该 USD 构建无 CreateWidthsPrimvar,pxr 延迟
   导入)——cfd_demo USD 测试 **129 passed**;i18n 文案修正(风冷结果场 vs overlay 的现状区分);
   vitest 3003/3003。提交 `98b26846..8f1f44e4`(feature,**未合 main**,等本 Stage 门)。

## 未决阻断:renderAck 中继回归(08:15+ 启动的 Kit 实例)

**症状**:浏览器 onCustomEvent 静默;OvViewport 4 分钟 ack 超时后自愈回退 Three.js
(提示诚实)。视频流与浏览器→Kit 命令通道始终正常。

**隔离链**(逐级实测,仪器代码已回滚):
1. Kit 侧 `_render_ack` push 执行(临时 print:`render_ack PUSH/PUSHED`)。
2. 自有总线观测器触发(`BUS OBSERVER FIRED dcdtuni:renderAck`)→ carb 总线投递正常。
3. messaging 扩展 `_on_message_to_send` 触发并 `queue_event('omni.kit.livestream.send_message')`
   ——自有 send 事件观测器触发(`SEND EVENT FIRED len=460`)→ messaging 中继入队正常。
4. **断点在 webrtc 插件出站或浏览器库对该实例的解析**(插件为 C++ .so,kit-cae 克隆无源码)。
对照:07:39 实例(代码 `8cf4ca14`)同 messaging 补丁(用户缓存 07:06 版)、同浏览器库,
中继正常(07:46/08:03 ack,含分块 progress ack)。08:15/08:25/08:31/08:47/08:53 各实例
(代码 `8f1f44e4` 或含临时仪器)均不正常。已排除:启动路径(start_server vs 手动)、
GPU 占用(FVGN 训练在旧实例存活期间也上线过)、Xvfb、信令端口、隧道进程(命令/视频同隧道正常)、
浏览器 sessionStorage(全新标签页同样失败)。verbose 日志实例已就绪过但从未获得干净单连接。

**竞争记录**:browser MCP 与用户 SC2107 TA 会话共享同一浏览器;08:40 与 08:58 两次我的标签页
被外部导航/关闭。续跑须先与用户确认浏览器空闲窗口,或用独立浏览器实例。

## 续跑 runbook(下一轮)

1. 与用户确认浏览器窗口可用;只开一个自有标签页,全程不新建多余标签。
2. 40901:`fuser -k 8301/tcp` 后**无仪器**干净重启 Kit
   (`DC_CFD_GPU=0 DC_CFD_GB300_COUNT=0 DC_CFD_AI=0 DC_CFD_SIGNAL_PORT=8301 bash scripts/start_demo.sh`,
   或经 `start_server.sh --app react --port 8395 --ov --ov-gpu 0`),等 `RTX ready`。
3. 单连接切 OV:若 ack 恢复 → 回归与 08:15+ 实例的代码差(`98b26846..8f1f44e4`,仅 widths/
   i18n/start_demo 钩子)无关,转查环境(驱动 ICD 告警、GPU 共享态);若仍断 → 用 verbose 实例
   (`DC_CFD_EXTRA_KIT_ARGS=--/log/level=verbose`,注意 start_demo.sh 钩子行须带续行反斜杠)
   抓 webrtc 插件对 send_message 的出站日志定位最后一跳。
4. 二分备选:在 40901 用 `git worktree` 检出 `8cf4ca14` 单独起 Kit 对照(同环境旧代码),
   一次实验区分"代码差"与"环境差"。
5. 过门后:合并 feature→main(含 widths/i18n/本回执),补齐 Stage 2 门槛
   (液冷/风冷双 case 几何一致截图、网格开关、ACK/revision 语义),更新计划文档。

## 现场状态(移交)

- 40901:后端 8395(case SyntheticAirDisplay,`--ov` 一体化启动器)运行中;Kit 已停
  (仪器/verbose 实例已杀,仪器代码已 `git checkout` 回滚,工作树仅余 BHUMANS 既有脏文件);
  GPU0 的 Kit 占用已释放(FVGN 训练仍共享 GPU0-3)。
- Mac:隧道 8390/8391/8301 与 8395/8396 存活;8390 后端为 Stage 1 实例(仍运行,case Shinaide)。
- 8360/8361(PID 3282138)全程未触碰。

## 补记(同夜稍后,无浏览器工作)

- **传输层分析**(node_modules 库 5.18.2 反查):onCustomEvent 由 **WebRTC data channel**
  的 onmessage 处理链触发(与命令上行同通道);stats 走同一通道。故断点进一步收窄为
  "webrtc 插件对该实例的出站写"或"插件会话绑定",浏览器库解析差异可能性低(同库同信封
  在旧实例工作)。
- **二分实验已预置**:`40901:/tmp/ov-bisect-ext/` = 8cf4ca14 扩展树(git archive,
  SetInterpolation=0 验证旧码);启动命令与判读规则见
  `docs/archive/omniverse-2026-09-10/ov_slice_streamline_design_2026-09-29.md` §6(信令 8302、GPU4、
  同 kit-cae 构建同环境,仅扩展树变量)。
- **Stage 3 设计已成文**:`docs/archive/omniverse-2026-09-10/ov_slice_streamline_design_2026-09-29.md`
  (切片主方案=VTK cutter 点彩面片独立于体渲染;切面预览圈循种子球仲裁;流线复用
  setStreamlines/setSeedSphere;主 React OV 后处理面板区块;测试与 browser-use 门清单)。

## Stage 2 过门补记(同夜后续):"ack 回归"定性为操作噪声;生产路径复验 PASS;降级模式入保险

- **根因定性**:08:15–09:5x 的"ack 不回"全部可归因于**操作噪声**——多次重启中
  `pgrep|head -1` 式 kill 泄漏僵尸 Kit,SO_REUSEPORT 使同端口多监听器并存,浏览器连接
  轮盘落到半死插件态;clean single-listener 实例(10:14 旧扩展二分实例、10:25 生产实例)
  上 ack 中继完全正常(progress ack 驱动 3612 chunks 完成 + 最终 success ack)。**非代码
  回归、非持久环境退化**。教训入 SOP:Kit 重启一律 `pgrep -x kit` 全清后单实例起,
  起前 `ss -tln | grep <port>` 确认零监听。
- **bisect 无效化自误**:首次二分提取漏 `shared/schema`(render_scene parents[7] 依赖
  仓库深度)致扩展启动失败,已修正提取深度;该轮"命令不达"是启动失败而非中继差异。
- **生产路径终验**(新扩展代码,`start_server.sh --app react --port 8390 --ov --ov-gpu 0`,
  单监听):Shinaide 液冷工程 → OV **正常 ack、无 banner**,1930 primitives 授权,
  像素 meanLum 145.7 / nonBlack 0.984,5313 帧解码。`evidence/ov-24`。
- **降级显示模式(保险,已测)**:ack 通道超时而视频存活时,OV 以"未确认重渲染"横幅
  保持显示(产品裁决 OV 仅显示),无 pacing 突发重传保证 Kit 侧授权完整;编辑实时同步/
  相机回读/结果层在该模式明示禁用;`ovAckTimeoutDegraded` 纯函数单测 3 例;
  i18n `renderer.ov_unconfirmed` 双语。本轮未在真实断链上触发(中继已恢复),
  触发路径由单测 + 此前 4 分钟超时自愈回退行为覆盖。
- **回归**:vitest 3006/3006(290 文件)、cfd_demo USD/协议测试 129、`tsc -b && vite build`
  干净(dist 1af06084→914a48be 两度部署 40901)。
- **Stage 2 门槛核对**:液冷/风冷几何一致(1930 prims vs 32 模型/800 网格快照语义、
  overlay 6 prims vs 3 patches×2)、模型树开关、网格 overlay、ACK/revision 语义、
  双向往返——全部有截图/日志/像素证据。**过门,合 main。**

## 补记:操作立方体↔OV 摄像机联动修复(用户报障,本夜修复并 browser-use 验证)

- **报障**:OV 模式下点操作立方体(view cube),Three 相机动而 OV 场景不动。
- **根因**:`applyViewPreset/animateViewPreset` 只改保留的 Three 相机;桥侧
  `updateViewState→render_scene.apply_view` 一直存在但 React 侧无人调用。
- **修复**:`viewportBus` 新增 camera-settled 通知(预设应用、预设飞行结束、orbit
  手势 end 三落点,连续 orbit 帧不转发);`RendererControl` 订阅后以
  `captureViewState`(复用快照的坐标变换)发 `updateView`;降级模式下不转发(横幅已声明)。
  `setViewportHandle` 对无 addEventListener 的假 controls 加守卫(修 ExtraSeedLocate 测试)。
- **验证**:browser-use 真机——`applyViewPreset('top')` 后 OV 呈正俯视平面图、
  `iso` 后呈三维等轴(机架/CDU 管路可辨),Three 相机向量同步(top: up=(0,0,-1));
  截图 `evidence/ov-32/ov-33`。bus 单测 6 例(预设/手势 end/句柄替换/飞行完成与中途)。
- 回归:vitest 3012/3012(291 文件)、build 干净(dist bca767a6 已部署 40901)。

## 补记 2:拖拽实时联动(用户二次报障修复)

- **报障**:离散预设已联动,但**边拖拽边联动**未生效(首版只转发离散落点)。
- **修复**:viewportBus 订阅 controls `change`(pointer 速率),120 ms 节流 + 尾沿保证,
  `end` 立即 flush;cube 拖拽经 orbitCameraByPixels→controls.update() 触发 change。
  首帧 leading edge(lastMoveEmit=-Infinity)。
- **验证**:browser-use 真机拖拽 cube——中途截图(低侧角度)≠ 拖前 ≠ 结束后(方位继续
  推进),松手后稳定;Three 相机 (-4.58,6.24,12.89)→(-8.7,5.7,5.49)。`evidence/ov-34/35`。
- 单测:节流用例(change×10 @20ms → 2..9 次 emit,非每帧;end 立即 +1)。
- 回归:vitest 3013/3013、build 干净(dist deb93dca 部署 40901)。
