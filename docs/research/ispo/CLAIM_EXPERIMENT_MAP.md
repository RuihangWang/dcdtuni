# ISPO 主张—实验台账

2026-10-08 所有者写作与研究精神的执行入口。保留计划 v2 的完整研究范围；下列“主张”是要用实验完成的论文命题，待补部分不提前写成已证实事实。原始记录、负结果与失败费用保留，判据在对应实验开始前登记。

## C1：按信息阶数与保真度组织物理后训练具有独立的方法价值

- 故事位置：论文核心贡献，覆盖损失分量、残差信息、梯度与目标纠偏，不缩成单个接口技术点。
- 对应实验：E1 信息析因与消融、E2 质量—完整开销前沿、E3 不同高保真形态。
- 已有效：固定策略的参数梯度机制；首批七臂、十种子的真实后训练；残差辅助路径项在固定更新次数下的正向配对效果。
- 原始 summary：`case/agent/controlled2d-frontier-analysis-20261008/logger/2026-10-08_205315_SGT__complete-cohort-support-and-fixed-update-diagnostics-v3-68516dbd/secondary_summary.json`；Git 中同名 evidence 副本与终态清单。
- 原因与修法：共同墙钟区间为空已定位到模型加载阶段的异常长等待；继续定位其 I/O、设备和加载路径原因，并验证修复。LF 路径与标量纠偏的弱响应先查接线、特征、训练与已定协议，不直接归结为方法不足。
- 待补实验：完成 I1、完整自适应信息控制、经加载计时修复后的同开销比较和完整 E1–E3。

## C2：物理信息指导的采样与纠偏能有效利用不完美的低保真

- 故事位置：解释方法为何有效，以及在哪些信息/成本条件下有效。
- 对应实验：计划内 E1 残差与梯度消融、E2 保真差距与预算变化、E3 目标残差/伴随形态、计划内信任低保真区实验。
- 已有效：目标加权残差在已登记设计上的真实校正、原始场和离散伴随核验、付费信息与路径信息的分离。
- 原始记录入口：`docs/research/ispo/COMPLETION_AUDIT.md` 的目标残差与参数梯度小节及其原始 run/summary；冻结记录继续保留。
- 待补实验：完成已计划的移动策略、自适应选择与不同偏差条件实验，将机制接到完整学习收益。

## C3：相对于强多保真方法、非 LLM 策略与推理期搜索，ISPO 提供明确增量

- 故事位置：回答方法贡献和后训练的必要性，不靠弱基线成立。
- 对应实验：E4（含原生 MFPG/MFPG-PPO 及已登记对照）、E5（结构化策略与逐任务优化）、E6（工具调用 Agent 和设计后精修）。
- 已有效：原任务 MFPG warm/状态与评价门；完整 HF-only 单种子；MFPG-PPO 四个实际小规模试跑；E5 真实公共 SFT。
- 原始记录入口：`case/agent/mfpg-hopper-public-curve-v7-20261008/`、`case/agent/controlled2d-mfpg-ppo-e4-20261008/`、`case/agent/controlled2d-structured-formal-e5-20261008/`，各自独立 logger 与冻结清单。
- 原因与修法：E5 模型有效，外层记录器丢失终态费用；修复 observer 与进程回收并核对历史计时边界，不把未知费用写成零，也不把记录器缺陷当作方法失败。
- 待补实验：完成原任务复现、已定强基线主比较、E5 和 E6；各方法按其原生耦合方式及已登记协议运行。

## C4：方法能够完成有工程意义的风液混合数据中心设计自动化

- 故事位置：Shinaide 是主要 playground，完整设计和工程验证是旗舰证据。
- 对应实验：Shinaide 主场景的 E1–E7、真实工程/硬约束验收及 E8 最终设计复核。
- 已有效：56 台有风扇液冷服务器的同源液冷、一维风网、共享冷源能耗和付费梯度接口；真实工程与热量账相连。
- 原始记录入口：`case/agent/shinaide-fan-zonal-lf-20261008/`、`case/agent/shinaide-energy-reward-interface-20261008/` 与 `case/agent/shinaide-feasible-domain-audit-20261008/`。
- 原因与修法：现目录容量与全覆盖封闭区/N+1 风量需求不匹配；保留原服务器、负载和液路，建立明确的设备目录、分区、供回路径与故障调度规格。40901 继续网格/CFD 工程修复，Pro 完成研究接口和训练基础设施。
- 待补实验：完整可行参考设计、风扇语义下的真实 HF、设计学习与最终工程复核。

