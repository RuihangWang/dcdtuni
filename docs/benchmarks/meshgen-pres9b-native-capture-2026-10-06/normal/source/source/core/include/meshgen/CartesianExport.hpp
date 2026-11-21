#pragma once
// CartesianExport.hpp —— cut-cell 多面体网格导出 + AMR 契约落盘
//
// 所有权：T2（导出+算例）。T1/T3/T4 只调用，不修改。
// 基线：T0 机械搬运自 core/src/Cartesian.cpp（writePolyMeshFoam/writePolyMeshVtu）。
// T2 扩展（SPEC §2.4，docs/cartesian_dev/03_SPEC.md）：
//   1. 五件套 header 补 location "constant/polyMesh" + OpenFOAM banner；
//   2. boundary 按引擎给的 patchNames 直写（新引擎远场拆为
//      xMin/xMax/yMin/yMax/zMin/zMax 六 patch + wall*），导出端不重命名，
//      仅断言同一 patch 的边界面构成单一连续段（否则 boundary 无效）；
//   3. AMR 三件套 cellLevel/pointLevel/level0Edge + constant/dynamicMeshDict，
//      格式模板严格照 docs/cartesian_dev/01_U3_amr_contract.md §5/§6.1 实测样例。
//   4. T-HIST（AMR v1.5，roadmap A.2）：refinementHistory 完整森林史落盘
//      （8 子齐全节点 `parent (i0..i7)`，不完整组各为根 `-1 0()`，
//      visibleCells[胞元]=条目下标；导出前 checkIndices 等价自检，失败拒写）。
//      v1 曾不写（官方 snappyHexMesh 同行为，U3 §2）；v1.5 起有持久树即写。
//
// PolyMesh / CartesianResult 完整定义在 meshgen/Cartesian.hpp；本头只前向声明，可独立包含。

#include <array>
#include <map>
#include <string>
#include <vector>

