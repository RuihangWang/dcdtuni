# Complete-cavity consumer plan: independent source review, 2026-10-06

结论：hook 放在 dcCartCutCells.C:14940 后、14942 固定 gather 前是可行的后续接点；现基线没有 complete-cavity consumer，不能称当前 tiny actual oracle、原子几何替换或正常全网格接受已经完成。当前授权的 exact primitives + atomicLineage 可独立继续；consumer 仍需下述 context 才能进入生产接点。这是设计接入阻断，未把未实现 consumer 的风险列成现生产新增 P1/P2。

## 审查绑定与边界

作者 consumer-plan 包 11 payload / 47,008 B 全部逐 SHA 匹配；manifest SHA256 `7fe755f070add50abf7163e49c1f9847c279656502a56db18af02310fd92feb0`。9 个源绑定作者 checkpoint `afbafdcdcadbbb49d4cb16625e5678b433510c24`；C/H/APP、Snapshot/PostCarve、W/finite 正常源实读匹配；Lineage core 用独立旧冻结副本，Authority 用已接受 v2 cap 冻结副本，避免把正在修改的 Lineage WIP当基线。所有 exact path/SHA 在 source-and-packet-identities.json，紧要行在 baseline-source-excerpts.txt，9 个完整基线均保全。

只读源码、既有包及历史记录；未调用测试、probe、compiler、build、solver/checkMesh、API、浏览器、Git、服务。只在本证据目录写副本/报告；CPU28–31，CUDA空。本文不会授权 C/APP 新改动、替换接点、main 合并或新增实验。

## 实际 API 障碍及最低修正

1. **当前 installed producer 缺失（未来 admission 的高影响阻断）。** C:14215–14258 的真实 earliest oracle/bindings 生命周期结束后销毁；H:163–171 的 baseline 保留的是 earliest checkpoint/coupled 证书。C:14798–14810 的 unionArea/Centre/CC/V/Det 是 cut 缓存，不等于当前 installed accepted-prefix。不得拿这些缓存重新命名 actual。首版把 discovery proposal 与 installed admission 分开：bounded cut/既有 seed 数据只能提名；生成候选时才构造 SHA/epoch 绑定的当前局部 installed-before/after。无 normal alias/order/closure 信息就返回 Unknown。若需要完整 current actual 全局 bad-cell census，必须另建一次固定阶段的正常 producer；小 header 不应隐式重建全局 oracle。

2. **人工截断边界会改变原质量角色（未来 admission 的高影响阻断）。** WPreview.H:67–75 的 D 只选真实 internal/coupled SF；77–101 的 W/R/N 使用真实 neighbour CC/V，而 physical boundary 使用投影且 skew reach 系数不同。tiny view 把真实 internal face 当 boundary 后，直接调这些函数会漏 D 或改变 W/R/S/N。必须区分用于封闭计算 CC/V 的 artificialCut 几何面与 sourceRole：保留原 internal/physical/coupled、D-selected、完整端点 native ID，按原角色评价，不能用 tiny.nInternalFaces 代替。affected/guard cell 的所有面及其真实累加顺序完整；指标所需外端点必须有同一 current epoch 的完整几何或已由正常 producer 复制的 actual CC/V。闭包超过 cap/缺端点/coupled unknown 拒绝，不沿邻接递归扩成全网格。

3. **writer normalization 不能从局部 renumber 猜（未来 admission 的高影响阻断）。** C:15869–74 依 native 存活顺序 compact cell；15955/15960 反转整个 ring；15968–76 排 internal(owner,neighbour)、stable patch boundary；16035–47 按 face traversal 首次使用 compact points。mergeCoincidentPoints C:9138–9175 是全点 canonMin/grid优先、flatten alias chain并重写全部面，within-box不是完整 global alias 等价保证。context 必须提供原生全局顺序/actual owner orientation/归一化 ring point0/face排序键与逐 cell 实际累加顺序、正常完整 alias 代表及受影响等价类。内部排序同键不明、新点可能与 halo 外的点碰合、代表被移除或新 slot 改变 owner 翻转却无精确 proof 时拒绝。raw outer ring 与 installed normalized ring 要分开保存；不能把 WPreview:637 的 begin+1 anchored reverse照搬为真实 writer 的 full reverse。这不是要求改旧 W verifier，其现约束另保留。

4. **保护旧提交必须来自 normal state。** WPreview.H:152–175 已有 committed Packet.cells/faces/aliases、完整 halo 的 raw shape 和 installed lineage；由这些记录形成保护集合，不读 JSONL 取得权限。保护包括 alias key、representative 和候选新点可能影响的等价类；只检 core cell 交集不足。C:14810 的 non-W literal accepted 计数存在，完整常驻 footprint 缺失，C:14812 是可选日志。最小首版可要求该 normal local accepted==0；否则全 cavity stage DependencyUnknown 拒绝。以后有 bounded mutation journal/epoch 才允许准确 disjoint 放行。P13 rank0 的 empty literal/W/D 只是一份历史 eligibility，不豁免运行时检查。

