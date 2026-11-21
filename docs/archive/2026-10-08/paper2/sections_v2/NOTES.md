# `sections_v2/NOTES.md` — 改写说明、核实清单与数字溯源

日期：2026-09-15（Asia/Singapore）。所有核实均在 `NTU-Pro6000` 上用一次性
`curl` 抓取 primary source 完成，未采信任何模型的记忆，也未采信本地那份
`RELATEDWORK_audit_2026-09-15.md` 的结论（只把它当 leads）。
写入的文件只有四个：`related_work.tex`、`contributions.tex`、
`references_additions.bib`、本文件。`main.tex` 未动，未 commit，未碰其它文件。

---

## 0. 交付物与编译验证

- `related_work.tex`：§II 的 drop-in 替换（从 `\section{Related Work}` 到
  Table I 结束）。六个 subsection：A 估计器、B 物理梯度进 learned designer、
  C LLM design agent、D 离散结构 + 连续物理、E concurrent work、F positioning。
  Suh / Onoda / Scheffler / Hu 在**第一段**就被正面引用，delta 在第二段就说清。
- `contributions.tex`：§I.B `Contributions` 的 drop-in 替换（含原来
  "We are explicit about what is not new" 那段的功能，改写成
  `\emph{What we do not claim.}` 五条否定陈述）。每个数字旁边有
  `% source:` 注释，指明 case 与 artefact 路径。
- `references_additions.bib`：Section A 新增 8 个 key；Section B 修复 17 个
  现有 key（key 名不变，所以 `main.tex` 里的 `\cite` 一个都不用动）；
  Section C 6 个可选 metadata 补全；Section D 是"故意不碰"的清单。
  每条上方有 `% verified: <source> <date>`。
- 编译验证：在本地 `/tmp` 建了临时目录（服务器只读，未写任何文件），把
  `main.tex` + `references.bib` + 新文件拼成 `main_v2.tex` / `merged.bib`
  （合并时 additions 覆盖同名 key），`latexmk -pdf` 通过，14 页，
  **bibtex 0 warning、0 undefined citation、0 undefined reference**。
  唯一的 overfull hbox（4.88pt）在 §III 原有的公式 (1)，与本次改动无关。
  新的 Table I 改成了 `table*`（双栏宽），因为加了 2 列后单栏放不下；
  渲染检查过，6 列 10 行 + dagger 脚注都在版心内。若必须单栏，删掉最后一列
  即可，正文不依赖该列。

---

## 1. 改了什么、为什么（framing 层面）

1. **放弃"pathwise vs score-function"的框架级结论，把它还给 Suh/Onoda/
   Scheffler/HPO。** 新 §II 第一段就承认：first-order 估计器在 nonsmooth 事件
   附近 low-variance/high-bias、bias 可以被 corrected/bounded、pathwise+SF 可以
   无偏混合，这四件事分别由 Suh 2022（α-order estimator）、Onoda 2026
   （DDCG + IVW-H）、Scheffler 2026（corrected first-order randomized
   smoothing）、Alvo 2026（HPO，保持无偏）拥有。我们的 estimator 侧内容被
   明确降级为"一个工程域里的测量"。Contribution 2 的结尾显式写了
   "we give no bound on the pathwise bias and no unbiased first-order estimator"。
2. **把 novelty 从"机制"移到"relaxation 是一个物理设计"。** 新增的 Table I
   列 `relaxation is physical` 就是这条 claim 的载体；§II 第二段说明
   Suh/Onoda 的 nonsmoothness 在 dynamics 里，我们的在 model 输出的 design
   里，因此它是一个 modelling object 而不是 estimator correction。
3. **Concurrent work 单独成节并正面区分。** §II.E 用 arXiv 2609.06323 的
   abstract 与 HTML 全文里核实过的事实（52 networks、8,140 frames、
   1.137e-13 ft、906→1 solve、595 vs 8,060 model calls；以及全文中
   "language model/Gumbel/straight-through/token/relax" 出现 0 次、
   "combinatorial" 出现 2 次，含原句 "status switching is a genuine
   combinatorial event: ... The backward pass freezes the configuration the
   forward pass converged to."）说明：他们的 decisions 是连续参数、组合部分被
   freeze；我们的问题是会跨越那个 boundary 的扰动。并明说"读者要
   production-grade differentiable hydraulic solver 应该用他们的"。
