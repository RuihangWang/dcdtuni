我现在想构思一篇面向 AIFLUIDs / AI for CFD / data-center CFD automation 方向的论文。请你基于下面背景，帮我继续细化论文定位、创新点、实验设计、baseline 对比、论文结构和摘要。

我的项目背景如下：

我开发了一个面向数据中心 CFD 模拟的软件。后端基于 OpenFOAM，前端有 GUI，并且有一套完整的 JSON-defined project file。这个 JSON 工程文件可以描述数据中心的几何、机柜、服务器功耗、风口/CRAC、边界条件、网格加密区域、求解参数等。系统可以根据 JSON 自动生成几何模型、导出 STL、定义 mesh refinement、生成 OpenFOAM case 文件，并执行 CFD 仿真。

我的想法是结合 Claude Code / GPT / agentic AI 工具，让 AI 根据数据中心的原始工程资料，例如 CAD 图纸、Excel 表格中的服务器或机柜功耗数据、设备清单、文字设计说明等，直接编写这个 JSON project file。然后我的确定性后端 compiler 根据 JSON 生成 STL、网格定义、边界条件和 OpenFOAM 求解文件。前端 GUI 可以完整显示 AI 生成的数据中心几何外形、机柜布局、风口、热源、边界 patch、refinement zone 等，使 AI 和人类工程师可以共同 debug。人类可以通过 GUI 检查和修正 AI 生成的工程模型，再回写 JSON 并重新生成 OpenFOAM case。

我希望这篇论文不是简单写成“我做了一个 OpenFOAM case generator”，而是写成一个更学术的问题：

How can large language model agents reliably convert heterogeneous data-center design documents into verifiable, human-debuggable OpenFOAM CFD cases?

或者：

A human-AI collaborative, schema-constrained OpenFOAM case generation framework for data-center CFD.

论文的核心定位应当是：

LLM-assisted, schema-constrained, human-verifiable OpenFOAM CFD case generation from heterogeneous data-center engineering documents.

我希望文章强调的不是 AI 直接生成 OpenFOAM 文件，而是：

1. LLM 从 CAD、Excel、设计说明中抽取工程语义；
2. LLM 生成受 schema 约束的 JSON project representation；
3. JSON 经过 validation layer 检查；
4. deterministic compiler 生成 STL、mesh refinement、boundary patches、OpenFOAM dictionaries 和 solver configuration；
5. GUI 显示 AI 生成的工程模型，使人类可以检查、纠错、回写 JSON；
6. 最后运行 OpenFOAM 并验证 CFD 结果。

我希望论文主张是：

The LLM is used for semantic extraction and project scripting, while all CFD file generation is performed by deterministic, schema-validated translators.

或者：

The LLM does not directly generate STL or OpenFOAM dictionaries. It generates a schema-constrained CFD project representation, which is then compiled deterministically into geometry, mesh, boundary conditions, and solver files.

相关工作和需要区分的方向如下：

一、OpenFOAMGPT / LLM for OpenFOAM automation

OpenFOAMGPT 这类工作主要是把 LLM、RAG 和 iterative correction loop 用于 OpenFOAM workflow automation。它通常从自然语言 prompt 出发，让 agent 生成或修改 OpenFOAM tutorial-like case。创新点包括 RAG 检索 OpenFOAM tutorial、自动运行 solver、根据 error log 迭代修复、比较 GPT-4o / o1 等模型，以及展示 case setup、边界条件修改、湍流模型替换、OpenFOAM 版本迁移等能力。

这类工作的实验通常看：
- zero-shot case setup；
- RAG/few-shot case setup；
- case 是否能运行；
- solver 是否收敛；
- correction iteration 次数；
- token cost；
- 在 cavity、PitzDaily、Hotroom、Dambreak、particle column、mixed vessel 等 tutorial cases 上测试。