5. **Lineage preparation 不等于几何原子提交。** 旧 core:172–198 join 会 intern/set live keys，214–231 compactKeys 只是 mapping-only；不能先 join 再发现几何失败。新 prepared token 应绑定旧 live slots/keys、完整 origins、明确新 native slots、同一 normal snapshot 和 geometry epoch。prepare 用独立 owned 值，检查所有 cap/clock/array一致性；在最后 guard 前完成所有必需 reserve。commit 只执行不分配、不回调、不 MPI 的 swap/标量写，geometry/all cell arrays/lineage/alias caches 一次成为一致新状态。H:103–147 所列 points/faces/O/N/patch/zone/alive、nCells/全部 cell metadata、nGridPoints、baseline/W shared state及 lineage 不能只更新其中之一。失败保留旧逻辑状态，旧 baseline 和旧 W/D packet 不 reset。多 origin 新 cell 的 level/leaf/piece 不能冒称一个旧 parent；normal full-origin lineage 是来源权限，geometry rekey 不是来源。

6. **预算与固定 MPI 不应藏在局部函数内。** C:16195–96 的 toPolyMesh 包含 patch gather/scatter，即使 baselineOnly=true 仍可走这些操作；不得每 trial 调用它。C:14942 gather 本身位于 literalUnions 的统一分支内，未来 enable 应按正常 APP 全 rank 同步并绑定该统一路径；超时/空/unknown rank 返回结果后仍进同一 gather。prefix/final 验证和 C:16575–16595 earliest coupled reduce 保持匹配。O(M) adjacency/census/alias directory build 需要复用正常 index 或明确一次收费；128 core cap 不限制这项成本。71,920 plane systems只能筛 proposal，不能各跑 installed oracle；所有 <=256 optimizer requests 共享实际剩余 clock。4s、16MiB journal 不是峰值 RSS/300s 全链保证；reserve 可能有全局向量重分配成本，必须计时。首版 N warning 可用 local count/max 不增的保守条件；ROOT 未定全局额度时拒绝需要额度的候选，不能使用历史88.2作为权限。

## 后续三个切片（按依赖执行）

**当前 source-only 切片：exact primitives + prepared lineage。** 接受条件仅为 error/ambiguity/cap 结构和无 live mutation 的 prepare/commit token 语义；旧 pair body、C/APP/生产调用数不变，compact unknown=-1 不授权，未声称几何替换接受。报告应明确 primitive 数学范围及未接实际 topology consumer。

**下一 generic discovery / local installed oracle 切片：** 实现 CONTEXT-API.md 的 normal binder，先固定 epoch/clock，再保护旧提交，收集有限 point stars，构造 bounded 完整 source/geometry/quality 闭包。精确几何只提出最多4 template；仅最优有限 candidates 执行真实 installed primitiveMesh/triPointRef 局部几何，其 rings/order/aliases/roles来自 binder。tiny synthetic boundary只封闭几何，不改原指标角色；默认 coupled/unknown/tie/alias外溢/legacy-footprint缺失均拒绝。旧 before 必须是 current accepted-prefix，earliest baseline 完全另存。没有正常全局 alias index 时可以保守不接受新 alias 可能外溢的候选；不能用一份人工 fixture 映射替代 normal producer。

**以后接 C/APP 的独立授权切片：** 在固定 gather 前接一次 local result，保存 bounded normal admitted journal；writer after addZones 的 actual prefix，以及 finite point repair 后 actual final，使用真实 final bindings 复验新拓扑/角色。所有 ranks 固定 summary/prefix/final reduction，local refuse不提前退出集体操作；最终全局失败不得 write。再由正常流程做同源码 full mesh/checkMesh 及计时，才可能讨论产品验收。

## 独立验收应覆盖的反例（本次未执行）

- 同一 closed geometry 将一条真实 internal face变 tiny artificial boundary：原 D-selection和两端点 W/R/N保持；若直接 tiny mask 会变化的例子必须拒绝错误实现。
- 选择点顺序变化、外部更优 canonMin/grid代表、late alias supersede、owner翻转、同 owner/neighbour排序 tie；不能靠仅 local ID renumber取得绿。
- raw shape不变但 current accepted-prefix已被旧 literal/W/D影响；normal committed依赖拒门，optional JSONL删除与否不得改变权限。
- token准备后 epoch/任一 facet/point/lineage key变、origin>8/core>128/halo超cap、prepare分配失败、最终 clock不足；commit前旧逻辑数据不变且拒绝。
- empty/expired/disabled rank与有 candidate rank同一次固定集体协议；不得在每 trial触发toPolyMesh或demand-driven coupled exchange。
- exact binary64非有限/下溢/overflow/ambiguous拒绝；普通有限正例允许完整原生流程，但独立数学例不冒称实际全 mesh接受。

## 历史与 publication 范围

两份 P13 reference（full-case-raw-manifest、root-coupled-and-WD-lineage-verification）已逐SHA核；三份 author copied raw payload与该 raw manifest对应条目一致。唯一观测是 f019c5d0 rank0 empty literal JSONL、installed-prefix/final records=[]；不是其他 rank、当前进程、新候选或全case权限。所读source绑定afba基线；正在制作的新 exact/Lineage source未审、未验收。本文只写IT owned证据，没有 publication/docs修改。
