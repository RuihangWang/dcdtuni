#pragma once
#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "meshgen/Vec.hpp"
#include "meshgen/CartesianExport.hpp"  // 导出函数（writePolyMeshFoam/Vtu）；T0 机械拆分，T2 拥有

namespace meshgen {

// ---------------- 三角表面几何 ----------------
struct TriSurface {
    std::vector<Vec3> verts;
    std::vector<std::array<int, 3>> tris;
    std::vector<int> patch;             // 每个三角形所属 patch id
    std::vector<std::string> patchNames;
    std::vector<double> firstLayer;     // 每个 patch 的第一层高度（<=0 = 不特殊加密）
                                        // 弃用兼容（SPEC §2.3）：新管线换算相对级
                                        //   rel = clamp(ceil(log2(H/h)) − baseLevel, 0, maxLevel − baseLevel)
                                        // 与 surfaceLevels 并存时 surfaceLevels 优先；T3 已给出告警。

    bool loadSTL(const std::string& path, std::string& err);
    bool loadJSON(const std::string& path, std::string& err);
    // 装载后校验（防御纵深）：全部三角形索引 0<=idx<verts.size()、
    // patch.size()==tris.size()、patch 值在 patchNames 范围内。违规返回 false 并给出定位信息。
    bool validate(std::string& err) const;
    Vec3 bboxMin() const;
    Vec3 bboxMax() const;
    int patchId(const std::string& name);  // 不存在则创建
};

// ---------------- 通用多面体 polyMesh（cut-cell 输出） ----------------
struct PolyMesh {
    std::vector<Vec3> points;
    std::vector<std::vector<int>> faces;  // 每个面的有序顶点
    std::vector<int> owner, neighbour;    // neighbour=-1 为边界面
    std::vector<int> facePatch;           // 边界面 patch id；内部面 -1
    std::vector<std::string> patchNames;
    int nInternal = 0;
    // ---- T-CFDRUN：zone 载体（可选；缺省全空 ⇒ 五件套/AMR 落盘逐字节不变）----
    // faceZone：与 faces 平行——baffle 切割面所属 zone id（master/slave 成对面共享
    //   同一 zone id）；非 baffle 面与内部面 = -1。空向量 = 无 baffle（历史行为）。
    //   仅在装配期声明了 baffle 时填充；导出端据此可选写 constant/polyMesh/faceZones。
    // cellZone：与单元平行（下标 = 终网格胞号）——cellZone id（-1 = 不属任何 zone）。
    //   空向量 = 无 cellZone（历史行为）。导出端据此写 constant/polyMesh/cellZones。
    std::vector<int> faceZone;
    std::vector<std::string> faceZoneNames;
    std::vector<int> cellZone;
    std::vector<std::string> cellZoneNames;
    long nCells() const { return owner.empty() ? 0 : (long)owner.size() ? maxCell() + 1 : 0; }
    long maxCell() const {
        long m = 0;
        for (int o : owner) m = std::max<long>(m, o);
        for (int n : neighbour) m = std::max<long>(m, n);
        return m;
    }
};

struct RoiBox {
    Vec3 mn, mx;      // 区域包围盒
    double size = 0;  // 目标单元尺寸（<=0 用 maxLevel）
};

// ---------------- 运行时 AMR 区域（T-AMR） ----------------
// refineCartesian/coarsenCartesian 的目标盒：与该盒相交的叶参与加密/粗化。
struct AmrBox {
    Vec3 mn, mx;     // 区域包围盒
    int levels = 1;  // 加密级数（v1：每次调用迭代 levels 轮，每轮升 1 级 + 2:1 闭包）
};

// ---------------- 层级控制（SPEC §2.1 冻结） ----------------
// snappy 相对层级区间 (min, max)，相对 baseLevel；绝对级 = baseLevel + 相对级。
// 写死语义见 docs/cartesian_dev/03_SPEC.md §2.3。
struct LevelSpec {
    int lo, hi;
};

// ---------------- baffle / cellZone 声明（T-CFDRUN；SPEC 见 docs/cfdrun_ingest.md） ----------------
// BaffleSpec：把一个表面 region（= TriSurface patchName）声明为 baffle。装配期该 region
//   的切割面（薄板两侧各一张边界面）按 createBaffles 语义拆成成对 boundary patch：
//   master = name，slave = name + "_slave"（绕向法向沿 baffle 主轴正向者为 master）。
//   faceType = 两个 patch 的 OpenFOAM 边界类型（空 = 缺省推断，通常 "wall"；cfdrun 可
//   指定 "cyclic"/"cyclicOpening"/"wall" 等）。region 未匹配任何表面 patch ⇒ 该声明无效
//   （装配期静默跳过，导出 patch 集不含之）。
struct BaffleSpec {
    std::string region;    // 表面 region 名（匹配 TriSurface::patchNames）
    std::string name;      // 导出 patch 基名（master=name，slave=name_slave）
    std::string faceType;  // OpenFOAM 边界类型（空 = 缺省推断）
};

// CellZoneSpec：把一个轴对齐盒内（胞心 containment）的终网格胞元声明为 cellZone。
//   导出期写 constant/polyMesh/cellZones（OpenFOAM cellZoneSet 列表格式）。
struct CellZoneSpec {
    std::string name;      // cellZone 名
    Vec3 mn, mx;           // 包围盒（胞心落入 [mn,mx] 即归属；多 zone 命中取先声明者）
};

struct CartesianConfig {
    // ---- 新 API（SPEC §2.1，T0 冻结） ----
    int baseLevel = 3;      // 背景八叉树基础级：均匀预分裂 baseLevel 次 ≡ blockMesh（根=0 级）
    int maxLevel = 6;       // 最大绝对级；snappy 语义：绝对级 = baseLevel + snappy 相对级
    double domainPad = 0.2; // 表面包围盒外扩（相对最大边长）
    bool hasBox = false;    // 各向异性背景域（可选扩展）：为 true 时用 boxMin/boxMax 取代 bbox+pad
    Vec3 boxMin, boxMax;
    int nCellsBetweenLevels = 3;          // snappy：层级间过渡单元数（S5 锚定波 n）
    long long maxGlobalCells = 2000000;   // snappy：全局单元预算（S3/S5 候选截断）
    int minRefineCells = 10;              // snappy minRefinementCells：单轮加密最小候选数
    std::vector<Vec3> keepPoints;         // locationInMesh/outsidePoints：S7 洪泛种子（保留其所在 region）
    std::vector<std::pair<std::string, LevelSpec>> surfaceLevels;  // 按 region/patch 名的表面层级
    double mergeVolFrac = 1e-3;           // S9 小单元判据：V < mergeVolFrac·edge³（与最小面宽阈值并用）
    bool qualityGateWarn = false;      // warn 档：S10 错向/质心越界(kind2/3)降为 stderr 告警继续导出；kind1 开放胞恒拒出。默认 strict 零容忍不变；仅用于与容忍型网格器对照/速度基准
    int mergeMaxPasses = 20;              // S9 小单元合并最大轮数