4. **Contribution 4 从"我们赢了 GRPO"改成"诚实记账"。** 只保留数据支持的：
   4B 的 feasibility 提升（Fisher p=0.014，27B p=0.10 不显著 → 4B-only、
   provisional）、95–100% 的 J 下降是 penalty、feasible subset 上真实设计改进
   只有 6–18%、达不到 label 的质量、per-task 输给 2 s 的 gradient-free search、
   break-even 两个轴（~340 tasks wall clock / ~3.3 tasks physics budget）。
   统计口径的修正（seed-level t 区间重叠、n=3 时 exact floor p=0.10/0.25、
   population divisor 使 ±sd 小 1.22×、"median" 实为第 9/16 个 order
   statistic、step-0 sd 恒为 0、≥5 seeds 必需）全部写进 contribution 4。
5. **Benchmark validity 升级为 contribution 3。** 把 E0a 的发现（reverse-flow
   root、414/13122、372×、1.4e10 dynamic range、rel_bias=1.000/rel_var=0.000
   对所有 arm 同时成立）写成"benchmark 不成立则一切测量作废"，并给出修复
   （check valve + saturated penalty）、adjoint 存活的证据、以及与 production
   solver 上 limit cycle 的同源性，接到 Onoda 的 nonsmooth-event regime。

另外两处 characterisation 修正（旧稿会被审稿人抓的点）：GRADE 与 DiffRO 不再
并列声称"order-of-magnitude variance reduction over REINFORCE"——只有 GRADE
报了 >14×（arXiv abstract 原文），DiffRO 是把 reward 对 codec token 可微、
没有方差对比；TopoTransformer 不再写成 "a transformer over design states"，
按核实过的 abstract 写成 sensitivity-conditioned Bernoulli flow matching +
DPI 论证 + pseudo-sensitivities，并指出它把 adjoint 当 **input field**、
我们当 **weight 的梯度**。Phythesis 改写成 LLM + physics-guided evolutionary
的 bi-level、gradient-free（自引，已注明）。

---

## 2. Bibliography 逐条核实清单（verified = 37 条）

方法缩写：**arX** = `curl -sL https://arxiv.org/abs/<id>` 读
`citation_author/title/date` 与 `tablecell comments/jref`；
**CR** = `https://api.crossref.org/works/<doi>`；**OA** = OpenAlex；
**PMLR** = proceedings.mlr.press；**HAL** = api.archives-ouvertes.fr；
**HTML** = arxiv.org/html 全文 grep。全部执行于 2026-09-15。

新增 8 条（references_additions.bib Section A）：
| key | 核实 |
|---|---|
| `suh2022diffsim` | arX 2202.00817（4 作者、jref "ICML 2022"）+ PMLR v162:20668-20696 |
| `onoda2026ddcg` | arX 2604.18161（4 作者、jref "…ICLR 2026"） |
| `scheffler2026cfobg` | HAL hal-05629540（4 作者、2026-05-21、docType UNDEFINED、无 DOI/venue）→ **HAL-only** |
| `hu2023abs` | arX 2310.04363 v3（7 作者）+ OA 交叉核对 |
| `zhang2026leap` | arX 2605.15341（5 作者，无 venue） |
| `mu2026gga` | arX 2609.06323（9 作者，2026-09-06）+ HTML 全文 |
| `lorraine2020implicit` | arXiv API（1911.02590，comment "Submitted to AISTATS 2020"）+ PMLR v108:1540-1552 |
| `montaut2022collision` | arXiv API（2209.09012，6 作者，无 venue） |

