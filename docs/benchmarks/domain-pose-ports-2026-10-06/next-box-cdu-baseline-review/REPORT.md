# BOX/CDU baseline independent review, 2026-10-06

结论：31-payload original baseline可作为当前有限 BOX/CDU **结构**改造的有效冻结 oracle；三个真实 record/input 与14个当时 producer源码匹配，无 golden重录或归一化kind污染的证据。它不覆盖WebGL材质/贴图、PDU display detail、CDU液冷details或载体交互生命周期。ROOT授权后的实现应保留这些旧carrier语义，不能把10个BasicMaterial控制通过扩大为这些能力验收。本轮是基线审查；新产品源未冻结/未审，非最终promotion。

## 独立绑定

- accepted next-plan: 6 payload / 54,710 B，manifest SHA `df892f850bcde2d2681119a04e3f1ad018369529ec934005d88be36bfb1329d3`。
- baseline: 31 payload / 1,574,993 B，manifest SHA `22a7a807558c507b2e3ff7a63eb2fc5d68a2a48a506acc150bc3d826a1e6c90c`。
- 两包逐payload hash/size/total核验并保存副本；14 producer-before copies与manifest相符，本次初始实读IT14源也逐SHA相符。该实读发生于作者报告开始product实施前；不据此声称后续WIP源仍不变。
- 实际 LiquidSingle CDU m_1、Shinaide UPS BOX m_9、Shinaide CDU m_30 的 model，与完整原输入 SHA绑定的 JSON pointer逐字段完全一致。全部10sample无persisted kind；7种synthetic明确标记。10case / 66 structural parts是fixture覆盖，非所有实体或prims。
- exact path/SHA/coverage在 identities-and-coverage.json，源码准确行在 source-excerpts.txt。未运行 capture、tests、probe、compile、build、Git、浏览器、API、服务或业务；CPU28–31/CUDA空，仅写本owned目录。

## 可直接支撑的 contract

**原始 F32/index与两次量化。** ShapeGeometry.js:85–88将positions/normals/UV先写Float32；geometry_scene.js:2269–70、2298–99再对这些局部Float32执行layout.applyMatrix4写回。geometry_pure.js:981–86的六面布局/方向/尺寸是实际被调用的普通分支。fixture保存实际renderMeshes局部positions/normals/UV原字节和index原字节/类型、part顺序，而不是新算法重造golden。domain红要求owned Float32及同index字节，并用registered bucket选择盒/CDU，适用于这10例。作者实现必须保留两次量化；world pose是独立Three载体Double矩阵，不应在数值product中额外再量化到世界Float32。矩阵/snapshot作为JSON有限number序列冻结，并非单独F64 byte-hex证明；现原carrier控制PASS支持现选例，不宜声称所有signed-zero/nonfinite matrix bits覆盖。

**STL/matrix/snapshot。** 原 capture确实先rebuildModels→updateMatrixWorld→读parts→buildGroupedStlMap→固定camera/target/time/selection的buildSceneSnapshot；final baseline控制重复同序列。stl_writer:93–114是真实world Double变换、mirrored B/C交换、重新cross/normal、toFixed(6)全文。fixture包含完整grouped STL，不是triangle count；mirrored/nonuniform/quaternion样本有实际负scale世界矩阵。多synthetic实际STL含-0.000000，故小负数舍入符号有实证覆盖，不能误列为缺失。degenerate过滤在writer:37–63存在，但这10例没有明确zero-size/degenerate阈值反例标签；writer本轮不改，因此限制声明即可，不能宣布全部阈值已测。

**noKind真实域边界。** final-red.test.ts的entityFor直接给persisted record，无legacy normalize加入kind；carrier仍调用原normalizeStateV4，首个错误harness日志保留。此修正与真实输入相符，没有把record改写为新kind以造绿。商品方法应通过entity definition/bucket，不依赖raw.kind必存在。unsupported ACU/rack/wall的红仅证明方法应拒门，不能授权其legacy渲染消费者被破坏。