    // ---- T-PT：boundary 边界面 type 覆盖映射（可选） ----
    // patch 名 → OpenFOAM 边界类型（如 "yMin"→"symmetry"）；导出 boundary 时命中即按
    // 配置写 type，未命中保持缺省推断（wall 前缀→wall；front/back→empty；其余→patch）。
    // 空表 = 历史行为，六基线缺省路径逐字节不变。仅影响导出，不影响网格几何/装配。
    std::vector<std::pair<std::string, std::string>> patchTypes;

    // ---- T-2D：2D 模式（单胞厚 + empty 补丁；roadmap C.2）----
    // 纯 3D 引擎跑 2D 算例的专项模式（语义见 docs/cartesian_dev/05_roadmap.md C.2）：
    //   · 域：x/y 按表面 bbox+domainPad（与 3D 同）；z 向厚度 = 1 个基础胞
    //     （H·2^-baseLevel），表面棱柱必须恰好张满该 slab（脚本侧保证）；
    //   · 加密：maxLevel 仅作用于 x/y——八叉树照常分裂（S3-S6 不改分裂规则），
    //     但 slab 上下边界视为虚拟域壁（S5 波/S7 洪泛不穿越），装配期把同一
    //     (ix,iy,level) z 柱的全部叶合并为单个贯穿 slab 的棱柱胞 ⇒ 终网格严格单胞厚；
    //   · 导出：front（z-）/back（z+）两个边界面补丁，类型 empty（writePolyMeshFoam）；
    //   · 限制：v1 仅支持外流（流体在表面外侧）；表面须为张满 slab 的闭合棱柱
    //     （上下盖与 slab 面共面者按域壁处理，不参与裁剪/壁面发射）。
    //   运行时 refineCartesian 对 mode2d 按整柱标记（保持柱一致性），可正常加密。
    bool mode2d = false;

