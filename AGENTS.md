<!-- 同步文件：本文件在 Research 卡 `Research/DCTwin/` 与仓库根（见 §1 项目地图）各一份，逐字一致；改任一处，同一轮同步另一处并核对 md5。下面的全局块由 Mac `Research/_tools/sync_guides.py` 写入，不要手改。 -->
# DCTwin（仓库 dcdtuni）— Agent 工作指南

> DCTwin 用 LLM 多智能体把平面图、设备表和自然语言需求转成能收敛的数据中心 CFD 算例，几何与数值交给确定性的设计工具 dcdtuni（本仓库）：风冷走 OpenFOAM，液冷是自有的可微 PyTorch Newton DAE，前端是 React 编辑器与 Omniverse 显示桥。会议版已被 AIFLUIDs 2026 录用并报告。现阶段：40901 `main` 上持续做工程（风冷、液冷、风液混合、显示），同一仓库在 Pro 6000 的研究分支上作为第 4 篇 ISPO 的数据中心设计工具，并承载 NVIDIA PEK02 液冷合作。细节见 `LEDGER.md`。

<!-- GLOBAL:BEGIN —— 本块由 Mac Research/CLAUDE.md 统一维护，经 Research/_tools/sync_guides.py 原样写入各项目 AGENTS.md；不要在副本里手改。块内不写 IP、用户名与 ssh 别名，它们只在不进 git 的机器级全局文件里。 -->
## 全局信息（所有研究项目共用；全局版本 2026-10-26）

### G1 研究项目由哪几部分组成

- 所有者 Li Tianyu，NTU AI-X（IGP）博士生。日期时间一律按新加坡时间（SGT，UTC+8）。
- 每个研究项目由下面几部分组成，各部分的具体位置写在该项目 `AGENTS.md` 的「项目地图」里：
  1. **Research 卡**：Mac 上 OneDrive 里的 `NTU_stuff/Research/<项目>/`（完整路径见机器级全局文件），没有 git。放构思、推导、计划、决策、文献与现状页 `README.md`；不放代码、实验产物和权威稿件。`Research/` 根下的 `CLAUDE.md` 是全局规则与连接细节的真源，`LEDGER.md` 是跨项目现状总索引。
  2. **服务器 git 仓库**：NTU 服务器上 `~/mycode/<仓库>`，所有仓库都放在 `~/mycode/` 下。源代码、case、实验记录与产物、稿件都由 git 管理（大文件按仓库规则 gitignore），这是项目的权威副本；实验主要在 NTU 服务器上做。同一仓库可能在几台机器上各有 checkout，分支与 HEAD 不自动同步。
  3. **Mac 本地 clone**：`~/Desktop/AgentWorkspac/<仓库>`。目录名就是 `AgentWorkspac`，末尾没有 e。用于本地编译、查看与前端调试，不跑正式实验，用前先 `git pull`。
  4. **JUPITER 副本**（部分项目）：见 G3。
- 每个项目有三份**同步文件**，在 Research 卡与仓库根各一份、逐字一致：`AGENTS.md`（规则全文，Codex 直接读）、`CLAUDE.md`（只导入 `AGENTS.md`，Claude Code 读）、`LEDGER.md`（项目账本：现状 + 带日期的历史）。指南里只写现行规则，历史与流水账一律进 `LEDGER.md`。

### G2 位置自检：每次开工先做

1. 运行 `hostname; pwd; git rev-parse --show-toplevel 2>/dev/null; git branch --show-current 2>/dev/null`。
2. 对照本项目「项目地图」，判断你在哪一份：Research 卡、某台服务器上的主副本或其他副本、Mac clone，还是 JUPITER。主副本以外的 checkout 可能落后，动手前先 `git fetch`，再看 `git status -sb`。
3. 按地图里那一行写的职责工作。所有者让你去另一台机器干活时，按 G3 与本机的机器级全局文件连过去。不要把当前目录默认当作权威副本，也不要在 Research 卡里写代码或跑实验。

### G3 机器

| 机器 | 硬件 | 主要用途（2026-10-26） |
|---|---|---|
| **40901** | 8× RTX 4090 24 GB；有 sudo | dcdtuni 工程主仓（`main`）、NeuralNetworkPerformancePrediction 主副本（10-21 起）、MeshGenerator、NeurIPS 竞赛仓库；CVPR2027 辅助；SCANO 的历史产物（09-30 起让给同学，不在这里跑 SCANO） |
| **40902** | 7× RTX 4090（10-17 原 GPU3 硬件隔离，编号重排；按 UUID 选卡，多卡任务最多 7 个进程）；有 sudo | CVPR2027 主力；Gen-FVGN-3D 主副本（10-22 起）；NeuralNetworkPerformancePrediction 的备用 GPU 与 10-21 前的产物；dcdtuni 的网格保留版本与隔离复验；SCANO 克隆（只用批准的卡，重排后编号见 SCANO 指南）；第三方 DiffLiquidTwin 只读、禁止引入 |
| **Pro 6000** | 2× RTX PRO 6000 Blackwell 96 GB；与其他用户共用 | LearnedPreConditionNewtonSolver（JFNK 的 NTU 侧机器，10-20 起工作树未更新，开发在 JUPITER）；ISPO-AgentForDesign；AirCraftTwin；dcdtuni 的 ISPO 研究分支；SCANO 的 NTU 侧机器（10-08 起闲置，SCANO 实验在 JUPITER） |
| **DGX V100-2** | 4× V100 32 GB | Gen-FVGN-3D `dev/turbulence` 镜像与 `wt-*` worktree；NeurIPS 镜像 |
| **A100**（阿里云，公网） | 1× A100 80 GB；无 X11；登录用户名与其他机器不同 | Gen-FVGN-3D `dev/turbulence` 最新副本 |
| **JUPITER**（德国 JSC 超算，GH200） | 与合作者 Chengyun Wang（TUM，Thuerey 组）合作使用 | SCANO 与 JFNK 的实际开发与实验所在（10 月起），NeuralNetworkPerformancePrediction 也在这里做大规模实验。与 NTU 内网机器、Mac 互不可达：代码、结果与稿件经 GitHub 同步，个别项目经公网的 A100 中转大文件（见该项目指南）。要更新 JUPITER 上的仓库，由所有者联系 Chengyun。JUPITER 上的 agent 只经 git 交接，不尝试连接 NTU 内网机器 |

- 次要机器：DGX Spark 1/2 部署本地 LLM（SGLang），不做研究实验。NSCC（ASPIRE 2A）只能密码登录，**登录/控制节点只允许 `ls` 和数据传输**，搜索、统计、`du`、Git、Python、编译、训练一律走 PBS 计算节点（批处理 `qsub`，交互 `qsub -I`）；这条要转告每个子代理。
- **怎么连**：IP、登录用户名、ssh 别名与跳转方式写在不进 git 的机器级全局文件里，agent 启动时会自动读到。Mac 上是 `Research/CLAUDE.md` 与 `~/Desktop/AgentWorkspac/CLAUDE.md`；每台服务器上是 `~/mycode/CLAUDE.md`；Codex 读 `~/.codex/AGENTS.md`。只用密钥登录（NSCC 除外）。服务器之间多数可以直接 ssh，不通时经 Mac 中转。
- 共享机器纪律：启动前查 GPU 与 CPU 占用，按 UUID 选卡，限制线程数；不杀、不抢他人进程；不重复启动仍在运行的实验。运行状态以实际进程、日志与 status 文件为准，提交作业不等于完成实验。

### G4 共同约定

- **写入边界**：新文件、临时文件、缓存与运行产物只写在对应仓库内（临时文件放仓库内被 gitignore 的临时目录，多数仓库是根 `_tmp/`，以项目指南为准；没有忽略规则就先补上），不写系统临时目录、家目录或别的仓库。仓库外已有的数据只读，经仓库内被 gitignore 的符号链接引用。
- **git**：本地提交可审阅的改动；**push 须所有者授权**。改了同步文件（`AGENTS.md`、`CLAUDE.md`、`LEDGER.md`）之后，同一轮同步到 Research 卡（或从 Research 卡同步到仓库），并核对 md5。全局块只在 Mac `Research/CLAUDE.md` 改，再用 `Research/_tools/sync_guides.py` 分发；在 JUPITER 或服务器上发现全局信息过时，就在项目 `LEDGER.md` 记一条，请所有者在 Mac 端更新。
- **文档分工**：Research 卡写构思、推导、假设、计划、决策与现状；仓库 `README.md` 写安装、运行与测试，实现记录与实验记录写在仓库 `docs/`；带日期的过程记录写项目 `LEDGER.md` 或仓库自己的工作日志。状态变化同日更新项目 `LEDGER.md` 的现状节、Research 卡 `README.md`，以及 `Research/LEDGER.md` 里对应的那一块。
- **证据**：数字必须能追溯到原始产物（summary、日志、commit）。"代码存在""测试通过""作业已提交"都不等于结果成立。子代理与自动审计的结论只当线索，关键数字回到一手来源核对。
- **命名与保密**：对外一律用 SCANO（Drag&Sim 只是内部代号）。项目卡标为保密的项目，其材料不进入公开材料、开源仓库或外部总结；同步文件里也不写入其他项目的保密信息。

### G5 研究与写作精神（所有者原话，逐字；适用于研究、实验设计、日常判断、汇报与写作）

一、写作精神（所有者原话，逐字）  
多用定性的语言描述可以，只有特别必要的时候才在正文引用具体的数字，或者倍率说明，这个行为要特比特别小心  
我们的长处保留， 难以说明有冲突的地方删除， 我们的短处这予以平缓的语言陈述即可。  
补充两条：  
不要为了规避审稿风险，把贡献缩成窄技术点。  
每个主张都要落到一个实验上。缺实验就列为"待补实验"去完成，而不是删掉主张。  
二、不苛刻、不自我否定、不钻牛角尖（所有者 09-30）  
我说了不要太苛刻和自负，请你加入规则和记忆，我们要的是漂亮有效的完整故事，在文章内部闭合，不要专牛角尖  
这一条也管平时的进展判断、汇报和实验设计：  
目标是一个漂亮、有效、在文章内部闭合的完整故事。  
汇报先讲哪些已经有效、在故事里的位置；还缺的，用一句话写成"下一步补哪个实验"。  
已定的协议不再反复质疑，例如基线按各自原生的耦合方式运行、采用 τ*。  
不为假想的审稿攻击临时加对照臂，只加能让故事更完整、更强的实验。  
底线不变：数字真实，能追溯到原始 summary。  
三、R29：不利结果先查原因、修好再写（所有者 10-07）  
禁止对不利于我们论文的结果给一句一句"论文交代"就完事……对于不利于我们的论文主张的结果，都应该详细调研具体的原因然后去修复，禁止直接就"承认"我们的方法/思路是差劲的  
范围： 误差偏大、响应偏弱、消融打平或反向、训练不稳、某个数据集上失效，都算。  
先查原因： 可能是数据或特征缺陷、实现 bug、训练与协议问题，或方法的真实边界。要给出证据：代码行、数据、零训练检查、扰动或消融。  
能修的先修： 修好之后重训、重评、独立复算，再写。  
只有确认是真实边界且无法修复，才写进论文： 要写清原因和适用范围。"短处予以平缓的语言陈述"只适用于这种情况。  
汇报与笔记也一样： 先写"原因与修法"。  
判据在看结果之前登记，不为迁就结果改判据，不挑 seed，不删运行。  
补充（所有者 10-12，逐字）：维持我的研究精神： 不卑不亢，遇到与我们假设和预期的结果不符的情况，不能直接承认报错然后退让idea，应该坚持先探索调研，是不是代码写错了，或者有地方实验不公平不合理，最后才是考虑理论有缺陷需要修补。而修补理论的前提一定是通读全文之后理清脉络，然后在做决定  
执行顺序（非原话）：① 代码与实现（bug、函数形式、数据管线）→ ② 实验是否公平合理（对照是否只差一个变量、归一化与预算是否一致、基线是否按其原生方式运行）→ ③ 才考虑理论缺陷；修补理论前先通读全文、理清前后逻辑，再决定改什么。  
四、学术用语（所有者 10-12，逐字）  
论文写作的时候请你使用专业的学术用语，禁止使用类似什么"部署头" "头" "臂""拉偏" 等等这样的metaphor或者叫"黑话"。 禁止禁止！！  
范围（执行说明，非原话）：正文、补充材料、图表的标题、坐标轴与图例都适用；实验内部代号（如 `SF_fs`、W2、arm）只留在代码与研究记录里，论文中改用描述性名称（"training configuration"、"initialisation" 等）；给所有者的中文汇报同样不用"臂""头""拉偏"之类说法。  
五、新增实验须与论文逻辑相关（所有者 10-12，逐字）  
每多一个实验都需要完整理解论文的前后逻辑关系，要合理的有关的才加  
执行说明（非原话）：提出任何新实验之前先通读论文，写清它支撑哪一条主张、回应哪一处薄弱环节，以及结果会放在正文还是补充材料的哪个位置；与主线无关、只为多做一点的实验不加。每份预登记文件都要有一节"在论文中的位置"。  
<!-- GLOBAL:END -->