修复 17 条（Section B，key 不变）：`grade2026`（arX：单作者 Nel, Lukas Abrie）、
`diffro2025`（arX 3 作者 + CR 10.21437/Interspeech.2025-704 → Interspeech 2025,
2450-2454）、`hpo2026`（arX：Alvo/Russo/Kanoria）、`fluidgym2026`（arX：
Becktepe/Franz/Thuerey/Peitz）、`hydrogym2026`（CR 10.1038/s41586-026-10917-6 →
Nature 657(8131):369-376, 21 作者；arX 2512.17534 同序 21 作者）、
`topotransformer2026`（arX：Rashed/Valoroso Madeira/Gholami/Guerbuez/Yang/Thuerey）、
`metaneuralTO2025`（arX：Kuszczak/Kus/Bosi/Bessa）、`scidesignbench2026`
（arX：van Dijk/Vrkic）、`phythesis2025`（arX 4 作者 + CR
10.1145/3744255.3798124 → e-Energy '26, 363-378, year 2026）、`lcopt2025`
（CR 10.52202/085713-5325 → NeurIPS 38 D&B, 176897-176946, 10 作者；arX
2511.00116 同 10 作者）、`jadhav2026codesign`（arX：Jadhav, Shrenik / Liu,
Zheng + jref ASME JESBC）、`jadhav2026lifecycle`（arX：Jadhav/Nagaraju,
Vidhyashree/Liu，修掉多余花括号）、`agyeman2026benders`（arX + CR
10.1002/aic.70342 → 第二作者是 **Li, Zhe** 不是 Li, Junyao；AIChE J 72(6)）、
`yuksekgonul2024textgrad`（CR 10.1038/s41586-025-08661-4 → Nature
639(8055):609-616, 2025, **8 作者含 Pan Lu**）、`brewer2024exadigit`
（CR 10.1109/SC41406.2024.00029 → SC24, 1-18, 11 作者；删掉指向不存在路径的
journal 占位符，标题改为发表标题）、`li2023chattwin`（CR
10.1145/3600100.3623719 → BuildSys '23, 208-211, 6 作者；原稿未被 \cite）、
`jadhav2026mechdesigner`（CR 10.1080/09544828.2026.2624356 → J. Eng. Design
37(8):2904-2940）。

可选补全 6 条（Section C，均已 CR 核实）：`williams1992reinforce`
(10.1007/BF00992696)、`giles2000adjoint` (10.1023/A:1011430410075)、
`knoll2004jfnk` (10.1016/j.jcp.2003.08.010)、`bendsoe1989simp`
(10.1007/BF01650949)、`geoffrion1972benders` (10.1007/BF00934810)、
`raissi2019pinn` (10.1016/j.jcp.2018.10.045)。

核实为正确、未改 4 条：`shao2024grpo`（arX 2402.03300，11 作者与现条目逐字
一致；GRPO 确在此文引入）、`ma2024sga`（arX 2405.09783，8 作者一致）、
`allen2022physicaldesign`（arX 2202.00728，7 作者一致）、`mohamed2020mcgrad`
（OA：JMLR 21(132):1-62）。

合计 verified = 8 + 17 + 6 + 4 + 2（`jiang2019glonet`、`blommaert2020adjoint`
在 CR 核实过，见 Section C 注释）= **37 条**。

---

## 3. 无法核实 / 未核实（明说，不猜）

1. **`scheffler2026cfobg` 没有 arXiv id。** arXiv API 按精确标题、标题片段、
   作者（au:"Scheffler_M"）都查不到；本地审计给的 2603.16538 经核实是 Kong et
   al. 的 3D Gaussian splatting 论文，**确认是错的**。故按 HAL-only 引用，
   bib 里写了 `% HAL-ONLY CITATION` 说明。缩写 "CFoBG" 在 HAL 记录里查不到，
   全文未使用该缩写。
2. **`fluidgym2026` 与 `topotransformer2026` 的 "ICML 2026" venue 未核实。**
   arXiv 无 comments/jref；OpenReview api2 标题搜索返回 count 0 且
   `searchUnavailable: true`；Semantic Scholar 返回 HTTP 429。处理：两条都按
   preprint 引用，`topotransformer2026` 的 `note = {ICML 2026}` **已删除**；
   若作者能拿出 OpenReview/PMLR 记录再恢复。
3. **`hu2023abs` 的 "ABS gradient estimator" 名称未核实。** arXiv v3 HTML 全文
   中 `ABS` 与 `estimator` 均出现 0 次（`GFlowNet` 98 次）。因此正文只按核实过
   的 abstract 描述（amortized Bayesian inference / GFlowNet fine-tuning、
   distribution matching），不使用 ABS 这个缩写。另注：v1/v2 的第 7 作者是
   "Malkin, Nikolay"，v3 与 OpenAlex 是 "Whitammer, Esmeralda S."，bib 用 v3。
4. **`li2026dctwin`（AIFLUIDs 2026, Chania）与 `todini1988gga` 无法用数据库
   核实**（arXiv/Crossref/OpenAlex 均无记录）。未改，标 `%% UNVERIFIED`，
   提交前请作者对照会议 proceedings / 出版社记录。
5. **`sokolowski1999topological` 按任务要求未动、也未复核**（任务书明确说该条
   正确、不要"修"）。其 DOI 10.1137/S0363012997323230 只作为 lead 记在 bib
   Section D1，未独立验证。
6. **未复核的 4 条非关键条目**：`jang2017gumbel`、`maddison2017concrete`、
   `bengio2013ste`、`hu2020difftaichi`（作者完整、不承载任何被修正的 claim，
   为控制 diff 未动）。
