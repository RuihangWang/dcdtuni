# CompleteCavityAuthority 独立接受标准与风险清单（实施前 v1）

2026-10-06。只读计划/旧生产基线；**未审查 AIR 当前 WIP 差分，未运行业务、测试、build、browser、mesh/checker/solver**。冻结实现后须由 ROOT 再授权逐行复核。本文件是接受标准，不能当已过门回执。

来源：AIR `.codex-tmp/two-lobe-native-transplant-plan-20261006/`、`two-lobe-finite-native-replay-20261006/` 的 14 个已读计划/证据文件，完整 owned copies 和 SHA 在 `read-identities-v1.json`。旧 H/C/APP/两个 snapshot H/旧pair contract 6 个 original-*.gz 已独立核对解压全文 SHA/size，并保存基线副本和行号摘录。live 5/6 文件已不同于作者初始基线；只登记 current SHA，**没有把 WIP 当成旧基线，也没有实施审查**。

## 接受层级与当前边界

- **A：源 authority/lineage 基础设施。** 新独立 CompleteCavityAuthority 可以实现多来源验证和正常 lineage 目录；需要 source/API 和后续定向证据，不能据此授权拓扑修改或宣称 five-origin product candidate 成功。
- **B：有界拓扑消费者。** 必须从当前正常网格发现完整 core/halo/两 lobe 与 separator，再作原子替换、实际 installed quality 和守恒/边界角色检查。消费者不存在时本层始终未通过。
- **C：完整 native writer 与产品。** 必须保留 earliest baseline，最终真实 compact polyMesh、point alias、owner/face order、全 rank 证书及完整 checkMesh；完整 API 的 ≤300s 另由真实运行测量。局部或 API 测试不替代本层。

作者明确：当前 max128 是 `collectCompleteOrigins` 的 **live core cell list** 上限；不是 halo/replacement/geometry guard 的上限，并且 source-only 尚无拓扑消费者。未来 B 消费者必须另加 old complete affected halo / proposed replacement + new complete halo 的独立 cap；不能将核心 cell 数界当作所有 work/peak-memory 已有界。

现有 finite replay 的“32→40 core、101→109 complete halo、局部 D5→0”等都是作者报告，本文未重新运行数值核查。报告明示原 normal 五来源 rank/level/leaf/piece 未绑定、最终 writer 未跑、P13 全局仍失败。其 native handles/TRACE parent/base/成功 seed 坐标只能用于 audit，不能被导入新源 producer 作算法权限。

## 不可替代的旧源码约束

基线 `dcCartCutCells.H:59–75` 的 weightUnionOrigin 包含 rank/nativeParent/level/leaf/piece/完整 sourceFacet tuples；`117–120` 将 TRACE 父/base 标为证据。`cellRank/Level/Leaf/Piece` 才是普通网格 ancestry；`writerBindings` 是 native slot→compact slot 的真实映射（79–82）。

旧 `dcCartWeightUnionSourceContract.H:89–131` 从当次 dictionary、单次固定 gather/scatter、正常 snapshot producer 和 actual remaining callback 建 authority；wrapper 在 129 强制 exactly two distinct parents。旧 `dcCartPostCarveFluidOwnershipPreview.H:115–136` verifier 在 123 同样拒绝非两来源。**这两处 pair 表达式/限制不能放宽；不得重复调用 pair verifier 拼凑五来源授权。**新 API 独立验证 bounded OriginSet，旧 pair 仍使用原契约与 final verification。

`SnapshotCore.H:40–88` 的 validShape/verify 是结构来源 guard：完整有向 ring/edge closure、正体积、identity/ordered facets 和 digest 一致，不能替代 installed tet/quality/覆盖证书。旧 Snapshot (24–30) 与 verify (79–80) 没有 boundaryMembershipDigest；新独立契约需补 fresh snapshot 的真实 boundary digest 绑定，作者称 WIP 已补，冻结后再核代码/dataflow。

APP 基线 `dcCartMesh.C:2254–2259` 正常成功 carve 才置完成 flag；`2840–2853` 取 `timeLimit - timer.elapsedTime() - 60` 并用同余量约束 snapshot 的 4s 局部 cap；`2856–2861` skipped/未知正常路径不能准入。`dcCartCutCells.C:14194–14261` 最早 installed writer checkpoint 只捕获一次；`14222–14240` W packet 当前在同一初始 oracle 中捕获，拓扑消费者插入后必须移到当前 mesh 的新捕获入口，不能沿用旧 packet/cache/oracle。

## A 层接受标准：producer、consumer 与 lineage