    // ---- T-CFDRUN：baffle / cellZone 声明（可选；缺省全空 ⇒ 六基线逐字节不变）----
    // 仅当非空时装配/导出走 baffle/zone 分支；空表 = 历史行为（纯 cut-cell wall/远场）。
    // 由 meshgen-cli cfdrun 子命令从 .cfdrun 清单摄入，或 cartesian JSON 的
    // meshGenerator.baffles / meshGenerator.cellZones 新键提供（见 CartesianCfdrun）。
    std::vector<BaffleSpec> baffles;
    std::vector<CellZoneSpec> cellZones;

    // ---- dc17 Step 8 / 任务书 S3：DC 黄金 boundary 出口契约（缺省空 ⇒ 逐字节不变）----
    // 合并去重注记（impl log 合并节）：总开关 bool dcMode 只在【结构体末尾】声明一份
    // （引擎 Step 1 位置，语义 = 引擎洪泛/S7 报告 ∪ 出口契约的并集）。本块 = 出口层
    // 专属钩子，dcMode=true 时出口层只改【边界命名/元数据】，不改几何语义：
    //   (1) VERBATIM 命名：表面 patch 名逐字发射（跳过 wall_ 前缀，Cartesian.cpp 的
    //       两个生产 nameOf 与 assembleBoundaryBaffles::rawName 三处同一开关）；
    //       dcPatchRename 命中者按映射改名（geometryExport.patchName / baffle zone 折叠），
    //       未命中原名。多个表面 patch 映到同名 = 有意合并（merged_* 文件级 zone 语义），
    //       首现序 out-id 稳定排序保证面段仍连续。
    //   (2) baffle 对命名：<zone>_master / <zone>_slave（OFF：<name>/<name>_slave）；
    //       faceZoneNames 按 zone 名去重（多个 BaffleSpec 可共享一个 zone——ingest
    //       open item #4 的 FILE-STEM 键消费端）。
    //   (3) 远场六侧：dcFarFieldNames 非空项把 xMin..zMax 改名为该名字（房间包络
    //       domain-face 映射 ⇒ Room_Wall_*/Room_Floor 等 golden 兼容名）；空项保留
    //       默认名。命名是双射且引擎几何不变时 faces/owner/neighbour 与 OFF 逐字节一致
    //       （G3 门；合并 Step 3a 洪泛后 ON 路径面集改变 ⇒ G3 按 post-flood 重锚，
    //       见 31_dc17_export_log.md §4.3 第 1 条与 impl log 合并节）。
    // mode2d 与 dcMode 组合未定义（front/back 优先，dcFarFieldNames 不作用于 side 4/5）。
    std::vector<std::string> dcFarFieldNames;  // 六项（xMin,xMax,yMin,yMax,zMin,zMax 序）；空串=保留默认名
    std::vector<std::pair<std::string, std::string>> dcPatchRename;  // 表面 patch 名 → DC 出口名

    // ---- 遗留字段（旧式配置解析仍写入；新引擎语义如下） ----
    bool balance2to1 = true;  // 新引擎忽略：2:1 平衡为 SPEC S6 强制（worklist 不动点+硬断言），无开关
    int buffer = 3;           // 新引擎忽略：过渡带由 nCellsBetweenLevels 驱动（SPEC S5 锚定波）
    std::vector<RoiBox> roi;  // ROI 区域加密（SPEC S4 保留该字段，size→targetLevel）

    // ---- dc17：seeded-flood DC 模式（31_dc17_design/approach1.json；Step 1 脚手架）----
    // false（缺省）= 历史行为，四套锚件（六基线/四适配案/TriPart 签名/ctest）逐字节不变，
    // 契约同 qualityGateWarn(:114)/baffles/cellZones(:136-141) 的"空/false=历史行为"。
    // true = dcdtuni 数据中心原始几何模式：开放壳（零厚板/非流形机架框）不再被 S7 水密
    // 守卫拒出（改为 [DC-S7] 报告）；后续步骤依次接入 barrier 洪泛（Step 3）、cfdrun 真
    // 映射（Step 4）、两侧流体胞构造 + 重合面对 + shellSplit（Step 5-7）、导出 parity（Step 8）。
    // 放在结构体末尾：既有成员偏移全不变（Step 1 的 flag-OFF .o 漂移面最小化实验位）。
    bool dcMode = false;