7. **两个数字 claim 未核实，正文刻意不用**（见下节 §5.1、§5.2）。

---

## 4. 数字溯源（正文每个数字 → case/log 路径）

`contributions.tex` 里每个数字都带 `% source:` 注释；这里汇总，便于抽查。

**Contribution 1**
- adjoint 2.6e-10 / 5.9e-9 / 28 dirs：`ddl/case/benchB/E0_adjoint/loggers/results_e0_lc.json`
  → `adjoint_rel_err_median` 2.6333e-10、`adjoint_rel_err_max` 5.9147e-09、`n_checks` 28。
- closure C_min=1e-5：0.26% / 0.21 K / 1.9% / 2.8%：同一文件
  `closure_sweep["1e-05"]` = {pump 2.5998e-03, T 0.21290 K, duty 1.8987e-02,
  leak 2.7593e-02}；"随 C_min 单调下降"读自 1e-02…1e-06 五点 sweep；
  sqrt(C_min) 律沿用 main.tex §VII-B 的表述。
- limit cycle 7.3% → 0.35%：`ddl/docs/RESULTS_index.md` B5（42/576 → 2/576）
  与 main.tex §VII-B（7.3% → 0.7% line search → 0.35% continuation）。
  注意：archived json 里只有 `n_damped_rescues` 238 / `n_homotopy_rescues` 24，
  **并不直接编码 42/576 与 2/576**；这两个比值来自 solver log（RESULTS_index B5）。

**Contribution 2**
- 0.94 / 0.79 / 0.30 / 0.27：`ddl/case/benchB/E0b_split_decision_type/loggers/results_e0_lc_split.json`
  → `resistance.diam` {sp 0.9425, mono 0.7878}、`resistance.assign` {sp 0.3004, mono 0.2733}。
- 0.63 / 0.53 与 "6.4×"：`ddl/case/benchA/E0b_relax_variants/loggers/results_e0_relax.json`
  → `"z=linear,b=D5"` {rho_assign 0.62773, rho_diam 0.52784}。gap 0.6421 vs
  0.0999 → 6.43，是两个已记录 gap 的算术比；**注意符号相反**（合成 benchmark 上
  assignment 反而更高），正文已按此写。
- per-block rel bias 5.0 / 4.6（task 0）、最高 55 / 13：
  `ddl/case/benchA/E3_hybrid_estimator/loggers/calib_20260914T194257Z_gpuserver/results_e3.json`
  → `measured_block_bias`（task0 z 5.010 / b 4.610；z 最大 55.145、b 最大 13.390）。
- 13.18 → 0.864（15×）、0.061 → 2.077（34×）：
  `ddl/case/benchA/E3_hybrid_estimator/README.md`（其引用的 run
  `loggers/calib_20260914T200102Z_gpuserver` **不在本机**，loggers 被 git-ignore）。
  本机最近的 run 给出 rel_bias 12.645 → 0.910（calib_path_only）、SNR
  0.027 → 0.588（calib_path_only）/ 0.056 → 0.169（typewise_calib）：
  bias 结论复现、SNR 方向复现但幅度不是 34×。见 §5.3。
- adj_top1 4.33/0.19、adj_top3 0.138/0.69、adj_top8 0.004/0.94、
  steepest_full 0.000/0.94：`ddl/case/policy/inference_search/loggers/20260914T184924Z_gpuserver/results_inference_search.json`
  → `summary.by_split.val`（4.3272/0.1875、0.13808/0.6875、0.0042855/0.9375、
  0.0/0.9375）。

**Contribution 3**
- 13,122 / 414 / 371.87× / J 5.08…7.12e10 / range 1.4018e10：
  `ddl/case/benchA/E0a_physicality_audit/loggers/20260914T183018Z_gpuserver/results_e0a.json`
  → `enumerated["legacy|raw"]`。
- ‖grad E[J]‖ ~3.5e7 vs ‖‖ ~1e3、rel_bias=1.000 / rel_var=0.000 对所有 arm：
  该 case 的 `README.md`（"Feeds the paper" 段）与 `FINDINGS` §1。
  本机 `benchA/E1_estimators/loggers/results_e1.json`（legacy 物理）没有 arm 标签，
  但存在 rel_bias≈1.0006 / rel_var≈0.000000 的行，与描述一致；未能逐 arm 复核。