**已发生的旧carrier mutation。** disconnected-wall-fragments有两个front wall parts而初始wallOpacities只有一个，capture normalizedInputSHA!=normalizedAfterSHA；其余9例未出现该hash变化。geometry_pure:292–295原ensureWallOpacity会补数组slot。product必须不mutation，legacy carrier仍需显式执行原补写并保留normalizer前后hash；不能拿domain不mutation目标删除旧save delta，也不能在纯product内复刻文档写入。新实体二次product返回独立buffer的红是有意义的所有权要求；carrier将UV缩放或dispose时仍须避免修改可重用product/cache数组。

## 已告知ROOT/作者的两个实现风险

**material-context oracle未覆盖。** 全10例webglAvailable=false；geometry_scene:2555–56直接BasicMaterial，跳过2541–54真实材质和2585–88的pbrRole/tileM UV缩放。全部imageFields为空、snapshot textures=0；opacity样本不是WebGL透明深度/材质证明，numeric red也不比较material recipe。已通知ROOT。作者随后明确：product只给raw UV，旧spawnRenderable material/image/PBR UV IO不动。这是合适范围控制，待冻结源码核实；若改为product预缩放或变动这些分支，现golden不足，须由ROOT另行决定小material-context基线，不能自行扩capture。

**共享factory的旧caller比BOX/CDU更宽。** SceneBuilder:361–365的非wall-mounted ACU、custom无cache等也走renderBoxModel；geometry_scene:2237–39含ACU patch角色、2304含cold_plate材料。registry:84允许BOX别名heat_source/crh/cold_plate。新BOX/CDU producer不能让这些既有caller静默转换、抛unsupported新错误或失去旧物理角色。数值product支持范围和legacy通用factory范围必须显式分开；本10case未覆盖它们。geometry_pure.buildFaceIslands:573–76仍需跳过非patch-bearing rack数组face，intersection catch/union顺序/difference异常语义原样保留。已把这条准确源码边界发作者，未请求新增capture。

## PDU、material/carrier及尚未证明的边界

registry:110–111仅record.assetDefinition.type='pdu'选择PduEntity，definition.kind仍为box；BaseEntity:140–141说明仅显式assetType恢复此类。SceneBuilder:367–373的name-PDU是display-only detail，物理kind仍BOX。当前3actual中选的是UPS BOX，baseline没有name-PDU或显式assetPDU记录，不能称PDU身份/细节golden已覆盖。继承BoxEntity的PDU数值形状可用BOX contract，名称不得改物理类型；最终只读审查应看displayOnly/exportExcluded和现分支仍保留。

sceneSnapshot:497以uid/child path/instance/group组成ID，所以新增carrier层Group、改变child顺序或把显示detail加入renderMeshes会影响协议；本fixture覆盖当前Basic结构path，真实WebGL edges/PDU/CDUdetail不在此控制。保留原spawnRenderable、同modelGroup child路径/parts顺序及原细节增添顺序，比直接声明snapshot协议已完成更准确。capture固定selection参数用于snapshot，未执行highlight→restore/fade/material-cache/dispose交互；这些约束仍应按已接受plan保留并由ROOT后续实际验证。当前liquidModeActive=false，CDU detail exclusion也不是已测结论。

## 本轮处理建议

不改变冻结baseline31，不新增capture或测试。本轮允许的结构实现使用单一Earcut/boolean numerical producer并保留原carrier材料、UV IO、命名与modelGroup结构；领域product从registered kind/bucket恢复，不改变physics或原JSON。先等exact source freeze，再只读核单一数学所有权、two-stage F32、legacy caller分支、wallOpacity兼容写入和product数组所有权。本报告不修改publication/docs、不接受新产品source，也不声称browser/Kit、checkMesh、完整OOP或全部物理类型过门。