## 1. 项目地图

先按全局块 G2 做位置自检，再对照下表确认你在哪一份。分支与 HEAD 是 2026-10-26 的读数，动手前 git 实查。

| 位置 | 这一份是什么 | 在这里做什么 / 不做什么 |
|---|---|---|
| Mac Research 卡 `Research/DCTwin/` | DCTwin 项目族的构思、计划、决策与现状页（OneDrive，无 git）。含子卡 `LiquidCooling-DC/`、`Agent-PostTraining-DiffSolver/`（各有薄 `AGENTS.md`），以及有自己三份同步文件的项目卡 `DCTwin-Journal/`（ISPO） | 写需求、计划、裁决与状态索引；不写代码、不跑实验、不放稿件副本 |
| 40901 `~/mycode/dcdtuni` @ `main`（`c682cb298`，与 origin 一致，工作树干净） | **主副本**，工程主仓：液冷 DAE、React 编辑器与 OV 显示、风液集成、dcCart 网格主干、AIFLUIDs 会议稿 | 工程开发、验证与提交；push 获授权后同时推 `origin` 与 `shared`（2.2.2）。本机仓库根另有不入 git 的 `CLAUDE.local.md`（与同学协作的 git 通道、LFS 与禁忌），在这里做 git 操作前先读 |
| Pro 6000 `~/mycode/dcdtuni` @ `main`（11-16 起，所有者指示：工作树全部并入 `main` 后删除） | Base201 反演与网格层级（L0/L1/HF）的开发检出；ISPO 仓库的 `Ref_code/dcdtuni` 仍是指向这里的软链接 | 跟随 `main`（发布印章规则见 2.2.8）；ISPO 的数据中心运行改用新提交前先在 ISPO 登记（2.2.17） |
| 40902 `~/mycode/dcdtuni` @ `main`（`f9507829`，10-01；1 个未提交文件 `.claude/settings.json`；本地救援分支 `local/case-edits-20260928`） | dcCart 保留版本与隔离复验机（所有者 09-28 指定），**旧** | 只在所有者要求时做 dcCart 隔离复验；用前 `git fetch` 并对齐 `main`；不做日常开发。同机第三方仓库 `DiffLiquidTwin` 只读、禁止引入 |
| Mac clone `~/Desktop/AgentWorkspac/dcdtuni` @ `main`（`f9507829`，落后 origin 819 个提交；9 个未跟踪文件；另有本地分支 `rescue/viewport-bus-20260928`） | 旧的前端调试树，**落后很多** | 用前 `git pull`；只做本地查看与前端调试，不跑正式实验；未跟踪文件不擅自删除 |
| 云端会话容器（GitHub 克隆，`claude/*` 工作分支） | 无 GPU 的临时开发环境 | 做代码、测试与小算例；**不做 Omniverse 相关工作**（所有者 10-26），不做大算例；只推送自己的工作分支，合入 `main` 的结果以 40901 实查为准 |

- 没有 JUPITER 副本。
- GitHub：`Litianyu141/dcdtuni`（保密仓库），默认分支 `main`。其他重要分支与标签：
  - `research/ispo-v3-20261021`：ISPO 专用研究分支（10-21 从 `main` `88376d4a` 分出，随时合入 `main`）；`research/ispo-v3-m2-20261024`：ISPO 数据中心 M2 的小改动（从 `dde0b53c` 分出，已合入研究分支）。
  - `research/ispo-validation-20261012`：旧 ISPO 研究分支，10-20 冻结于 `4026e2b95`，只作移植来源；`research/ispo-v2-20261008` 更早，同样冻结。
  - 标签 `agent-pre-slim-2026-10-22`：删除前的旧 agent 树（`src/agent_legacy/`）；提交 `bc5312a9b`（分支 `claude/sharp-rubin-0dlp7p`）：移除 meshgen 之前的完整代码；另有标签 `legacy-html-editor-last-working`、`dev-stl-completed`。
  - 40901 上另有两个本机裸仓库 remote：`shared`（向同学下发基线）与 `inbox`（收同学的提交，只读）。
- 相关仓库：
  - `ISPO-AgentForDesign`（Pro 6000；项目卡 `Research/DCTwin/DCTwin-Journal/`）：经软链接只调用本仓库的脚本化 API，不复制、不修改本仓库代码。
  - `dctwin`：本仓库是它的上游，用 `/sync-dcdtuni-dctwin`、`/sync-dctwin-dcdtuni` 同步（2.2.15）。
  - 40902 `~/mycode/DiffLiquidTwin`（第三方 `ZHANG-QINGANG/DiffLiquidTwin`）：只读参考思路，禁止引入（2.1）。
  - `MeshGenerator`（项目卡 `Research/MeshGenerator-Agent/`）：独立项目；10-26 起不再是本仓库的网格引擎。
- Research 卡内的文件：
  - `README.md`：项目族现状页。
  - `LIQUID_DAE_UPGRADE_PLAN_2026-09-28.md`：液冷图 DAE 分阶段升级计划（S0–S7）；Stage 5–7 未收口，其中 Stage 5（伴随与穿过求解的自动微分）与 ISPO 相关。
  - `PIPELINE_SMOOTHNESS_RESEARCH_2026-09-28.md`：风冷管线全链路架构裁决报告（09-28 快照，引用行号前按新 HEAD 复核）。
  - `TRANSIENT_KEYFRAME_REQUIREMENTS_2026-09-30.md`：风液瞬态关键帧需求与 09-30 裁决；仓库侧需求、设计、计划已归档在 `docs/archive/transient-keyframes-2026-09-30/`。
  - 子卡 `LiquidCooling-DC/`（NVIDIA PEK02 液冷合作资料，`NV/` 只读）、`Agent-PostTraining-DiffSolver/`（thesis 级构想）；项目卡 `DCTwin-Journal/`（ISPO）。
- 仓库内的入口：`README.md`（安装、运行、测试）；`docs/developer/PROJECT_STATE.md`（工程现状：已可用、最近整改、下一步）；`docs/developer/index.md`、`docs/reference/index.md`、`docs/reference/scripted-api.md`；`src/agent/README.md`；`docs/benchmarks/`（带日期的回执）；`docs/archive/README.md`；`.claude/commands/`（Agent 技能）；`BHUMANS/`（所有者需求笔记）。
- 状态与历史：同目录 `LEDGER.md`（同步文件）；跨项目现状：Mac `Research/LEDGER.md`。

## 2. 规则

### 2.1 所有位置都适用

**单人维护：口令不轮换，不为凭据加安全措施（所有者 2026-10-21，强制执行）**

> 所有者原话（逐字）：我现在就强劲告诉你，这个仓库目前只有我一个人个人作者在维护，所以我不非常勤的非常勤的轮换我的口令，这很麻烦，对于我的开发来说，请你把这个规则一定加入当前的仓库里面，你不需要为此增加太多的安全措施

执行落点（不替代上面的原话）：
- 本仓库由所有者一人维护。不要建议、提醒或要求轮换口令和凭据，也不要把"凭据已外泄、应当轮换"之类的话写进汇报、文档、待办或记忆。
- 不要为凭据新增安全措施：不扫描、不清除仓库里已有的口令字符串；不引入密钥管理、加密存储、预提交扫描之类的流程；不为此改写现有文档或脚本。
- 只有登录在功能上已经不通的地方才改，例如脚本里写的口令服务器已不再接受。这时按现行机制修到能用为止：本地模式凭据文件 `src/server/auth-local/admin_credentials`，或环境变量 `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD`。
- 本条优先于任何要求删除、替换或轮换明文口令的任务说明和历史记录。

**工程真源**
- 交付物是自有 dcdtuni 与 `.cfdproj`。`.cfdproj` 统一承担三维几何、风冷网格与求解设置、液冷物理与控制定义；`.cfdrun`、`.liquidrun` 是各有独立 schema 的严格派生运行文件。新字段要验证保存、重开、编译和后端解析一致，不能用随意 JSON 绕过契约。
- 液冷链：`.cfdproj → React 图编译 → 严格 v3/v4 .liquidrun → backend/liquidcooling 图编译 → PyTorch Newton DAE → 后处理`。研究用的硬设计得分必须从这条工程链生成；连续松弛梯度只在已编译图的张量参数上定义，不能声称 JSON/React 编译本身可微。
- 研究按固定提交或分支取用工程能力，不跟随变化中的工作目录；跨机器运行前核对版本（所有者 10-08）。

**证据口径（本项目补充 G4）**
- 现场数据、mock、制造解（MMS）、风冷网格质量、求解收敛、实测验收分开表述。"代码存在""测试通过""HTTP 200""CPU 契约或 USD 检查通过"都不等于能力验收；OV 显示以真实 Kit 视频、scene ACK 与拾取回执为准。
- 液冷结果目前来自 mock 与短时域算例，没有现场校准。没有独立的泵或 CDU 耗电点位，不宣称节能百分比；CRAC 实时数据不在合作范围内，不承诺全机房动态风场或全站 PUE/WUE 优化。
- 不要把三维展示、稳态计算、瞬态预测和闭环优化混称为已完成的能力。NVIDIA 合作交付与期刊科学主张分别验收。
- 风液混合：单独风侧或单独液侧的成功不能写成风液联合验收。风液混合服务器保留液冷身份（冷板与液路）并使用工程保存的正风量；适配时逐 UID 核对风量与 `Q_IT = Q_air + Q_liquid + storage`，不得改成纯风扇服务器而丢掉液冷拓扑（所有者 10-08 裁决）。工程里的负载、风液热分配与设备控制参数带 synthetic/mock 身份，实查后再固定；已有演示记录不能当作新论文的优化收益。

**第三方代码**：40902 上的 `DiffLiquidTwin`（`ZHANG-QINGANG/DiffLiquidTwin`）只能只读参考思路；禁止把它的代码、模块、依赖或子模块引入、复制或移植到本项目。液冷求解器与控制在本仓库独立实现。

**保密与匿名**：dcdtuni 是保密仓库。对外材料、数据集输出和其他仓库（例如 ISPO）里不出现算例原名，一律用 2.2.16 的匿名名；也不写本仓库的内部文件路径和网格器、求解器、运行器的内部实现名（ISPO 仓库 10-26 已按所有者要求做过脱密）。

**旧期刊路线（所有者 09-28 裁决）**：旧 DDL/STE 路线已放弃。稿件 `paper2`、`paper3_icml`（`docs/manuscripts/` 与 `docs/archive/2026-10-08/`）、旧 `ddl/` 的 A/B/C benchmark 结果、TNNLS PDF 与 `paper2_TNNLS_2026-09-16/` 都是历史快照：原始记录保留，不作为任何新论文的证据或"当前稿"。`DDL_paper_2026-09-14_SUPERSEDED_do_not_read.pdf` 与两处 `_superseded/` 明确废弃，不读、不引。AIFLUIDs 会议稿保持不动。

### 2.2 在仓库里工作时

#### 2.2.0 所有者现行指令（逐字，带日期）

**2026-10-23｜旧版 HTML 编辑器停止维护**

> 所有者原话（逐字）：我现在告诉你旧版本HTML放弃维护了

执行落点（不替代上面的原话）：
- React 编辑器（`src/frontend/react/`）是唯一维护的编辑器。新功能、修复、界面改动只做在 React 和共用层（`src/frontend/shared/`、后端），不再同步到 `src/frontend/web/STL_generate.html`，也不再为它写测试或做浏览器验证。
- 文件和 `--app html` 启动方式保留、不删除，只作历史参照；共用 JS 的改动不需要再保证旧版页面能跑。
- 旧记录里的"两个编辑器同步"约定、"STL_generate.html 强制编码规则"和"所有改动同步 HTML"的要求不再约束新工作（原文见 `LEDGER.md`）。

