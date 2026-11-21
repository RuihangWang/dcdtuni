# src/agent 精简重构设计（2026-10-22）

所有者决定（2026-10-22）：把 `src/agent` 重构为精简 agent 层，以 `backend.api` 为唯一工具接口；旧的按日期探针与门控整体移出主干、保留可追溯；验收只跑 CPU 测试与小算例冒烟，不训练、不产出研究结论。

调研依据（只读，带 文件:行号）：`_tmp/campaign_reports/agent_{inventory,pipeline,platform,research}.md`，脚本与 JSON 在 `_tmp/agent_understand/`。

## 1. 现状要点

- 991 个 `.py`、约 12 万行。按依赖闭包分类：A1 产品向核心 88 个文件 + A1 必需依赖 19 个（约 1.28 万行）；A2 基准与旧 DDL 58 个；B 探针/门控/回读/发布 674 个；C 死代码 12 个；测试 131 个。B 约占 71%，主要来自"复制一份再升版本号"（769 个文件带 `_vN`）。
- 依赖方向干净：A→B 的边为 0，B 整体移走在静态上可行。
- agent 里没有一个文件用 `backend.api`。平台求值走 5 条私有路径（进程内 `backend.liquidcooling`、子进程直跑 `run_cfd`、继承 `STLGenRunner`、自带 JSDOM `.mjs/.ts` 导出驱动、旧口令 `PlatformSession`），其中两条违反调试规则 1/8。
- 本机测试 1169 条：1012 通过，153 未过，全部属于环境、缺 Pro6000 产物、冻结身份漂移（泵律已由平台采纳），没有真实断言回归。
- 外部引用：`src/backend/aidesign/`（11 个反向转发壳，backend→agent 方向错误）、`ddl/src/llm/**`（13 个转发壳）、`src/backend/tests/test_projcomp_template_version.py`、`case/agent/**`（28,885 个文件引用 952 个模块的路径或 SHA）、109 个 `python -m agent.…` 命令、研究稿 `verify_draft.py` 与 `figures/*.provenance.json`。
- 研究线已迁往 ISPO-AgentForDesign；它的 v3 分支示范了"研究仓库依赖 dcdtuni"的方式。主干要为它提供稳定接口，而不是让它复制代码。

## 2. 目标结构

```
src/agent/                      # 精简主干，只单向依赖 backend.api（和 numpy/torch 等通用库）
  __init__.py                   # 版本号、__all__；安装旧名查找器；导入时不改环境变量
  api.py                        # 稳定公开面（见 §3）
  tools/                        # 唯一的平台层
    project.py                  # edit_project / query_project / project_identity / validate_project
    edits.py                    # 语义编辑 → build 步骤（MoveModel、RotateModel、RemoveModels、SetServerLoad、
                                #   SetRackPower、SetAcuSupply、AddProbe、SetCduSupply、SetPumpSpeed、SetValveOpening、SetPipeDiameter）
    evaluate.py                 # admit_air / mesh_air / solve_air / evaluate_air / evaluate_liquid / liquid_gradient /
                                #   evaluate_hybrid / evaluate_many —— 全部返回原样 result.json + 轻量类型化视图
    results.py                  # read_result 与视图；平台已给出的数不再重算
    fidelity.py                 # Fidelity（ADMISSION/LF/LIQUID_STEADY/LIQUID_TRANSIENT/AIR_MESH/AIR_FULL/HYBRID）+ evaluate() 分派、预算与记录
  scenarios/
    air/                        # air-v1：spec、energy(+coilwise)、metrics、objective、tolerances、rules、placement、bc、
                                #   l0_network、l0_geometry、compile（设计 → build 步骤，取代 JSDOM 驱动与 projcomp）
    hybrid/                     # Shinaide 风液混合：task contract、training task spec、six-ACU protocol/geometry、constraints、
                                #   energy(+inactive ACU)、zonal air networks（open/CAC/HAC/perimeter + numerics）、thermal stream、
                                #   codec、design compile（→ build 步骤 + export_liquidrun）、LF oracle（liquid 走 backend.api）
  optim/                        # estimators（HF 采购概率/控制变量/AIPW/RLOO…）、ISPO 控制器、predictable controls、
                                #   optimizers（heuristic / CMA-ES / MFBO / adjoint-descent，抽成通用 oracle 接口）
  policies/                     # 可选 extra：numeric / structured / language 策略头、joint/information losses（torch；transformers 惰性）
  records/                      # RunStore（原 artifacts）、RunLogger（原 runlog）、CostLedger（process_costs + main_study_costs）、
                                #   owned process groups；根目录作为参数注入，默认仓库内
  cli.py / __main__.py          # python -m agent {evaluate,edit,optimize,identity} …
  tests/                        # 每个公开符号一份 CPU 契约测试 + 保留的 A 类测试

src/agent_legacy/               # B、C、A2、T-B 及旧 DDL 链路，按原字节移入，目录深度不变（parents[3] 仍是仓库根）
  PATH_MAP.json                 # 旧路径 → 新路径 → sha256（覆盖证据里引用的全部路径）
  README.md                     # 只读、可追溯、不保证可运行；如何按路径与 tag 取回原字节
```

