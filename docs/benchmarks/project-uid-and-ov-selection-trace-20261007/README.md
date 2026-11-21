# 工程 UID 与 OV 选择诊断 checkpoint — 2026-10-07

新建工程和历史空 UID 工程现在通过同一个安全 UID 工厂取得稳定身份。已保存的有效 UID 保留原值；历史文件在正常加载后的内存中补齐，下一次正常保存才写入。旧版迁移先完成，再补齐当前格式身份。OV 原有项目 UID、源哈希、场景版本和整对象选择授权门保持不变。

此 checkpoint 将 Root 的精确 10 个正常目标、独立回放记录、正常测试/构建/发布回执及真实 GUI 保存记录归档。选择诊断默认关闭，只用于定位真实 Shift 选择问题；这份记录没有验收 Shift 整对象选择，也没有验收 OV 背景网格的新实现。

## 正常变更与实际验证

[authorization.json](root-normal/authorization.json) 和 [application.json](root-normal/application.json) 固定每个目标的 before/after SHA，包含 6 个 UID 目标、3 个诊断生产目标和 1 个原生测试载体修正。原有测试的 16 条断言不变；载体增加提取实际诊断方法，解决假类缺失方法造成的 AttributeError。

| 范围 | 正常目标 |
|---|---|
| 安全 UID 工厂 | `src/frontend/shared/js/cfdproj_state_v4.js` |
| 迁移后安装身份、类型出口 | `src/frontend/react/src/projectStore.ts`、`src/frontend/react/src/legacy/cfdprojModules.ts` |
| UID 回归和能力测试、原始空 UID GUI 夹具 | `src/frontend/react/test/projectUid.test.ts`、`projectUidCapabilities.test.ts`、`test/fixtures/project_uid/GridNative_OneBox_GUI_20261007.cfdproj` |
| 默认关闭、严格布尔启用的选择诊断 | `src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/extension.py`、`source_selection_trace.py` |
| 固定启动 opt-in | `src/frontend/omniverse/cfd_demo/scripts/start_demo.sh` |
| 原生测试载体绑定 | `src/frontend/omniverse/cfd_demo/tests/test_source_selection.py` |

正常验证的原始 [validation.json](root-normal/validation.json) 和 [logs/](root-normal/logs/) 显示：前端 13 个 suite、129 项通过；原生选择 16 项通过；`bash -n`、TypeScript 和生产构建均返回 0，无超时。保留构建中的动态导入和大 chunk 告警，不把零返回码描述成零告警。正常应用及验证时，2248 条既有源码指纹和 main 的 29 条用户改动指纹均受保护。归档时另读 2252 条预期源码（含新目标），全部相符，见 [SOURCE_GUARDS.json](SOURCE_GUARDS.json)。这只是文件内容核对，不是 Kit 内存或浏览器输入证明。

Root 新鲜 CPU 回放分别见 [root-fresh/uid/](root-fresh/uid/) 和 [root-fresh/selection-trace/](root-fresh/selection-trace/)：UID 原始 8 项为 6 个产品失败、2 项通过，候选 104 项通过；诊断原始观测缺口返回预期 1，候选 13 项通过。诊断的原始 RED 是可观测性覆盖缺口，不能写成真实 Shift 故障的重现。所有 CPU 载体和原生实机验收口径分开。

[发布回执](root-normal/publish/receipt.json) 保留 assets 先写、index 最后原子写的顺序和旧非 index 资产保护。真实界面所加载入口为 `assets/index-BgYtDz6h.js`，SHA256 `54be045d1ea037c89ee893906276bebe54142d7f322e26c807e88c0de05c8b9e`；HTML SHA256 `072f4a0d14e982b08ba01345174d5652bdc8e091f1649a8ca00a503bf3cf1604`。不归档 dist 或 node_modules。

## 真实 GUI 的身份生命周期

这些操作由 Root 在 Windows 本机的内置浏览器对 40901 的 React 调试端口 8396 完成。归档者只读保存的证据，没有追加浏览器操作。

| 真实 GUI 操作 | 保存的身份与字节结果 |
|---|---|
| 新工程、默认一个 BOX、正常保存 | [工程](root-normal/browser/GridNative_OneBox_UID_GUI_20261007.cfdproj)：8426 B，UID `proj_7abe0219-8925-409e-8a25-9ff1a276dbd7`，SHA256 `1972401aa0970a54877a5a0ac180ac39456084b573d6a000bda573bbd9fd8c66` |
| 加载真实历史空 UID 文件，再正常保存到独立副本 | [修复副本](root-normal/browser/GridNative_OneBox_Repaired_UID_GUI_20261007.cfdproj)：8426 B，UID `proj_5d881f11-3549-49eb-9647-b47eac82498b`，SHA256 `fdebf79709704f288bf35d8490222564da0d09f79af373bd6449c269aa880093`；只有 UID 改变，原文件字节保持 |
| 从磁盘重开该修复副本，再正常另存 | [重开副本](root-normal/browser/GridNative_OneBox_Reopened_UID_GUI_20261007.cfdproj)：全部字节、UID 和 SHA256 与修复副本相同，见 [稳定性回执](root-normal/browser/reopened-gui-uid-stable.json) |

原始空 UID 文件 8385 B、SHA256 `e1517b2c5120c20a4b9cb9908336e8de1dbded3339d5f110fcb3f55451727a54`，作为精确夹具保存在 [作者包](author/uid-f2/candidate/src/frontend/react/test/fixtures/project_uid/GridNative_OneBox_GUI_20261007.cfdproj)。重开仍为空 UID 的原始旧文件会再生成一个内存身份，直到正常保存；本修复没有自动改盘或把无身份旧结果重新授权。

