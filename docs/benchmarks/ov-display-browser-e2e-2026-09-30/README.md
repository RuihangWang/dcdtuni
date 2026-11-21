# OV 显示战役 Stage 5 回执(2026-09-30)——浏览器 E2E 终验

**结论:过门。** 风冷(Base_coldAisleIso)与液冷(Shinaide_Liquid)两条 OV 显示链在真实浏览器里走通:切换、场景 ack、体渲染、
表面切片与视口拖拽闭环、流线与种子球拖拽重积分、液冷全部件着色与滑条、断线重连(OV→Three.js→OV);GPU 与端口释放有证据。
终验途中发现并修复 5 个真实缺陷(见文末),3 项如实登记为未决。机读回执 `receipt.json`,风冷探针原始输出 `receipt-air.json`。

环境:40901,`src/start_server.sh --app react --port 8390 --ov --ov-gpu 0 --case-dir <repo>/case/Base_coldAisleIso`,
Kit 单实例、8301 单监听、仅 GPU0(其余 GPU 为其他会话的训练);代码 `20d26a32`(Kit/服务器),React 构建 `d60778c6`。
风冷腿用 40901 Xvfb 上的有头 Chromium(Playwright,登录后操作,凭据运行时读取、不落盘);液冷腿用内置浏览器窗格经 Mac 隧道。
8360/8361 全程未触碰。

## CHECKLIST

| # | 项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 起服前后端口 / 单监听 | 起服后 8390/8391 + 8301 各一个监听、Kit 进程 1 个;Kit 每次重启前全杀并确认零监听 | `receipt.json` `single_listener` |
| 2 | 登录 | 风冷腿 `/api/auth/login` 后操作 | 探针 |
| 3 | 顶栏切换 + 场景 ack(风冷) | 19.4 s 确认(非降级) | `e-a01` |
| 4 | 结果数据集 | `2026-06-19-02-02-27 @ t=60` 经服务器 foamToEnsight + 签发票据载入 | `e-a01` |
| 5 | 体渲染 T / \|U\| | T ack 7.1 s、色标 288.1–295.3 K;\|U\| ack 3.6 s、0.0353–5.11 m/s | `e-a02-volume-T/U` |
| 6 | 表面切片 + 预览圈拖拽闭环 | 4832 三角 → 视口拖 gizmo → 回读:原点 y 1.425→2.592,已采用并重切 4724 三角;Kit `slice from viewport -> 2.592` | `e-a03/04/05` |
| 7 | 流线 + 种子球拖拽重积分 | 48951 点 → 拖种子球 → 重积分 42957 点;Kit `seed from viewport -> 2.592` | `e-a06/07/08` |
| 8 | 流线着色 | 色域在 USD 与 Fabric 中一致(288.15–295.36 K,p1..max);\|U\| 流线全色谱(`e-a10`);T 流线大部分在室内 ~295 K 暖端,符合数据 | `e-a08`、`e-a10` |
| 9 | 断线重连(风冷) | OV→Three.js→OV 16.5 s 重新确认,非降级 | `e-a09` |
| 10 | 换工程清场 | 风冷结果显示(流线/切片/体/数据集)在载入另一工程场景时被清除;修复前流线叠在液冷机柜上 | `e-l03`(修复前)、`e-l01`(修复后) |
| 11 | 场景 ack(液冷) | Shinaide 2143 图元 91.2 s 确认 | `e-l01` |
| 12 | 液冷着色 | 4.0 s 载入,1167 图元(manifold 28 · cdu 1 · pipe 2 · tee 14 · valve 14 · server 56),零未匹配;固定色标 水路 308.7–329.2 K / 芯片 313.2–344.2 K | Stage 4 回执 + 本表 |
| 13 | 滑条联动 | t=0/90/180 s 帧 1.17/0.85/0.76 s | `receipt.json` |
| 14 | 液冷自发光 | 着色部件 emissive=0.35×色,板下管路透过架空地板可见为色带 | `e-l02` |
| 15 | 断线重连(液冷) | OV→Three.js→OV 101.3 s 重新确认(整场景重传),非降级 | `receipt.json` |
| 16 | GPU 占用与释放 | 运行中 GPU0 仅 Kit 4171 MiB;teardown 后 GPU0 1 MiB、0 个进程 | `receipt.json` `gpu` |
| 17 | 回归 | vitest 302 files / 3092 tests、`tsc -b` 干净、`vite build` 成功;40901 cfd_demo 174、server 61、DAE experiment/runtime/actuator 35 | — |
| 18 | teardown | 停 8390/8391 服务 + Kit + Stage 2 遗留 8395/8396;之后 5 个端口零监听、Kit 0 个 | `receipt.json` `teardown` |