但 OpenFOAMGPT 更像早期 demonstration paper。它主要证明 agent 能跑 OpenFOAM，而不是严格证明复杂工业工程资料到 CFD-ready case 的语义准确性和物理保真度。

我的工作应和它区分为：

OpenFOAMGPT/Foam-Agent 解决 natural language → OpenFOAM tutorial case automation；
我的工作解决 heterogeneous engineering documents → verifiable data-center CFD digital model → OpenFOAM case generation。

二、Foam-Agent / MetaOpenFOAM / ChatCFD 等后续工作

后续 LLM-CFD agent 工作更系统，包括 multi-agent architecture、hierarchical RAG、reviewer node、dependency-aware file generation、HPC script、ParaView/PyVista post-processing 等。实验上开始使用更大的 benchmark，例如 100+ OpenFOAM tasks，并和 MetaOpenFOAM、OpenFOAMGPT-like baseline 对比。

ChatCFD 这类工作特别值得借鉴，因为它强调“能运行不等于物理正确”。它提出 physical fidelity 的评价思路：初始/边界条件是否匹配用户描述，物性和模型是否合理，后处理流场特征是否与 ground truth 对齐。很多 runnable cases 虽然能收敛，但边界条件、热源、初始场或多相设置可能是错的。

我的论文应借鉴这一点：对于数据中心 CFD，机柜功耗、风口方向、边界类型、热源映射、refinement zone 即使错了，OpenFOAM 也可能正常收敛，但结果没有工程意义。因此我的评价指标不能只看 case 是否能跑，而要看工程语义准确性和物理保真度。

三、ChatTwin / ChatDC / Li Minghao 相关工作

Minghao Li 等人的 ChatTwin 是面向 data-center digital twin generation 的工作。它用 GPT-4 自动生成数据中心 digital twin 的 scene description document，例如 JSON / DTDL / USD 一类层级描述文件。核心方法是 Segment-and-Generate，即把长文本生成任务按数据中心设施层级拆成 room、ACU、rack、server 等子任务，然后生成 JSON 片段并拼接，之后用规则或整数规划修正不合理数值。

后续 ChatDC 加入 geometric-aware generation 和 Segment-Generate-Optimize workflow，并用 DCBuild 等领域代码库提高生成数据中心 digital twin scene 的成功率和几何合理性。再往后还有 Fusion Intelligence、Phythesis、DCoPilot 等，把方向扩展到 GenAI + PhyAI、data-center digital twin platform、生成式数据中心设计、控制策略生成等。

我需要和这条线清晰区分。不能写成“LLM 自动生成数据中心数字孪生”，否则会和 ChatTwin/ChatDC 撞题。

我的差异化应写成：

ChatTwin and ChatDC demonstrate that LLMs can generate data-center digital twin scene descriptions by decomposing long-text generation into structured subtasks and optimizing generated layouts under domain constraints. However, their focus is scene-level digital twin generation, whereas our work targets CFD-ready OpenFOAM case construction from heterogeneous engineering documents, including geometry export, mesh refinement, boundary-condition assignment, solver configuration, and human-in-the-loop visual verification.

核心区别：
- ChatTwin/ChatDC 是 LLM-to-digital-twin scene；
- 我的工作是 LLM-to-verifiable OpenFOAM CFD case；
- ChatTwin/ChatDC 更偏 scene/layout/digital twin generation；
- 我的工作更偏 CFD-specific semantics、OpenFOAM case compiler、physical fidelity、GUI human-AI debugging。

四、MIT VideoCAD / CAD agent / CAD-Llama 相关工作

最近 MIT 有 VideoCAD 这类工作，目标是让 agent 学习 CAD UI 操作，从 video / UI action sequence 中学习如何在 Onshape 等 CAD 软件中建模。它的贡献不是简单“做 CAD copilot”，而是指出专业工程软件自动化比普通 UI agent 更难，因为 CAD 操作链长、几何约束强、需要精确参数和 3D 推理。它通过构建 benchmark、定义 command accuracy、parameter accuracy、perfect action sequence、Chamfer distance、invalid CAD rate 等指标来组织论文。

