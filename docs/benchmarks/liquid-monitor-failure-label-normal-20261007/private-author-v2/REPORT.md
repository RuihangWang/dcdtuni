# 私有液冷失败原因与参考场景名称窄修

基于 Root 确认正常 I@38c5a3e6/CTu，不修改正常源码、服务、Kit、浏览器、API、GPU、Git或cfdproj。当前正常A F2批准状态保持。apply-map是 integration exact guard，Main不能blind整文件复制。

3 production targets：LiquidRuntimeMonitor.tsx正确reference_scenario accessible name与当前failed原因显示；liquidRuntimeClient.ts把真实server {type,message,last_successful_seq}错误类型补齐，并提供同一runtimeFailureMessage纯文本formatter；LiquidExperimentPanel.tsx仅必要当前求解卡错误消费者复用formatter（原object直接React文本会崩溃），不改左旧legacy入口/布局。右监视布局、全部MMS/物理计算/时间轴/图曲线/renderer/pick/science源都保持。

formatter仅当当前session.status为failed才显示该会话error；原state.error/当前求解卡local error优先，不能改solver状态。真实结构化error严格检查文字type/message与非负safe整数last_successful_seq；旧session string兼容；未知/缺失/空结构用既有localized“失败”fallback，绝不序列化未知object。新run接受/reset清旧原因；completed/cancelled/running不显示旧session原因；transport重连提示优先，恢复后重新显示当前solver原因。formatter不授予任何来源/求解真实性权限。

真正同一actual runtime client + mounted UI controlled HTTP回归：logs/before-combined-complete原3production源码11项9失败/2通过（原因缺失、错误名称、当前solve cardobject React崩溃）；新test SHA7b30635cc699e4f3c3be8b09a61355061e78dcd6b44fdcc40b397d5b5a2a2cf4逐字保持。after-focused全部8suite95/95、tsc0。actual-import-source.json逐个证明before/after确实加载私有3目标字节，而非symlink误加载normal生产源。只mock HTTP和canvas与左legacy subcomponent，无runtime生命周期mock；原7个旧suite中的2个布局transport mock改为复用actual纯formatter，独立新tests仍actual store。没有browser/native/solver/GPU能力声称。

保留全部首次原证据：logs/before第一版9项7失败，其中request-kind新标记先截断恢复断言，不单算产品RED；before-product仅重排断言先证明原source恢复原因丢失7失败。后来增加必要solve-card真实崩溃2反例，完整11测试baseline9失败。私有初次focused/tsc缺shared/transient/vendor/schema JSON导致收集或类型缺文件，非normal产品RED；全部实际原依赖补齐。类型真实TS2322揭示同一left consumerobject缺口并获Root授权修。修标签后旧MMS test仍按错误旧accessible name选select，单测合同1失败；2处selector改为可见“参考场景”，MMS数学/身份断言零改。首次脚本错误以为只有1处，actual2，未写源码，记录test-selector-correction.json。不删除任何失败日志。

7 exact map targets：3生产+新增LiquidRuntimeMonitor.failure.test.tsx+2原布局mock只复用实际formatter+LiquidDAEControls两个select名称。依赖输入只用于私有复验，原3cfdproj逐字copy、从未写正常project。参考范围由../current-ui-static-audit-shared-20261007/FROZEN-readonly-v1保留五项静态要求与旧真实UI边界。

便携复验：python REPRO.py --out B/.codex-tmp/新的独立目录 --cpu 36 --types。Root另独占CPU可显式38。脚本FULL检包、installed11依赖入口SHA、在新owned copy跑同baseline→8suite→types，所有TMP/cache在输出内、Vitest cache=false/1worker/CUDA空；每组120s上限。脚本不触normal构建/API/browser/Kit/Git，输出不能复用或放integration内。仍待Root独审/normal guard apply+生产构建+用户正常登录后的实机检查，OV grid/roof/projection/Shift像素未由此闭合。

## v2 便携脚本纠正

F1完整原包不改。原便携fresh baseline真实9failed/2passed、3源码SHA与normal7held均正确；REPRO由于Vitest在FORCE_COLOR=0仍输出ANSI，直接匹配raw stdout误报expected=false。v2只在判读前去ANSI，原stdout逐字保留，所有7 normal apply目标字节/map/source.patch完全不变。harness-v2.patch/F1-reference.json及evidence/portable-first-ansi-mismatch保留一手证据；此不是产品RED。
