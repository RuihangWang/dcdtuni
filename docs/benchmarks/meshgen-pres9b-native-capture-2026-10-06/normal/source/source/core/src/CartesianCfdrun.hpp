#pragma once
// CartesianCfdrun.hpp —— dcdtuni .cfdrun 清单摄入（T-CFDRUN）
//
// 目标：把 DC（dcdtuni）导出的 .cfdrun 清单翻译成 MeshGenerator 笛卡尔 cut-cell 引擎
// 的 CartesianConfig + 表面装载计划，使 MG 能直接吃 DC 算例并与 snappyHexMesh/dcCartMesh
// 的 patch 集合对齐。摄入是纯翻译层——不做物理、不改网格几何语义。
//
// 兼容两种清单形态（见 DC src/backend/aircooling/runner/cfdrun_schema.py）：
//   · V1（CFDExport 顶层）：room/racks/acus/baffles/walls/.../meshConfig 直接在顶层
//     （DC docs/manuscript/.../case_00_sketch.cfdrun 即此形态）；
//   · V2（CfdrunV2）：schemaVersion==2，几何 v1 blob 挂在 meshGeometry（过渡期 P3 T6），
//     boundaryBindings/porousBindings 在顶层。
// 两种都归一到内部「v1 blob」(geom) 上解析；V2 额外读 boundaryBindings 补 faceType。
//
// 翻译映射（任务书 §1）：
//   房间包围盒 → domain：优先 meshConfig.blockMeshBounds{min,max}；否则 room.plane+height
//     推 x/y/z 包围盒 ⇒ cfg.hasBox/boxMin/boxMax。
//   cellSize/级 → baseLevel/maxLevel：baseSize=背景胞尺寸；baseLevel=round(log2(rootEdge/
//     baseSize))；maxLevel=baseLevel+max(refinementLevel)（snappy 相对级语义，clamp≤20）。
//   regions → surfaceLevels/patchTypes：walls/baffles/racks/acus/floors/heatSources/
//     wallObjects/partitionWalls 逐条 ⇒ region 名 + 相对级 + 边界类型。
//   baffles/faceZones/cellZones 声明 → zone spec：baffles ⇒ cfg.baffles（BaffleSpec，
//     faceType 由 physics.baffleType 推：wall→"wall"、perforated→"cyclicOpening"；
//     V2 boundaryBindings.bcType 命中可覆盖）；cellZones（若清单显式声明）⇒ cfg.cellZones。
//
// 缺省安全：清单未声明的项一律不写（cfg 保持默认），六基线路径不受影响。

#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "meshgen/Cartesian.hpp"