- 修复后 4.05e4 / 3.46e5× / 414→6 / 3.618× / median 1786.93→1774.87（0.67%）/
  adjoint 5.1223e-10 / 2.8620e-07 / 288：同一 json 的
  `enumerated["checkvalve|tempered"]`、`verdict.dynamic_range_reduction`
  346015.6、`adjoint["checkvalve|raw"]`。

**Contribution 4**
- 66.5 GB、19–60 s/step、1600 physics evals/run：`RESULTS_index.md` L5 +
  main.tex §VII-E "Costs" + `ddl/case/llm/E2_27b_estimators/loggers/cmp27b/ste_s0.log`
  （step 100 时 t=1876.9 s → 18.8 s/step；`mem_peak_gb` 66.45，全 log 最大 66.98）。
- feasibility 0.31→0.56（4B）：`FINDINGS` §4（0.3125→0.5625，源自
  `case/analysis/J_decomposition`）。Fisher p=0.014 / OR 3.05 / 29/48 vs 16/48：
  `ddl/case/analysis/statistics/loggers/20260914T194745Z_gpuserver/results_statistics.json`
  → `comparisons.4b.200.ste_vs_grpo.feasible_rate.fisher_exact_on_task_counts`
  （p 0.013691）。27B：27/48 vs 18/48，p 0.10131（同文件 `comparisons.27b.100`）。
- 95.1% / 100.0% / 98.5% / +18.1% / +6.0% / "达不到 label 质量"：`FINDINGS` §4。
  **%% UNVERIFIED**：写稿时 `case/analysis/J_decomposition/loggers/20260914T193056Z_gpuserver/results_jdecomp.json`
  的 `rows` 与 `aggregate` 均为空（run 仍在进行，stdout 04:18 还在跑），见 §5.2。
- 2.0 s / 127 solves / regret 0 / feasibility 0.94(val)：inference_search json
  → `summary.by_arm.steepest_full`（forward_solves_mean 126.75、
  wall_secs_median 2.022）+ `by_split.val.steepest_full`。
- ~340 / ~3.3 tasks、1900 s、5.58 s、1600、491：同 json `amortisation`
  （breakeven_tasks_wallclock 340.26、breakeven_tasks_solvercalls 3.258、
  search_wall_secs_per_task 5.5839、search_solves_per_task 491.075）。
  注意该 break-even 的对照臂是 `coord_descent`；若对照 `steepest_full`
  （2.02 s、126.75 solves）wall-clock break-even 会更差（1900/2.09 ≈ 910 tasks，
  这是我做的算术，未写入正文，正文只说"更差"的方向由对照臂决定——实际正文写的是
  recorded 的 340/3.3 与 7 ms 的解释）。
- 4.303、区间重叠、Welch 0.146/0.086、exact floor 0.10/0.25、Holm min 0.23049、
  1.2247×、第 9/16 个 order statistic、step-0 sd=0：statistics json 的
  `headline_non_overlap_claim`、`verdicts`、`multiple_comparisons`、
  `metric_definition_audit`、`checkpoint_selection`；"≥5 seeds" 与 power
  27%/42%→62%/86% 来自 `case/analysis/statistics/README.md`。

**related_work.tex 的数字**（全部来自被引论文自己的 abstract/全文，非本仓库）：
GRADE >14×（arX 2601.11574 abstract）；SciDesignBench 520/14/五档/29.0%/
RLSF +8–17pp（arX 2603.12724 abstract）；LEAPBench 55 tasks/53%/不敌 BO
（arX 2605.15341 abstract）；Mu et al. 52 networks/8,140 frames/1.137e-13 ft/
25 exact/906→1/595 vs 8,060（arX 2609.06323 abstract）；"posted 6 Sep 2026、
nine days before this draft"（arXiv citation_date 与今日日期的算术）。

---

## 5. 与 FINDINGS / 任务书 / 旧 README 的冲突及处理

### 5.1 paired Wilcoxon p=0.021 与 bootstrap CI（1.74–11.63 / 1.12–18.51）——**不用**
任务书与 `FINDINGS` §4 都要求写"paired Wilcoxon over tasks p=0.021（n=16 的
exact floor）+ bootstrap CI 排除 0"。但 statistics case 的 artefact 明确记录
`paired_over_tasks.available = false`，理由 "no log record on this host contains
a per-task regret vector"（v1 trainer 把算出的 per-task 向量丢弃了），且
`run.py` 要等 `IMPL_VERSION train_rl-v2-20260915` 的 run 才会自动启用配对检验；
本机没有这样的 run。另外 "exact floor at n=16" 与 0.021 也对不上（n=16 无 tie 的
exact Wilcoxon 双侧 floor 是 3.05e-5）。因此 `contributions.tex` **没有**使用这组
数字，并在该处留了 `%% UNVERIFIED` 注释说明原因与解锁条件。等 v2 run 落盘、
`paired_over_tasks` 变 true 后再补这句。