还有 CAD-Llama 这类工作，把参数化 CAD 历史转成类似 Python 的 Structured Parametric CAD Code, SPCC，让 LLM 学会生成和编辑参数化 CAD。

我的方法和这些工作有相似点，因为我的软件也能通过脚本定义对象尺寸、rack、U-slot、服务器、几何 primitive，并自动生成 STL。但我不能只强调“AI 生成参数化 CAD/STL”。我的创新应当是：

普通 CAD primitive 只描述形状；
我的 data-center CFD primitive 同时携带几何和物理语义。

例如一个 rack object 不仅有：
- width, depth, height, position, orientation, U-slots；

还应有：
- total power, per-U power, server power distribution；
- inlet face, outlet face, porous resistance or fan curve；
- patch labels and boundary condition mapping；
- mesh refinement level；
- post-processing probe planes；
- OpenFOAM boundary-condition semantics。

因此我的 JSON 更像 CFD-oriented parametric intermediate representation，而不是普通 CAD representation。

可以将我的中间表示命名为：
- DataCenterCFD-IR；
- DCFoam-JSON；
- CFD-oriented data-center project schema；
- Data-center CFD intermediate representation。

我希望借鉴 VideoCAD 的论文组织逻辑：

不要写成“我做了一个工具”，而要写成：

现有 AI-CAD 和 LLM-CFD 工作缺少某个关键能力；
我定义了一个新的任务；
提出一个中间表示；
构建了一个 workflow；
并用 benchmark 和 baseline 证明它有效。

我的 gap 可以写成：

Existing CAD agents can learn to construct 3D geometry, and existing LLM-CFD agents can generate or modify OpenFOAM tutorial cases. However, practical data-center CFD requires converting heterogeneous engineering documents into a CFD-ready representation that captures geometry, thermal loads, airflow boundaries, mesh refinement, solver settings, and post-processing definitions. This document-to-CFD translation remains largely manual, error-prone, and difficult to verify.

建议论文方法 pipeline：

CAD / floor plan / Excel power table / equipment schedule / design notes
→ LLM document agent
→ schema-constrained DataCenterCFD-IR JSON
→ validation layer
→ parametric geometry compiler
→ STL + patch metadata
→ mesh/refinement compiler
→ OpenFOAM dictionaries
→ GUI visual inspection and correction
→ OpenFOAM run and physical validation

建议论文贡献：

1. A CFD-oriented data-center project schema / intermediate representation:
   用 JSON 统一表达数据中心几何、rack/U-slot、服务器热源、风口/CRAC、边界条件、网格加密、求解参数和后处理设置。

2. A document-to-CFD generation workflow:
   从 CAD、Excel、设备清单和设计说明中抽取工程语义，而不是只从自然语言 prompt 生成简单 tutorial case。

3. A deterministic JSON-to-OpenFOAM compiler:
   自动生成 STL、patch metadata、mesh refinement 和 OpenFOAM dictionaries，避免 LLM 直接写 solver 文件导致 hallucination。

4. A GUI-based human-AI debugging loop:
   工程师可以可视化检查 rack 位置、U-slot/服务器功耗、风口方向、热源映射、boundary patch、refinement zone，并把修正回写到 JSON。

5. Optionally, a small benchmark:
   构建一个 data-center CFD generation benchmark，包含不同复杂度的数据中心案例，用工程语义准确性、OpenFOAM 可运行性、物理保真度和人工修正成本评估系统。

建议 baseline：

1. Manual expert baseline:
   CFD 工程师手工从 CAD/Excel/设计书建立 OpenFOAM case，作为 ground truth 或 upper bound。

2. Direct LLM-to-OpenFOAM baseline:
   让 Claude Code / GPT 直接生成 OpenFOAM case 文件或 STL 脚本，不经过 JSON schema 和 GUI。

