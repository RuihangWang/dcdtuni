# Stage 5 — browser-use E2E 终验 checklist(2026-09-29 凌晨,SGT)

**结论:8/8 项 PASS(其中第 6 项在修复一处 UI 下载缺陷后复验通过)。**
浏览器:Mac 端 Playwright MCP(真实 Chromium),经 SSH 隧道访问 40901 隔离服务。
登录:repo dev account(bootstrap `admin`,见 `src/server/auth_manager.py:151-163`;口令不复述)。

| # | 项 | 判定 | 证据 |
|---|---|---|---|
| 1 | 隔离端口起停 | **PASS** | 前:8390/8391 空闲(lsof 空),8360/8361 = PID 3282138(SyntheticAirDisplay,既有服务,未触碰)。起:`start_server.sh --app react --port 8390 --case-dir case/Shinaide_L`(CUDA_VISIBLE_DEVICES=""、OMP 2 线程;需显式 `CONDA_EXE`,否则 `resolve_python` 在非交互 shell 静默退出——已记录)。启动 1.6 s,HTTP 200。后:8390/8391 释放,8360/8361 仍为同一 PID。启动前 HEAD `3d86990d`,case sha `6501c68c…` |
| 2 | 登录 + 加载工程 | **PASS** | 首页 200 后写 API 401 触发登录覆盖层,admin 登录后消失;加载 `Shinaide_dae_cpu.cfdproj`:32 模型 / 800 meshes(与 09-27 回执一致)。`e2e-01/02` |
| 3 | 创建期设置面板 | **PASS** | demo 默认即 stage4 场景(600 s / 10 s / 负载阶跃 ×1.5@60 s / 供水固定绑定);MMS case 下改配 60 s / 1 s / 阶跃 ×2@30 s 全部可编辑;steady 禁用与非 DAE 隐藏由 stage3b 测试钉住。`e2e-03` |
| 4 | 运行中在线命令 pending→accepted | **PASS** | Shinaide 运行中排队 热负载 120% + 供水温度 296 K:回显"待应用:120%"与"下一步生效:m_30_hx = 296",帧历史不变;单步后变为"已接受倍率:120%"与"当前已接受:m_30_hx = 296"。热输入 37.80 kW = 21×1.2×1.5(倍率与计划阶跃相乘语义正确)。`e2e-04/05/06` |
| 5 | 曲线与 3D 着色 + MMS overlay | **PASS** | Shinaide 600 s 完成(61 帧):一次回水 25.02→27.70 °C(+2.68 °C 可见温升),残差 ≤3.4e-6 W→2.07e-10 W;后处理时间滑条 600→60 s 联动;芯片/管道温度着色开启。MMS 夹具重放(run `1c9a2c2fec36`,配置 `92c9619636`):overlay 判**可比**,UI 一次回水 17.11 °C / 二次供水 17.35 °C 与闭式解 290.2630/290.4965 K 显示精度内一致;像素探针:解析灰 #9a9a9a 4493/5958 px 与运行橙 #e7a967 共存(虚线覆盖实线,肉眼不可辨)。Shinaide 运行(dt=10 s≠夹具)overlay 判**不可比**并显示原因——诚实门两侧都验。`e2e-07/08/09/10/11/12` |
| 6 | 下载 + replay 逐值一致 + SHA 后核对 | **PASS(修复后)** | **发现缺陷**:旧 bundle 下载走 fetch→JS parse→stringify,浮点拼写被改写(100.0→100),UI 下载的 checkpoint 过不了 canonical config-hash,无法重放(修复前损坏下载件之 checkpoint 与 executed-input 已入 evidence/ 作发现证据,result.json 3.4 MB 仅以 sha256 记录;API 字节件 hash 匹配且重放 **24/24 序列逐位一致**,run `6096b36ee9af…`)。修复 `e204eddc`:下载改为服务器字节直传;复验:UI 下载 checkpoint 与 API 字节 **cmp 逐字节相同**(22451 B),hash 匹配 config_hash(v3,seq 60),并以其成功 from_checkpoint 重放(61 帧,completed,稳态值与解析一致)。SHA 后核对:Shinaide case `6501c68c…` 未变,MMS case 与 HEAD 一致,tracked 零改动 |
| 7 | 全量回归 | **PASS** | 后端 `test_liquid` 597 passed / 5 skipped + `test_hybrid_air_heat_handoff` 10 passed(40901,stage4 提交点);前端 vitest **2998/2998**(291 文件,含 stage5 两个修复的新测试)+ `tsc -b && vite build` 干净 |
| 8 | Teardown + 证据 + 提交 | **PASS** | 服务停、8390/8391 释放、8360/8361 PID 3282138 未动、Mac 隧道关闭;本目录含 13 张截图 + UI 下载 checkpoint + receipt.json;提交带 trailer;PROJECT_STATE/OneDrive 侧摘要另行更新 |

