# Native V3 independent review, 2026-10-07

结论：在“默认 OFF、同一 job 至多一次成功物理点提交、原始 plain checkpoint、现行实际 C writer”的已声明范围内，V3 的 source/checkpoint/coupled 内容尾部围栏通过独立源码审查和 fresh actual OpenFOAM 复验。未发现此范围内新的可复现反例。此结论不授权正常施用，不表示完整多点/联合链、真实耦合正控、整场景 RSS/时限或大 Shinaide 质量通过。

包身份：AIR/.codex-tmp/p20-v2-physical-point-owned-content-fence-v3-private-20261007/manifest.json，SHA b8673ad0dac6da231d2cde0632288df45e61eca46aa3b19e8602b9446a9366d6。独立完整读取、重哈希全部 232 payload / 5,648,408 B，零 mismatch；见 full-payload-verification.json。本报告目录 R 为 /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/review-physical-point-owned-content-fence-v3-independent-20261007。actual B/IT/AIR AGENTS 同 SHA28342f...，按本任务更窄的资源/动作限制执行。

最终两个头：LocalOracle.H SHA344937e5d79abd16cb21fa171f2f803fc1cecb07d83d0d59b657e90d63c0cdaf；Lifecycle.H SHA5bfcf76c8dbfb9787b6514fa857d5fef12960260bc169d854fc666e81836e063。完整最终 V2→V3 patch 15,456 B，SHA edf02ac716e951c784c6b3a0a75370d52fc27a20f42055237c9e0a36f6197690；其中 snapshotSeconds 单独分类差分 1,155 B，SHA1b5030bff8213c39ecd2605134cb2f7a7d5542fbaa595c1337f975a846fc572a。其余四个候选源保持 V2 字节身份，尤其 C SHA bc33390717692ec19b919554e05cdad4c4841dc06123613fbf9d2ea4c597744d。

## Fresh results

全部以 CPU12、CUDA_VISIBLE_DEVICES 空、OMP/OpenBLAS/MKL 单线程、TMPDIR 在 R/tmp 运行；没有 normal 源写入、Git 动作、正常构建、Allwmake、mesher/checkMesh/solver、API/浏览器/服务/GPU 操作。实际 g++17 / OpenFOAM2412 / DP / label32，链接 held 原生库；没有镜像实现或替代验证器。

| Actual driver | Fresh result |
|---|---:|
| 基础工厂/installed/current/atomic |46/46|
| 原 native-point adversarial |51/51|
| 原 read-shape adversarial |52/52|
| V2 独审原 source CPP，字节未改 |55/55|
| V2 独审原 shared-checkpoint CPP，字节未改 |53/53|
| 新 commit source/recipe/coupled 内容 |69/69|
| 新 final source/statistic/rekey 内容 |59/59|
| actual C reference OFF / V3 OFF / V3 ON |7/7 各自|
| 自有四母胞 single-commit + second-refusal 范围控 |20/20|