### 5.2 J_decomposition 的百分比（95.1/100.0/98.5/+18.1/+6.0）——**用，但标 UNVERIFIED**
`FINDINGS` §4 给出这些数，但写稿时该 case 的 json `rows: [] / aggregate: []`、
run 仍在进行（ps 可见 `python run.py --scales 4b,27b ...` 自 03:30 起运行）。
正文按 `FINDINGS` §4 引用并在 `% source:` 里标 `%% UNVERIFIED`，等 run 完成后
用 `results_jdecomp.json` 的 `aggregate` 复核（该 case 的 `label_free_fields`
里正有 `share_of_J_drop_from_penalty`、`feasible_rate` 等字段）。

### 5.3 E3 的 13.18→0.864 / 0.061→2.077——**用 README 的值，并记录本机 run 的偏差**
README 引用的 `loggers/calib_20260914T200102Z_gpuserver` 不在本机（loggers 被
git-ignore）。本机 `calib_20260914T194257Z` 给出 rel_bias 12.645→0.910（≈14×，
与 15× 同量级）但 SNR 只有 0.027→0.588（calib_path_only）/ 0.056→0.169
（typewise_calib），即 34× 复现不出来。正文用 README 的值（RESULTS_index.md 规定
README 是 durable record），`% source:` 注释里写明两个 run 的差异，并建议提交前
重跑 `ddl/tools/run_case.sh benchA/E3_hybrid_estimator`。per-block bias >1 在
本机 run 里**逐 task 复现**（5.010/4.610 起，最高 55.1/13.4），所以"mis-scaled
not merely offset"这个定性结论是稳的。

### 5.4 inference_search README 的 break-even 是旧的
README "Last recorded result" 写 ~445 tasks / 4.27 s / 397 solves / 4.6 s，那是
16-task val 的早期 pass；已完成的 40-task run（`20260914T184924Z`）给出
340.26 / 5.584 s / 491.075 solves（对照臂 coord_descent）。正文用后者，与
`FINDINGS` §3 一致。同理 README 里 "exhaustive (6 small tasks)" 在 artefact 里是
n=12（6 val + 6 test）。

### 5.5 "asymmetry ~6× larger" 的符号
`FINDINGS` 只说"~6x larger"。实测：production 上 diam−assign = +0.6421，
synthetic 上 = −0.0999（**符号相反**）。正文按"幅度 6.4×、且方向相反"写，
比 FINDINGS 更精确，也与两个 json 完全一致。

### 5.6 其它已核对一致的数字
steepest_full 2.0 s/127 solves、adj_top* 阶梯、closure 表、E0a 全表、
statistics 全套、27B 成本——均与 artefact 逐字段一致（见 §4）。

---

## 6. 后续动作建议（按优先级）

1. 等 `analysis/J_decomposition` 的 run 结束，用 `aggregate` 复核 §5.2 的五个百分比。
2. 重跑 `benchA/E3_hybrid_estimator`，确认 34× SNR 还是 ~10–20×，并更新正文。
3. 跑一个 v2（`train_rl-v2-20260915`）LLM run 以解锁 per-task 配对检验（§5.1）。
4. 给 `fluidgym2026` / `topotransformer2026` 找 OpenReview 或 PMLR 记录，或永久
   按 preprint 引用；顺手考虑把 `topotransformer2026` 改名（需同步 main.tex 4 处）。
5. 作者本人核对 `li2026dctwin`（AIFLUIDs proceedings）与 `todini1988gga`
   （出版社记录）。
6. **main.tex 的 abstract 与 §I.A "This paper" 仍带着被撤回的 framing**
   （"low-variance but biased pathwise signal … when doing so pays"、
   "characterise the bias … in terms of non-linearity and temperature"）。
   本次只交付 §II 与 Contributions，这两处需要单独一轮改写，否则与新的
   Contributions 自相矛盾。
7. `raissi2019pinn`、`knoll2004jfnk`、`li2023chattwin` 现在被新 §II 引用，
   不再是 orphan key；若采用 Section C，请一并替换 references.bib 中对应条目。