**2026-10-26｜meshgen 全仓库放弃；OV 不在云端环境做**

> 所有者原话（逐字）：meshgen在全仓库范围内放弃，OV这部分先不在这个云端环境做。 其他都你来决定并推进

执行落点（不替代上面的原话）：
- MeshGenerator（`meshgen` 引擎）已从 runner、脚本化 API、React 网格面板、bootstrap、测试与现行文档中移除；网格引擎只剩 `dcCart`（默认）与 `snappy`。移除前的完整代码在提交 `bc5312a9b`（分支 `claude/sharp-rubin-0dlp7p`），更早的 MG 指令归档在 `docs/archive/meshgenerator-2026-09/`。提到 meshgen 的旧段落是历史记录，不再约束新工作。
- 已保存的工程若写着 `meshEngine: "meshgen"`，React 载入时改为 `dcCart` 并在操作日志中提示；runner 与 API 收到 `meshgen` 的 `.cfdrun` 时明确报错，不静默换引擎。
- Omniverse 相关工作（Kit、部署门槛 2、OV 后处理回执、环境光遮蔽）不在云端容器中进行，留到有 GPU 的机器上。

**2026-10-28｜改参数不重出网格，改 patch 只做局部网格更新**

> 所有者原话（逐字）：你还能不能帮我重新开发一个逻辑，就是检测什么时候需要重新生产网格，我给你举个例子，以下情况如果是已有网格的情况下，可以不用重新进行网格生成，或者说不用进行全面完整的网格生成，而求解或者说AI推理的时候能感知到的边界条件变化/全场参数变化：1. 仅调整ACU，server的风速，功耗，送风温度等等 2. 调整起始室温，调整部分模块的透风率，调整物理密度，空气粘度等等 以下是我期望仅做局部网格重新生成即可（snappyhex和dcCartMesh都应该同时支持） 3. 为某一个object的某一个face添加patch，并设置这个patch新类型或者给定新的边界条件

执行落点（不替代上面的原话）：见 2.2.6「网格变更判别与局部更新」。

**2026-10-30｜收敛必须有能量平衡**

> 所有者原话（逐字）：当然要加，避免假收敛出现，而且你要调研文献，确定数据中心场景的CFD模拟里面就是要加这个条件才能判断收敛

执行落点（不替代上面的原话）：见 2.2.5「收敛判据」的能量平衡一条。起因：Alibaba-sige 在第 204 步被判 CONVERGED，而空调只带走了服务器发热的 2%。

**2026-10-30｜SCANO 只在 DCTS 上训练过，泛化性有限**

> 所有者原话（逐字）：因为现在的SCANO只在DCTS这一个数据集上训练了，所以泛化性特别有限，这一点你要加入仓库记忆和CLAUDE.mdzhong

执行落点（不替代上面的原话）：
- 现有 SCANO 模型（`scano_c118_s18`）只在 DCTS 上训练过。在其他机房上的推理结果只能当参考，不当作验证过的预测，也不写成精度结论。
- 在其他算例上结果差、或 AI 初场没有收益，先按这一条理解（Alibaba_hangchou：AI 初场 447 步、均匀初场 368 步），不要归因于求解器或把它当作模型缺陷的新发现。
- 大算例的 CFD 默认用均匀初场；AI 初场只作选项。

其他所有者裁决散见各节并注明日期：`--app` 默认 `react`（10-20）、网格 5 分钟为软目标（10-20）、按 ACU 回风判收敛（10-20）、网格质量只报告不拦截（10-22）。

#### 2.2.1 仓库身份与环境

| 项 | 值 |
|---|---|
| 仓库 | `Litianyu141/dcdtuni`（个人 GitHub，保密） |
| 路径 | `~/mycode/dcdtuni` |
| Conda 环境 | **`dcdtuni`**（Python 3.10，torch 2.7.1 + CUDA 12.8；每台机器只保留这一个环境） |
| 默认端口 | 启动器 `src/start_server.sh`：HTTP **8000** / WS **8001**；直接运行 `server.py`：8005 / 8006 |
| 版本 | 4.0.1（从 `./src` 可编辑安装） |

- 同一 git 历史里包含 dcdtuni（CFD 仿真引擎）与前端（可视化 STL 几何编辑器）。
- **只用 `conda activate dcdtuni` 或 `conda run -n dcdtuni`，不用别的 conda 环境。**
- 安装：`setup_env.sh`，或在仓库根 `conda run -n dcdtuni python -m pip install -e .`。所有 Python 进程（服务器、风冷管线、液冷、SCANO AI 推理进程、测试）都在同一个 `dcdtuni` conda 环境里。`torch`（固定 2.7.1，CUDA 12.8：4090 的 sm_89 与 PRO 6000 的 sm_120 都有内核）+ `networkx` 是可选的 `liquid` extra，`torch` + `scipy` + `torch-geometric` 2.8.0 + `torch-scatter` 2.1.2（从 data.pyg.org 的 `torch-2.7.0+cu128` 页安装）是可选的 `ai` extra，`gpu-solver` extra 另加 `warp-lang` 1.18.0、`pyamg`、`h5py`、`tomli`，都由 `setup_env.sh` 尽力安装，OpenFOAM 风冷管线不需要；手动补装的命令见 `README.md`「Python environments」。不要用 `poetry install`：在 conda 下它解析到 base 解释器，装进 base 环境而不是 `dcdtuni`。
- 新克隆缺 kit-cae 构建和未跟踪的演示资产，部署见 `docs/developer/fresh_clone_deploy.md` 与 `src/frontend/omniverse/cfd_demo/DEPLOY.md`。
- 语义级代码理解用 `codebase-retrieval` MCP；确切事实用直接读文件与 git 核对。部分机器的用户级还装有 ARS / Nature 写作类命令（`/ars-*`、`/nature-*`、`/deep-research`、`/codex:review`），按需使用。

#### 2.2.2 写入、提交与推送（本仓库补充 G4）

- 任务临时文件放仓库内被 git 忽略的 `_tmp/`（包括 cookie 文件与调试截图）；启动器自己的运行目录和仓库内环境在 `.codex-tmp/`。旧文档或脚本示例里写 `/tmp` 的地方不照做。不另建仓库外工作树；可以从旧位置只读核查、迁移既有文件。
- 有价值的源数据与验收证据提交进仓库：`docs/benchmarks/` 下是带日期的回执目录，代码与测试会引用这些路径，原地保留；运行缓存留在仓库内并按需清理。
- `BHUMANS/*.md` 是所有者自己的需求笔记，只读，永远不改其文字。
- `docs/superpowers/plans/` 被 git 忽略，较新的计划只在本机。
- 40901：push 须所有者授权；获授权后先 `tools/release_stamp.sh stamp` 给要推送的 `main` 盖签名印章（2.2.8），再两步都做：`git push origin main` 与 `git push shared main:main`（同学只从 `shared` 取基线，漏推会让他在旧基线上继续开发）。同学的提交经只读 remote `inbox` 收取并 `--no-ff` 合并；收取步骤、LFS 处理与禁忌（不在同学的副本里跑 git、不往 `inbox` 写任何东西）见 40901 仓库根不入 git 的 `CLAUDE.local.md`。
- 40901 旧工作树 `~/mycode/dcdtuni-liquid-platform` 已于 09-28 归档并移除，不得再使用或重建；清理回执见 `docs/archive/liquid-platform-2026-09-28/`。所有旧源码提交都在 `main` 历史中。

#### 2.2.3 计算资源

- 同时最多使用一张 GPU，除非所有者另有授权。每次运行前核对空闲设备，按 UUID 限定进程，不占用他人设备；退出时确认释放。CPU 按任务设置亲和与线程数，小规模多核即可，不占满机器。
- Omniverse Kit 只用一张物理 GPU（2.2.12）。
- Pro 6000 上由 ISPO 发起的本仓库运行计入 ISPO 的资源授权（见 ISPO 项目 `AGENTS.md`）。
- 云端容器没有 GPU：不做 OV，部署门槛 2 留到有 GPU 的机器上。

#### 2.2.4 三智能体架构与俯视图 SOP

本项目实现 Builder–Evaluator–Judge 多智能体流水线，自动做数据中心 CFD 建模。已录用的 AIFLUIDs 2026 论文由 `docs/manuscript/paper_build/gen_docxml.py` 生成（产物 `paper_final.pdf`）；同目录 `paper.md` 是 2026-05 的早期稿。

| 角色 | 技能 | 作用 |
|---|---|---|
| Orchestrator | `/dc-orchestrate` | 主 agent：派发子代理，运行 Judge 打分 |
| Builder | `/dc-builder` | 理解输入 → 建 `.cfdproj` → 导出二维俯视图 |
| Evaluator | `/dc-evaluator` | 对比俯视图与输入 → 结构化修正报告 |
| Judge | `/dc-score` | 对照标签 cfdproj 定量打分 |

其他技能：`/dc-run-cfd`（CFD 流程，经脚本化 API）、`/dc-analyze`（结果分析）、`/dc-dataset-reverse`（数据集生成）；跨仓库同步 `/sync-dcdtuni-dctwin`、`/sync-dctwin-dcdtuni`；浏览器 `/install-WebGL-browse-use`、`/browser-verify`。

俯视图由二维房间编辑器 `window.room2d` 渲染，把三维模型投影到 XZ 平面。两个编辑器都提供：React 在 `src/frontend/react/src/room2d/`（`room2dApi.ts`，技能默认驱动它）；旧版在 `src/frontend/web/room_2d_editor.js`（直接在 `web/` 下，不在 `shared/js/`）。
- **Builder**（`/dc-builder` 第 3 阶段导出俯视图）：必须用 `room2d.enterEditor()`（不是 `applyViewPreset("top")`），打开标注，截图到 `{workdir}/topview_generated.png`。
- **Evaluator**（`/dc-evaluator`）：逐项对比俯视图与输入（图片、Excel）里机柜、ACU、挡板、封闭通道的数量、位置与朝向。
- **Judge**（`/dc-score`）：对照标签 cfdproj 定量打分。
- 区别：`applyViewPreset("top")` 是三维视口的俯视（带透视）；`room2d.enterEditor()` 是专用二维编辑器（干净的平面图、颜色编码、标注）。topview.png 必须用后者。

#### 2.2.5 风冷流水线

```
.cfdproj（编辑器保存）
  -> 浏览器导出
  -> <case>/model/<name>.cfdrun  +  <case>/Mesh/constant/triSurface/*.stl
  -> python -m backend.aircooling.runner.run_cfd --from-cfdrun <path>
  -> STLGenRunner
  -> OpenFOAM 网格 / 求解 / 结果
```