冻结 tag `agent-pre-slim-2026-10-22` 指向重构前的提交，`git show <tag>:src/agent/<旧路径>` 永远能取回原字节。

## 3. 稳定公开面 `agent.api`

| 接口 | 来源 | 说明 |
|---|---|---|
| `Task` / `Design` / `Scenario` 协议 | `scenario_air.spec`、`controlled2d_tasks` 的 validate/identity 先例 | `validate(task, design) -> list[str]`、`compile(task, design) -> list[build step]`、`identity() -> sha256` |
| `Codec` 协议 | Shinaide codec | 语法文本 ↔（离散 s，连续 x），暴露数值定义域和 schema 版本 |
| `Evaluator` / `EvaluationRecord` / `Fidelity` | `tools.fidelity`、`RunLogger.physics_call` | 记录 fidelity、支持的导数、validity、costs、evaluator 版本、design 与 result 身份 |
| `CostLedger` | `process_costs`、`main_study_costs` | wall、自身 CPU、已回收子孙 CPU；C0 只计一次；失败按尝试计费 |
| `Acquisition` | `methods.estimators` | `allocation_probabilities` 等；加"先登记、后采购"的小 ledger（q 与预测量在 outcome 之前落盘） |
| `RunStore` / `RunLogger` / `new_run_id` | `harness.artifacts`、`harness.runlog` | 写入根作参数注入；默认 `case/agent/<case>/` |
| `tools.*` | `backend.api` | §2 列出的全部函数 |

`__all__` 带语义化版本号 `AGENT_API_VERSION = "1.0"`；每个公开符号一份 CPU 契约测试。

## 4. 迁移规则

1. 主干模块去掉 `_vN` 后缀、合并"旧版冻结、新版复制"的族：每个概念只保留下游实际在用的最新语义；被折叠的 A1 依赖（19 个）并入对应新模块。
2. 平台调用一律改走 `backend.api`：自带 JSDOM 驱动、直跑 `run_cfd`、继承 `STLGenRunner`、`PlatformSession`、`backend.liquidcooling` 私有类（包括 `pump_dae_nominal_affinity`——平台已采纳该泵律）全部不进主干。I3 泵梯度改用 `run_liquid_case` 的隐式伴随（`pump_rpm_ratio`）加 `fd_check`。
3. 编辑语义：关闭对象用删除（`removeModels`），不用 `setEnabled`；液冷管网编辑缺的契约动词（泵转速、阀开度、管径）在 React 自动化契约里补齐并加入 `BUILD_VERBS`。
4. 旧名兼容：重构前的整个包（含 A 类原文件）按原字节移到 `src/agent_legacy/`，成为一份完整、自洽的冻结快照。新主干的子包名与旧名不重叠（`api`、`tools`、`scenarios`、`optim`、`policies`、`records`、`cli`），`agent/__init__.py` 安装一个 MetaPathFinder，把新主干里没有的 `agent.<旧名>`（`harness`、`training`、`analysis`、`methods`、`scenario_*`、`_runtime`、旧 `tests`）按旧名从 `src/agent_legacy/` 下的原文件加载，所以旧代码之间的相互导入、`python -m agent.<旧名>` 与 ddl 的转发壳都照常工作，且不会被解析到语义已变的新模块上。新主干模块由 A 类原文件重写而来，不 import 任何旧名。
5. `src/backend/aidesign/` 退役（backend 不再 import agent）；`test_projcomp_template_version.py` 随 projcomp 进入 legacy 测试。
6. 证据文件一律不改：路径变化由 `PATH_MAP.json` 与 tag 解释。
7. `pyproject.toml` 显式打包 `agent`（不含 `agent_legacy`）；LLM 依赖做成可选 extra。
8. `.gitignore` 中任意深度的 `tests/`、`scripts/` 规则要给新目录加例外，避免新文件被静默忽略。

## 5. 验收（CPU 与小算例）

- 主干测试在本机全绿：新契约测试 + 保留的 A 类测试（场景、目标、约束、能耗、L0 网络、记录、估计器、优化器）。
- 冒烟（经 `agent.tools`，进程总数 ≤ 8）：SingleRack `mesh_air`；LiquidSingle_Control `evaluate_liquid(steady, fd_check)` 与 `liquid_gradient` 对得上；Shinaide `evaluate_hybrid`（短 end-time）；air-v1 场景用一个优化器在 LF 档跑几轮并记录，再取最优候选做一次 `admit_air`。
- 迁移完整性：`PATH_MAP.json` 中每个旧路径的 sha256 与 tag 上的原字节一致；109 个 `python -m agent.<旧名>` 能被查找器解析（只做 `find_spec`，不执行）；`case/agent` 与研究稿里引用的路径全部可解析。
- 文档：`src/agent/README.md`、`AGENTS.md` 的 ISPO 路径说明、`docs/research/ispo/README.md`、`docs/AI_DESIGN_EVALUATOR.md`、`.claude/CLAUDE.md` 同步。

## 6. 不在本次范围

- 训练、部署、协议登记、运行监管（留在研究仓库）。
- 研究 v3 分支的 `backend/dcdesign`、`backend/plant`、`backend/aircooling/zonal` 并入 main（另行决定）。
- `backend.api` 的 CPU/GPU 成本字段与评估器版本字段（列为下一步）。