## C5：完整故事具有跨任务、跨物理场景的适用性与可复核性

- 故事位置：翼型与受控场景共同支持方法的一般性；泛化与最终设计验证支撑外推范围。
- 对应实验：E7 留出需求/几何/规模与翼型任务变化，E8 网格加密或更高保真复核。
- 已有效：原生翼型梯度与两网格检查；支持的 CST16/IDWarp 真实形状伴随及一个联合方向的两步长 FD。
- 原始 summary：`case/agent/dafoam-cst16-supported-benchmark-20261008/evidence/TERMINAL_FIRST_SHAPE_PHYSICS_V5_ROOT_2026-10-08.json`。
- 原因与修法：此前形状伴随失败已定位并修复为诊断代码干扰 AD tape；同候选重评与原始数组独立复算已通过，旧失败和费用不删。
- 待补实验：翼型全设计方向与任务范围的验证、跨任务学习，以及完整 E7/E8。

## 2026-10-08 晚推进记录

- C3：两臂真实物理反馈及新初始化完整计时已通过；正式20次非LLM试验已启动。MFPG原任务复现按全部固定种子继续，MFPG-PPO重复计算的实现原因已定位；下一步补同状态单GPU等价/效率验证，再完成既定比较。
- C4：六台真实派生布局已通过几何/液路一致性检查；OPEN的物理挡板语义与零流故障接口已有具体修法，下一步补正常与六种单机故障的联合LF实验。
- 原记录和判据保留；不得把尚缺的实验改写成缩窄后的最终贡献。

- C3 后续原因与修法：真实GPU检查发现旧MFPG-PPO重复prompt重放在同SFT参数下未重现行为密度；新单context路径能重现。保留旧/新等价门失败，下一步按原PPO的未更新参数密度恒等性完成修复验证，不改原生耦合/τ或挑选样本。

## 2026-10-11｜完整证据与下一步（本节更新前述日期快照）

- **C1/C2 已有效**：原七臂完整队列继续支持固定更新次数下的信息收益；目标残差与固定策略参数梯度机制保持原始证据。完整 ISPO 控制器的十种子研究已修复外层记录器的进程退出竞态，保留成功 seed1301、失败 seed1302 的原始结果与成本，再按原顺序继续。协议和37项训练源码未改，不读取中途质量或封存终测。入口：`case/agent/controlled2d-ispo-full-controller-20261009/root-formal-recovery-approved-v2-20261011.json`。**下一步补实验**：完整控制器终态复算、登记信息消融与成本前沿；既有七臂空共同区间不被后续研究改写。
- **C3 已有效**：原任务 MFPG/HF-only 的完整四十次研究已全部完成并通过状态、耦合、评价隔离、checkpoint 和成本复算，支持原任务 MFPG 优势。原始 summary：`case/agent/mfpg-hopper-full-cohort-20261008/evidence/2026-10-11_144740_SGT__full40-original-Hopper-aggregate-v12-dbf1a141/summary.json`。这为强基线提供实现锚点；共享设计任务的正式 E4 四十次队列已准备，等待当前单 GPU 优先队列完成。
- **C3/E5 已有效**：结构化策略两种登记更新法各十种子全部完成，各种子均较共同 SFT 起点改善；LAX 的热目标收益与控制代价变化解释其接近的终态总分。原登记两种方法、两种架构的四十次目标具有非空共同成本区间，全部试验保留并按“最新可支付 checkpoint”独立复算。原始入口：`case/agent/controlled2d-structured-formal-e5-20261008/evidence/E5_FORMAL20_COMPLETE_PRIMARY_PUBLICATION_2026-10-11.json`；`case/agent/controlled2d-structured-formal-e5-20261008/evidence/2026-10-11_145051_SGT__complete-E5-statistics-and-terminal-analysis-v3/ROOT_INDEPENDENT_FRONTIER_AND_TERMINAL_REVIEW_2026-10-11.json`。**下一步补实验**：真实逐任务优化器、隔离部署计时与工作量摊销，E6 工具 Agent 比较。
- **C4 已有效**：Shinaide 派生 OPEN 名义点和六个单机故障均通过原登记风液联合 LF 可行性门；`case/agent/shinaide-six-acu-open-lf-20261008/evidence/ROOT_SEVEN_LF_ARITHMETIC_REVIEW_2026-10-09.json` 保留完整逐 UID 热量及能耗账。真实候选编译器扩展到 OPEN/CAC/HAC 工程、空气与液路导出和可信 TaskRef；五个连续域点完成真实联合 LF，端点热约束不满足作为结果保留。部分 I3 的条件控制头路径与 score 修正被明确分块，不能零填缺失几何梯度后扣掉完整 score。新入口：`case/agent/shinaide-design-compile-20261011/SHINAIDE_INTERFACE_HANDOFF_v1.json`。**下一步补实验**：周边设备三类布局真实编译与正常/故障 LF、点级控制梯度、旗舰训练及40901同源 HF。
- **C5 已有效**：粗网格中心的全部十六维原始 CD/CL 导数通过原双步长门。原因与修法：旧冻结壁距遗漏几何链；仓库自有注册桥恢复原生 meshWave 壁距更新，并保持旧前向解。所有64个冷前向差分与原生伴随向量独立复算；旧失败和成本不删。入口：`case/agent/dafoam-cst16-recovered-full16-20261011/evidence/ROOT_ALL16_NATIVE_STDOUT_REVIEW_2026-10-11.json`。廉价 panel 评估器的自身矩阵、残差与16维可微信息已核验（`case/agent/airfoil-panel-lf-cst16-20261011/evidence/TERMINAL_PANEL_LF_2026-10-11.json`），不冒充 HF 残差或硬奖励梯度。**下一步补实验**：已冻结第二工作点、真实细网格和验证网格，随后更广任务域及学习；完整 E7/E8 继续保留。