这些计数大量重叠，不能求和为 unique 覆盖。fresh-test-summary.json 指向全部原始 stdout；每个 compile/run 的 logs/*.json 保存 argv、白名单 env、rc、wall、affinity、候选源绑定和 held 结果。run_independent.py、run_fresh_focused.py、run_fresh_normal.py 与 templates 是审查驱动，不改变实现；最终驱动使用本目录模板，固定 P 路径仅用于原命令字符串重映射，不再读取移动候选模板。

原两 CPP：R/independent-late-source-budget.cpp SHA519ec9c4e43ef2c30afd2f0b35e5d540743e4f03071bb8f7b7a22c8dc86e064a；R/independent-shallow-checkpoint-clean.cpp SHAbe5175e5adcb8d93a076be0ba7fe9695da5264be6de0903468952d48818ce8ef。与 V2 独审文件逐字节相等。前者最后 remaining callback 10/10 变异同实例 factory Origin.piece / producerDigest，V3 acceptedAfterSourceMutation=0，两项均拒；后者最后 commit callback 34/34 改 checkpoint.points[0]，V3 accepted=0，点/current/context/W 状态均未发布。原 V2 RED 保留在旧独审包，本包没有覆盖。

Actual C 三路输出均由 decomposeConformingCones→toPolyMesh 写真实 topology、binary64 points/CC/V/Sf/FC/quality、patch roles。reference OFF 与 V3 OFF 各 6,594 B 完全相同，SHA46e3d9735734e6ede20e10582e18f37fef017bf0d91c1cea1c30a7c4950458e3；ON 为 SHA27edf233c800d3f554d2b2d3a0cfd396a74f58e72bd0d2fe83040c740ac7f4ca。ON captured/staged/committed1/1/1，D4→2；四个 touching union examined4/accepted0/protectedRefused4。source/alias/full-halo prefix/final failure0。夹具只有 2 source parents、20 installed cells、0 coupled certificate faces。

## Source-bound closure

Lifecycle.H:473–490 在 begin 首次 capture owned monotonic deadline 后，核六个工厂 fingerprint、计数/溢出/256MiB aggregate payload，再 make_shared<const SourceSnapshot>(*src)。sourceSeal_ 是 Transaction 私有；Journal 的 dependencySeal_ 亦私有（LocalOracle.H:436–463），没有公开 getter 或 tail refresh。原 factory 的 make_shared<Snapshot>() 分配并非真正 const；V2 使用合法 const_pointer_cast 的反例仍合法，V3 通过私有深值封条拒绝，而非声称 public shared_ptr<const> 自动不可变。

Lifecycle.H:219–238 比较 Cut 的全部 primitive recipe：binary64 points/reference、ordered rings、owner/neighbour、patch/zone、alive/source/refinement/piece/evidence 数组以及 Lineage active/revision/keys。:337–346 要求原 earliest checkpoint 四个 operator pointer 全空、原 checkpoint/baseline instance 不变，逐值比较 original earliest 与先前独立 inputRecipe。:585–592 在 capture 证明 earliest/input 一致，再复制 coupled baseline 内容，不能在 final 最后 callback 后再拷贝新 baseline 冒充旧身份。Coupled faces 的 incidence/aliases/coords/roles/DP几何、maps、bytes、copySeconds/constructSeconds 均比较；本夹具的 append-certificate/role-map negatives 只证明拒绝，不能替代真实 coupled positive。

Commit :764–774 完成 available/current 外部回调，最后 remaining sample 后以 owned-only clocks 做 dependencySeal.current + sameRawInput 全输入重核。:776–777 首次 live 写仅四个预分配 swap；随后不调用 source/clock/MPI 或分配。拒绝不回滚调用者自己的 mutation，只不发布本事务；成功后的 caller rollback 仍是预分配 swap，fresh 正/负控通过。

Final :868–877 先用 owned clock 捕获 entry revision/derived cellPiece ledger，在 external remaining callback 之前完成。:878 捕获 original remaining；:884–895 完成 actual source authority/origin callbacks；:899 最后 remaining sample；:903 再核完整 owned source/earliest/coupled 值。后续 :905–911 重核所有相关 cardinality 后才 indexed read，:912–1006 以 owned tail 重核 entry childPiece、source metadata、protected points/alias full classes、实际 lowered owner/ring/map、installed CC/V/Sf/FC/determinant/tet/patch/quality。有限值、全强 tet 与既有质量门未放松。真实 derived childPiece rekey 在验证入口前仍通过；factory Origin.piece 的最后 callback 变异拒绝，这两类身份没有混同。

Final owned 4s entry budget 与 originalRemaining、lastSample 三者相交；PublicationBudget(正常现行 PreparedTopology.H:22–42) 在 callback 前记录 begin，只取 min(4,remaining)，ticks 向下截断，不能被 later larger sample 延长。Fresh delayed-last callback 4.0501s accepted0；first0.5s/later-large+550ms accepted0。事务 begin 不能重置，原 repair/job clock 没有从头算。Actual APP source2853–2857 的 remainingW 是 timeLimit-timer.elapsedTime()-60 的纯读计算；2862–2865 snapshot cap 也计入同 whole clock。此源码纯读事实不撤销 public API 对恶意 callback 的拒绝需求。

SnapshotSeconds 例外有实际源依据：C14394 是唯一正常赋值，发生在 physical commit14352 后；其余正常用途仅 JSON/Pout/复制到最终 evidence。原错误全时间 seal 的实际 ON 拒绝由作者冻结在 intermediate-full-time-seal，未当 green。独审重新遍历 normal37 与 final candidate 的所有当前 source uses，结果存 snapshotSeconds-independent-current-source-use.json：最终 seal 仅此诊断项不作为 identity；copySeconds/constructSeconds 仍 bit compare 并被 late negatives 验证。统计项正控通过。不能把统计豁免推广到任何 source/geometry/deadline 字段。

默认 OFF 仍是 CutCells.H:814 earliestPhysicalPointRepair=false；C12616–12625 两个固定 all-rank setup reductions 要求请求一致且 current/repair 同启。source unknown、foreign same-digest instance、missing creator、expired clock、alias/ring/role mutation 均拒；没有 native arbitrary permission 或 quality relaxation。

## Explicit scope limits and failed exploratory expectations

自有四母胞探针保持两份真实工厂母胞组、原 checkpoint、第一次成功点提交。第二个 disjoint physical point在 V2 与 V3 均 capture stale3；原 API 在 capture 设置 legacyJournal 后的 O::current 仍受旧 global literal-point/journal 门约束。V3 added earliest equality 即使到达也会拒原 recipe 与 postcommit 当前 recipe 的差异。现行 actual C14352–14353 一旦 commit 成功立刻 break，且整个入口只有一个 Transaction；Journal.matches 仅 p==point_，没有 chain。因此这不是 V3 正常回归或安全漏洞，也不是完整 multiple repair 已实现。

最初 independent-two-disjoint-points.cpp 误保留二母胞 bindings.cell[2]==0 断言，四母胞实际首 live 为 cell4；同时试探 second capture 的 unsupported-success 期望，两次原日志各18PASS/2FAIL 原样保留。独立 clone independent-two-disjoint-points-scope.cpp 只修夹具索引并明确 second-refusal 范围控，20/20。没有修改 V2/V3 实现、Frozen 包或正常源。

不能声称 N 次成功导致 N 个 whole-cut 保留：当前正常路径至多一次成功，探针第二次亦拒。可确认本次成功 journal 延长一份独立 whole input Cut 寿命，begin 新增一次完整 SourceSnapshot 深值 copy，再加 coupled certificate copy；stage 同时存在原/current/lowered cut 和 installed caches。fresh 小夹具 source payload1820B、Snapshot object296B、baseline object176B，不能外推大 case RSS。256MiB cap 排除 allocator overhead；Cut/certificate copy 和外部 callbacks 是阻塞操作，owned deadlines 可拒过期结果，却不保证总调用 wall≤4s或可中断 allocation。未作大 case 性能/RSS测试。

四个 operator shared pointers 的 content 并没有被一律深拷贝：原 earliest 必須为空图，live prewrite 允许正常 context/W references，tail对它们要求 instance identity，专门 deep-seal source/earliest primitive/coupled values。任意 unsynchronized vector mutation、未知 nested context graph、general shared context callback behavior 不在此次 scoped guarantee 内。没有据此扩大 unsupported grammar。真实 coupled positive和27-star/wholecase需要后续明确设计与授权。

Normal controls 前后均 independently37/37 held；APP551,736B SHAb21a0c10d903b013dbba8e4f15eb9f359f27d227c5ccc76dd2bc507316d3f9d1；LIB3,033,048B SHAa9993fe4bcd948a1118f178510f9781f8d92a9c2bd049f33ccbf339ccdc8bbd5。原 normal Shinaide Failed3 D909/W100/VR2 及独立300s deadline失败未被本次 tiny 验证改变；本次没有运行大 case。Root 决定后续 exact source scope，独审没有正常 apply/enable/build 授权。
