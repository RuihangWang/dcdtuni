# 固定温度面：独立私有绑定修复

当前候选解决实际 wallObject 固定温度边界无法匹配 STL 的来源错误，未应用到正常源码。此前结构体来源候选保持原 Frozen manifest 5789e8a6…、一文件 bfc1470c…，本包以该精确文件为基线，单独新增 thermal delta 0ce90cc3…，新文件 1503bafe…。累计正常源码补丁另列，不能把两版测试或范围混为同一候选。

## 真实错误与修复范围

共享 producer 的 patchSolidNameFor 已调用 composeModelScopedExportName，结果已带物体前缀；固定温度分支再次 modelName + '_' + hpSolid，生成 Observedwall_fixed_patch_Observedwall_fixed_patch_Front_patch。真实 OO/STL 名是 Observedwall_fixed_patch_Front_patch。正常完整 BCSpec 虽保留 temperature=42，边界名称不在任何实际 sourceRegions；附加 strict reader 还因 stlFileId=None 拒绝。两个独立问题均有旧原始输出保留。

新分支只使用一次已有 scoped name，对同名 semantic entry 立即拒绝，不覆盖 return/其他物理角色。随后按真实 current BOX/CDU owner、相同 OO 快照和 grouped literal、modelBoundary 操作、唯一区域、原生 product 的非 body part + 同一 face/单一 patch refId 绑定，计算真实 world-space 三角形面积，填入真实已有 STL 文件 ID/路径；CDU 固温面使用真实 constructions.cdus 路径。没有手填 role/digest，没有热参数重定，没有原 case/STL 改写。body wall、重叠 patch 或多人共享 exportName 不会被标成固温面；flat/nested custom payload 明确拒绝本原生路径。

这次仅覆盖当前 BOX/CDU 非 heatSource 的 patch.cfd.fixedTemperature。旧 cfd.heatPatches power 路径不改，旧 reader 不加 null fileId fallback。结构体纯 body 描述不派生温度或体积热源。源合同仍为原 global 保守范围；它接受此真实边界输入，不等于局部 bulk/material 拓扑支持已完成。

## 生产执行证据

真实 React reviveKinds、OO CPU emitter、shared manifest、bindStlFileHashes、正常 STLGenRunner preflight/dict 与完整 ResolvedRun 均执行。42 对生产导出、1040 条来源/物理参数/边界检查通过；本包没有 native APP/checkMesh/风冷求解执行。normal 23 个 producer/backend/source authority 哈希与旧 Frozen 全 1523 文件均保持；Root 另行已授权的 APP 默认 OFF 源码编译变动单独记录，不纳入“全部 normal held”声称。

9 个合法目标：原 wall-fixed-patch、CDU 固温、多 patch (42/20 C)、6 面 patch、缩放旋转、整面覆盖、存储 schedule、固温面与另外的旧 CDU return 面、隐藏物体。全部生产 raw/STL/sourceRegions/sourceSurfaces/UID/源科学设置保持相同，只有面名/其绑定键/实际 path-file-area 及 CDU owner 路径修正。完整实际 BCSpec 在重命名对应下逐字段相等，温度仍为 42/20 C。8 个没有另一个旧 null 引用的目标附加 resolve_stl=True 全通过。旧 CDU return 共存 fixture 的固温面已正确，但原 return descriptor 的 null STL 引用仍导致 strict reader 拒绝；其 return entry 未被覆盖，原 translator 尚未形成的 return BC 也未被虚称已修复。

同面 return + fixedTemperature、body 与 patch 名合并、重叠不同 patch、两个物体共名、legacy flat 与 V4 nested custom 都拒绝。28 个结构候选原对照里的非目标项目保持 manifest/rich/STL 与实际 BC 完整相等，热源 321 W 的字面 STL 面积积分仍为 321 W，既有 heatOutput 321→482 W schedule 与实际 BC 数据保持。

固定温度 wallObject 的存储 timeVarying 字段保持；当前完整 BCSpec.timeVarying 仍为空，这次不改变原有 schedule 执行能力。对 power heatSource 的 schedule 则检查原生产数据和其 literal 面积积分公式，不能以字段保存替代风冷瞬态实际求解验收。

## 留存错误与限制

两次 assessor RED 均留存 history：首轮误读实际 schedule 键为 heatFlux（实际 heatOutput），第二轮误写 Weight 合同名（实际 dcdtuniWeightUnionSourceContract）。仅修正证据采集脚本，候选文件始终 1503bafe…；无生产或合同放宽。最终记录 20 条分离的旧 strict/seed 拒绝，其中包括 baseline 固温旧错误、旧 heat/rack/server/CDU return null 引用和 floor seed，不能把这些看作全 case 已通过。

先前结构 Frozen 的 REPRO.py 路径物化缺陷已另有外置修正版 + 真 fresh 重放回执；本包采用修正路径物化器，不改旧 Frozen。各新文件均在 dcdtuni/.codex-tmp 下，不复制正常树或第三方代码到工程；所有执行限定 CPU8–9、线程不超过2，无 GPU/native/browser/service/Git。需要独立审查后才考虑正常源码应用及完整 wind/case strict 验证。

## Root 后续实际 native 限制（独立执行）

Root 已从结构候选完整 fresh 重放获得 raw bbd9daef…，正常安装 APP serial OFF/ON 及 ON np2 对同一个 owned tiny 真实 wall 输入完成 native 运行。实际 stock checkMesh 全仍 Failed 3：D14 / W8 / VR2；ON captured/staged/committed 为 0/0/0。原 source gate 的确已接受且 lineage active，无 source/budget 拒绝，仍不能推出网格质量已修复。原正常 APP OFF 输出还与旧 OFF 字节相等。

我已回读并按哈希保留 Root actual-native 原始 stdout/checkMesh 回执，而不是只引用口头摘要；路径和 26 份 primary 绑定见 evidence/root-genuine-structural-native-limitation.json。这不是 thermal 候选 native 验证：thermal 本身仍仅完成真实生产导出和 BC/字典校验。来源绑定只是必要修复，用户的真实全部 case strict mesh 与 5 分钟要求仍需其他 native 层修复及验收；不得写成 APP positive。

修正路径物化的 fresh 重放也已完成：repro-fresh/REPRO-summary.json 的 42 对 / 1040 检查 / 0 失败、raw 5d2d3dd4…、源码 1503bafe…全部与首次生产结果一致。所有新输出确实落 fresh 路径；正常依赖和原冻结基线仍原哈希。

实际 esbuild 依赖逐项映射另在 actual-frontend-provider-bindings.json：当前独立 baseline/thermal bundle 真正读入 66 个外部正常安装依赖；原 67 绑定集多出未被此 bundle 加载、但仍必须 held 的正常 cfdrun_build.js。两者分别记录，两个私有来源文件由 redirect/map/manifest 绑定，不误声称累计 normal file 已加载。