论文正文现以完整控制学习、机制、策略经济性与旗舰应用组织；详细接口证据进入独立补充材料。所有主张仍按计划完成相应实验，当前稿不代表整个期刊目标已完成。


## 2026-10-11 晚｜完整成本、六族参考与主控制器终态
- C1/C2：完整ISPO原十种子现全部完成64步；原37训练源码与4恢复外层未变，failed1302原记录/费用保留。按聚合质量读取前冻结的FULL_CONTROLLER_TERMINAL_ANALYSIS_PROTOCOL_2026-10-11.json独立复算；未经复算的训练终态不写成收益。下一步补完整controller统计与信息消融。
- C3/E5：完整v6付费outer账保留70success/61failed/350rawprefix与全部原质量。root直接核3987primarybindings、70terminal/350prefix和680原生H5目标；ROOT_COST_V6_AND_HF680_PRIMARY_ACCEPTED_2026-10-11.json。全70共同区间仍空，原E5四十次全部共同区间及原统计规则保留。结构化全部terminal模型的真实infer/export/HF闭环已完成；下一步补语言部署、native240逐任务优化器和同质量摊销。warm n诊断明确以已就绪model/service与两个公开context为条件，CPU/完整break-even仍pending。
- C3/E4：ISPO完整队列终止后条件释放正确，但first HF reply后requestledger O_EXCL冲突停止，0training iteration/39unstarted。先修唯一记录入口与同类checkpointcurve，原nativecoupling/τ*/critics/allocation及原firstreply/failurecost不变；下一步补日志修复CPU验收和完整40既定队列。
- C4：全部六类真实编译参考的normal与所有原single-off联合LF可行，保留原loggingfailure及12unpaidfault source-only recovery。原56UID风液heat/拓扑/能耗独立重算；ROOT_CONTAINED14_COMBINED_PRIMARY_ACCEPTED_2026-10-11.json与ROOT_PERIMETER21_PRIMARY_ACCEPTED_2026-10-11.json。六族numeric/conditionalcontrolpath和实际LanguageDesignPolicy源码hook已建立，但actual模型/物理I3/训练未验收；下一步补model前向、点级sensitivities、旗舰训练及40901固定提交HF。
- C5：粗中心full16与第二工作点joint验证已有效。细网格停滞已由原生4步phase trace定位为局部SA两周期；只改nuTilda iterationrelax .70→.35的原五cold+两adj已冻结并root授权，原物理/0fields/几何/seed/差分步长/所有gate不变。下一步补该冷gate独立复算、verification网格、更多任务域与训练。
完整C1–C5/E1–E8/4B+27B/OOD/旗舰HF/研究训练仍全部保留；当前稿继续明确pending。

## 2026-10-12｜原生基线、部署原因与网格修法