3. LLM-to-JSON without validation:
   AI 生成 JSON，但不进行 schema validation 或 rule checking。

4. LLM-to-JSON without GUI:
   AI 生成 JSON 并直接编译 OpenFOAM case，但不做人类可视化检查。

5. Full system:
   LLM + schema validation + deterministic compiler + GUI correction + OpenFOAM run。

6. Optional template/script baseline:
   使用固定模板或传统参数化脚本生成 case，不使用 LLM，用来证明我的方法处理 heterogeneous design documents 时更灵活。

建议实验指标：

A. Engineering semantic accuracy:
- rack count accuracy；
- rack position error；
- rack dimension error；
- U-slot/server mapping accuracy；
- server/rack power mapping MAE；
- CRAC / vent / perforated tile count accuracy；
- airflow direction accuracy；
- boundary patch type accuracy；
- refinement zone coverage；
- JSON schema pass rate。

B. Geometry fidelity:
- STL validity；
- non-manifold error count；
- Chamfer distance between generated geometry and manual geometry；
- IoU / overlap ratio；
- patch area error；
- object centerline / bounding box error。

C. OpenFOAM executability:
- mesh generation success rate；
- solver convergence rate；
- number of correction loops；
- number of failed solver runs；
- token cost；
- setup time；
- manual correction time；
- wall-clock case generation time。

D. Physical fidelity:
- rack inlet temperature MAE；
- maximum inlet temperature error；
- hotspot location accuracy；
- pressure drop error；
- mass balance residual；
- energy balance residual；
- velocity field similarity；
- temperature field similarity；
- difference from manually prepared OpenFOAM case or sensor/experimental data。

E. Human-AI GUI debugging value:
- number of errors caught before solver execution；
- number of semantic errors remaining after AI generation；
- correction time with GUI vs without GUI；
- comparison between no-GUI and full GUI workflow；
- qualitative examples of errors: wrong rack orientation, wrong power mapping, reversed inlet/outlet, missing patch, refinement zone not covering hot region, wrong CRAC boundary type。

建议 benchmark cases：

可以构建 5–12 个数据中心案例，按复杂度分为 short / medium / long：

Short:
- single room；
- 10–20 racks；
- uniform rack power；
- simple inlet/outlet or CRAC；

Medium:
- hot aisle / cold aisle；
- non-uniform rack power from Excel；
- multiple CRACs；
- local mesh refinement；
- several vents or perforated tiles；

Long:
- raised floor；
- perforated tiles；
- containment；
- obstacles；
- irregular room layout；
- multiple cooling zones；
- nonuniform server-level or U-slot-level power；
- CAD + Excel + textual design description combined input。

每个 case 最好有：
- input documents；
- manually authored ground-truth JSON；
- manually prepared OpenFOAM case；
- baseline CFD results；
- automatic generated results。

建议论文结构：

1. Introduction
   - 数据中心 CFD 对冷却优化、热点识别、能耗分析很重要；
   - 但从 CAD、Excel、设备清单到 OpenFOAM case 高度手工、耗时、易错；
   - LLM-CFD agent 和 AI-CAD agent 已经出现，但还没有很好解决 heterogeneous engineering documents → CFD-ready OpenFOAM case；
   - 提出 human-AI collaborative, schema-constrained CFD case generation framework。

2. Related Work
   - LLM for CFD / OpenFOAM automation: OpenFOAMGPT, MetaOpenFOAM, Foam-Agent, ChatCFD；
   - LLM / AI for data-center digital twin generation: ChatTwin, ChatDC, Fusion Intelligence, Phythesis；
   - AI-CAD / parametric CAD generation: VideoCAD, CAD-Llama；
   - Human-in-the-loop scientific/engineering workflow；
   - 明确指出本文的不同：CFD-ready, OpenFOAM-specific, schema-constrained, GUI-verifiable, document-grounded。

