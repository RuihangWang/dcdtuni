# OO Phase A1 独立源绑定审查（2026-10-07）

结论分为两部分：FROZEN-v1 的完整自足 fresh 复现不通过；候选产品代码在 capability+Baffle+export-solid fragment 的窄范围内通过本轮独立审查，未发现新增几何/材质/科学参数差异。Root 接入前应要求作者生成修正 materializer 的新版本，保持 v1 与原失败证据。此结论不批准 normal apply，不验收完整 OO 目标、浏览器像素或科学 solver。

## 输入身份与工作边界

服务器40901，B=/mnt/lv/home/litianyu/mycode/dcdtuni，IT=B/.codex-tmp/goal-20261005/integration。实际B/IT AGENTS.md已读。F=B/.codex-tmp/oo-surface-capability-phase-a-private-20261007/FROZEN-v1。

全部1112 payload均完整读字节并校验：63,793,935B，无 mismatch。manifest SHA256 029e7bfb38195856874b98f2cd7bc6fcfdae17a4dab9d9dfa43ce935c3127ac6；apply-map SHA f1a2e3849e54b70b0b0eb9b307ead8fe5c980959d7167122c10c4ada0851dbb7；inverse-map SHA15273d8abd30b4a1fdcc59b4b3cef3f2a1ed770b74ac984f1c7f477476639b9f；candidate.patch SHA0dc9a581a93e79a145ce0f6c40a7a1ac1b162efa3ccc7ca3d264a3bea2d9b7df。

源码审查针对19exact targets中的14产品目标、3测试及2固定原始捕获夹具，阅读了产品diff及相关纯数值/材质/既有compiler分支。主要核对薄Baffle几何/材质、capability实际-kind区分、共享Three/classic装配、科学solid context、输入/输出ownership和保护基线。全部新工件仅位于本审查own目录R=B/.codex-tmp/review-oo-a1-independent-20261007。CPU12亲和、OMP/OpenBLAS/MKL=1、CUDAempty、PYTHONDONTWRITEBYTECODE=1。不启动服务/API/browser/GPU，不操作Git或normal源码，无native Allwmake或业务案例运行。

## 唯一确认问题：冻结文件权限使materializer不能覆盖候选目标

F/materialize_private.py:19使用shutil.copy2(source-base, destination)，把F已冻结源的0444权限复制到新私有目的文件。随后:21–22再次shutil.copy2覆盖candidate modify target，第一次写src/frontend/react/src/domain/BaseEntity.ts即PermissionError。F正常只读语义正是复现应支持的状态；因此作者冻结前的fresh materializer证明不能证明这个FROZEN-v1可fresh重建。

本轮原失败完整argv/env/raw traceback保留于candidate-orchestrator.log和fresh-orchestrator-commands.json。命令在own资源包装脚本中执行；包装仅把PACKAGE指向F、将原硬编码线程2改为任务授权线程1，没有改变复制逻辑。before重建可完成，candidate原重建exit1，未进入candidate tests。

继续产品源码验证时仅修改own materializer副本，在每次复制目的文件后令其u+w。差异在materializer-own-workaround.patch；F与normal没有任何修改。另用全新candidate-writable-copy目的路径重建。19个own target实际SHA逐一匹配F afterSHA（normal-targets-and-own-candidate.json）。这个workaround是审查工具修补，不能当作v1原REPRO通过。

建议新版本使用copyfile或显式只给own目的文件恢复可写权限，在冻结后从新的不存在目的路径运行同一REPRO。保留v1及其失败，重新冻结脚本、回执、manifest，不覆盖原包。

## Fresh验证结果

所有实际子命令及exit/log/JSON在before/receipts与candidate-writable-copy/receipts；外层脚本shell exit0不代表每门全绿。