    // ---- dc17 Step 3a：side-node 洪泛的角色/报告输入（全部"空=历史行为"契约）----
    // dcPatchBarrier：按 TriSurface patch id 的洪泛屏障位（1=barrier 0=透明）。来源 =
    //   DcRawSolidRecord.floodBarrier（through_zone/穿孔 baffle = 0，delivery 签核 A2）。
    //   空 = dcMode 下全部三角视为 barrier（夹具口径）；非空时 size 必须 ==
    //   surf.patchNames.size()（generateCartesian 入口 fail-fast）。不参与任何距离判定。
    std::vector<uint8_t> dcPatchBarrier;
    // dcRoomBBox*：房间壳 bbox——E-A7 报告的 kept 体积内/外拆分与解析 pad 体积；nodeClass/
    //   泄漏根诊断口径。dc17 Step 5a-iii（E1c）起【在 kept-root 门控内】参与 mode-3 组件
    //   劈分：bbox 面平面只做围护平面的【选择】（劈分平面值 = 支撑 barrier 板三角自身
    //   坐标，clipTol 内共面核验）；门控（kept 泄漏根内 chord 类桥记录）保证 GO/clean 案
    //   结构性不可达（scout §9.3 裁决）。非 K3-2 距离常数（声明包络，非可调 ε）。
    //   dcHasRoomBBox=false ⇒ 报告降级为域 bbox 口径，且 mode-3 劈分整体关断。
    Vec3 dcRoomBBoxMin, dcRoomBBoxMax;
    bool dcHasRoomBBox = false;
    // dcProbePoints：报告用探针点（E-A7 种子解析链同款：逐点报 leaf/kept/sideMask/区域）。
    //   夹具（F11 盒内部/F14 plenum/F15 框架内部）与战役脚本（BL ACU/plenum 归因）消费；
    //   不参与任何谓词。
    std::vector<Vec3> dcProbePoints;
};

// ---------------- 持久八叉树网格（AMR 载体，SPEC §2.1；T1 实现） ----------------
// 布局：Node{key:Morton; parent:i32; child[8]:i32; level:u8; flags}，
// 节点号 = splitCells 拓扑序（父先于 8 子，8 子连续——原引擎既有布局）；
// 叶表恒按规范（canonical）Morton 键有序（排序数组+二分，废除全部 std::map）；
// 几何由 (key, level) 解码（整数格点 → double 仅一次），废除浮点 c/h 存储。
// 规范键：层级 l 的整数坐标 (ix,iy,iz) 交错成 3l 位 Morton 码，再左对齐到
// kMaxLevel（3×21=63 位），叶覆盖区间 [key, key + 2^(3(kMaxLevel−l)))。
struct CartesianMesh {
    static constexpr int kMaxLevel = 20;         // 3×21 位 Morton，规范键 ≤ 63 位
    static constexpr uint8_t kFlagAnchor = 1;    // S5 冻结表面交面（分裂时 8 子继承）
    static constexpr uint8_t kFlagProtected = 2;  // AMR 保护（cut/合并胞；导出侧用）

    struct Node {
        uint64_t key = 0;  // 规范 Morton 键（层级位已左对齐到 kMaxLevel）
        int parent = -1;
        int child[8] = {-1, -1, -1, -1, -1, -1, -1, -1};
        uint8_t level = 0;
        uint8_t flags = 0;
        bool leaf() const { return child[0] < 0; }
    };

    // ---- T0 冻结 API ----
    long long nLeaves() const;                                   // 叶单元数
    int leafLevel(long long leaf) const;                         // 叶的绝对八叉树级
    void leafBox(long long leaf, Vec3& mn, Vec3& mx) const;      // 叶的轴对齐包围盒
    void refineBatch(const std::vector<long long>& leaves);      // 串行批量分裂（候选按 Morton 序）
    long long leafOfPoint(const Vec3& p) const;                  // 点所在叶（叶表下标；域外=-1）
    // PERF：带提示版 leafOfPoint——从 hint 叶下标起指数扩窗后二分，结果与 leafOfPoint
    // 逐位一致（同一 upper_bound 语义）；邻居叶键在 Morton 序上通常紧邻本叶 ⇒
    // faceNeighbors 热路径 O(log 距离) 替代 O(log N) 全程二分。
    long long leafOfPointHinted(const Vec3& p, long long hint) const;

