# AGENTS.md「dcdtuni 项目工作约定」2026-09-28 版原文

> 归档注记（2026-10-21）：这是根目录 `AGENTS.md` 中「dcdtuni 项目工作约定」一节在 2026-10-21 整理前的原文，逐字保存。其中的机器、端口、服务、集成基线和当日任务状态是 2026-09-28 的快照（例如 8360/8361 服务、基线 `461102a0`、OV 结果来源 422 与"真实 Kit 未得到视频/ACK"），已被后续进展取代：OV 切换、scene ACK、拾取与原生后处理都已在浏览器实测。仍然有效的约定保留在 `AGENTS.md`，当前状态见 [PROJECT_STATE.md](../../developer/PROJECT_STATE.md)。文中指向 `docs/developer/archives/liquid-platform-2026-09-28/` 的清理回执现在位于 [docs/archive/liquid-platform-2026-09-28/](../liquid-platform-2026-09-28/README.md)。

# dcdtuni 项目工作约定

本文件适用于整个仓库。2026-09-28 更新；用户明确的工作路径、资源限制和任务状态优先于旧文档示例。详细构建和服务入口仍见 `.claude/CLAUDE.md`，当前进度和未验收事项见 [PROJECT_STATE.md](../../developer/PROJECT_STATE.md)。

> **2026-09-28 16:42 SGT 最新定位**：各机器的 `main` 已收敛，代码集成基线是 `461102a0`；后续文档提交可能令 tip 前移，动手前查实际 Git。网页服务状态（2026-09-28 晚更新，取代 16:42 的“已关闭/未重启”口径）：40901 的 8360/8361 React 服务已按用户指示在统一 main 上以 `src/start_server.sh --app react --port 8360` 重启（CPU0–3 亲和、CUDA 不可见、初始 case SyntheticAirDisplay）；四套 HTML 实例（8050/8052/8054/8064）的逐实例启动参数无记录，按需再逐个恢复，不盲启。40901 主工作树已切回 main；无独有代码的 `feat/liquid-dae-gpu-compile` 分支已删除。用户指定 40902 `main` 的 dcCart 为保留版本，集成后同路径源码树仍为 `b3390bb6`；弃用网格侧线已删除。旧 `paper2`/`paper3_icml` 与 A/B/C 结果放弃作为新期刊论文证据，保留原始记录供追溯。

- **远端唯一工程工作仓库**：40901 `/mnt/lv/home/litianyu/mycode/dcdtuni` 的 `main`，完成验证后推送 `origin/main` 与 `shared/main`（GitHub 为 `git@github.com:Litianyu141/dcdtuni.git`）。旧 `/mnt/lv/home/litianyu/mycode/dcdtuni-liquid-platform` 已于 2026-09-28 归档独有文件、停止旧服务并移除 Git 工作树；不得再使用或重建。清理回执见 `docs/developer/archives/liquid-platform-2026-09-28/`。
- **本地工作仓库**：`/Users/litianyu/Desktop/AgentWorkspac/dcdtuni`。项目新增源码、资料、运行输入、测试产物、日志和临时目录只能放在本地或上述远端仓库内；临时产物使用仓库内 `.codex-tmp/`，不要照旧文档示例写入 `/tmp` 或另建仓库外工作树。可从旧位置只读核查和迁移既有文件。
- **计算资源**：用户持续授权同时最多使用一张 GPU。每次运行先核对空闲设备，以 UUID 限定进程，不占用他人设备；退出时确认释放。CPU 不限于一个核心，按任务设置合理亲和与线程数。
- **研究线分工**：Physics loss → Agent 后训练已进入同一 `main` 的 `ddl/`；Pro6000 `/home/litianyu/mycode/dcdtuni` 当前检出 `main`，旧 `ai-design-eval` 分支已在合入后删除。D0 仅从保存的 `.liquidrun` 验梯度接线；**D1 才是 Agent 改 `.cfdproj` 副本 → React 编译严格 v3 `.liquidrun` → 图编译 → PyTorch Newton DAE** 的项目全链。D1 只在合成小工程对 t0 稳态初始化做单步 Qwen4B/LoRA 接线烟测，尚无 27B 或设计质量/迭代次数科研结论。代码与旧结果的身份不能混用。
- **资料与持久记忆**：Mac OneDrive 资料已移到 `NTU_stuff/Research/DCTwin/LiquidCooling-DC/`，工程代码仍在上述两个 Git 仓库。研究位置索引为 `Research/LEDGER.md`；相对链接按搬迁后的层级解析。本轮用户明确要求更新这些资料文件，允许在这些指定记忆文件中维护记录；新增代码、运行产物和临时目录仍只在 Git 仓库内。
- **任务状态（当日更新）**：液冷 DAE 的 PyTorch GPU/CPU 与 `torch.compile` 阶段代码已进入 `main`，原开发任务按用户要求临时停止。Agent 研究与 OV 结果/选择桥已在隔离分支验证后并入 `main`；原 OV 侧分支已删除。OV 结果来源 API 仍 422 fail-closed，Kit 拒绝没有独立可信来源的结果命令。真实 Kit 两次验收均未得到视频、scene ACK 或 pick；前端显示能力不能据 CPU 契约/源码存在宣称已验收。精确回执见 [集成记录](../../benchmarks/journal-liquid-ov-integration-2026-09-28.md)。GPU/CPU 使用仍按最新用户授权和运行前实测执行，不能从历史一次性 episode 推导当前许可。
- **NSCC 特例**：所有 NSCC 登录/控制节点只允许 `ls` 与文件传输；搜索、Git、Python、压缩、统计、计算等必须在 PBS 计算节点或本地进行。40901 不是 NSCC 登录节点。
- **工程真源**：交付自有 `dcdtuni` 与 `.cfdproj`，沿 `.cfdproj → React 图编译 → 严格 v3 .liquidrun → backend/liquidcooling 图编译 → PyTorch Newton DAE → 后处理` 实现。研究的硬设计得分必须从这条工程链生成；连续松弛梯度只在已编译图的张量参数上定义，不能声称 JSON/React 编译本身可微。液冷算法须用可向 GPU 迁移的 PyTorch 向量化实现。第三方 `DiffLiquidTwin` 只可只读参考，不复制或引入其源码。
- **前端与验收**：React 负责 Three.js/Omniverse 和液冷建模联动，Web 服务只用 `src/start_server.sh` 启动。现场数据、mock、MMS、风冷网格与实测验收须分开表述；有价值的源数据和验收证据提交到指定 Git 仓库，运行缓存保持在仓库内并按需清理。