- 浏览器导出的 STL 是几何真源；服务器把 `.cfdrun` 清单写进 `case/<name>/model/`。
- `src/backend/aircooling/runner/stlgen_runner.py`（STLGenRunner）载入 `.cfdrun`，用 Pydantic 校验，发现 STL solid，生成 OpenFOAM 字典，运行网格与求解，收集结果。
- 两个求解器都在本仓库构建：`buoyantBoussinesqSimpleFoam_new`（稳态）与 `buoyantBoussinesqPimpleFoam_new`（瞬态），由 `STLGenRunner._solver_for_simulation_config` 选择；缺 Pimple 构建是错误，不回退到 PATH 上的求解器。
- 不要重新引入 `room.json -> GeometryManager -> regenerate STL` 往返。旧文档里的 `config.toml`、`room.json`、`CaseRunner`、`GeometryManager`、`cfd_export.json` 不属于现行管线；新工作用 `.cfdrun` + `STLGenRunner`。
- **求解结果位置**：求解把 `Mesh/` 复制到 `case/<name>/log/<timestamp>_<case_name>/` 并在那里运行（`timestamp` = `%Y-%m-%d-%H-%M-%S`，日志 `<timestamp>_<case_name>.log`）。`Mesh/` 是会被重置的工作目录，持久的日志与结果在 `log/` 下，不在 `Mesh/`。
- **收敛判据（所有者 10-20，10-27 修订）**：只看每个 ACU 回风温度是否先变化、再在最近 100 步内（10-30 由 200 步改为 100 步）平稳到停止阈值以内（工程 `simulationConfig.stopToleranceK`，默认 0.5 K，React 求解页可改）；残差与点探针只作诊断。10-27 起去掉固定的 1000 步下限：达到阈值就提前停止，达不到才跑满用户设定的步数；不足 100 步的运行判为 INCONCLUSIVE（too-short）。稳态残差阈值默认各变量 1e-3（10-27）。求解默认用 fast 预设，其温度松弛自 10-30 起为 0.95；非正交修正默认开（所有者 10-30：修正器 1 次，格式 `limited corrected 0.5`，新建工程与 fast 工程同此）。不用 `uncorrected`：它在 Alibaba-sige 的 1U 窄缝里发散。**能量平衡（所有者 10-30）**：回风平稳之外，还要求判定窗口里每一步空调带走的热量（求解器逐步输出各 ACU 送回风口的 Σφ·T 与 Σφ，按通量计算）都在声明热负荷（服务器 `powerW` 加热流面）的 `simulationConfig.energyBalanceTolerance`（默认 1%）以内，才判 CONVERGED，提前停止用同一判定；声明热负荷不完整（定温热表面、体积热源）时退为空调热量在窗口内波动不超过该比例，并写明原因。依据与说明见 `docs/reference/scripted-api.md`「Convergence verdict」。
- **已知过渡债务 `meshGeometry`**：网格管线仍经 v1 的 `meshGeometry` 块读取几何与 `meshConfig`：`_load_cfd_json_only()` 与 `_load()` 里的 `CFDExport.model_validate(execution_blob(raw))`。这个块是 `buildCfdrunJson()` 的**派生**形状——该函数在共用模块 `src/frontend/shared/js/cfdrun_build.js`，不在任一编辑器里：嵌套的 `gravity {x,y,z}`、计算出的 `blockMeshBounds`、`monitoring`、`roomTemperature`。它与根级/原始界面配置**不对等**，后者是扁平的 `gravityX/Y/Z` 与 `roomTemperatureC`。`simulationConfig` 依清单版本而定：`.cfdrun` v2/v3 带两份、runner 执行块内那份；v4（React 编辑器自 E8a、2026-10-01 起导出）只在根级带一份，由 `derived_config.execution_blob` 推出执行配置。删除几何块（thin-contract 第 2–4 阶段：零损失设置审计、`geometryExport.meshHints`、删除块）尚未开始，见 `docs/superpowers/specs/2026-07-08-cfdrun-v3-thin-contract-design.md`。依赖行号前先重新 grep。
- **批量验证（是模式，不是固定脚本）**：批量运行放在仓库内：`docs/manuscript/paper_experiment/run_all_cases.py` 遍历每个 `case_*` 目录（16 个匿名算例），`batch_cfd_pipeline.py` 对 `http://localhost:8005` 逐例做建模 → 导出 STL 与 cfdrun → 网格 → 求解（该脚本的 cookie 写在 `/tmp/stlgen_cookie.txt`，是旧写法）。打分与对照：`score_case.py`、`diff_cfdproj.py`，结果在 `batch_results.json`。"N 个网格成功、M 个求解成功"之类计数是时点结果，要重测，不是固定事实。

#### 2.2.6 网格引擎与网格验收

两种引擎，由 `meshConfig.meshEngine` 选择（`stlgen_runner.py` 的 `_resolve_mesh_engine`；缺省或未知值即 `dcCart`；显式写 `meshgen` 会被明确拒绝）。`_resolve_mesh_mode(meshConfig)` 从引擎推出语义，忽略 `meshMode` / `cartesianMode` / `structuredGrid`（三选一的 `meshMode` 已于 2026-09-09 退役）。React 网格面板两种都提供。

| meshEngine | 语义 | Snap | 背景网格 / 状态 |
|---|---|---|---|
| `dcCart`（默认） | 纯切割单元笛卡尔网格（`cartesianLocalized` 加密） | 关 | 均匀背景 + 逐物体八叉树加密。几何由 `third_parties/dcCartMesh/src/mesh/dcCartMesh/dcCartCutCells` 的切割步解析（每个连通 STL 壳一次裁切，端盖按壳的三角形分到各 patch）。任意进程数用**同一个**算法：`parallelMesh` + `meshProcess>1` 以 `mpirun -np N dcCartMesh -parallel` 运行（自分区、halo 切出处理器 patch、碎片合并按轮次在所有 rank 间共享，所以单元不依赖 N）。处理器分区保留给 createBaffles、checkMesh 与求解；求解进程数相同时 `_decompose_for_solve` 直接复用，否则重构后重新分区。单进程是不经 mpirun 的同一代码；MPI 运行失败就是错误，绝不串行重跑——见 `DCCARTMESH.md`「Parallel run」。全部十个发布算例在这个引擎上通过严格 checkMesh（`docs/benchmarks/air-release-cases-2026-10-21/`） |
| `snappy` | 贴体 snappyHexMesh（`bodyFitted`） | 开 | 均匀 blockMesh；作为选项保留，dcCart 成为默认后未在严格门下重新验证 |

- 网格对齐的"结构化"背景（物体面断点、交叉加密）是阶梯状的权宜做法，已从两个界面移除；`_mesh_mode_is_cartesian()` 只对 `dcCart` 为真。
- **网格质量只报告、不拦截（所有者 10-22）**：求解页不因网格未通过或未做严格检查而上锁，有网格就能求解，未通过时提示并提供"仍然求解"。runner 对未通过的 checkMesh 只记警告、照常求解，求解前仍把这次的质量记录绑定到同一网格与 `.cfdrun`（来源绑定的运行仍要求那份记录存在）；脚本化 API 的 `air` / `hybrid` 默认不要求通过，`--require-check-mesh` 显式打开。
- **网格变更判别与局部更新（所有者 10-28）**：求解页与 AI 推理页运行前经 `POST /api/mesh-change`（`runner/mesh_change.py`）把当前导出的 STL 与这份网格当初上传的那批（`log/air-inputs/<id>/`，由 `Mesh/mesh-source-batch.json` 指明）比较：`none`（只改了边界值、室温、开孔率、物性）直接求解或推理；`patches`（同一曲面换了 patch 划分、patch 的角色在墙与开口之间变化、开启待机 ACU、删除开口）点「局部更新网格」（`run_cfd --mesh-only --patch-update`：保留单元，按面中心把边界面划到新 solid，`createPatch` 重排，类型取自 runner 生成的 snappyHexMeshDict，dcCart 的 `dcCartSourceRegions.json` 按实际重划重写，之后照常记录回执与 checkMesh）；`geometry`（几何变了，或网格设置变了——与网格当时的清单比较基础尺寸、加密级别、引擎、locationInMesh 等两边都有的键，进程数与界面显示开关不算）才要求重出网格；核对结论出来之前（编辑后的防抖与核对期间）和网格不符时，求解与推理按钮都禁用，点击处理再按当时的结论拦一次（10-29）。局部开口沿已有网格面划分（整面开口精确），挡板改动仍需重出网格。dcCart 源区域回执的网格身份自 v4 起不含运行值，自 v5（10-29）起也不含界面显示开关，自 v6（10-30）起也不含开孔率数值（只保留各机柜门开孔率是否一致，它决定门是合成一个 patch 还是各自一个）。snappy 的边界面偏斜度上限与严格 checkMesh 一致取 4（10-29）。说明见 `docs/reference/mesh-engines.md`「When an edit needs a new mesh」。
- **网格验收门**（`src/backend/aircooling/runner/tools/regen_all_meshes.py`，以 `python -m backend.aircooling.runner.tools.regen_all_meshes` 运行）：经服务器 API 对每个 `case/*` 重新生成网格，执行两道门。
  - **时间**：批量工具自己仍会中止在 `--timeout`（默认 300 s）内未完成的算例并记为 FAILURE。管线内部 5 分钟只是软目标（所有者 10-20，`mesh_budget.py`）：`STLGenRunner._start_mesh_budget()` 只计时、超时告警，不发 SIGTERM，`dcCartMeshDict` 写 `timeLimitSeconds 0`。所以慢算例过不了批量工具的门，但经编辑器能跑完。
  - **Patch**：runner 在 `snappyHexMeshDict/refinementSurfaces` 里**声明**的每个区域都必须出现在 `polyMesh/boundary`（`backend.aircooling.runner.mesh_acceptance`）。非失败按证据分类，不猜：faceZone 挡板按 cyclic 主从对计（`createBafflesDict`）；重合的 solid 成组，任一成员满足即可；某区域所在平面上没有网格面时，两侧都没有东西到达该平面记 `buried/sealed`，两侧都有面而平面上没有则是 **UNRESOLVED（真实缺陷）**——有单元跨过该曲面，边界条件无处附着。用 `--log-dir` 保留每个算例的作业日志：每个 mpirun 步骤都会覆盖 `Mesh/log.mpi_solver`，盘上那份只属于最后一步。

#### 2.2.7 脚本化 API（脚本与 agent 的无界面入口）

`src/agent/scripted_api/`，参考 `docs/reference/scripted-api.md`。一次调用无浏览器跑完上面的管线，在新目录 `<case>/log/api/<YYYY-MM-DD_HHMMSS>_SGT__<kind>/` 写出结构化 `result.json`（输入的 SHA-256、dcdtuni 提交与脏文件、网格器与求解器二进制及 OpenFOAM 版本、各阶段耗时、成功或失败的阶段与原因）。

```bash
cd src
python -m agent.scripted_api air ../case/X/X.cfdproj --mesh-processes 8 --solve-processes 8 --max-processes 8
python -m agent.scripted_api air X.cfdproj --mesh-only --mesh-engine snappy           # 或 --solve-only，用该算例已有网格
python -m agent.scripted_api hybrid ../case/Shinaide_Liquid/Shinaide_liquid.cfdproj --solve-processes 4 --max-processes 8
python -m agent.scripted_api liquid ../case/X/X.cfdproj --fd-check 3                  # DAE 稳态 + 隐式伴随梯度
python -m agent.scripted_api liquid X.cfdproj --mode transient --duration 20 --pump-rpm m_1_pump=0.8
python -m agent.scripted_api build spec.json --out ../_tmp/Room/Room.cfdproj          # 契约步骤在 JSDOM 中执行，存盘字节与保存按钮一致
python -m agent.scripted_api export-air X.cfdproj --out-root ../_tmp/exports          # 另有 export-liquid
python -m agent.scripted_api batch batch.json --out-root ../_tmp/batches --max-parallel 2 --max-processes 8
```

- **风冷**：React 编辑器自己的导出模块以无界面方式运行（`src/frontend/react/src/headless/`），再由经 `start_server.sh` 启动的**私有**服务器接收编辑器的 `/api/run-mesh` 请求体（先网格、后求解）。所以它服从调试规则 1 与 8（自己从不调用 `run_cfd`）和单算例服务器规则；结束时只用 `fuser -k` 停自己的端口。指标：runner 的 `convergence.json` 判定、`sensorResults/`、机柜进风与 ACU 送回风温度，以及由求解器面通量 `phi` 算出的能量平衡。
- 私有服务器是 `start_server.sh --app api`（无界面）。收敛判定看最近 100 步，不足 100 步的运行判为 INCONCLUSIVE（too-short）。输入文件格式错误时失败阶段为 `failure.stage = "input"`。
- **风液混合**：先液冷会话，再在同一服务器上带着它的热量（`hybridHeat`）做网格与求解。**建模**：`{"verb", "args"}` 步骤经自动化契约执行（`"$N"` = 第 N 步的返回值），以编辑器自己的字节保存，并用它的风冷导出检查。
- **液冷**：进程内 `DAESimulation.equilibrium`，加上经 `solve_steady` 隐式伴随的 `torch.autograd.grad`（稳态），或 DAE 时间推进（`--mode transient`，可带泵速等输入）；不另造求解器。结果带逐边带符号流量与网络拓扑。
- Python 入口：`from agent.scripted_api import run_air_case, run_hybrid_case, run_liquid_case, solve_liquid_steady, solve_liquid_transient, build_project, export_air_case, export_liquidrun, run_batch`。`/dc-run-cfd` 用这个 API；agent 层（`src/agent/`）的其余模块只经它接触平台。
- 网格层级：`meshConfig.coarsenLevel = "L1"`（细网格的 1/100）/ `"L0"`（1/1000）由 dcCart 粗化同一个 HF 网格得到，保留全部 patch 与几何；`meshConfig.blankingPanelPatches = "perRack"` 让每个机柜的盲板成为自己的 patch（逐机柜漏风参数）。L0 网格的求解是 zonal 网络（`gpu_solver/entries/dc_zonal_solve.py`），L1/HF 是 GPU-JFNK RANS 或 OpenFOAM。
- dcRANS 正向与反演：`agent.dcrans`（`solve_dcrans` / `invert_dcrans`，L0 与 L1；`python -m agent dcrans-solve` / `dcrans-invert`）。