Root 的新 UID 一个 BOX 真实 OV 切换得到显示；[实际界面截图](root-normal/browser/actual-new-uid-onebox-ov-no-visible-grid-20261007.png) SHA256 `f8f812ebfd887a8dfc2372643f6190cb7edc3c6c56f27b228df75b5fa6310345` 留下灰色背景、不可见网格和未确认横幅。该截图来自诊断重启前的 Kit，会话没有被冒充为重启后的验证。这是 GUI 身份与切换观察，不能证明原生 Shift、每个面选中、背景网格、布局/投影或编码像素一致性。

## 诊断启动现场与未验收边界

Root 只停止自己负责的旧 8396 进程，保护 main 的 8390 四个角色。第一次启动遗漏隔离工作树借用主仓 Kit 的 asset/build 路径，正常 preflight 拒绝缺失 wrapper；[首次失败日志](root-normal/restart/start-server.log) 和 [说明](root-normal/restart/attempt2/preceding-launch-failure.md) 原样保留。这不是首跑成功。

受控重试显式恢复已有 Kit 路径，正常 `src/start_server.sh --app react --port 8396 --ws-port 8397 --ov --gpu 0 --ov-port 8302 --ov-stream-port 48000` 启动。固定环境 token `DC_CFD_SOURCE_SELECTION_TRACE=bounded-picks-v1` 对应唯一固定参数 `--/exts/dc.cfd_bridge/sourceSelectionTrace/enabled=true`，不能任意转发 shell 参数。[ready.json](root-normal/restart/attempt2/ready.json) 保存新 launcher `3817050`、supervisor `3817070`、backend `3817105`、Kit `3817224` 的 PID/start/cwd/argv，实际 GPU UUID 为 GPU0 `GPU-dd97ad37-4a36-19df-d789-0ee503ba4d93`。原 main 四角色仍在。准备就绪口径是 signaling，ready 回执当时仍明确标记重启后的真实 live video 待验。

归档捕获 [真实点击前的 Kit 诊断前缀](root-normal/browser/post-restart-before-native-click-kit.txt)：场景 author 和查询/树选择回显已出现，诊断载荷始终声明 `gestureModifierProof=false`、`transportDeliveryProof=false`。这不证明鼠标命中、Shift 输入、消息到达或同一编码帧。原生全局修饰键是回调/查询时的只读样本，不是手势时刻证明。日志的速率和总量上限意味着缺日志也不等于没有回调。不得用局部异步 framebuffer、源代码或 USD 对象存在替代可见像素证明。

本 checkpoint 仍未验收真实 Shift+单击 CDU/BOX 任意面后的完整 object 高亮，未应用 native grid 候选，未验收背景网格显示/步长切换，OV 视口下沿黑带亦仍未收口。后续真实手势和画面验证可以作为新证据追加，需重新生成外层白名单与 manifest；原作者 manifest 和冻结载荷不得改写。液冷科学 MMS/AD 与整场风冷 mesh/CFD 验收不属于此 checkpoint。

## 按值归档与复核

| 不可改写的作者包 | payload 数 / B（不含各自 manifest） | 原 manifest SHA256 |
|---|---:|---|
| [UID F2](author/uid-f2/MANIFEST.json) | 52 / 883769 | `49098769072c02df767d198a1c0f383ab06de31743a8c2be044c85decfcd1674` |
| [UID 边界补充自审](author/uid-boundary-audit/MANIFEST.json) | 26 / 470539 | `a256a34b67a0207ca3b6f45d933e936467258b376f6d4bbba58a5895fcea49ec` |
| [诊断启动](author/selection-trace-launch/MANIFEST.json) | 32 / 221743 | `3749234dcd5e1d7049f3627913a6d5a8d0f01cdddc10f9835593d319d405d1e0` |
| [诊断原始 REPRO](author/selection-trace-original/MANIFEST.json) | 21 / 192321 | `b84a0d501cc51af0a475724147bec7b64f9c6a8885786417c77d1e489747d95e` |

边界补充自审的两项测试覆盖实际非零液冷 playhead 与热输入变化；最早不一致的受控瞬态模式输入和失败记录一起保留。它不是独立科学审查，也没有运行 DAE。四个包复制前后均完整重哈希，作者 manifest 全部原字节保留。

[COPY_RECEIPT.json](COPY_RECEIPT.json) 列出 199 个按值复制文件，共 2816893 B；运行中的 launcher 日志是明确标记的有界字节快照。Root REPRO 只取实际结果/原始日志，不复制整个无关源码树、缓存、临时目录或 dist。作者 REPRO 的固定服务器依赖与旧源码保护条件原样保留，不能声称这些历史 CPU 回放在已升级的正常源码上无需准备即可再跑；不要为了重放改写正常源码。诊断原始包的按值 REPRO 与测试资产完整保留。

外层 [MANIFEST.json](MANIFEST.json) 包含 204 个 payload、2948177 B；目录实际 205 个文件。它只排除本目录的外层 `MANIFEST.json` 自身，嵌套作者 manifest 也作为 payload 纳入。新增路径白名单见 [ADDITION_WHITELIST.json](ADDITION_WHITELIST.json)。执行 `python VERIFY_ARCHIVE.py` 只读校验文件集合、全部字节 SHA、每个作者 manifest 和新增路径白名单；不跑测试、不建目录、不操作服务或 Git。

归档者只创建此新 benchmark 子树；正常 10 目标保持未提交，现有 handoff、Git/staging、服务和浏览器由 Root 负责。
