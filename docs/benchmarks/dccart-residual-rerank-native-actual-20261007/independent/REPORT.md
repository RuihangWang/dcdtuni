# tiny native 独立只读审计（2026-10-07）

**结论：接受这一份 tiny、serial、private ON 网格的严格质量结果；拒绝将它等同于 normal dcCart、完整 Shinaide、并行或 300 秒全流程验收。** 当前没有需要否决 tiny 一手结果的质量/几何篡改发现。未运行任何 OpenFOAM/native、编译、服务、GPU、API、浏览器或 Git；仅在仓库内读文件和做 CPU36 单核数学解析。

## 核验范围与证据

目标 F：`/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp/dccart-residual-rerank-native-once-20261007-exclusive/FROZEN-actual-results-v1`。MANIFEST SHA256 `38c52b0cabab7412fadf2448fcc5c61d6c06f32352bc3bd9b424f2e2bcfb67df`；独立 FULL 检查 204 payload、7,944,192 字节，逐文件哈希/长度、文件集合无差异。旧 nonlinear-tail F 200 payload 亦 FULL 核验。`observations.json` 和 `audit.py` 给出独立解析与条件。

实际 39 源码相对旧已执行 tail 仅 H 改变，且实际 diff 与被审 source.patch 逐字相同：H `17b21… → 2bf2…`；其余 38 文件持平。构建原始记录确认 6 翻译单元、6 新对象、6 新依赖、零对象 seeds，库 CutCells dependency 确含候选 H。APP `75781e19…`、LIB `95e44f06…` 和两臂原始 LD_DEBUG 的 calling-init 都绑定新 private 路径；不是仅凭环境变量或旧 binary 验收。当前 stock checkMesh 字节与运行计划锁定 hash 相同。

原始 stock 文本两臂均明确 OpenFOAM 2412、nProcs 1、`-allTopology -allGeometry`、完整拓扑/几何项和 End。OFF 为 `Failed 1 mesh checks.`、D minimum 3.15405e−5、D set {1547}；ON 为 `Mesh OK.`、D minimum .001102、全部 stock sets 空。两臂 native/checkMesh 原始 status rc0、无 timeout；checkMesh 前后 8 网格哈希不变。**rc0 本身没有被当成 OFF 成功。** 没有删除 cells、badsets 或跳过 checker 的证据。

自写二进制解析（不是导入实验 RUN.py）确认 1704 points / 7514 faces / 6847 internal faces / 3047 cells，OFF 与已冻结旧 tail-ON 的 8 网格及原始 sets 逐字相同。OFF→ON 仅一个 written points row 687 坐标改变；faces、owner、neighbour、boundary、全部三类 zones 逐字相同；667 外边界 facets、全部 660 边界 point 的 IEEE754 bits 不变。对全部实际有向边界作独立 exact-dyadic 三角扇积分，边有向闭合、6V 与 24M1 两臂严格相同，并与一手回执吻合。这里证明外壳守恒，不发明内部 sourceUID 或材料移动权限。

原始输入绑定 12 项、native 前两臂 fresh-input proof、其来源文件哈希全部相同；运行后其他 11 项逐字保持，唯一可变文件为 native 派生 dcCartSourceRegions.json 的 startFace/nFaces。完整 schema、来源 UID、facet inventory、roles、其他字段保持；每条 patch index/name/range 与实际 boundary 相符。原始 source.cfdrun hash 与 meshDict 的源授权 hash 相符。

直接从原 native stdout 解码 uint64 observation：OFF 四 proposal 与 ON 前四 proposal 逐字相同，前八 fraction 逐字相同。新增第五 proposal dimension3 / moving diagnostic 687 / requests256 / iterations27 / restoration0。primary fractions 1/.95/.9 被既有 veto4 拒绝，.5 被既有 hard-veto3 拒绝；原 best-hard checkpoint1、fraction1 的 called=127、veto=0、D/W count 1→0，随后原日志确记 commit。保护域与 complete-cavity/earliest-physical 仍为原来的无新提交状态。