#### 2.2.8 服务器、启动器与登录

**强制：`src/start_server.sh` 是启动三个网页前端的唯一方式。** 它是 HTML 编辑器、React 编辑器与 Omniverse 演示的唯一入口，负责端口选择、启动后端、Vite 代理目标、局域网绑定与清理。手工运行 `npm run dev`、`vite` 或 `python src/server/server.py` 即使看起来能用也算违规，因为会悄悄丢掉启动器做的事：

| 手工做法 | 丢掉什么 |
|---|---|
| 在 `src/frontend/react` 里 `npm run dev` | `--host`（Vite 只绑 127.0.0.1，别的机器打不开）、`DCDTUNI_WEB_PORT` / `DCDTUNI_API_TARGET`、后端、端口冲突拒绝、启动横幅 |
| `python src/server/server.py` | 界面不启动；8000/8001 与 8005/8006 的默认值变成猜测 |
| 单独运行 `cfd_demo/scripts/start_demo.sh` | Kit 后端没有网页界面，8301 也没登记清理 |

启动器表达不了的需求，**给启动器加参数**，不要绕开；新的前端或服务方式也加在这里，让"怎么启动 X"始终只有一个脚本。

```bash
./src/start_server.sh                      # React 编辑器（默认），界面在 8000
./src/start_server.sh --app html           # 旧 HTML 编辑器（2026-10-23 起停止维护），8000/STL_generate.html
./src/start_server.sh --app omniverse      # Kit 后端 + CFD 演示界面
./src/start_server.sh --port 9000          # 任意端口；WS 默认 port+1
./src/start_server.sh --dev --api-port 8005        # Vite 开发界面，复用已在运行的后端
./src/start_server.sh --case-dir /abs/case          # 其余参数传给 server.py
```

| `--app` | 界面由谁提供 | 后端 | 浏览器打开 |
|---|---|---|---|
| `react`（默认） | Python 服务器（`dist`）；`--dev` 时为 Vite | `--port`；`--dev` 时为 `--api-port`，默认 `--port + 2`（WS +1） | `--port` |
| `html` | Python 服务器本身 | `--port` / `--ws-port` | `--port/STL_generate.html` |
| `omniverse` | Vite（cfd_demo/web） | Kit 在自己的 8301 上串流 | `--port` |
| `api` | 无，只有 `/api` | `--port` / `--ws-port` | 无；私有服务器 `python -m agent.scripted_api` 自己启动 |

- **签名发布印章（所有者 10-27，强制）**：同学的机器可能在内网、连不上任何服务器，所以启动前的版本校验完全离线。`start_server.sh` 与 `server.py` 启动时都运行 `tools/release_stamp.sh verify`（`server.py` 经 `src/server/release_check.py`，脚本化 API 的私有服务器也经过它），以下任一不满足就拒绝启动并打印原因与恢复步骤。**校验范围只有 `src/` 加校验器自己的 `tools/release_stamp.sh` 与 `tools/release_allowed_signers`**（所有者 10-28）：`case/`、`docs/` 等其余目录是使用者的数据，同学改已有算例、新建算例、在本地提交都不影响启动。
  1. 仓库根 `RELEASE_STAMP.sig` 是所有者私钥对 `RELEASE_STAMP` 的有效签名（`ssh-keygen -Y verify`，公钥在 `tools/release_allowed_signers`）；
  2. 范围内被 git 跟踪的文件没有未提交的改动（未跟踪、被忽略的文件不算）；
  3. 当前提交里范围内全部被跟踪文件的哈希等于 `RELEASE_STAMP` 的 `tree_sha256`；
  4. 范围内每个 Git LFS 文件（材质、模型等）在磁盘上是真实内容，SHA-256 与大小等于它的指针（只有指针文本也拒绝）。
  `RELEASE_STAMP` 另记盖章时间 `stamped_at`（SGT）与对应的内容提交 `content_commit`，只作显示；编辑器把服务器的印章状态写进操作日志。
  - **私钥**：ed25519，只在 40901 主副本的 `.release-signing/release_signing_key`（git 忽略，0600）。不复制到别处，不打印、不提交。
  - **盖章与推送**：推送 `main` 之前在 40901 运行 `tools/release_stamp.sh stamp`（要求校验范围内没有未提交的改动；写出 `RELEASE_STAMP` 与签名并单独提交一次），再推 `origin` 与 `shared`。所有者的推送前钩子 `tools/git-hooks/pre-push`（由所有者装到 `.git/hooks/`）拒绝推送没有有效印章的 `main`。
  - **所有者检出**：仓库根放所有者签名的 `.dcdtuni-owner-tree`（git 忽略）跳过校验，由 40901 上的 `tools/release_stamp.sh owner-marker` 生成，可拷到所有者的其他检出（Pro 6000 活检出与 `_tmp/wt-*`、40902、Mac、所有者的云端容器）。签名无效的标记同样拒绝启动。
  - **收同学的提交**：合并 `inbox` 之前运行 `tools/release_stamp.sh inbox-check main..inbox/<分支>`；改动了校验脚本、印章、公钥、启动器或 `release_check.py`，或者删掉了两处启动校验调用时，它列出提交并返回 1，须由所有者逐行审阅后再决定。
  - **写给任何代理**：这是所有者的强制规则。不得修改、删除或绕开上述校验（脚本、印章、签名、公钥、启动器与 `server.py` 里的调用），不得自建所有者标记或自行盖章，不得不经 `start_server.sh` 启动服务。校验不通过时只能把检出同步到所有者发布的版本，或停下来告诉使用者。
- **AI 推理显卡**：`--ai-gpu N`（显卡序号、`GPU-…` UUID 或 `cpu`；`--scano-gpu` 同义，环境变量 `DCDTUNI_SCANO_GPU` 同效）指定 SCANO 推理进程用的那一张卡，启动前校验；默认 0 号卡（所有者 10-28），与 OV 的 `--gpu` 默认一致。所选显卡空闲显存不足约 6 GiB（`DCDTUNI_SCANO_MIN_FREE_MIB`，10-29 起；之前为 14 GiB）时：带 `--scano` 拒绝启动，不带时横幅警告、AI 推理报错，两处都列出各卡空闲显存，由用户用 `--ai-gpu` 换卡。`--scano [模型]` 在启动时加载并预热模型。横幅打印所选显卡。AI 推理页可选推理方式：自动（整网格的预估显存放得下就整网格，放不下就用论文的 TTA 分块推理；模型卡可设自动优先 TTA）、整网格、TTA（重复次数 R），并实时显示所用 GPU、显存余量与本次的预估峰值（10-29，`docs/developer/scano-ai.md`「Deployment」）。
- **默认 `--app` 是 `react`**（所有者 10-20，取代 09-10 的 `html` 默认）；`--dev`、`--build` 不带 `--app` 时即 `react`。界面源码比 `dist/` 新时启动器自动重建，`git pull` 后不必加 `--build`。
- **端口（长期）**：经启动器默认 HTTP **8000** / WebSocket **8001**；WS 端口为 `--port + 1`，除非传 `--ws-port`。启动器**不扫描**空闲端口：端口被别的用户占用时报错退出并给出端口号，换一个 `--port`；自己进程占用的端口用 `fuser -k` 释放。横幅打印实际要打开的 URL 与后端端口。直接运行 `src/server/server.py`（只用于测试，不用于提供界面）仍默认 8005 / 8006；旧文档写的 8005 / 8006 是以前的默认值。真源：`src/start_server.sh`（`PORT=8000`、`free_port_or_die`）与 `src/server/server.py`。
- **管理员凭据（没有内置口令，10-20 起）**：没有显式凭据时服务器拒绝启动。优先级：环境变量 `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD`，然后 `--admin-credentials <属主 0600 的两行文件>`。两者都没给时 `start_server.sh` 用**本地模式**：一次性生成并复用 `src/server/auth-local/admin_credentials`（用户名 `admin`、随机口令、git 忽略、0600），并以该目录为 `--auth-dir`。所以直接 `./src/start_server.sh` 总能启动，agent 读同一个文件登录——口令不抄进文档、日志或提交。脚本化 API（`agent.scripted_api`）每次运行自己生成一对凭据，不受影响。共享或局域网部署时传环境变量或 `--admin-credentials`（并给自己的 `--auth-dir`），不用本地模式。

```bash
./src/start_server.sh --port 8005            # 本地模式；横幅打印凭据文件位置
CRED=src/server/auth-local/admin_credentials
curl -s -c _tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/auth/login \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$(sed -n 1p $CRED)\",\"password\":\"$(sed -n 2p $CRED)\"}"
# 之后的调用复用 cookie：
curl -s -b _tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/set-case-dir \
    -H "Content-Type: application/json" \
    -d '{"caseDir":"/abs/path/to/case"}'
```

- 主要接口：`POST /api/set-case-dir`、`POST /api/prepare-case`、`POST /api/run-mesh`、`GET /api/log-stream?job_id=<id>`、`GET /api/job-status?job_id=<id>`、`POST /api/browser/export-cfdrun`、`POST /api/browser/export-stl`、`POST /api/browser/exec-js`、`GET /api/browser/screenshot?name=xxx`。
- **单算例服务器，没有用户隔离。** 一个 `start_server.sh` 进程（一对端口）只持有**一个全局**工作算例目录，网格与求解作业**一次一个**。`POST /api/set-case-dir` 在进程范围内改这个状态：两个人连同一个端口各设不同算例目录时**后写者胜出**，先前用户的算例目录在会话中途被悄悄覆盖、运行被破坏。这是框架限制，不是 bug。**要并行跑相互隔离的任务，每个任务起一个 `start_server.sh`，用不同端口（`--port`/`--ws-port`），各指向自己的算例目录**；不要让并发的用户或算例共用一个服务器。在共享机器上脚本化网格与求解时，不要以为某个端口是你的：每个作业前用 `set-case-dir` 核对算例目录，或者用自己的端口。登录 cookie 按 host 保存、对端口不敏感，同一 host 上两人同时用浏览器会互相顶掉登录态，用不同的 host 字符串或浏览器 profile 规避。

#### 2.2.9 调试规则

1. **调试 STL 导出、网格与求解优先用 `browser-use`**：先启动服务器，再用 `browser-use` 操作前端（`--headless=new` + NVIDIA Vulkan 的 GPU 加速 WebGL；用 `/install-WebGL-browse-use` 安装，用 `/browser-verify` 核验），最接近真实用户体验。不需要 Xvfb：`--headless=new` 在 GPU 上渲染 WebGL，截图可用。

   ```bash
   export PATH="$HOME/.local/bin:$PATH"
   browser-use open http://localhost:$PORT/          # React 编辑器
   browser-use state          # 列出页面元素
   browser-use click <n>      # 点击按钮（载入工程、导出 STL、网格、求解）
   browser-use screenshot _tmp/debug.png  # 截图核对
   ```

   只是触发网格或求解这类简单操作，用 `curl` 调服务器 REST API：
   ```bash
   curl -s -b _tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/run-mesh \
       -H "Content-Type: application/json" -d '{"meshOnly":true}'
   ```

   **调试 STL/网格/求解时禁止（违反就重做）**：`conda run -n dcdtuni python -m backend.aircooling.runner.run_cfd ...`；用 `python -c "from backend.aircooling.runner... import ..."` 做端到端运行；任何绕过服务器的做法。**允许的例外**：`pytest`（runner 内部的单元/回归测试，先写失败的测试）；`python -c` 只用于纯 Python 逻辑调试（schema 校验、算法检查），绝不做端到端 STL/网格/求解。