- before新增73：28PASS/45FAIL；candidate新增73：73PASS/0FAIL。
- protected旧214：before与candidate均200PASS/14FAIL。same-protected-failure-names.json确认文件+完整名称同名，10个BOX/CDU旧carrier golden、2个material golden、2个CP旧graph/source固定SHA门仍失败。不能标为旧214全绿，亦未刷新任何golden或原输入。
- current capture选择26项：两侧均1PASS/11FAIL/14pending，其既有golden RED刻意保留。实际捕获产物另作byte比较。
- 新抓12个BOX/CDU/material-extra/实际CP graph+saved source文件，总3,998,335B，before→candidate逐字完全相同。fresh-literal-protected-output-comparison.json逐文件保存双SHA/大小；两侧文件本体均保留。
- types exit0；production build exit0。这是私有源码构建，未部署或触碰服务。

protected两侧同样红仅证明本候选未增加该已知差异；12真实当前产品逐字相同提供另一份实际当前输出证据，仍不修复旧golden差异。

## 产品窄范围审查

1. entityCapabilities.ts按实际definition/kind/version/supported区分typed/opaque。BOX/CDU/Baffle有owned product；Wall/Floor/partitionWall及其他现有carrier显式legacy-delegate且owned geometry/material unsupported；network/probe/definition/port/face类不伪造BOX。未知版本/源kind mismatch fail closed。capabilities为availability-only，不声称数据合法、已显示或科学admission。coldPlatePair仍指现有参数kernel；BaseEntity.physics仍明确record-view，本包不把所有物理声明变为独立owned参数。

2. baffle_geometry_product.js只建立front薄片，复用既有BOX/CDU的frame、earcut、polygon-clipping和STL命名数值函数。每调用得到独立Float32 buffers及Uint16/Uint32 index，保留patch/wall顺序、refIds、wallIndex、rawUV和exportName。纯产品没有Three/DOM/WebGL资源读取；UID+finite x/y+front face前提只保证可运行产品，不赋予badSTL/科学physicalValid权限。partitionWall厚CSG保持原delegate，未替换成薄片。

3. BaffleEntity product/material方法通过现有支持检查；项目恢复/UID/schema/physical数值函数未改。geometry_scene装配纯buffers与material recipe，仍保留载图/cache/ensureWallOpacity等carrier层行为。当前28份原始Baffle carrier控制和新product门复验通过，buffer/index/UV/材质/命名/STL/source-after-normalization与固定原始捕获相符。classic HTML仅加入相应共享kernel加载顺序；无实际browser/像素验收。照片rawUV与carrier stretch UV没有混为同一字段。

4. cfdrun_export.js的porous数值producer主体未改，只将入口改为owned clone，再按原master/slave顺序写输出。新的resolveAirSurfaceFragment要求真实export-solid名称，调用现行translateOpenings，保留solidName、patchName、objectPath/surfaceUid、sigma clamp、缺省和未知perforation扩展；sourceTerms为空。它不把实体UID映射成merged_wall_baffle，不改整个cfdrun compiler/schema。master/slave在同一产品内共享parameters是既有producer行为；对输入、独立下一次fragment、sourceEntry的引用泄漏被clone隔离。本轮常规JSON/data输入及输出变异门通过。此helper不证明任意调用者提供的solid context是真实STL，也不授予未知source几何科学权限。

5. callback没有被加入这些新几何/材质kernel的公开执行流程；材料context仍是明确creation intent，照片/Three IO留carrier。本轮没有看到新的callback原子发布场景。未知函数型扩展不属于持久化JSON合同；不能把本轮结果外推到任意getter/proxy/renderer-resource对象。

## 身份守卫与范围

末次19normal target前像仍全部held（12新增目标仍不存在），19own candidate target全等F afterSHA；文件与SHA在normal-targets-and-own-candidate.json。Root另有已告知取消/OVfit正常变更，故source-base整树只用于该私有实验复现，不可覆盖IT。HEAD仅按Root给出的metadata记录，本审查没有checkout/stage等Git操作。

F末次manifest/payload检查1112项均held。没有normal apply。后续Root需要新的可自足冻结版本，再按19exact map守卫接入；真实browser/STL compiler边界、scientific strict/checkMesh/solver、OVpixels、一般OO owned产品仍是独立未验收事项。