namespace meshgen {

// 一个被声明的表面 region（供 CLI 装载 STL 并按 region 名归并到 TriSurface patch）。
struct CfdrunRegion {
    std::string name;       // region/patch 名（= 期望的 TriSurface patchName）
    std::string stlFile;    // 归属 STL 文件（相对清单/artifacts；可空）
    std::string solidName;  // STL 内 solid 名（可空 = 整个文件归该 region）
    std::string kind;       // "wall"|"baffle"|"rack"|"acu"|"floor"|"heatSource"|"wallObject"|"partitionWall"
    LevelSpec level{0, 0};  // 表面相对级（相对 baseLevel）
    std::string patchType;  // OpenFOAM 边界类型（空 = 缺省推断）
    bool isBaffle = false;  // 该 region 是否声明为 baffle
};

// 摄入产物：CartesianConfig（已填 domain/级/surfaceLevels/patchTypes/baffles/cellZones/
// keepPoints）+ 表面装载计划（regions/stlFiles）+ 告警。
struct CfdrunSpec {
    CartesianConfig cfg;
    std::vector<CfdrunRegion> regions;   // 全部声明 region（含 baffle）
    std::vector<std::string> stlFiles;   // 去重后的 STL 文件引用（相对路径，供 CLI 解析装载）
    std::vector<std::string> warnings;   // 翻译期告警（缺键回退、级 clamp 等）
    std::string name;                    // 工程/运行名（projectName/name）
    bool hasDomain = false;              // 是否成功解析出 domain 盒
    bool valid = false;                  // 解析成功（可继续装载/生成）
};

// 解析 .cfdrun JSON（已 parse 的 nlohmann::json）→ CfdrunSpec。
// 失败返回 false 并置 err（顶层非对象/缺 meshGeometry 等硬错误）；软问题记 warnings。
bool parseCfdrun(const nlohmann::json& j, CfdrunSpec& out, std::string& err);

// 便捷：从文件路径读取并解析（内部读文件 + json::parse + parseCfdrun）。
bool loadCfdrunFile(const std::string& path, CfdrunSpec& out, std::string& err);

// ============================================================================
// dc17 STEP 4 / 任务书 S2 —— RAW DC 算例摄入（--dc-raw，缺省 OFF）
// ============================================================================
// 背景：DC 生产清单（case/<name>/model/*.cfdrun，schemaVersion=2）的 geometryExport.solids
// 只覆盖一部分磁盘 solid（Base_leakage 49/61、CMI-China 146/216、hongchou 10517/11757），
// 且 golden boundary 里有些 patch 名（Pillar*/PDU*/Batt*/P1_*…）在任何清单键里都不出现——
// 它们只活在 merged_box.stl 等导出几何里。故 RAW 摄入必须 **磁盘扫描为主**（disk-scan-
// primary）：枚举 --surface-dir 下全部 *.stl 的每个 solid，再用 cfdrun 仅做 **分类**
// （role/level/patchType/baffle/exportKind）；清单未命中的 solid 回退命名约定启发式并被
// 如实报告，绝不静默丢弃（verdicts.json：report INFEASIBLE with arithmetic, never fail
// silently）。OFF 路径（parseCfdrun/CfdrunSpec/CfdrunRegion）逐字节不受影响。
//
// 角色分类（verdicts.json 法官嫁接的五类 + baffle 声明叠加）：
//   BARRIER_SHEET  墙/地板/吊顶/隔断/壁面 baffle/机架顶底板/服务器非风扇面 —— 洪泛屏障，
//                  exportKind=wallPair（声明 baffle 者=baffleMasterSlave）。
//   FRAME          机架框架（frameSolidNames）—— exportKind=plain。
//   SOLID_BOX      闭合箱体（merged_box、Pillar/PDU/Batt/UPS/CDU、ACU 箱体面）—— plain。
//   FLUID_VOLUME   through_zone —— 洪泛透明 **且** 不发射边界（exportKind=none）。
//   FLOW_MEMBRANE  风扇/服务器进出风面、ACU 送/回风面（发射 patch）；穿孔 baffle
//                  （气流穿过，floodBarrier=false，exportKind=cyclicPair）。
// baffle 不是第六个角色，而是叠加在 BARRIER_SHEET/FLOW_MEMBRANE 上的 **声明**
// （baffleZone/baffleType=wall|perforated），来源 meshGeometry.baffles + porousBindings +
// 文件名约定（merged_wall_baffle*/wallBaffle_*/perforated*/perforatedBlankingPanel*）。

// 磁盘扫描产物：triSurface 目录里每个 STL solid 的原始记录（未分类）。
// 由 scanTriSurfaceDir 填充；ASCII 多 solid 文件逐 "solid <name>" 块解析，单 solid 文件
// 保留其真实 solid 名（不归一为 "wall"——那是 loadSTL 的网格路径行为，分类不需要）。
struct DcRawSolidInput {
    std::string solidName;        // STL 内 "solid <name>"（空/缺名 → 文件茎+序号）
    std::string stlFile;          // 归属文件名（basename，含 .stl）
    long triCount = 0;            // 三角形数
    Vec3 bboxMin{0, 0, 0};        // 该 solid 顶点包围盒
    Vec3 bboxMax{0, 0, 0};
};

// 分类产物：每个磁盘 solid 的完整 region/role/level/patchType/baffle/exportKind 记录。
struct DcRawSolidRecord {
    std::string solidName;        // = DcRawSolidInput.solidName
    std::string stlFile;          // 归属文件
    std::string region;           // 归属 cfdrun region/对象名；无归属 = "STL_ONLY"
    std::string regionCategory;   // wall|floor|rack|acu|baffle|partitionWall|box|STL_ONLY|...
    std::string bindSource;       // 命中来源键（category:walls / rack:frameSolidNames /
                                  //   rack:server.inlet / acu:supplyFaces / boundaryBindings /
                                  //   porousBindings / geometryExport / prefix:<obj> /
                                  //   filename:<stem> / stlOnly）
    std::string role;             // BARRIER_SHEET|FRAME|SOLID_BOX|FLUID_VOLUME|FLOW_MEMBRANE
    LevelSpec level{0, 0};        // 相对级（相对 baseLevel）
    std::string levelSource;      // 级来源 meshConfig 键（refinementLevel/rackRegionLevel/...）
    std::string patchType;        // OpenFOAM 边界类型（wall/patch/cyclicOpening；空=不发射）
    std::string patchName;        // 导出 patch 基名（geometryExport.patchName 优先，否则 solidName）
    bool baffle = false;          // 是否声明为 baffle
    std::string baffleZone;       // baffle zone 名（master/slave 折叠后）
    std::string baffleType;       // wall|perforated（空=非 baffle）
    std::string exportKind;       // none|patch|wallPair|baffleMasterSlave|cyclicPair|plain
    bool floodBarrier = true;     // 洪泛是否被阻挡（FLUID_VOLUME/穿孔 baffle = false）
    bool explained = true;        // 是否被解释（false = unexplained，验收目标 0）
    long triCount = 0;
    Vec3 bboxMin{0, 0, 0}, bboxMax{0, 0, 0};
};

// refinementZone（meshGeometry.meshConfig.refinementZones）→ ROI 盒记录。
struct DcRawRefineZone {
    std::string name;
    int level = 0;
    Vec3 mn{0, 0, 0}, mx{0, 0, 0};
};

// ============================================================================
// dc17 修正案 E-A4 —— DC locationsInMesh 种子合成（dual-side createBaffles 种子）
// ============================================================================
// 移植 dcdtuni stlgen_runner.py 的种子合成语义（_collect_create_baffle_seed_specs /
// _build_patch_driven_none_seed_entries / _build_partition_wall_seed_entries /
// _build_partition_hole_cellzone_seed_entries / _nudge_seeds_off_grid_planes /
// _build_locations_in_mesh_entries）。验收 = 与 golden snappyHexMeshDict 的
// locationsInMesh 精确对齐（逐案见 docs/cartesian_dev/31_dc17_seed_parity.md）。
//
// 每条 createBaffles patch 产生一对「双侧 none 种子」：面片面积加权质心 ± 0.5·h_local·法向，
// h_local = meshConfig.baseSize / 2^max(baffleRefinementLevel)。射线净距 < offset 时
// offset 夹到 min_clearance·0.8（GTC/STDCT 各 1 例）。结构化模式的背景网格平面上落点
// 会被 nudge（0.25·local 朝更宽邻居）——golden 数据混合版本（见 parity 文档）。

// 一个 createBaffles 种子规格（= Python _PatchSeedSpec）。
struct DcSeedSpec {
    std::string zone;         // zone_name（已 sanitize 的 solid 名）
    std::string kind;         // patch_kind：floor_opening_*|floor_surface_*|rack_wallBaffle|
                              //   rack_door|baffle_model_perforated|baffle_model_wall
    std::string stlFile;      // 解析出的 STL 文件
    std::string solidName;    // 该文件内的 solid
    bool hasSeedCfg = false;  // 是否有 seedConfig 覆盖
    bool cfgAnchor = false, cfgDir = false, cfgOff = false;  // seedConfig 各字段是否存在
    Vec3 anchor{0, 0, 0};     // seedConfig.anchor（无则=质心）
    Vec3 direction{0, 0, 1};  // seedConfig.direction（无则=法向）
    double offset = 0;        // seedConfig.offset（无则=缺省 0.5·h_local）
};

// 一条合成/装配后的 locationsInMesh 种子。
struct DcSynthesizedSeed {
    Vec3 point;
    std::string zone;         // "none"（主/额外/patch/partition-wall）或 cellZone 名（partition-hole）
    std::string rule;         // primary|extra|patchDriven|partitionWall|partitionHole
    std::string sourcePatch;  // 来源 spec 的 zone/solid，或 "room"
};

// dc-raw 摄入总产物：域/级/keepPoints（复用 parseCfdrun）+ 逐 solid 分类表 + baffle zone +
// refinementZone + 统计 + 告警。writeDcRawMappingJson 把它落成机器可读 JSON sidecar。
struct DcRawMapping {
    CartesianConfig cfg;                       // domain/baseLevel/maxLevel/keepPoints/roi/...
    std::vector<DcRawSolidRecord> solids;      // 逐磁盘 solid 分类（确定序：文件名字典序+文件内序）
    std::vector<DcRawRefineZone> refineZones;  // refinementZones → ROI
    std::vector<std::string> baffleZones;      // 去重排序的 baffle zone 名
    std::vector<std::string> patchNames;       // 去重排序的最终 patch 名集（供 golden 交叉核对）
    std::vector<std::string> warnings;         // 摄入告警（[dc-raw] 前缀由 CLI 加）
    // ---- dc17 E-A4：种子合成产物 ----
    std::vector<DcSeedSpec> seedSpecs;             // 收集到的 createBaffles 种子规格（诊断）
    std::vector<DcSynthesizedSeed> seedsSynthesized;  // POST-nudge 装配后种子（当前 DC 语义，规范输出）
    std::vector<DcSynthesizedSeed> seedsPreNudge;     // PRE-nudge 几何核心（匹配 pre-fix golden）
    bool seedsNudged = false;                    // nudge 是否移动了任何种子
    double seedHLocal = 0;                       // h_local = baseSize/2^max(baffleRefinementLevel)
    double seedDefaultOffset = 0;                // 0.5·h_local
    int seedClamped = 0;                         // 射线净距夹取的 spec 数
    std::string meshMode;                        // 解析出的网格模式（structured/bodyFitted/...）
    // ---- 统计（报告/验收门）----
    long nSolidsOnDisk = 0;
    long nBoundManifest = 0;     // 命中清单某键（含 geometryExport/boundaryBindings/porousBindings/
                                 //   category/rack/acu 嵌套名/prefix 派生）
    long nStlOnlyClassified = 0; // 仅靠文件名/bbox 启发式分类（STL_ONLY）
    long nUnexplained = 0;       // 未能解释（验收目标 0）
    std::string name;            // 工程名
    std::string manifestPath;
    std::string surfaceDir;
    bool valid = false;
};

// 枚举 dir 下全部 *.stl（文件名升序），逐文件解析 ASCII "solid" 块 → 每 solid 一条
// DcRawSolidInput（名/三角数/包围盒）。确定性：文件名字典序 + 文件内 solid 出现序。
// 非 ASCII（二进制）STL 无法携带 solid 名 ⇒ 记 warning 并按文件茎造单条记录（不静默丢）。
bool scanTriSurfaceDir(const std::string& dir, std::vector<DcRawSolidInput>& out,
                       std::vector<std::string>& warnings);

// 纯分类（无磁盘 I/O）：manifest JSON + 已扫描 solid 列表 → DcRawMapping。
// 复用 parseCfdrun 取 domain/级/keepPoints，再磁盘扫描为主地逐 solid 绑定 region+role+
// level+patchType+baffle+exportKind。失败返回 false 并置 err。
bool classifyDcRaw(const nlohmann::json& manifest, const std::vector<DcRawSolidInput>& solids,
                   DcRawMapping& out, std::string& err);

// dc17 E-A4：种子合成（纯函数 + 按需读三角）。manifest（含 meshGeometry blob）+ 已扫描
// solid（带 bbox）+ surfaceDir（读 spec solid 三角求质心/法向 + 射线净距）→ 填充
// out.seedSpecs / out.seedsSynthesized（POST-nudge）/ out.seedsPreNudge / out.cfg.keepPoints
// （= declared room seed + extras + synthesized，按 DC 装配规则去重）。失败返回 false 置 err。
// 不修改任何磁盘文件（只读 STL）。
bool synthesizeDcSeeds(const nlohmann::json& manifest,
                       const std::vector<DcRawSolidInput>& solids, const std::string& surfaceDir,
                       DcRawMapping& out, std::string& err);

// 便捷：读清单文件 + 扫描 surfaceDir + classifyDcRaw + synthesizeDcSeeds。
bool ingestDcRaw(const std::string& cfdrunPath, const std::string& surfaceDir, DcRawMapping& out,
                 std::string& err);

// 把 DcRawMapping 写成机器可读 JSON sidecar（映射完备性报告）。
bool writeDcRawMappingJson(const DcRawMapping& m, const std::string& path, std::string& err);

// ============================================================================
// dc17 STEP 8 / 任务书 S3 —— dcMode 出口契约构建（ingest 分类 → 导出端选项）
// ============================================================================
// 把 DcRawMapping（逐磁盘 solid 的 role/patchType/exportKind/baffleZone）+ 清单的
// 房间包络 domain-face 映射（适配标记 "adapted":"domain-face:<side>"）翻译成
// writePolyMeshFoam 的 DcBoundaryOptions + 引擎 cfg 的命名钩子（dcFarFieldNames/
// dcPatchRename）+ 增强后的 baffles 声明。纯函数（无 I/O），供 CLI 与单测复用。
//
// 决策（31_dc17_export_log.md D1-D5）：
//   D1 远场：包络声明侧改名 golden 名（type wall），未映射侧保留 xMin..zMax（有面即
//      洪泛泄漏，CLI 响亮上报）；无包络声明 = 外流案，现行为不变。
//   D2 matchTolerance 恒 "0.0001"（Step-0 golden 普查 382×0.0001 vs 28×0.01；0.01 族
//      = DC 求解侧 fix_cyclic_match_tolerance 的追补产物，出口端不复制）；per-zone
//      覆盖 = 直接改 opts.cyclic[...].matchTolerance。
//   D3 zero-face 契约名 = baffle 对成员 + 包络名 + 磁盘 solid 出口名（exportKind≠none）；
//      仅 bindings 声明、无磁盘 solid 的名字【不发射】（适配代理案会产生上千幻影名；
//      RAW 案里 bindings 名皆有磁盘 solid ⇒ 已被覆盖），计数如实上报。
//   D4 FLUID_VOLUME（exportKind=none）→ suppressedPatches（出口端兜底；洪泛透明语义
//      由引擎 Step 3/5 落地）。
//   D5 baffle 分区以 OFF 声明（base.cfg.baffles）为基（保 G3 几何同构），zone/类型按
//      ingest 记录增强（baffleZone = FILE-STEM 键，ingest open item #4 的消费端）；
//      ingest 声明而 OFF 未声明者不新增分区（适配厚板做 master/slave 拆分几何上是错的），
//      如实上报。
struct DcExportContract {
    DcBoundaryOptions opts;       // 写出端选项（cyclic 元数据/zero-face/抑制/报告回填）
    std::map<std::string, std::string> patchTypes;  // 出口名 → OpenFOAM 类型（含 master/slave/包络）
    std::vector<std::string> farFieldNames;         // 六项（xMin..zMax 序）；空串 = 保留默认名
    std::vector<std::pair<std::string, std::string>> patchRename;  // 表面 patch 名 → 出口名
    std::vector<BaffleSpec> baffles;                // 增强后的 baffle 声明（region = 表面 patch 名）
    bool envelopeDeclared = false;                  // 房间包络已声明（domain-face 映射或 walls/floors BARRIER_SHEET）
    long nBindingsWithoutGeometry = 0;              // bindings 声明而无磁盘 solid 的名字数（D3 报告）
};

// 构建出口契约。manifest = 已解析的 .cfdrun JSON；map = ingestDcRaw/classifyDcRaw 产物；
// base = OFF 路径 CfdrunSpec（baffles 基）；surf = 实际装载的 TriSurface（patch 名核对）。
// 失败返回 false 并置 err（当前无硬失败模式，保留签名）。
bool buildDcExportContract(const nlohmann::json& manifest, const DcRawMapping& map,
                           const CfdrunSpec& base, const TriSurface& surf,
                           DcExportContract& out, std::string& err);

}  // namespace meshgen