## 预算与授权

新源码仅增加 exact1、serial-only、默认关闭的调度条件和一次可选重排；原 20 秒 start 只构造一次，所有 census/ranking/proposal/安装/guard 都消费同一个 elapsed 与原外部 remaining budget。两轮总 admission≤256；每 proposal 请求≤256、迭代≤32。第二轮须第一轮有成功 commit、有 admission 余量及原 clock 余量。原七项短路 leaf guards、完整 joint installed guard、全 untouched-point bits、原 overrun rollback 仍在；没有刷新 oracle、重设 clock、降低 .001/.05/.01 等门槛或扩展源授权。

CLI110、native outer60、stock120 有单独明确 Root 确认。原 all15 common flags 相同，唯一 absent→exact1 为 residual flag。RUN.py 删去 Tail-OFF restoration zero 断言的理由是现在两臂皆 Tail-ON；并没有删掉完整 trace、stock 或源绑定验证。两项准备工具断言失败发生于编译/native 前并留存；Root 初 FULL verifier 按 list 解读 dict 的 TypeError、独审一次 UTF8 解码二进制 points 的 UnicodeDecodeError 均是只读工具问题，未触发 science 重跑或改动实验结果。改用明确二进制 schema 解析后独审一次成功。

## 元数据缺陷（需附加勘误，不改原 F）

1. receipt.json `/actualPair/actualNativeRun=false` 是从 build-only pair 对象继承的残留，与实际 native status、LD_DEBUG、产物及 checker 一手记录矛盾。它不应被用于自动认定“没有实际运行”；对外收口或下游 machine consumer 应使用明确实验级字段，并在新 review/checkpoint 里说明原字段过时。
2. run-plan.json `/noNewProposalOrQualityCalls=true` 为残留或语义不精确字段。新调度确实可能、且这次实际新增一个 proposal 与质量调用。只有“observer 本身不增加决策调用”可以成立，不能声称 residual 修复没有额外调用。该字段不改变 native 预算/代码，但需附加勘误。
3. case generation-receipt.nativeRun=false 描述的是旧原始输入生成阶段，属于不同阶段身份，不能把它误读为此次执行结果。

## 推广阻断与最小下一步

本次 tiny 为 private neutral wall-box、base .25，singleton contract 的授权域是 globallyNoBulkSourcesAndNoMaterialDomains；native callback 还要求安装后的 cellZones 全空、完整 writer binding sizes、物理边界/zone/coupled-star 保护。**真实含 rack 体积热源、porous 或材料域的 Shinaide 不能仅凭这个结果获得 singleton 移动权限。** 除该 2989-byte 小调度 patch 外，private H/C 相对 normal 还有之前累积的候选差异；源码、producer、flag 或 binary不能混标成 normal 已验收。

最小下一步先做只读 normal-versus-成功 cohort 的明确差异/依赖表，以及真实 Shinaide producer 的源授权适用性判定；独立列出必要 production 行为与纯 observation 行为。保持现 normal 与 private flags 默认关闭。确认源角色边界可满足后，在唯一冻结、fresh 6-unit build 的候选上做一个 fresh 几何/来源相符的中等案例严格 checker（每 case 一次、总 budget 硬门、保留原 flags/thresholds），再去 full Shinaide 300 秒全链。若 Shinaide 源授权不满足，先完成 source-role 安全设计与完整实际绑定验证，不能改 contract 文本伪装 eligible，也不能拿 neutral fixture 的 identity代替。并行和真实风冷收敛仍需分别验收；tiny .267 秒不是所有 case 分钟级证据。

native index、written row 与 source UID 的逆映射仍未建立。观察到同 label/坐标并不构成映射；本审计只将 687/1547 用作各自记录域里的诊断编号。