2. **改了 server.py 之后**经启动器重启：先 `fuser -k <http>/tcp <ws>/tcp` 停旧进程，再 `./src/start_server.sh --app <react|html|omniverse> --port <port> [--case-dir ...]`，然后经 browser-use 或 API 重新触发操作。不要手工重开 `python src/server/server.py`（见 2.2.8）。
3. **绝不使用 `kill`、`pkill`、`killall`、`xargs kill` 或 `pgrep`**：在共享机器上很危险，可能杀掉 VSCode Remote Server、JupyterLab 等。唯一允许的终止方式是 `fuser -k <port>/tcp`（例如 `fuser -k 8005/tcp 2>/dev/null`）。
4. **看浏览器里的 Operation Log**：底部面板实时显示 dcdtuni 输出，所有错误都应在这里可见。
5. **截图调试**：请用户点 Operation Log 栏的相机按钮，从 `case/{case_name}/screenshots/ui_screenshot_*.png` 读取截图（多模态读图）。
6. **中止运行中的作业**：用户可在网格或求解时点停止按钮，服务器向子进程及其所有子进程发 SIGTERM。开始新作业前，先查上一个作业是否还在跑。
7. **绝不用串行求解器**：不带 `-parallel` 时绝不运行 `buoyantBoussinesqSimpleFoam` 或 `buoyantBoussinesqPimpleFoam`。所有求解都经 `mpirun -np N <solver> -parallel`（先 `decomposePar`）或服务器 API（`/api/run-mesh`）。串行求解在大算例上极慢，也不代表真实用户体验。
8. **业务操作绝不用 `conda run`**：网格、求解、导出不得用 `conda run -n dcdtuni python -m backend.aircooling.runner.run_cfd ...`，按优先级只走：
   - **`browser-use`**（载入 cfdproj → 导出 STL 与 cfdrun → 触发网格与求解），首选，完整端到端；
   - **`curl` 调服务器 REST API**（`/api/prepare-case`、`/api/run-mesh`），只做简单操作；
   - **脚本化 API `python -m agent.scripted_api`**：无界面运行，它自己驱动私有的 `start_server.sh --app api`，所以同样经服务器；
   - **`/api/browser/*` 服务器端 Playwright**：服务器内置的浏览器通道，默认是 Xvfb `:99` 上的有头浏览器（`DCDTUNI_BROWSER_DISPLAY`），只有 `DCDTUNI_BROWSER_HEADLESS=1` 或 `DCDTUNI_BROWSER_SOFTWARE=1` 时无头。

   `conda run` 只允许用于：`pytest`、`python -c`（内部逻辑）、`decomposePar`/`checkMesh` 等 OpenFOAM 工具命令。
9. **多个网格区域不等于错误**：有冷热通道封闭时，checkMesh 报多个区域（"Number of regions: N"），这些是合法的独立流体域。自动清理按**体积占比**而不是区域数（`_remove_disconnected_cells`）：小于最大区域 1% 的次级区域（`_ORPHAN_REGION_FRACTION`）当作 snappy 伪影删除，更大的次级区域**保留**并作为 `disconnected_regions` 警告报告（多半是合法封闭）；只有零个或一个内部面的单元总会经 `oneInternalFaceCells` 集合取反 + subsetMesh 删除。checkMesh 严重度（`mesh_quality.py`）：小孤立区（<1%）为 critical，大次级区域为 informational。概念上，合法区域有自己的边界条件；代码里的操作代理是 1% 体积占比（加零/一面单元规则），**不是**区域数。行号会漂移，先按函数名 grep。
10. **`createBaffles -parallel` 很脆弱**：它会悄悄丢掉跨处理器边界的 faceZone 面（架空地板或墙挡板上约 3%），在多孔/墙跳跃面上打出洞。管线在 snappy 建出这些 zone **之后**用带 `preserveFaceZones` 约束的 `redistributePar` 重新分区（`preserveBaffleFaceZones { type preserveFaceZones; zones ( ".*" ); }`），见 `openfoam_case.py` 的 `generate_decompose_par_dict(..., preserve_face_zones)`。snappy 之前的分区不打开这个标志（zone 还不存在）。`createBaffles` 本身只处理网格。

#### 2.2.10 编辑器

- `src/frontend/react/` 是**唯一维护的编辑器**：React + TypeScript + Three.js r184（npm `three`），用 vitest 测试（`npm test`）。由 `start_server.sh` 与 `/api` 同端口提供。脚本驱动它只经自动化契约 `window.__dcdtuni`（`src/automation/contract.ts`），绝不直接写 `state`。撤销由 `projectStore.edit()` 自己做检查点。
- `src/frontend/react/src/liquid/` 与 `components/Liquid*.tsx`：液冷与风液混合工作流——实时就绪检查、分五段的求解页（每块冷板有监视）、后处理页（管网表、热量与换热、本次运行的求解定律、三标签对象曲线、混合热量账本、恢复已保存运行为会话）。三维显示可按温度、流量、流速或压力给管网着色并带色标，沿渲染管路按实际流向画流向箭头（大小随流量、颜色随流速，可动画，`shared/js/liquid_flow_anim.js`；Kit 由已验证结果画同样的箭头）。
- `src/frontend/shared/` 是两个编辑器共用的：`js/`（`.cfdproj` 格式模块与几何构建器）、`i18n/`（en-US / zh-CN）、`vendor/`（自托管的 Three.js r184 与插件，不用 CDN）、`textures/`。React 直接从源码导入，它们不是旧版应用的私有文件。注意 `room_2d_editor.js` 直接在 `src/frontend/web/` 下，不在 `js/`。
- `src/frontend/shared/textures/` 是**唯一的材质与纹理池**（10-21）：`materials.json`（`dcdtuni.material-library/v1`）规定每种渲染 objectKind 与库角色的材质、纹理集、照片、patch 预设与共用灯光；两个编辑器（`MeshStandardMaterial`、`js/scene_lighting.js`）、Omniverse 桥与 Kit 都读它。OV 按池引用（`pool:<file>#<sha256>`）接收纹理、Kit 绑定同一批文件，所以这里改一个材质两个渲染器同时变。`tools/gen_pbr_textures.py` 只写测得的字段与生成的纹理集。证据：`docs/benchmarks/materials-shared-pool-2026-10-21/`。
- `src/frontend/web/STL_generate.html`：旧版单文件应用，由 `--app html` 提供，**10-23 起停止维护**（2.2.0）。保留不删：它是 React 移植可能没复现的行为的唯一可运行参照，可以读它做对照。两轮 React 迁移缺口登记已收口（归档在 `docs/archive/react-migration-2026-08/`），自动对等扫描（`src/frontend/react/parity/`）自 2026-08-30 后没有重跑；服务器风口之间的吸附已于 10-25 移植到 React（`3eb341e7d`）。旧版的编码规则与"两个编辑器同步"约定只是历史，原文在 `LEDGER.md`。
- 渲染转换保留工程 UID、几何变换、米制与 Y-up、PBR 材质与贴图、UV；外形未经确认的设备明确标为通用模型。
- **browser-use（GPU WebGL 浏览器测试）**：在独立 NVIDIA GPU 上经 ANGLE/Vulkan 以 `--headless=new` 渲染 Three.js 前端，做自动化 WebGL Chromium 测试。它是**每个开发者本机的工具，不是仓库产物**：各自在自己的主机上用 `/install-WebGL-browse-use` 安装（需要 Python 3.11+，与 3.10 的 `dcdtuni` 环境分开），再用 `/browser-verify` 核验。不要写死二进制路径或 GPU 型号。可移植的验收标准：未屏蔽的 WebGL renderer 必须读出 `ANGLE (NVIDIA … Vulkan …)`，不能是 `llvmpipe`/`SwiftShader`，并且 `nvidia-smi` 里应出现 `chrome --type=gpu-process`。

  ```bash
  export PATH="$HOME/.local/bin:$PATH"
  browser-use open http://localhost:$PORT/                    # 打开 React 编辑器
  browser-use state                                           # 列出可交互元素
  browser-use click <n>                                       # 按序号点击
  browser-use input <n> "text"                                # 输入文字
  browser-use eval "window.createDefaultBaseModel()"          # 执行任意 JS
  browser-use screenshot _tmp/test.png                        # 截图
  browser-use close                                           # 关闭浏览器
  ```

  已知问题：在这个浏览器环境里，`innerHTML` 中的 `onclick` 属性不起作用（1:1 处 SyntaxError），新的交互元素用 `addEventListener`。

#### 2.2.11 液冷

- **生产求解器是可微 Newton DAE**（`src/backend/liquidcooling/solver/dae_newton.py` + `solver/equation_graph.py`，经 `dae_runtime.DAESimulation` 运行），输入严格的 `.liquidrun` v3/v4 清单（v4 由 cfdproj v5 时间轴编译而来）：稠密 Jacobian 到 4000 个未知量，以上走稀疏路径 `solver/dae_sparse.py`，在准入时选择。React 工作台、`/api/liquid/runs` 会话与 `agent.scripted_api liquid` 都用它。测试在 `src/backend/tests/test_liquid/`。
- **本构律版本写进每个结果的 `parameter_provenance.solver_laws`**（`solver/newton_hydraulic.py`）：泵相似律 `H = N²·H₀ − a·Q·|Q|`（v3）；64/Re–Swamee–Jain 摩擦，对数自变量上限 0.5（v2）；每条边计 `ρ·g·Δh` 并检查回路高程闭合（v2）；每台 CDU 的 `hxModel` 为 `counterflow-ntu`（新建 CDU）或 `mixed-volumes`（没有该字段的旧文件）；三通局部阻力 `tee_law`（v2）。没有泵驱动路径的停滞块与无稳态的回路给出报告，不硬解；记录定律之前保存的运行恢复时把定律列在 `solver_laws.inferred` 下。
- DAE 内的 PI 闭环作为连续律与对象在同一后向 Euler 步内求解；不建模的微分项、限速与慢采样一律拒绝，不静默忽略；被控执行器拒绝在线指令。
- 兼容用的旧路径：v1/v2 是 Newton GGA 水力 + 热推进（v1 走 `solver/hybrid.py` → `hybrid_solve`，v2 走带 PID 的 `LiquidSimulation` 运行时；`runner.py` 按清单版本分派）；**Adam ODIL**（`solver/adam_odil.py` → `solve`）对全部状态变量做纯 Adam 优化，只作研究对照基线。子包：`graphcompiling/`（配置 → 张量编译）、`solver/`（模型与优化器）、`handlers/`（部件物理）、`postprocess/`（结果与可视化）。
- **求解器工程纪律（所有者 09-26 强调）**：
  - 技术主线：`.cfdproj` 三维几何与连接 → 编译物理拓扑图 → 在图上赋设备、材料、储能、边界、控制信息 → 非因果部件残差 `F(t,y,ydot,u)=0` → PyTorch 可微 Newton DAE。稳态令储能导数为零；瞬态用隐式积分；要处理一致初值与模式切换，不能把"跑很久"当稳态求根。
  - 按部件或管段批量张量计算，利用多线程 CPU/SIMD，同一套残差、Newton 与伴随能迁移到 GPU。设备、精度、线程数由执行配置决定；求解器里不写死 CPU 或单线程。热路径上不逐设备或逐单元 `.item()`、不写 Python 循环、不转 NumPy/SciPy；拓扑解析在构建期完成。用 profiler、线程对照、MMS 与梯度对照核实；`.to('cuda')` 或代码存在不能代替 GPU 实测。
  - 必须做 MMS（独立的制造解与强迫项、时间与空间阶、约束残差）、守恒与梯度的独立核验。"逐步准稳态水力 + 热推进"与小算例或真实算例上的梯度检查，不算完整 DAE/MMS 平台的验收。
  - 一次侧回水必须来自有限的一次侧流量、热容与换热耦合，不能用恒温无限热库代替后宣称预测了回水。一次侧/二次侧按设施侧/IT 侧命名；现场点位的方向仍需核对。
- 算例：主验收算例 `case/Shinaide_Liquid/Shinaide_liquid.cfdproj`（风液混合）；NV mock `case/NV_PEK02_Mock/`。原始工程只读：研究或派生先复制再改，运行记录写进 case 的日志目录，不改写工程本体；工程本身的修改走 git 提交。
- 需要 GPU 的液冷测试用 `DCDTUNI_RUN_CUDA_TESTS=1` 打开。

#### 2.2.12 Omniverse 显示（React `--ov` 桥与 cfd_demo 参考界面）