1. **同一当次正常不可变 snapshot。** 新 authority 的 origin producer 与完整 set verifier 捕获同一个 `shared_ptr<const Snapshot>` 和按值 fresh material contract；callback 生命周期不得引用 APP 局部已销毁对象。snapshot.complete、completedFluidOnlyPath、当次 normal flag、正常 patch/zone registry 成立；所有缺失/错形/未知/skip/超时/非有限都 reject，不能由 env/global bool/defaultRegion/空 zones/cellOrigAlive 授权。
2. **全 digest 绑定。** 当次 rawSha256、rolesDigest、materialKey/materialDigest、producerDigest、boundaryMembershipDigest 与 point-role contract 必须一致；setup all-rank exactRowsAgree 不跳过失败/空 rank。改任何一项或将上一运行 snapshot 与新 contract 组合，应拒绝。仅检查字符串格式或全局单相空气已知不够；也不需要每个 origin 重复制完整全局 mesh/dictionary。
3. **真实 unique origin set。** participating live local cell 的 dense lineage key/显式 sparse union set 导出 rank,parent,level,leaf,piece 和全部有序 sourceFacet handle/patch/zone。按 full identity 去重；相同 identity 的 shape/facet 内容不一致必须拒绝。empty、超过8、未知/无效 key、重复伪增来源、别 rank、缺字段/缺facet/顺序角色篡改、死 source parent、非 finite shape 均 fail-closed。source nativeParent 是 snapshot 句柄，绝不能用当前 child native slot 或 TRACE label 猜它。合法 leaf/piece 数值 0 不能仅凭“看似默认”误判，完整 snapshot 比较决定真实性。
4. **dense + sparse 目录不变量。** 明确定义 dense key 是单一 snapshot parent、union sentinel 还是 unknown sentinel；dense 长度等于当前 native cell slot 数，sparse 仅保存 canonical bounded 多来源 set。unknown 不能回退默认 label。value clone 必须深复制 dense/sparse，不共享可变 lineage；全部 append、same-parent literal merge、accepted cross-parent merge、删除、rollback 与 compaction 输出路径一致。
5. **正常传播。** cone 创建时第一个重用旧 cell slot 和后续 append child 都继承同一正常 source key；同父 literal union 继承完整 set；跨父 survivor 的 union set 是所有已验证来源的完整 union，不覆盖成最后一个 parent。dead drop slot 不得再作为 active origin。有限集合超过8立即拒绝，不能截断/只留first/去掉未知记录。TRACE 开关不影响目录的分配/结果，evidence 输出不得反过来给目录赋值。
6. **真实 compact 映射。** toPolyMesh 只用实际 writerBindings.cell 将 native lineage 输出到 compactKeys/union目录；没有映射、重复 compact 映射、dead/live冲突、长度不符、未知 entry 均拒绝。不是把 native slot 重新解释为 snapshot parent，也不是按 compact index 恢复 origin。旧 source shapes 在 writer alias/排序之后仍按原 snapshot验证；point aliases 属于独立 final certificate。
7. **旧 pair 完整保持。** 两个 exactly-two guard 保持；旧 pair API、authority bytes/域、正常拒门与 final W verification 不被新 API代替。允许为新 boundary field 添加 snapshot能力或 lineage同步，但需差分证明旧 pair条件本身不变、未偷偷以complete callback转授W权限。旧6源 baseline和 c9e1 finite kernel继续绑定，新增 API不意味改旧有限搜索维度/预算。

## B 层接受标准：最多8 origins/128 cells 的原子替换

- **界限可审计。** core live list ≤128且unique；explicit ≤8 unique origins。另在 halo census、lobe facet/edge-loop/plane枚举、replacement allocation之前限制每层 cell/face/point/incidence工作与内存。不得用 `resize(128)`/采样/早停截断假装 complete。超过任何界限应记录 bounded refusal，保留旧拓扑；source API的128 core bound不自行授权128以外的真实 halo。
- **完整发现。** 从当前 all-incidence 目录获取每个参与 cell 的所有faces/vertices和outside endpoints，完整 interior stars / embedded separator / 两个完整 lobes。几何循环、tie、separator、source shape、ownership或region不明确均拒绝；不按P12/P13 labels、坐标fixture、rank0专线或成功apex列表选洞。restricted grammar失败只表示拒绝本grammar，不表示global infeasible。
- **允许删除的内部面。** 所有移除 internal facets 的 actual neighbour/patch/zone/source-interface role 必须明确：local owned、noncoupled、patch=-1/sourceZone=-1且不跨受保护源界面。非局部/processor/cyclic/AMI/mapped/未知role拒绝；sourceZone是face provenance，不是材料ID。
- **literal外壳保留。** 保留每个外壳面的 original ordered ring、point0、DP点坐标、outside owner identity、patch/zone/sourceBase/完整 source role。physical baffle faces可以有非负sourceZone，只要exact literal role保持；不能 blanket veto全部外部physical zone，也不能以global materialKnown授权穿越内部protected sheet。actual fixture 的38外面/8物理面是固定模板的例子，不硬编码成通用允许数。
- **完整质量/覆盖。** 新 cells 的 strict visible seeds/positive volume/pyramids/tets/convex闭壳/不重叠/coverage/V/M1与当前完整outer一致，complete affected halo所有原passing D/W/R/S角色不回退；旧badD角色改善不掩盖新坏角色。已替换passing D继承使全部40新cells需要当前硬阈值；不能只查5坏D后代。actual installed arithmetic决定硬门，proposal tolerance只生成seed；坐标成功或局部det值不能充当authorization。
- **普通外部与coupled区分。** 普通外部几何/角色必须完整保持，但installed CC/V累加顺序可有有限ULP变化，只要真实hard-role门及守恒成立；不要虚称bit-exact。受保护coupled payload/DP/环/别名/patch role则继续依earliest→final exact证书，不能用ordinary容差替代。
- **真正原子性。** 在临时局部proposal或owned working副本做全部验证，commit前不更改live m.nCells/points/faces/owner/neighbour/patch/zone/alive、dense/sparse origins、writerBaseline、旧W状态或cache。count、origin、shape、role、clock、quality、allocation或final global许可任何失败均保留原输入；若采用回滚，覆盖所有目录/缓存/append计数而不是只恢复point数组。stage固定all-rank reduction协议，空/拒绝rank也参加，候选数量不同不能产生候选内不匹配MPI。
- **插入位置与新缓存。** 允许在 earliest checkpoint后、W packet/cache前插入；checkpoint自身不能刷新为已修改candidate。替换后从current mesh重建必需adjacency/area/centre/cache和W oracle/packet，不把 old unionParent/unionBase/weightPacket/installedBindings挂到新 cell slots。若插入更晚，必须完整 union前序正常 origins及source-role lineage，否则拒绝。
- **实际remaining clock。** 所有 setup/census/shape verification/geometry枚举/copy/guard/commit/final writer reserve使用当次APP monotonic whole-job余量；无固定环境grant、per candidate重新开始、90s diagnostic deadline替代、或仅kernel耗时计账。missing/NaN/Inf/≤reserve callback均reject；callback在各长循环/分配前后检查，collective顺序仍固定。≤8/128只是结构上限，不是≤300s证明。

