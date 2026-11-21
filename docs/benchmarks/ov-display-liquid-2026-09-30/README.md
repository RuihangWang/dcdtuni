# OV 显示战役 Stage 4 回执(2026-09-30)——液冷结果着色(全部件)+ 服务器签发血缘 + 可微 DAE 接入

**结论:过门。** 可微 DAE 液冷结果在 OV 中由 Kit 自行着色,覆盖管道、机柜供/回水 manifold、三通、流量控制阀、CDU 与服务器;
浏览器只转交服务器签发的票据与帧号,颜色由 Kit 从已验签、已复核哈希的结果文件计算。结果滑条毫秒级驱动帧,不重传场景。
DAE 实验在 `Shinaide_liquid.cfdproj` 上浏览器实跑:运行中 OV 随新帧重签、完成后全时域滑条联动。反例(服务器与 Kit 两侧)全部实机拒绝。

设计:`docs/archive/omniverse-2026-09-10/ov_liquid_result_design_2026-09-30.md`。提交:`9ed35203`、`272d3978`、`f03e9ced`、`7aff1e3e`、`46cebcea`。

环境:40901,`src/start_server.sh --app react --port 8390 --ov --ov-gpu 0 --case-dir <repo>/case/Shinaide_Liquid`,
Kit 单实例单监听 8301、仅 GPU0(其他会话训练占 GPU1–6);Mac 隧道 8390/8391/8301;8360/8361 全程未触碰。

## 诚实边界

- **DAE 参数为 mock**:实验入口是液冷求解页「采用 DAE mock 参数」→「准备实验」→「开始」(不是旧的「▶ 运行液冷求解」hybrid v1 路径)。
  mock 采纳只在内存中修改工程,**未保存回 `Shinaide_liquid.cfdproj`**;面板标注 "Not NV calibration"。所有温度只用于验证显示与血缘链路,不是物理结论。
- **「工程在该次求解后已修改」**:当页面载入的是磁盘工程(无 mock 参数)而结果来自 mock 采纳后的内存工程时,Kit 比对
  `projectContentSha256` 与已确认场景哈希不等,照常显示但标注——这正是血缘语义,不是缺陷。同一页面内采纳 mock 后直接实验,则不出现该标注(已实测)。
- 冷板、泵、换热器在图中只是边,没有网格,不单独着色。
- 实跑中脚本在热负载命令后 0.8 s 又排队了供水温度 303 K,被运行时客户端的"单请求在途"规则拒绝(`A liquid request is already in progress`),
  未重试;因此本次只有热负载阶跃生效(`primary_inlet_K` 全程 293.15 K)。这是取证脚本的操作节奏问题,不是面板缺陷。

## 过门证据

| 项 | 结果 | 证据 |
|---|---|---|
| 全部件着色 | 2143 图元场景一次同步;Kit 着色 **1167 个图元**,部件 manifold 28 · cdu 1 · pipe 2 · tee 14 · valve 14 · server 56,**无未匹配部件** | `s4-02`、`s4-05`、`s4-06` |
| 载入时延 | 场景空闲时载入 2.4–5.9 s(Kit 读文件 + 复算 sha256 + 绑定) | 面板 ack |
| 滑条联动 | 每帧 0.43–0.75 s(ack 往返),不重传场景 | 面板 ack |
| 固定色标 | 全时域色域(网络/芯片两套);run `6096b36e`(t 0→600 s)像素变化 3.31%;实跑 run `9c4eb1ce`(t 0→180 s,120% 热负载阶跃)全帧 0.37%(着色部件在总览视角中占比小),右侧机柜服务器由浅粉变深品红 | `s4-03/04`、`s4-09/10` |
| DAE 完成结果 | run `690d1918`:稳态起点、无输入扰动,181 个时刻逐值完全相同(结果文件实测 max\|ΔT\|=0),各帧色域一致——正确而非卡帧 | `s4-05/06` |
| **运行中跟随** | run `9c4eb1ce` 在 OV 着色开启状态下开始:面板「跟随运行中的实验」,样本 1→181 递增,每次增长重签并绘最新帧(468 s 内服务器 91 次结果重读、Kit 88 次载入,≈5.1 s 一次,与 5 s 节流一致);120% 热负载阶跃于 t=19 s 被接受(结果 `applied_inputs` 实测)后,芯片色域上限 338.3→344.2 K、水路 325.9→329.2 K;完成后状态转 `completed · 181` | `s4-07`(整页,含监视器阶跃曲线)、`s4-08` |