## 终验途中修复的缺陷

1. **Kit ack 丢失使整次传输失败**(`1038a948`):WebRTC 插件 `NVST_R_BUSY` 突发时 Kit→浏览器消息丢失(40901 实测一次会话开始连续 16 帧)。
   进度查询改为 12 s 短超时、最多 4 次重问;Kit 记录最后编写完成的场景请求号并由幂等 `getViewState` 回报,场景 ack 丢失时由浏览器查询恢复。
2. **液冷结果在场时返回 Three.js 页面崩溃**(`d60778c6`):ack 经 carb 事件载荷,空 Python 列表变 `{}`、None 字段被丢;
   `selectedUids {}` 进入树的多选导致 "r is not iterable";无结果时间时则相机交接被静默跳过。协议层统一规范化 view。
3. **流线恒为品红**(`83388f47`、`f8a05a3f`):kit-cae 创建时色域 (0,-1) 让 basic.mdl 关闭着色;改在首次追踪前钉色域,
   并把上限扩到数据最大值(按单元计数的 p99 在加密网格上偏冷区)。|U| 流线实机全色谱,Kit 日志 USD/Fabric 色域一致。
4. **风冷结果显示跨工程残留**(`20d26a32`):冷通道流线叠在 Shinaide 机柜上;载入另一工程场景时 Kit 清除体/切片/流线并卸下数据集引用。
5. **OV 后处理面板遮挡视口左部**(`1038a948`):改为贴右。

另:排查中一度以为"返回 Three.js 后信令连接不释放、阻塞其他客户端",复核证明是测试脚本改下拉框未进入 React 状态;
真实返回会调用 `terminate(false)` 并释放 Kit 连接——未作任何改动,如实撤回。

## 未决 / 如实登记

- **架空地板遮挡板下管路**:0.37 不透明度地板在 RTX 中呈磨砂玻璃,板下管路只剩模糊色带;自发光改善可读性但不清晰,
  模型树隐藏地板后清晰(PBR 回执 `p-03`)。候选:OV 面板提供"透视架空地板"开关(Kit 端即时隐藏,不重传)。
- **场景同步固定开销**:Shinaide 每次同步约"末段 ~50 s + Kit 编写 ~65 s";纹理跨同步缓存与编写剖析待做。
- 风冷探针的 gizmo 颜色识别在切面半透明覆盖下失效,本次显式传入枢轴坐标(截图可核对位置)。

## 复现

```bash
# 40901
CONDA_EXE=$HOME/miniconda3/bin/conda PATH=$HOME/miniconda3/bin:$PATH CUDA_VISIBLE_DEVICES="" OMP_NUM_THREADS=2 \
  src/start_server.sh --app react --port 8390 --ov --ov-gpu 0 --case-dir <repo>/case/Base_coldAisleIso
# 风冷腿(Xvfb :99,有头 Chromium;临时脚本不入库):
DISPLAY=:99 python .codex-tmp/ov-stage5/e2e_air_probe.py <out_dir> 800 443 800 443
```

液冷腿:浏览器经隧道(8390/8391/8301)加载 `case/Shinaide_Liquid/Shinaide_liquid.cfdproj` → 液冷模拟 → 后处理 → 渲染器 Omniverse →
OV 面板"在 OV 中按结果着色" → 固定色标 → 拖结果时刻滑条。