## 高风险差分检查表

| 风险 | 冻结后必须看到的源码/证据 |
|---|---|
| TRACE/label 注入 permission | normal producer调用链来自同APP snapshot；TRACE证据字段仅输出；相同normal输入TRACE开/关directory一致 |
| global material变成五来源许可 | 新consumer逐origin verify fulltuple+shape+所有digests，未复用pair callback绕数目限制 |
| digest漏boundary/过期scope | Snapshot实际capture实值、complete verifier比对boundaryMembership；stale-run/各digest负例拒绝 |
| complete core实际被截断 | 超128提前拒绝，8-origin只去重复不丢内容；没有采样/label白名单 |
| dense/sparse共享或漏append | cutMesh clone独立，首child/append/mergedsurvivor/deadslot/rollback/compact映射所有路径一致 |
| native child号充当source parent | 非身份存储key不可转换猜测；真实snapshot fullidentity/ordered facets逐项匹配 |
| W旧packet缓存失效 | topology insertion发生前后生命周期明确，新packet/cache从newmesh捕获，earliest baseline仍旧 |
| 只有core质量而漏halo | B消费者明确完整halo cap/全部role census；邻外physical/patch/zone/ring篡改拒绝 |
| source zone被当material或 blanket veto | internal删除strict -1，外部literalphysical角色保留；明确哪种sheet可在外壳存在 |
| live先mutate再latefail | commit唯一边界与完整rollback/working副本；timeout/invalidshape/allocation/global-refusal不留半改状态 |
| MPI local早退死锁 | setup/final固定collectives含空rank；局部candidate不MPI；超时rank仍按协议refuse |
| clock只是静态grant | true remaining callable绑定APP timer；census/copy/guard/downstream reserve记账，非fixture11.649s推300s |
| final certificate被current覆盖 | earliest checkpoint不refresh，最终realpolyMesh bindings/alias/DP与old baseline核；missing证据拒绝写mesh |
| API测试被称product通过 | A/B/C三层分别报告，source-only接口未承诺complete-cavity可用或全局checkMesh收敛 |

## 冻结后逐行复核顺序（待 ROOT 授权）

1. 核 AIR delivered immutable files/diff/manifest，与保存旧6SHA逐项绑定；精确记录新增H/C/APP路径、source版本、库/测试证据身份；不扩源码范围。
2. 从APP normal flag/dictionary/clock→snapshot→CompleteCavityAuthority→collectOrigins/lineage→toPolyMesh compactKeys完整dataflow逐行追踪；先找丢字段/猜key/alias mutation/提前return collective分歧。
3. 逐检查clone、cone首child/append、literal merge、cross-parent survivor、删除、timeout/refusal、compact-map各目录更新路径，确认source-only层没有隐藏topology consumer。
4. 用作者定向证据验证新API原型与负例：missing/stale material+每digest、wrong fullidentity/shape/facet role/order、0/>8 origins、duplicate/fullset、>128 core、unknown/dead/local-rank、clone隔离/merge/append/compact映射/true budget；**本reviewer仅在 ROOT 后续另授权时执行任何probe**。
5. 若交付只覆盖A，结论限定“A基础设施已审/未审”；B/C仍列未完成，不因API通过触发未经授权业务。真正五 normal origins由未来normal path实机输出与验证绑定，不能用本地fixture补缺字段。

执行 guard：CPU28–31，CUDA空，TMPDIR/新证据仅本IT目录；无外部依赖，无源码/Git/服务/浏览器修改。已读作者数值/shape报告未重新计算；所有结论分清源码观察、作者回执与待验条件。