## 血缘反例(实机)

服务器 `POST /api/ov/liquid-result-grant`:

| 输入 | 结果 |
|---|---|
| `../../etc/passwd`、`run-<hex>`(路径形态) | 400 `run_id_invalid` |
| 不存在的 32-hex | 404 `liquid_run_not_found` |
| 旧结果无对象绑定(`run-30817deb` completed、`run-0102313c` cancelled) | **422 `liquid_result_unbound`**,列出缺项 `object_bindings, cell_edge_ids, rack_summaries` |
| 有效 DAE run | 200,签发 `kind=liquidResult` 票据 |

空气结果层 `/api/pp/render-source` 未改动:过修订守卫后无条件 422(缺 lineage/质量/运行/量纲回执;无修订头时 428)。

Kit(经页面实际 OV 客户端发出):篡改票据载荷 / 伪造签名 / 替换路径 → 均 `display grant signature does not verify`;
越界帧 `sample 9999 outside 0..180`;倒置色域 `network domain must be finite and ascending`。反例之后有效帧照常绘制 1167 图元——可信上下文未被破坏。
"结果在签票后被改写"与"结果属于其他工程"两条由 `bind_result` 纯函数测试覆盖(运行中跟随期间未自然触发:Kit 在签票后毫秒级读文件,步间隔 1–2 s)。

## 过门途中修复的缺陷

1. **切换运行不重签**(`46cebcea`):只有样本数增加才重签,换到样本更少的新运行时 Kit 仍停在旧运行。改为运行身份变化或样本增长都重签。
2. **运行中不跟随**(`46cebcea`):DAE 实验帧只进运行监视器(节点/边),OV 读取的结果状态不更新。所显示结果即实验运行时,节流(≤1 次/5 s)重读其结果文件;「显示运行中的实验」按钮显式采纳,绝不静默切换。
3. **着色载入被场景重传饿死**(`46cebcea`,浏览器实测复现):结果变化触发约 45 MB 场景重传(OV 着色尚未接管时结果色仍烘焙进快照),紧随其后的 `loadLiquidResult` ack 60 s 超时。`LatestSceneSync.whenIdle()` 经 `ovSession.sceneIdle` 发布,载入先等场景空闲并提示;修复后同一操作序列正常载入。
4. **陈旧滑条下标**(`46cebcea`):采纳另一运行时滑条回到最新帧(与结果面板同规则),避免下标越过更短的运行。
5. 此前切片已修:着色开关翻转改变同步键形状导致整场景重传(`f03e9ced`,冻结结果分量);按序号定位面板 fieldset(`272d3978`)。

## 未决 / 如实登记

- 结果变化在 OV 着色**未开启**时仍会整场景重传(约 80–90 s/次,重载后曾连续触发 3 次);开启 Kit 着色后同步键冻结,不再重传。
  若要消除,需要让 OV 模式下结果色完全不进快照(Stage 5 或后续评估)。
- Three.js 与 OV 的逐值一致性由共享夹具契约测试保证(`liquid_result_fixture.json` / `liquid_colour_expected.json`,Python 与 TS 双向);
  实机只比对了 Kit ack 数值与面板,未做两个渲染器的逐像素对照(视角、光照与材质不同,像素不可比)。
- OV 后处理面板遮挡视口左部(Stage 3 已登记)。

## 回归

- vitest 296 files / **3047 tests** 全过,`tsc -b` 干净,`vite build` 成功(Mac)。
- 40901:cfd_demo pytest **171 passed**;server OV/液冷 **14 passed**;DAE experiment/runtime/live-actuator **35 passed**。

## 复现

```bash
# 40901
CONDA_EXE=$HOME/miniconda3/bin/conda PATH=$HOME/miniconda3/bin:$PATH CUDA_VISIBLE_DEVICES="" OMP_NUM_THREADS=2 \
  src/start_server.sh --app react --port 8390 --ov --ov-gpu 0 --case-dir <repo>/case/Shinaide_Liquid
```

浏览器:加载 `Shinaide_liquid.cfdproj` → 液冷模拟 → 液冷求解「采用 DAE mock 参数」→「准备实验」(不要保存工程)→ 后处理页 →
渲染器切 Omniverse → OV 面板「在 OV 中按结果着色」→ 液冷求解「开始」(1×)→ 可选:负载倍率 120%(在途命令完成后再发下一条)。
服务器日志可见跟随节拍:`GET /api/liquid/results?run=run-<id>` 每 5 s 一次。