**不在云端容器里做**（2.2.0）。两种形式都**只经 `src/start_server.sh`** 启动：它监管 Kit、等 Kit 就绪、清理自己的端口；不要手工调用 `start_demo.sh`、`dc_ov_demo.sh` 或 `npm run dev`。

- **主路径：React 编辑器的显示桥**（`--app react --ov`）。React 编辑器是唯一的建模界面（决策 D2，2026-09-29）。受监管的单 GPU Kit 应用（kit-cae + `dc.cfd_bridge` 扩展）把工程的 render-scene 写成 USD，经 WebRTC 串流进编辑器的 OV 视口；渲染器旁的 OV 按钮等 Kit 就绪后自动连接。求解结果由服务器转换（`foamToEnsight`，分区运行也可），只凭服务器签发的授权才进入 Kit；Kit 用 kit-cae 自己的工具后处理（边界面场、IndeX 体渲染、切片、GPU 流线）。液冷 DAE 结果可经 `/api/ov/liquid-result-grant` 在 Kit 里着色。OV 视口支持 Shift 整物体拾取，尚不支持在视口里编辑（移动/旋转）。
- **参考界面 `cfd_demo/web`**（`--app omniverse`）：原 GTC 演示（来自预转换 `data/internal.vtu` 的 T/|U| IndeX 体渲染、ROI、流线与种子球、载入 `.cfdproj` 几何）。保留作参考，不是产品界面。

| 路径 | 作用 |
|---|---|
| `src/frontend/react/src/renderer/ov*.ts`、`OvViewport.tsx` | React 端：会话与串流租约、带 ACK 的命令协议、场景传输、原生后处理适配 |
| `src/server/ov_result_dataset.py`、`src/server/ov_liquid_result.py` | 结果转换与签名显示授权，由 `server.py` 以 `/api/ov/result-dataset`、`/api/ov/liquid-result-grant` 提供；Kit 就绪状态在 `/api/ov-status` |
| `src/frontend/shared/schema/render-scene.schema.json` | render-scene 契约（`docs/developer/render-scene-contract.md`） |
| `src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/` | Kit 扩展：USD 写入与命令处理（`handlers.py`、`scene.py`、`parsers.py`） |
| `src/frontend/omniverse/cfd_demo/web/` | 参考 React + Vite 界面（`Viewport.tsx`、`ModelTree.tsx`、`Inspector.tsx`、`PostProcessing.tsx`、`stream.ts`、`streamMessages.ts`） |
| `src/frontend/omniverse/cfd_demo/scripts/` | `start_demo.sh`（由启动器调用）、`supervise_kit.py`、`resolve_vulkan_gpu.py`、`head_verify_*.py` 一族 |
| `src/frontend/omniverse/cfd_demo/data/` | 参考演示的 `internal.vtu` 与 `ranges.json` |
| `src/frontend/omniverse/Ref_code/` | 只读参考仓库（`omniverse-dsx-blueprint-for-ai-factories`、`OV_Ref_DCVerse_Linux`）；运行时不从这里加载任何东西 |

- **Kit 运行时在 `src/backend/kit-cae`**（10-18 从 `Ref_code/` 移出）：公开的 NVIDIA kit-cae v2.0.1 检出及其 `_build`，约 14 GB，git 忽略。`tools/bootstrap_dcdtuni.py --install --ov` 在那里克隆并构建；`start_demo.sh` 启动 `src/backend/kit-cae/_build/linux-x86_64/release/omni.cae_streaming.kit.sh`（用 `DC_CFD_KIT_BUILD` 覆盖）。构建缓存（Packman 包、Kit 扩展数据根）在 `src/backend/kit-cae/_cache/`；bootstrap 最后一步把构建产生的每个链接改写为相对链接（`relativize_kit_links`；`kit_output` 拒绝任何绝对链接或离开 Kit 检出的链接），所以该目录乃至整个仓库可以移动而不必重建。新克隆缺 kit-cae 构建与未跟踪的演示资产（`src/backend/kit-cae/data/GTC/` 场景、`cfd_demo/data/internal.vtu`、SCANO checkpoint 与基础算例）。
- **端口由启动器决定，读它的横幅**：`--port`（默认 8000）是要打开的页面（带 `--ov` 的 React 编辑器，或 `--app omniverse` 的参考界面）；`--ov-port`（默认 **8301**）是 Kit 的 WebRTC 信令端口；`--ov-stream-port`（默认 **47998**）是 Kit 的 UDP 视频端口。每个 Kit 实例要一对自己的端口；UDP 视频需要直连路由（不经 SSH `-L` 转发）。经 SSH 转发页面端口、使用终端时的后端 WS 端口与信令端口，例如 `ssh -L 8000:localhost:8000 -L 8301:localhost:8301 <user>@<host>`。
- **单 GPU 规则（强制）**：Kit 只在**一张**物理 GPU 上运行，用 `--gpu N` 选择（默认 **0**；启动器以 `DC_CFD_GPU` 交给 `start_demo.sh`）。CUDA 按 UUID 钉在这张卡上，多 GPU 渲染硬关闭（`--/renderer/multiGpu/enabled=false`），Vulkan 的 `activeGpu` 序号由 `resolve_vulkan_gpu.py` 解析到同一张卡（在单个 NVIDIA ICD 下跑 vulkaninfo，按 UUID 匹配）。`DC_CFD_ACTIVE_GPU` 只是对该解析的一致性检查，不能用来选另一张卡。`--gpu` 是 GPU **序号**选择，不是数量；没有多 GPU 代码路径。先看 `nvidia-smi` 找空闲卡。

```bash
./src/start_server.sh --app react --ov --gpu 0                    # 编辑器 + Kit 显示桥（主路径）
./src/start_server.sh --app react --ov --gpu 3 --port 8064 \
    --ov-port 8302 --ov-stream-port 47999                         # 第二个相互隔离的实例
./src/start_server.sh --app omniverse --gpu 0 --port 8081          # Kit + cfd_demo 参考界面
fuser -k 8064/tcp 8065/tcp 8302/tcp                                # 停止：在启动器里 Ctrl+C，或释放你自己的端口——绝不 kill/pkill
```

- Kit 日志在 `.codex-tmp/ov-runtime/<ov-port>.XXXXXX/kit.log`（横幅打印确切路径）。部分 `cfd_demo/scripts` 辅助脚本仍默认旧的 `/tmp/dc_cfd_demo.kit.log`，用 `KIT_LOG=<path>` 指向运行时日志。后端（`dc.cfd_bridge/*.py`）改动要重启 Kit：停掉启动器再启动，然后刷新页面。
- **一次只服务一个客户端**：Kit WebRTC 串流只服务一个客户端。视口全黑且 Kit 日志刷 `NVST_R_BUSY`，说明唯一的名额被另一个、幽灵或残留的浏览器占着（包括残留的 head-verify 会话）。处理：关掉另一个客户端，或重启启动器。`head_verify_stream_ok.py` 统计连接后的 `NVST_R_BUSY` 并关闭自己的浏览器以释放名额。
- **刷新页面会重置视口（参考界面）**：Kit 视口是长寿命的，所以参考界面的 `Viewport.tsx` 每次页面载入只在第一次 WebRTC `connected` 事件后（等 1000 ms）触发一次 `resetScene()`，由 `resetSent` ref 守护（会话中途重连**不**清掉工作）。后端 `scene.reset_scene()` 安全地退役 IndeX 体（`nvindex:composite=False` → `MakeInvisible` → 等 6 次更新 → `cache.remove '[viz:index_volume]'` → `RemovePrim`；直接删除活动的体会让 IndeX 崩溃），拆掉流线、种子图元与 `/World/internal` 数据集，并隐藏 ROI 框、`/World/Geometry`、地面网格和每个几何组图元（Floor/Room/Containment 与 `/World/Geometry` 是**兄弟**节点，所以逐个隐藏）。几何保持已构建并缓存，下次 **Load CFD Geometry** 很快重新显示。
- **调试用有头浏览器核对，绝不用无头 browser-use**：视口是 WebRTC 串流的 RTX 视频，不是 WebGL。`browser-use` 忽略 `--headed`（总是 `--headless=new`、渲染全黑、还占一个 GPU 进程），这里**禁止**使用。改用 `scripts/head_verify*.py`：Xvfb `:99` 上 `headless=False` 的 Playwright，`--disable-gpu --use-gl=swiftshader`（纯软件 H.264 解码，不碰 GPU），截取真实串流。这些脚本针对参考界面，一族包括 `head_verify.py`（完整流程）、`head_verify_reset.py`、`head_verify_roi_flow.py`、`head_verify_streamlines.py`、`head_verify_colormap.py`、`head_verify_stream_ok.py`；截图读取循环见 `browser-verify` 技能。
- 规格与计划：`docs/superpowers/specs/2026-06-29-omniverse-cfd-web-demo-design.md`、`docs/superpowers/plans/2026-06-29-omniverse-cfd-web-demo.md`；概览：`src/frontend/omniverse/cfd_demo/README.md`（参考界面）、`docs/developer/render-scene-contract.md`（React 桥）。

#### 2.2.13 代码地图（高价值位置）

后端（Python）：
- `src/backend/aircooling/runner/run_cfd.py`：`.cfdrun` 运行的命令行入口。
- `src/backend/aircooling/runner/stlgen_runner.py`：现行 OpenFOAM 管线编排器（一个大模块，按函数名 grep）。
- `src/backend/aircooling/runner/cfdrun_schema.py`：`.cfdrun` 的 Pydantic v2 schema；`bridge_bc.py`：内存中的边界条件注册表；`fields.py`：OpenFOAM `0/` 场生成。
- `src/backend/liquidcooling/`：液冷求解器（只在 dcdtuni，见 2.2.11）。
- `src/backend/aircooling/third_parties/foam/template/`：OpenFOAM 字典的 Jinja2 模板。
- `src/backend/aircooling/third_parties/dcCartMesh/`：可编辑的 OpenFOAM snappyHexMesh 源码，以及从零写的切割单元笛卡尔网格器 **`dcCartMesh`**（`src/mesh/dcCartMesh/`，应用在 `applications/.../dcCartMesh/`）。用 `./deploy.sh`（环境检查 → 构建 → 冒烟测试）或 `./Allwmake [-j] [--cart-only]` 构建二者。不破坏原有环境：二进制落在 `platforms/<WM_OPTIONS>/bin/`，conda 的 `snappyHexMesh` 不动。见 `DCCARTMESH.md`。
- `src/backend/aircooling/third_parties/foam/`：**`buoyantBoussinesqSimpleFoam_new`** 源码与 `deploy.sh`。原版 OpenFOAM 写 `type zeroGradient;`（T）与 `type noSlip;`（U）时不带值，原始结果文件在壁面数据处有空缺；这个求解器给每个物理 patch 重写逐面值。`controlDict` 的 `explicitBoundaryValues { mode preserveType; }` 保留边界条件类型（可安全重启）——`fixedValue` 完全显式，但重启后解的是另一个问题。`STLGenRunner._resolve_solver_binary` 优先用仓库构建的版本，而不是 PATH 上的。见 `third_parties/foam/README.md`。
- `src/agent/scripted_api/`：脚本化 API（2.2.7）。
- `src/backend/aircooling/gpu_solver/`：GPU-JFNK 风冷求解器与 L0 zonal 网络（从 LPCNS 的 dcRANS 分支 vendor，`tools/vendor_gpu_solver.py [--check]`，来源提交见 `VENDORED.json`；不要在这里直接改，改 LPCNS 再 vendor）；`runner/gpu_newton.py`：runner 的 GPU 引擎接口。
- `src/backend/aircooling/scano/`：SCANO AI 推理（`pipeline` 为入口；`vendored/` 是 SCANO 上游代码，`checkpoints/` 是模型）。
- `src/agent/`：只经 `agent.scripted_api` 的精简 agent 层（10-22；11-16 并入原 `backend/api`、`backend/dcdesign`、`backend/plant`）：`dcrans/`（dcRANS 正向与反演）、`dcdesign/`（风液混合设计语言与 L0/HF 评估）、`plant/`（冷站能耗）、语义编辑即契约步骤、按保真度评估、预算、运行记录、优化器、air-v1 场景及其设计编译器、Shinaide 风液混合任务及其编译器；旧树（`training/`、`harness/` 等，10-22 删除）保留在标签 `agent-pre-slim-2026-10-22` 与研究分支上。见 `src/agent/README.md`。
- `src/backend/tests/`：后端测试。