## 过程中发现并当场修复的两个缺陷

1. **下载字节保真**(`e204eddc`):见第 6 项。导出承诺"可独立复现",字节必须原样。
2. **全局快捷键 TypeError**(`6ed378d0`):document 级 keydown 处理器对 `event.key` 未设防,自动化/嵌入式组件派发的无 key 合成事件使生产 bundle 抛 "Cannot read properties of undefined (reading 'length')"。修复后同序列(加载→准备→开始→完成)**console 0 错误**。`e2e-13`

## 运行清单(run_id 全量)

| run | case/场景 | 用途 |
|---|---|---|
| `6096b36ee9af46dfb22552505250db62` | Shinaide demo 场景 + 在线 120% & 296 K | 主 E2E;replay 24/24 逐位一致 |
| `44ba07159b4e` / `4c83dde53899` | MMS 默认(误建,已取消) | UI 流程记录 |
| `1c9a2c2fec36`(配置 `92c9619636`) | MMS S1 负载阶跃 ×2@30 s | overlay 可比 + 解析一致 |
| `fa561b8cb4bb4ab5b77a26f7b330ecb1` | MMS 默认 | 修复后下载字节保真 + 重放 |
| `065448f4b85c` | MMS 默认(修复 bundle) | console 0 错误复验 |

## 边界

- 全部为合成 mock 夹具与 demo 参数,**非现场校准**;曲线对比仅在 MMS 夹具重放时成立(诚实门强制)。
- OV 渲染轨道不在本 checklist 内(Three.js 路径验收;OV 视频/ACK 仍按主线地图另行验收)。
- 首次加载时 `/api/set-case-dir` 401 是未登录预期行为(登录后正常);旧 bundle 的 1 次 TypeError 与 401 已如实记录于 receipt.json。

## 审查后追补(2026-09-29 午后,独立代码审查 019db670..4c95ed4e)

- 审查结论 **"With fixes"**(1 Important + 7 Minor);机器可读处置见 receipt.json `post_review_addendum`。
- **Important #1 已修复(本追补提交)**:原诚实门只验 DAE + 时间轴,未验**夹具身份**——任何配置为 60 s / 1 s 的 case 都能轴匹配,同夹具错幅值(×1.5≠×2)最具误导性。现加第三道门:运行自身固体温度轨迹须逐样本跟随 sha 绑定回执的 `dae_solid_K`(容差 0.02 K,远高于数值噪声、远低于场景差异),场景身份(load vs supply)随之强制。**据此,上文第 5 项"诚实门两侧均验"在回执时点应读作"时间轴两侧均验";追补后身份亦入门。**
- Minor:#2 证据位置(损坏下载件两件已入 evidence/,措辞已改)、#3 删除三个无调用者旧 fetch 函数及其孤儿测试(run-swap 守卫迁移至 fetchRuntimeArtifactText)、#4 守卫测试补齐、#5 label 类型收紧(去 `as never`)、#6 图例色板随 dash 画虚线段、#8 模块头注注明 dae_solid_K 的门控用途——均已修;**#7 经复核驳回**(smoke JSON 实含 `dT_*` 键)。
- 追补点回归:vitest **3003/3003**(290 文件)、`tsc -b && vite build` 干净、generator `--check` fresh。