3. Problem Definition
   - 输入：CAD、Excel power table、equipment schedule、design notes；
   - 输出：valid OpenFOAM case；
   - 中间：DataCenterCFD-IR JSON；
   - 任务：document-to-CFD case generation；
   - 约束：geometric validity, physical consistency, OpenFOAM executability, human verifiability。

4. Method
   - LLM document agent；
   - JSON schema / DataCenterCFD-IR；
   - validation layer；
   - geometry/STL compiler；
   - mesh/refinement compiler；
   - OpenFOAM dictionary generator；
   - GUI human-AI debugging loop；
   - case execution and post-processing。

5. Benchmark / Experimental Setup
   - cases；
   - baselines；
   - metrics；
   - models used: Claude Code / GPT / possibly open-source LLM；
   - hardware/software: OpenFOAM version, mesh tool, solver, GUI.

6. Results
   - semantic extraction accuracy；
   - geometry fidelity；
   - OpenFOAM executability；
   - physical fidelity vs manual baseline；
   - human-AI GUI debugging ablation；
   - cost/time analysis。

7. Discussion
   - why schema-constrained generation is more reliable than direct LLM-to-OpenFOAM；
   - what errors remain；
   - limitations: CAD parsing quality, ambiguity in design documents, model dependence, need for expert review；
   - future work: online sensor calibration, optimization, digital twin integration, surrogate modeling。

8. Conclusion
   - summarize: proposed a human-AI collaborative CFD case generation framework for data centers;
   - highlight: document-grounded, schema-constrained, deterministic compiler, GUI-verifiable, OpenFOAM executable, physically evaluated.

Potential titles:

1. From Design Documents to OpenFOAM: A Human-AI Collaborative Parametric Case Generator for Data-Center CFD

2. Schema-Constrained LLM Automation of Data-Center CFD Case Generation with Human-in-the-Loop Geometry Debugging

3. A CFD-Oriented Parametric Intermediate Representation for LLM-Assisted Data-Center OpenFOAM Case Generation

4. Human-AI Collaborative OpenFOAM Case Generation for Data-Center CFD from Heterogeneous Engineering Documents

5. DataCenterCFD-IR: A Schema-Constrained Intermediate Representation for Verifiable LLM-Assisted CFD Case Generation

Suggested abstract draft:

Large language models have recently shown promise in automating CFD workflows, but their direct use for industrial case generation remains limited by hallucinated solver files, weak geometric grounding, and poor debuggability. This work presents a human-AI collaborative case generation framework for data-center CFD. The framework converts heterogeneous design documents, including CAD layouts, equipment schedules, and rack-level power spreadsheets, into a schema-constrained JSON project representation. A deterministic compiler then generates STL geometry, mesh refinement definitions, OpenFOAM dictionaries, boundary conditions, solver configurations, and post-processing definitions. Unlike prompt-to-case systems, the proposed workflow exposes the generated engineering model through an interactive GUI, allowing users to visually inspect, correct, and regenerate the CFD case before execution. We evaluate the method on representative air-cooled data-center cases and compare generated simulations against manually prepared OpenFOAM baselines in terms of engineering semantic accuracy, geometry fidelity, case validity, setup time, and flow/temperature prediction fidelity. Results show that schema-constrained AI generation combined with human visual debugging can reduce CFD setup effort while preserving engineering traceability, physical consistency, and solver reliability.

请基于以上上下文，帮我进一步优化：
1. 论文的核心 scientific contribution；
2. 最有说服力的 problem formulation；
3. 如何写 related work 才能和 OpenFOAMGPT、Foam-Agent、ChatCFD、ChatTwin、ChatDC、VideoCAD、CAD-Llama 产生清晰区分；
4. 具体实验表格应该怎么设计；
5. 如果只能做 3–5 个实验，哪些最重要；
6. AIFLUIDs 投稿时应该如何包装这个工作。


and The required info are in /mnt/lv/litianyu/mycode/dcdtuni/docs/manuscript here

/brainstorming