- **C1/C2 已有效**：完整 ISPO 原十种子固定终点已由 root 从全部200原生验证状态独立接受；共同初始样本一致，平均目标变化正向且主要来自热分量，描述性区间包含零。原80运行共同预算仍为空，未写成完整同预算优势。入口：controlled2d-ispo-full-controller-20261009/evidence/ROOT_FULL_CONTROLLER10_NATIVE_PRIMARY_INDEX_2026-10-11.json（80beb0a3）。**原因与修法**：无损整组存储已实测，原重复拟合/压缩成本保留；runtime-v2b仅准备，下一步补同科学内容有界pilot和完整费用研究。
- **C3 已有效**：原生 heuristic、CMA-ES、MFBO、adjoint-descent 四法×六任务×十种子的240实验全部终态。Root核实61612来源绑定并独立复算11640原HF/LF状态、真实H5/工程/原最优HF、240完整成本和原描述性统计；根回执为 controlled2d-native-formal-e5-20261011/evidence/2026-10-12_135037_SGT__root-native240-primary-e8a61450/ROOT_NATIVE240_PRIMARY_ACCEPTANCE.json（85cfe781）。实际20结构化策略部署均优于原中点规则。**原因与修法**：相对完整CMA/MFBO的联合目标差集中在较高的平均控制速度，热分量更好，方差贡献较小；正在按全部原训练轨迹检查KL/学习率/梯度/收敛。该CPU结构化策略与4B完整控制器身份分开，不能混用参数或修法收益。下一步补质量匹配修法、语言策略真实部署和完整摊销；不新增事后等价门、删seed或把代价交叉当同质量结论。
- **C3/E4**：截至13:48 SGT，原正式40任务完成33、失败0，精确原队列/PID出生身份与唯一授权GPU仍匹配；未读取中途质量或封存终测。下一步在全部终态后按既定原生耦合和统计读回。
- **C4 已有效**：六族真实工程与联合LF正常/故障参考保持；完整语言前缀v3r1支持全scope与现engine子域分开记录，缓存/完整回放数学夹具通过。实际4B sampler/前向、物理I3和训练仍pending。最新40901只读原始记录为 docs/research/ispo/engineering_hf_readonly_20261012_140256_SGT/：N94–N98各完成原native/checkMesh但whole质量未通过、solver未执行，committed refs未变；不合并私人修复。下一步补真正source-bound HF和旗舰学习。
- **C5 已有效**：verification035五冷点原生/通量/几何/两步长差分经root独立接受（ROOT_NU035_VERIFICATION_NATIVE_AND_G5_ACCEPTED_2026-10-12.json，63ceb2ee）；原G5升力差仍未过门。**原因与修法**：129024-cell强近壁压缩单次mesh在原.001面积张量门停止，未进入CFD；保存单元显示近直角但高度各向异性，正在以原门评估温和首层间距的新候选，所有失败和费用保留。下一步补新mesh、冷解网格比较与更广域训练；不以局部验证代替完整C5/E7/E8。


## 2026-10-12 下午｜原始证据与待补实验更新

- **C1/C2**：完整控制器十种子终态和无损存储原始数组已由root接受；固定终点平均目标变化正向，描述性区间含零。runtime-v2b源码和有界pilot准备完成，原费用与失败保留；下一步补实际pilot与完整成本比较。
- **C3/E5**：全部原生240优化器及实际结构化部署/HF680已发布且可从归档独立复算。原100checkpoint KL/控制量级诊断经root独立数学复算；仅改共享KL权重的全部20种子修复实验已实际启动，原目标/步数/共同SFT不变。下一步补完整成对终态、真实部署与质量匹配摊销。
- **C3/E4/E6**：原40个MFPG-PPO研究任务仍按冻结顺序运行；终态来源绑定reader的新v2处于草稿，不能用其准备状态代替结果。原始Qwen direct/tool/结构精修三臂可执行pilot源码通过root准备验收，待原E4全回收和单GPU空闲后实际运行。原Hopper40为前代MFPG/REINFORCE复现，与2026新MFPG-PPO在共享设计任务的适配明确区分。
- **C4**：六族语言采样/数值头v5已source-ready并发布，实际前向/训练待验；点级物理I3与同源HF仍待补。40901N94–N98原始严格whole门未通过、solver未启动，未接入私人修复。
- **C5**：温和近壁129024网格几何/导数/真实工程通过。工程metadata缺失已修复；实际冷基点4000步未过原收敛门，四个后续点未启动。已保存完整失败与费用，近壁动量残差的阶段诊断在准备；验证网格独立运行。下一步补原门下的冷解/伴随/G5及更广任务域学习。

Git整理只保存和同步源码、稿件、协议与现有证据，不改变上述科学接受状态。原始运行目录留在仓库内，批量生成的agent_outputs/selected与logger/_tmp分别按规则保留并忽略；已跟踪或显式发布的证据继续随Git版本保存。完整C1–C5/E1–E8、4B/27B、旗舰和泛化仍继续。