    // ---- T1 定稿扩展（SPEC §2.1 授权；供引擎、测试与运行时 AMR 使用） ----
    void init(const Vec3& origin, double rootEdge, int baseLevel);  // 根立方体 + 均匀预分裂
    long long nNodes() const { return (long long)nodes_.size(); }
    int leafNode(long long leaf) const { return leaves_[(size_t)leaf]; }
    uint64_t leafKey(long long leaf) const { return leafKeys_[(size_t)leaf]; }
    const Vec3& origin() const { return origin_; }
    double rootEdge() const { return rootEdge_; }
    // 跨面最大级差（2:1 校验；=0/1 合法）。O(L·log L)。
    int checkBalance() const;
    // S5 锚点标记（分裂时自动由父继承到 8 子——"距离 0 几何转移至子面，不重新采锚"）
    void setLeafAnchor(long long leaf, bool on);
    bool leafAnchor(long long leaf) const;
    // 面邻居枚举（dir: 0..5 = x-,x+,y-,y+,z-,z+；返回叶表下标，可能 0/1/2/4 个）
    void faceNeighbors(long long leaf, int dir, std::vector<long long>& out) const;

    // ---- T-HIST（AMR v1.5）：持久森林史的只读访问器（仅新增，不改既有签名）----
    // 导出端（CartesianExport::writeRefinementHistory）据此重建 refinementHistory
    // 森林：节点号 = splitCells 拓扑序（父先于 8 子；8 子按 Morton 八分序 0..7
    // 连续）。叶→节点映射用既有 leafNode(leaf)；叶下标→条目下标的重建在导出端。
    long long nodeCount() const { return (long long)nodes_.size(); }
    int nodeParent(long long node) const { return nodes_[(size_t)node].parent; }
    int nodeLevel(long long node) const { return (int)nodes_[(size_t)node].level; }
    int nodeChild(long long node, int octant) const { return nodes_[(size_t)node].child[octant]; }
    bool nodeIsLeaf(long long node) const { return nodes_[(size_t)node].leaf(); }
    uint64_t nodeKey(long long node) const { return nodes_[(size_t)node].key; }

    // 数据（公开以供导出/调试检视；调用方不得破坏排序不变量）
    Vec3 origin_{};                 // 根立方体最小角
    double rootEdge_ = 1.0;         // 根边长 H（=level0Edge）
    std::vector<Node> nodes_;       // splitCells 拓扑序
    std::vector<int> leaves_;       // 叶节点号，恒按规范 Morton 键升序
    std::vector<uint64_t> leafKeys_;  // 与 leaves_ 平行的规范键（二分查找用）
};

// ---------------- 生成结果 ----------------
struct CartesianResult {
    PolyMesh mesh;
    // AMR 契约字段（SPEC §2.1/§2.4）：T1 生成时填充，T2 导出 cellLevel/pointLevel/level0Edge
    std::vector<int> cellLevel;   // 每单元一条：叶绝对八叉树级（根=0）
    std::vector<int> pointLevel;  // 每点一条：max{cellLevel(c): p∈c}（hexRef8 加点规则）
    double level0Edge = 0;        // 根单元边长 H（显式落盘，禁止运行时自动推算）
    // 现有统计字段（旧引擎使用，保持兼容）
    int nLeaves = 0, nCut = 0, nFluid = 0;
    double minFluidVol = 0;
    double genMs = 0;

    // ---- dc17 Step 2：dcMode 诊断报告（[DC-S7]）----
    // dcMode=true 时 S7 装入壳拓扑/种子决策/kept 摘要的 JSON 明细；后续阶段即使拒出
    // （如 S10 kind1）也保留——失败诊断是 dc17 kill-criteria 的证据链。空串 = 历史行为
    // （flag-OFF 从不装入，六基线/四适配案逐字节不变）。CLI 据此落
    // <exportDir>/../dc_report.json（成功与失败路径都落）。
    std::string dcReportJson;