前端与服务器：
- `src/frontend/react/`（2.2.10）；`src/frontend/shared/`、`src/frontend/shared/js/`（拆分出的前端模块，`liquid_*.js` 等）；`src/frontend/shared/vendor/`。
- `src/frontend/web/STL_generate.html`（旧版，停止维护）、`src/frontend/web/room_2d_editor.js`。
- `src/server/server.py`：REST/SSE API、认证、作业管理；`src/start_server.sh`：启动脚本。

#### 2.2.14 参考文档（按需阅读，不预加载）

| 类别 | 位置 | 说明 |
|---|---|---|
| 当前状态 | `docs/developer/PROJECT_STATE.md` | 已可用的部分、进行中的整改、下一步实验（中文） |
| 实施计划 | `docs/superpowers/plans/` | 按 YYYY-MM-DD 命名；目录被 git 忽略，较新的计划只在本机 |
| 设计规格 | `docs/superpowers/specs/` | 带日期，与计划配对；有些状态头落后于代码 |
| 开发者文档 | `docs/developer/` | 从 `index.md` 开始：安装、架构、API、runner、边界条件、render-scene 契约、液冷 |
| 用户手册 | `docs/user-guide/` | 安装、快速开始、建模、`.cfdproj` / `.cfdrun` 格式、后处理 |
| Schema 参考 | `docs/reference/` | 从 `index.md` 开始：`cfdproj-v5-schema.md`（现行）与 `cfdproj-v4-schema.md`、`cfdrun-v2-schema.md`（v2–v4）、`liquidrun-v2-schema.md`、`liquidrun-v3-dae-schema.md`、`mesh-engines.md`、`scripted-api.md`；被取代的格式在 `legacy/` |
| 基准回执 | `docs/benchmarks/` | 带日期的证据目录；代码与测试引用这些路径，原地保留 |
| 归档 | `docs/archive/` | 带日期的交接、决策快照、已完成计划；`README.md` 索引每个原路径与移动原因 |
| STLGen 用户指南 | `src/frontend/Instruction.md` | 为旧 HTML 编辑器写的中文界面说明（现在默认 React） |
| STLGen 开发提示词 | `docs/archive/frontend-history/stlgen-dev-prompts.md` | 中文，原始开发提示词 |
| BHUMANS | `BHUMANS/*.md` | 所有者自己的需求笔记（中文），历史背景，永远不改其文字 |
| Agent 层 | `src/agent/README.md` | 见 2.2.13 |
| 会议稿 | `docs/manuscript/paper_build/gen_docxml.py` | 英文，已录用的 AIFLUIDs 2026 论文（`paper_final.pdf`）；`paper.md` 是 2026-05 早期稿 |
| 公众号文章 | `docs/dctwinWechatPaper(Unpublished)/article.md` | 中文科普版 |

ISPO 相关的旧目录 `docs/research/ispo/`、`docs/manuscripts/ispo_tase/` 在 `main` 上是 10-12 的快照；ISPO 现在在独立仓库 `ISPO-AgentForDesign`。

#### 2.2.15 跨仓库同步（dctwin）

本仓库（`dcdtuni`）是 `dctwin` 的上游真源。代码用 `/sync-dcdtuni-dctwin` 与 `/sync-dctwin-dcdtuni` 同步，并做包名替换（`backend` ↔ `dctwin`）。目录对应：`src/backend/` ↔ `src/dctwin/`（Python 包改名 `backend` ↔ `dctwin`）；`src/frontend/`、`src/server/` 与算例目录两边路径相同。算例目录只同步 `.cfdproj` 与 `Ref_data/`。包括液冷在内的全部代码都同步。

#### 2.2.16 匿名数据集对照

`docs/manuscript/paper_experiment/` 为发表使用匿名名。原始工程文件都在 `case/<dir>/<name>.cfdproj`（目录名可能不同，例如 `case/GTC/GTC_2025.cfdproj`、`case/DAML-Testbed/DMAL_Testbed-patched.cfdproj`）。

| 匿名名 | 原始 cfdproj | 房间 (m) | 机柜 | ACU | 说明 |
|---|---|---|---|---|---|
| case_00_sketch | paintToDC1 | 14x7x3 | 24 | 4 | 手绘输入（参照） |
| case_01_testbed | DMAL_Testbed-patched | 6.1x3.2x2.7 | 2 | 4 | 实验室测试台 |
| case_02_leakage | Base_leakage | 4.4x6x3.6 | 4 | 2 | 泄漏场景 |
| case_03_cold_aisle | Base_coldAisleIso | 10x6x2.8 | 2 | 1 | 冷通道封闭 |
| case_04_polygon | Polygon_room | 10x6x2.8 | 2 | 1 | 非矩形房间 |
| case_05_triangle | Tri_angle | 7.6x3.8x3 | 2 | 1 | 三角形房间 |
| case_06_raised_floor | Slab | 10.4x6.9x3.4 | 8 | 2 | 架空地板 |
| case_07_compact | CMI_China | 7.9x7.9x4 | 20 | 4 | 方形紧凑布局 |
| case_08_midsize_A | Shinaide | 9x7.9x3.8 | 14 | 4 | 中型机房 |
| case_09_standard | STDCT | 18.2x8.3x3.8 | 24 | 3 | 标准机房布局 |
| case_10_dense_A | Base604 | 15.6x24.6x4.4 | 142 | 7 | 高密度机房 |
| case_11_dense_B | Base2-9 | 17x20x4.8 | 138 | 8 | 高密度变体 |
| case_12_large_A | GTC_2025 | 31x19.2x5 | 50 | 6 | 大型机房 |
| case_13_large_B | Alibaba-sige | 30x36.6x6.4 | 210 | 16 | 超大型机房 |
| case_14_large_C | Alibaba_hangchou | 41.3x20.7x7 | 200 | 8 | 超大型机房 |
| case_15_xlarge | CMI-Guichou | 17.4x38.5x4 | 238 | 46 | 特大型，46 台 ACU |

**任何发表材料或数据集输出里都不用原始名称。** 风液混合版 `case/Shinaide_Liquid/` 对外记作 `case_08_midsize_A_liquid`。

#### 2.2.17 各机器的特例

- **40901（主副本）**：工程开发都在这里；推送与同学协作见 2.2.2。同学在本机用 8105/8106 端口，不要占用。机器本身的注意事项（CPU 守护、数值计算用哪个 Python）在本机不入 git 的机器级全局文件里。
- **Pro 6000**：
  - 活检出 `~/mycode/dcdtuni` 在 `main`（11-16 起）；10 月的 ISPO 登记检出（`research/ispo-v3-20261021`、`_tmp/wt-*`）已并入 `main` 并删除。ISPO 的 `Ref_code/dcdtuni` 是指向它的软链接，所以 ISPO 的数据中心运行看到的就是这里的 `main`：改用新提交之前，先在 ISPO 对应 Variant 的 `config.toml` 登记，核对编译出的工程里哪些求解键是显式给出的（ISPO 仓库 `docs/provenance/TOOLS.md`）。
  - `main` 改过会影响数据中心 HF 结果的默认值（求解器 fast 预设、收敛判据 100 步窗口并要求能量闭合、fast 预设温度松弛 0.95、checkMesh 不通过不再停止运行），登记新提交时一并核对。
  - Base201 的三个层级 `case/Base201`（HF）、`case/Base201_L1`、`case/Base201_L0` 在这里；L0/L1 的 cfdproj 只比 HF 多 `meshConfig.coarsenLevel`，网格由 dcCart 从 HF 网格粗化。
- **40902**：dcCart 保留版本与隔离复验，只在所有者要求时使用；第三方 `DiffLiquidTwin` 只读、禁止引入。
- **Mac clone**：旧调试树，见 §1。
- **云端会话容器**：无 GPU；不做 OV 与大算例；只推送自己的 `claude/*` 工作分支。

#### 2.2.18 稿件写作（只在编辑稿件内容时适用）

只在写或改论文正文时适用（`gen_docxml.py`、LaTeX、`docs/manuscript/` 下的任何内容），不适用于代码、注释或提交信息。研究与写作精神见全局块 G5。
- **用词不拔高（Lebrun 方法）**：能用简单词就不用复杂词。禁用 "utilize" → "use"、"endeavour" → "try"、"ameliorate" → "improve"、"elucidate" → "explain"、"leverage" → "use"、"facilitate" → "enable"、"corroborate" → "confirm"。评价性开头（"Critically,"、"Importantly,"、"Notably,"）也算拔高，让内容自己体现重要性。有精确领域含义的术语（如 "buoyancy-driven flow"、"snappyHexMesh"）不算。
- **不拖延主动词**：主动词出现在主语后 10 个词以内，否则拆句；一句里并列动词不超过 3 个；句长上限约 35 词；一句里的编号贡献 "(C1)...(C2)...(C3)" 拆成独立句子。
- **流畅**：已知—新信息契约，每句以已知信息开头、以新信息结尾。不用括号引用，不写 "(Figure 1)"，写 "as shown in Figure 1"、"Table 2 summarises..."。不留孤立标题：每个节标题下先有正文再有子标题。引言以论文的直接领域开头。缩写首次出现给全称加缩写，之后只用缩写。
- **参考文献**：按首次出现顺序编号（[1] 是第一个被引用的）。归于被引论文的每个数字都要对照全文核实。作者名保留变音符号。

#### 2.2.19 关键领域概念

- **多 solid ASCII STL**：每个 `solid <name>` / `endsolid <name>` 块是 OpenFOAM 里的一个边界 patch。二进制 STL 不能携带区域名。
- **水密拓扑**：相邻区域共享完全相同的顶点坐标，无缝隙、无重叠。
- **外法向**：所有三角形法向指向流体域（右手定则）。
- **U 槽系统**（STLGenerator）：服务器机柜按水平槽位划分（44.45 mm = 1U）。带风扇的服务器形成贯通流道；无风扇设备四周为壁面。
- **水转印**（STLGenerator）：对导入的 STL，patch 是二维平面形状，沿某个向量投影到曲面上，再用 CSG 提取成独立 solid。

### 2.3 在 Research 卡里工作时

- 本卡只放构思、计划、裁决与状态；重跑或新增实验去对应服务器仓库，结果记在服务器，再把结论性摘要写回本卡 `README.md`（覆盖式）与 `LEDGER.md`。
- 保留项（Research 根 `CLAUDE.md` 目录规则第 9 条之外的本卡特例）：
  - `LiquidCooling-DC/NV/` 原始资料只读：不修改、不移动；其中逐字节相同的副本（`NV/液冷系统/` 与 `NV/液冷系统 2/`、两个 `液冷系统*.zip`）也须所有者明确授权才可归档。
  - `LIQUID_DAE_UPGRADE_PLAN_2026-09-28.md` 在 Stage 5–7 收口前保留；`PIPELINE_SMOOTHNESS_RESEARCH_2026-09-28.md` 与 `TRANSIENT_KEYFRAME_REQUIREMENTS_2026-09-30.md` 在其中裁决落地前保留。
  - 两个子卡各保留一份薄 `AGENTS.md` + `CLAUDE.md`（不是同步文件）；子卡旧指南的历史在本项目 `LEDGER.md`。
- 归档在 `Research/_archive/DCTwin/`（冻结，只作恢复与追溯）：`history/`（旧入口、旧 README 日志、09-28 主线地图、旧 AGENTS 快照段）；`docs_2026-09/`（09 月已收口计划与回执）；`dcdtuni_results/`（旧路线结果的只读镜像，不是权威副本，关键数字回到服务器日志核对）；`DCTwin-Journal/`（旧 DDL/TNNLS 路线快照、ISPO v1、旧脚本；其中 `DDL_paper_2026-09-14_SUPERSEDED_do_not_read.pdf` 与 `_superseded/` 不读不引）；`20260928/`（40901/40902/Mac 的自足 git bundle 与 stash patch）。
- OneDrive 文件读取失败先怀疑是未下载的云端占位文件；路径含空格与中文，shell 命令加引号。

## 3. 状态与历史

- 现状与历史：同目录 `LEDGER.md`。
- 跨项目现状：Mac `Research/LEDGER.md`。
- 旧版指南全文：Research 卡侧归档在 `Research/_archive/DCTwin/history/`；仓库侧见 git 历史（`AGENTS.md`、`.claude/CLAUDE.md` 的旧版本）。