namespace meshgen {

struct PolyMesh;         // 定义见 meshgen/Cartesian.hpp
struct CartesianResult;  // 定义见 meshgen/Cartesian.hpp

// ---------------- dc17 Step 8 / 任务书 S3：DC 黄金 boundary 出口契约（dcMode） ----------------
// writePolyMeshFoam 的可选第五参（缺省 nullptr ⇒ OFF 路径逐字节不变，六基线红线）。
// dcMode=true 时 boundary 按 DC/snappy golden 版式发射（值列对齐第 24 列——key 字段宽 16，
// 与 dcdtuni case/*/Mesh/constant/polyMesh/boundary 实测逐字节同；DC 求解侧
// openfoam_case.py:37 fix_cyclic_match_tolerance 以字面 'type            cyclic;' 匹配，
// 12 空格对齐是硬契约），并附加：
//   · wall 条目带 inGroups 1(wall)；cyclic 条目带 inGroups 2(cyclic <zone>) +
//     matchTolerance + transform coincidentFullMatch + neighbourPatch（golden 字段序：
//     type/inGroups/nFaces/startFace/matchTolerance/transform/neighbourPatch）；
//     patch 类型无 inGroups（golden 实证，含 zero-face 条目）。
//   · zeroFacePatches：声明但无面的契约名按给定序附在真实段之后（nFaces 0，
//     startFace = 边界面总数）。fields.py 严格门要求注册名在场（任务书 S3）；
//     已有面的名字忽略。
//   · suppressedPatches：FLUID_VOLUME（exportKind=none）——这些 patch 的边界面不写入
//     faces/owner/boundary（洪泛透明语义在引擎 Step 3/5 落地前的出口端兜底），计数入
//     report。空表 = 无抑制（适配案无此类）。
//   · faceZones：m.faceZone/m.faceZoneNames 非空时写 <dir>/faceZones（OpenFOAM v2412
//     ASCII zoneMesh 格式 = golden 结构：meta names 头 + `(zone { type faceZone;
//     faceLabels List<label> …; flipMap List<bool> …; } … )`；flipMap：属 <zone>_slave
//     patch 的面 = 1（createBaffles 语义），其余 = 0；faceLabels 升序）。
//   · 重复/不连续段拒出保持武装，dcMode 下先把全部重名段以 [dc-export] error 行
//     响亮列到 stderr（跨文件同名 solid = 战役级 bug，如实上报不粉饰）。
struct DcBoundaryOptions {
    bool dcMode = false;                 // 总开关（false ⇒ 本结构其余字段全部无效）
    // cyclic 对全元数据：patch 名 → {neighbourPatch, zone（inGroups）, matchTolerance 串}。
    // matchTolerance 缺省 "0.0001"（snappy golden 值）；per-zone 覆盖钩子 = 直接改串
    //（DC 求解侧对 0.0001 的自愈见 openfoam_case.py:37，出口端不复制其 0.01 追补）。
    struct CyclicPair {
        std::string neighbour;
        std::string zone;
        std::string matchTolerance = "0.0001";
    };
    std::map<std::string, CyclicPair> cyclic;
    std::vector<std::string> zeroFacePatches;    // 声明名（确定序）；无面者按序补 zero-face 条目
    std::vector<std::string> suppressedPatches;  // FLUID_VOLUME 出口名：其边界面不写（报告计数）
    bool writeFaceZones = true;                  // dcMode 且 m.faceZone 非空时写 faceZones
    std::vector<std::string> report;             // 写出端回填 [dc-export] 报告行（CLI 打印）
};

// 写 OpenFOAM constant/polyMesh 五件套（多面体）。
// header 含 location "constant/polyMesh" 与 OpenFOAM banner（SPEC §2.4）。
// boundary：type 推断——名字以 "wall" 开头 → wall，其余 → patch；
// 断言每个 patch 的边界面连续排列（重复段会生成重名 patch 的非法 boundary，
// 违反即返回 false 并报 err）。
// T-PT（可选）：patchTypes = patch 名 → OpenFOAM 类型（wall/patch/symmetry/empty/
// cyclic/...）的导出覆盖映射；命中即按配置写 type，未命中保持上述缺省推断。
// 缺省空表 ⇒ 行为与历史版本逐字节一致。
// dc17 Step 8（可选）：dc = DC 黄金 boundary 契约（见 DcBoundaryOptions）；缺省
// nullptr ⇒ 与历史版本逐字节一致（OFF 路径红线）。
bool writePolyMeshFoam(const PolyMesh& m, const std::string& dir, std::string& err,
                       const std::map<std::string, std::string>& patchTypes = {},
                       DcBoundaryOptions* dc = nullptr);

// 写 VTK XML 非结构网格 .vtu（VTK_POLYHEDRON=42，legacy 5 数组布局）。
// 可被 ParaView 拖拽直接打开、被 pyvista 读取；多面体逐面外法向绕向。
bool writePolyMeshVtu(const PolyMesh& m, const std::string& path, std::string& err);

// 写 AMR 输出契约（SPEC §2.4 / 01_U3 §6.1），全部落 constant/polyMesh/：
//   cellLevel   labelList                          —— 叶绝对八叉树级（根=0）
//   pointLevel  labelList                          —— max{cellLevel(c): p∈c}（hexRef8 加点规则）
//   level0Edge  uniformDimensionedScalarField      —— 显式写根边长 H，dimensions [0 1 0 0 0 0 0]；
//       v2412 关键字形式 `dimensions ...; value ...;`（readData 按 dictionary 解析强制要求）
// 另写 constant/dynamicMeshDict（dynamicRefineFvMeshCoeffs 模板）到 polyMeshDir 的父目录
// （01_U3 §5 实测：文件必须在 constant/，不是 system/）。
// 预检（失败即返回 false 并报 err，绝不静默猜测/自动推算）：
//   res.cellLevel.size()==单元数 且非空；res.pointLevel.size()==点数；
//   res.level0Edge>0（禁止自动推算，hexRef8.C:2784 教训）；
//   内部面两侧 |cellLevel 差|<=1（2:1 硬约束，checkRefinementLevels，hexRef8.C:4901-4919）。
bool writeCartesianAmrFiles(const CartesianResult& res, const std::string& polyMeshDir,
                            std::string& err);

// 单独写 dynamicMeshDict 模板（SPEC §2.4 系数）；供 writeCartesianAmrFiles 与集成者复用。
bool writeDynamicMeshDict(const std::string& path, std::string& err);

// ---------------- refinementHistory 完整森林史（T-HIST，AMR v1.5，roadmap A.2） ----------------
// 语义（01_U3 §4 方案 C + 03_SPEC §2.4）：
//   · splitCells 条目 = 八叉树节点的子集，拓扑序编号（父先于子）：
//       - 叶节点有条目 ⟺ 该叶是"洁净可见胞元"（终网格胞元整体含于该叶盒内，
//         导出端几何验证）；
//       - 内部节点有条目 ⟺ 其 8 个子节点全有条目（等价：整棵子树全为洁净可见叶）；
//         条目形如 `parent (i0..i7)`（8 子下标按 Morton 八分序）；
//       - 不完整 8 兄弟组不建父节点，其可见成员各自为根 `-1 0()`；
//       - 跨叶合并胞（S9 吸收邻居，几何上跨多个叶）不是任何单叶的"洁净"代表：
//         各分得一个合成根条目 `-1 0()`（运行时加密仍可用——hexRef8::storeSplit
//         对 visibleCells!=-1 的胞元直接挂新根；粗化本就不允许，合并胞受
//         dynamicRefineFvMesh protectedCell_ 保护）。
//   · visibleCells 每现存胞元一条（尺寸==单元数，hexRef8.C:1980 硬约束）：
//     洁净胞元 = 其叶条目下标；合并胞 = 其合成根条目下标。
//   · 支持 v2412 hexRef8 读回（checkIndices + active 重建）、运行时粗化合并回
//     生成器层级（完整 8 兄弟组共享 parentIndex，hexRef8.C:5215-5262）与
//     refinementHistoryConstraint 并行分解。
// 胞元→叶映射在导出端由终网格几何重建（不改引擎）：胞元顶点均值定位叶 +
// "全部顶点含于该叶盒"验证（对纯六面/切割胞恒真）；验证失败者判为合并胞，
// 改用"最大面内点沿内法向内移微量"的必内点定位其跨叶之一。自检失败即拒写。
// 依赖：res.tree 非空（generateCartesian/refineCartesian 成功后成立）。

// 森林史内存表示（供写盘与测试往返恒等复用）
struct RefinementHistoryData {
    std::vector<int> parent;                    // 每条目：父条目下标（-1 = 根）
    std::vector<std::array<int, 8>> children;   // 每条目：8 子条目下标；无子 = 全 -1
    std::vector<int> visibleCells;              // 每胞元：条目下标（本实现恒 >= 0）
    std::vector<long long> entryNode;           // 每条目 → 八叉树节点号（合成根 = -1）
};

// 构建森林史（不落盘）。失败返回 false 并报 err（含等价 checkIndices 自检）。
// res.tree 为空 / 网格为空 / 几何映射不自洽（非单射、级不符、父链不闭合）均拒绝。
bool buildRefinementHistory(const CartesianResult& res, RefinementHistoryData& out,
                            std::string& err);

// 写 constant/polyMesh/refinementHistory（OpenFOAM v2412 格式，01_U3 §3）：
//   // splitCells  <N> ( 每条 `parent (i0..i7)` 或 `-1 0()` )
//   // visibleCells <nCells> ( 每行一个条目下标 )
// 内部先跑 buildRefinementHistory 全部自检，失败即不写文件并报错。
// res.tree 为空时返回 false（调用方 writeCartesianAmrFiles 在无树时跳过本文件，
// 保持 v1 兼容行为）。
bool writeRefinementHistory(const CartesianResult& res, const std::string& polyMeshDir,
                            std::string& err);

}  // namespace meshgen