    // ---- dc17 Step 3a：side-node 洪泛产物（E-A1 sideMask = Step 5 单一真相源）----
    // 仅 dcMode 填充；空 = 历史行为（flag-OFF 从不填充）。逐叶（叶表下标）：
    //   dcSideCount[L] = L 的 side-node 数（0 = 透明叶：无 barrier 命中，整叶单节点）；
    //   dcSideMask[L]  = 保留侧位掩码（bit s = side s 被种子区域覆盖；>64 侧截断全 1
    //                    + census 计数，FRAME_DENSE 类）；
    //   dcKeptLeaf[L]  = OR(sides kept)（透明叶 = 节点 kept）——kept 装配同源。
    std::vector<int> dcSideCount;
    std::vector<uint64_t> dcSideMask;
    std::vector<uint8_t> dcKeptLeaf;

    // ---- 运行时 AMR 持久句柄（T-AMR） ----
    // generateCartesian 成功后填充：tree = 生成末态八叉树（原即丢弃，现持久），
    // ctx = 重新装配所需的最小生成上下文。旧调用方可完全忽略（shared_ptr 自动释放）；
    // refineCartesian 要求两者非空。失败或生成前均为 nullptr。
    struct Context {
        TriSurface surf;       // 吸附后的表面副本（与生成时切割几何位一致，勿改）
        CartesianConfig cfg;   // 配置快照（maxGlobalCells 预算、merge 参数等）
        Vec3 origin{};         // 根立方体最小角（S2）
        double rootEdge = 0;   // 根边长 H（= level0Edge）
        int baseLevel = 0;
        int maxLevel = 0;      // 运行时加密的级上限（与生成一致）
        int bandMaxGen = 0;    // 生成末态表面带（anchor 叶）最高级；运行时 refine 的
                               // 表面带闭包目标（填孔不抬带，见 refineCartesian 注释）；
                               // 0 = 无表面带（表面未加密）
        bool valid = false;
    };
    std::shared_ptr<CartesianMesh> tree;
    std::shared_ptr<Context> ctx;
};

// 生成笛卡尔八叉树 cut-cell 网格。签名冻结（cli/tests/gui 现有调用方不变）。
// 语义：纯 cut-cell + snappy 式层级控制 + AMR 就绪（SPEC §2.2 流水线 S0-S11），
// T1 原地重写实现（本文件同目录 Cartesian.cpp）。
bool generateCartesian(const TriSurface& surf, const CartesianConfig& cfg,
                       CartesianResult& out, std::string& err);

// ---------------- 运行时 AMR（T-AMR） ----------------
// 加密：res.tree 中与 region 相交的叶升 region.levels 级（迭代执行，每轮
//   "盒内叶 → refineBatch（内含局部 2:1 闭包）"）。若盒区命中表面交面带（anchor
//   叶），随后做【表面带闭包】：低于生成末态带内最高级（ctx.bandMaxGen）的 anchor
//   叶补升至该级（填平既有高层带中的低层孔洞；否则带内级差悬崖会使 S9 同级合并
//   在悬崖粗侧失去伙伴，级联出 S10 拒出的非凸合并壳）。随后全局 2:1 校验（同 S6
//   断言），再从当前树【全量重新装配】
//   S7-S11（与生成路径同一实现、同一硬断言：S8 界面配对、S10 闭合 ΣSf≈0 与面锥、
//   跨面级差≤1），刷新 mesh/cellLevel/pointLevel/统计。
//   预算：遵守 ctx.cfg.maxGlobalCells（盒区加密与表面带闭包各自守卫，超限回滚）。
//   失败返回 false 并报 err，res 完全回滚不变（树先拷贝、失败复原）。
//   v1 时间盒约定：性能不作要求（全量重建）；树级上限 = 生成时 maxLevel。
// 前置：res.tree/res.ctx 非空（即此前 generateCartesian 成功）。
bool refineCartesian(CartesianResult& res, const AmrBox& region, std::string& err);

// 粗化：v1.5 留接口【不实现】（T-AMR 时间盒约定，见 04_integration_report）。
// 恒返回 false 并给出诚实错误说明。规划方向：盒内"8 兄弟完整子树且无 cut/合并胞"
// 合并回父叶 → 2:1 闭包 → 全量重新装配。
bool coarsenCartesian(CartesianResult& res, const AmrBox& region, std::string& err);

}  // namespace meshgen
