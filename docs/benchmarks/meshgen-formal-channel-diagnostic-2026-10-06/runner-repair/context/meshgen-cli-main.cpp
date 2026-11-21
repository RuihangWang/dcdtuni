// meshgen-cli：多块结构网格生成器 + 笛卡尔 cut-cell 引擎 —— 无头命令行 + Agent 原生接口
//
// 批处理：  meshgen-cli <project.json> [选项]
//           meshgen-cli cartesian <config.json> [--export-foam dir] [--export-vtu file.vtu]
// RPC 服务：meshgen-cli --server stdio | --server tcp:<port>
//
// Agent 闭环：model.* / point.* / curve.* / block.* / couplings.* / refine.* / mesh.*
//             + cartesian.{loadSurface,configure,generate,report,exportFoam,exportVtu}
//             + cartesian.{refine,coarsen}（T-AMR 运行时 AMR；coarsen v1.5 未实现，诚实报错）
// 全部操作均可以一行 JSON 完成，便于 LLM Agent 自主布置块、填充几何区域。
// 笛卡尔新 schema（castellatedMeshControls + meshGenerator）与 AMR 契约见
// docs/cartesian_design.md 与 docs/cartesian_dev/03_SPEC.md §2.3/§2.4。

#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>  // dc17 Step 2：dc_report.json sidecar 目录创建
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "CartesianCfdrun.hpp"
#include "CfdrunGeometryView.hpp"  // T-CFDRUN：dcdtuni .cfdrun 摄入（住 core/src/）
#include "meshgen/Cartesian.hpp"
#include "meshgen/Coupling.hpp"
#include "meshgen/FoamWriter.hpp"
#include "meshgen/Mesher.hpp"
#include "meshgen/Quality.hpp"
#include "meshgen/Refine.hpp"
#include "meshgen/Simd.hpp"
#include "meshgen/ThreadPool.hpp"

#if defined(MESHGEN_WITH_MPI)
// MPI 域分解生成（docs/cartesian_dev/19_mpi_design.md §2.f 模式 G）：
// mpirun 拉起时全秩执行同一 cartesian 流水（树复制 + 逐叶场分布），
// 打印与落盘只由 rank0 做（避免 P 份重复 stdout/stderr 与互相覆写产物）。
#include "CartesianMPI.hpp"
// 展开为空（OFF 构建）⇒ 预处理产物与基线逐字相同；ON 构建为 rank0 守卫。
#define CART_ROOT_BLOCK if (meshgen::mpi::root())
#define CART_MPI_ON (meshgen::mpi::active())
#else
#define CART_ROOT_BLOCK
#define CART_MPI_ON (false)
#endif

#if defined(_WIN32)
// Windows Winsock 兼容层（跨平台 TCP JSON-RPC）
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#define close closesocket
#define read(s, b, l) recv((s), (char*)(b), (int)(l), 0)
#define write(s, b, l) send((s), (const char*)(b), (int)(l), 0)
static void init_sockets() {
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
}
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
static void init_sockets() {}
#endif

using json = nlohmann::json;
using namespace meshgen;
namespace fs = std::filesystem;  // dc17 Step 2（dc_report.json sidecar）

namespace meshgen {
// 笛卡尔导出侧缺省 patch 类型推断的【单一真源】（定义于 core/src/CartesianExport.cpp）。
// R14-项8：此前报告侧 foamPatchTypeWith 缺省走 FoamWriter::foamPatchType（子串规则，
// 含 symmetryPlane/empty 子串分支），与笛卡尔导出侧 boundary 的缺省规则（"wall" 前缀
// → wall；front/back → empty；其余 → patch）存在分歧（如 "symmetry" 报告侧报
// symmetryPlane、导出侧实际写 patch——报告与落盘不一致）。现两侧同调本函数。
// CartesianExport.hpp 属 T2 所有权（本任务只读，不改头文件），故此处外部声明链接。
std::string cartesianDefaultPatchType(const std::string& name);
}  // namespace meshgen

namespace {

struct Session {
    Model model;
    MeshResult mesh;
    bool meshValid = false;
    std::string projectPath;
    // ---- 笛卡尔 cut-cell 引擎状态（SPEC §2.3，T3）----
    TriSurface cartSurface;
    CartesianConfig cartConfig;
    CartesianResult cartResult;
    bool surfaceLoaded = false;  // cartSurface 已装载
    bool cartValid = false;     // cartResult 可导出/可报告
    // TCP 多线程共享同一 Session（runTcpServer 内 std::thread([cfd,&s])），请求级整体加锁
    // （SPEC §2.3 明示；原实现无锁）。std::mutex 不可拷贝/移动 → 整体替换会话改为 reset()。
    std::mutex mutex;

    void reset() {
        model = Model{};
        mesh = MeshResult{};
        meshValid = false;
        projectPath.clear();
        cartSurface = TriSurface{};
        cartConfig = CartesianConfig{};
        cartResult = CartesianResult{};
        surfaceLoaded = false;
        cartValid = false;
    }
};

json distToJson(const Distribution& d) {
    return json{{"type", Distribution::name(d.type)}, {"p1", d.p1}, {"p2", d.p2}};
}
Distribution distFromJson(const json& j) {
    Distribution d;
    if (j.contains("type")) d.type = Distribution::fromName(j["type"].get<std::string>());
    if (j.contains("p1")) d.p1 = j["p1"].get<double>();
    if (j.contains("p2")) d.p2 = j["p2"].get<double>();
    return d;
}
Vec3 vecFrom(const json& j) {
    if (j.is_array() && j.size() == 3)
        return Vec3{j[0].get<double>(), j[1].get<double>(), j[2].get<double>()};
    if (j.is_object())
        return Vec3{j.value("x", 0.0), j.value("y", 0.0), j.value("z", 0.0)};
    return {};
}

// 严格 vec3 解析（供 AMR 盒等“静默取原点=悄悄改变网格”的场景）：
// 只接受 3 元素纯数字数组（或含数字 x/y/z 的对象）；其余一律报错。
// vecFrom 保持原样供旧有宽松路径使用，避免影响其他调用点。
bool strictVecFrom(const json& j, Vec3& out, std::string& err) {
    if (j.is_array()) {
        if (j.size() == 3 && j[0].is_number() && j[1].is_number() && j[2].is_number()) {
            out = Vec3{j[0].get<double>(), j[1].get<double>(), j[2].get<double>()};
            return true;
        }
        err = "应为 3 元素数字数组（实际: " + std::string(j.type_name()) +
              "，长度 " + std::to_string(j.size()) + "）";
        return false;
    }
    if (j.is_object()) {
        const bool ok = j.contains("x") && j.at("x").is_number() &&
                        j.contains("y") && j.at("y").is_number() &&
                        j.contains("z") && j.at("z").is_number();
        if (ok) {
            out = Vec3{j.at("x").get<double>(), j.at("y").get<double>(), j.at("z").get<double>()};
            return true;
        }
        err = "应为 [x,y,z] 数字数组或含数字 x/y/z 的对象（实际对象键不完整或非数字）";
        return false;
    }
    err = "应为 [x,y,z] 数字数组（实际: " + std::string(j.type_name()) + "）";
    return false;
}

json curveInfo(const Model& m, const Curve& c) {
    return json{{"id", c.id},
                {"name", c.name},
                {"type", c.type == CurveType::Line ? "line" : c.type == CurveType::Arc ? "arc"
                                                                                       : "polyline"},
                {"p0", c.p0},
                {"p1", c.p1},
                {"nPoints", c.nPoints},
                {"dist", distToJson(c.dist)},
                {"hasCustomSeq", !c.customSeq.empty()},
                {"length", c.length(m)}};
}

json modelInfo(const Session& s) {
    return json{{"points", s.model.pointCount()},
                {"curves", s.model.curveCount()},
                {"blocks", s.model.blockCount()},
                {"refineZones", s.model.refineZones.size()},
                {"meshValid", s.meshValid},
                {"cells", s.meshValid ? s.mesh.cells.size() : 0},
                {"points3d", s.meshValid ? s.mesh.points.size() : 0}};
}

json blockNeighbors(const Session& s, int blockId) {
    CouplingReport rep = analyzeCouplings(s.model);
    json arr = json::array();
    for (const auto& fc : rep.faces) {
        if (fc.blockA == blockId)
            arr.push_back({{"neighbor", fc.blockB}, {"face", fc.faceA},
                           {"neighborFace", fc.faceB}, {"countMatch", fc.countMatch},
                           {"distMatch", fc.distMatch}});
        else if (fc.blockB == blockId)
            arr.push_back({{"neighbor", fc.blockA}, {"face", fc.faceB},
                           {"neighborFace", fc.faceA}, {"countMatch", fc.countMatch},
                           {"distMatch", fc.distMatch}});
    }
    return arr;
}

// T-PT：patch 名 → 类型覆盖表（vector<pair> 配置序 → map 查表；空表 = 缺省推断）
// E5c/M1 sanitize："cyclicOpening" 是 dcdtuni cfdrun 的内部 porous 类型（cfdrun_schema.py
// :906），不是 OpenFOAM polyPatch 类型——裸写入 boundary 会 FATAL。全元数据 cyclic 出口
// （dcm 契约路径）经 dc->cyclic 先命中，不走本表；能从本表漏出 cyclicOpening 的只有
// 无 neighbourPatch 元数据的裸类型写出口（dc-raw 生产路径，如 E5c/M1 baffle parity
// 转名面落到穿孔 surface 的逐字名）⇒ 统一降级 "wall"（屏障面语义保守正确；报告侧
// foamPatchTypeWith 共用本表 ⇒ 报告≡落盘不变式保持）。
std::map<std::string, std::string> patchTypeMap(
    const std::vector<std::pair<std::string, std::string>>& pts) {
    std::map<std::string, std::string> m;
    for (const auto& kv : pts)
        m[kv.first] = (kv.second == "cyclicOpening") ? std::string("wall") : kv.second;
    return m;
}

// T-PT：报告侧类型推断——覆盖表命中优先，未命中走引擎缺省规则（与导出侧同语义）。
// R14-项8：缺省分支改调导出侧单一真源 cartesianDefaultPatchType（见文件头声明处
// 注释）——与 writePolyMeshFoam 的 boundary 缺省推断逐字同规则，报告≡落盘。
// 注：结构网格报告（meshReport）仍走 FoamWriter::foamPatchType，与其导出端
// writeOpenFoamPolyMesh 的 boundary 写出自洽，属另一条导出链，不在此统一范围。
std::string foamPatchTypeWith(const std::string& name,
                              const std::map<std::string, std::string>& ov) {
    const auto it = ov.find(name);
    if (it != ov.end() && !it->second.empty()) return it->second;
    return cartesianDefaultPatchType(name);
}

json couplingsReportJson(const Session& s) {
    CouplingReport rep = analyzeCouplings(s.model);
    json j;
    j["mismatch"] = rep.nMismatch;
    j["sharedCurves"] = rep.sharedCurves;
    j["matchedCurves"] = rep.matchedCurves;
    j["mismatchedCurves"] = rep.mismatchedCurves;
    j["faces"] = json::array();
    for (const auto& fc : rep.faces)
        j["faces"].push_back({{"blockA", fc.blockA}, {"faceA", fc.faceA},
                              {"blockB", fc.blockB}, {"faceB", fc.faceB},
                              {"countMatch", fc.countMatch}, {"distMatch", fc.distMatch},
                              {"nA", {fc.nA[0], fc.nA[1]}}, {"nB", {fc.nB[0], fc.nB[1]}}});
    j["edges"] = json::array();
    for (const auto& ec : rep.edges)
        j["edges"].push_back({{"curveA", ec.curveA}, {"curveB", ec.curveB},
                              {"countMatch", ec.countMatch}, {"distMatch", ec.distMatch}});
    return j;
}

json meshReport(const Session& s, const QualityStats* q) {
    json j;
    j["points"] = s.mesh.points.size();
    j["cells"] = s.mesh.cells.size();
    j["faces"] = s.mesh.faces.size();
    j["internalFaces"] = s.mesh.nInternalFaces;
    j["genMs"] = s.mesh.genMs;
    j["patches"] = json::array();
    for (size_t i = 0; i < s.mesh.patchNames.size(); ++i)
        j["patches"].push_back({{"name", s.mesh.patchNames[i]},
                                {"type", foamPatchType(s.mesh.patchNames[i])},
                                {"nFaces", s.mesh.patchNFaces[i]}});
    if (q) {
        j["quality"] = {{"minSJ", q->minSJ}, {"avgSJ", q->avgSJ}, {"nBadSJ", q->nBadSJ},
                        {"maxNonOrtho", q->maxNonOrtho}, {"avgNonOrtho", q->avgNonOrtho}};
    }
    long refined = 0;
    for (auto f : s.mesh.cellRefined) refined += f;
    j["refinedCells"] = refined;
    return j;
}

json cartesianReport(const Session& s) {
    json j;
    j["surfaceLoaded"] = s.surfaceLoaded;
    j["generated"] = s.cartValid;
    if (!s.cartValid) return j;
    const CartesianResult& r = s.cartResult;
    j["leaves"] = r.nLeaves;
    j["cutCells"] = r.nCut;
    j["fluidCells"] = r.nFluid;
    j["internalFaces"] = r.mesh.nInternal;
    j["points"] = r.mesh.points.size();
    j["faces"] = r.mesh.faces.size();
    j["minFluidVol"] = r.minFluidVol;
    j["genMs"] = r.genMs;
    // AMR 契约字段（SPEC §2.1/§2.4）
    j["level0Edge"] = r.level0Edge;
    j["cellLevels"] = r.cellLevel.size();
    j["pointLevels"] = r.pointLevel.size();
    if (!r.cellLevel.empty()) {
        int lo = r.cellLevel[0], hi = r.cellLevel[0];
        for (int lv : r.cellLevel) {
            lo = std::min(lo, lv);
            hi = std::max(hi, lv);
        }
        j["levelRange"] = {lo, hi};
    }
    // 持久树统计（T-AMR）：叶数 / 节点数 / 级直方图；运行时 refine 后即时报最新树
    if (r.tree) {
        std::map<int, long long> hist;
        for (long long i = 0; i < r.tree->nLeaves(); ++i) hist[r.tree->leafLevel(i)]++;
        json lvArr = json::array();
        for (const auto& kv : hist) lvArr.push_back({{"level", kv.first}, {"count", kv.second}});
        j["tree"] = {{"leaves", r.tree->nLeaves()},
                     {"nodes", r.tree->nNodes()},
                     {"levelHistogram", lvArr}};
    }
    j["patches"] = json::array();
    std::vector<long> nFaces(r.mesh.patchNames.size(), 0);
    for (size_t f = 0; f < r.mesh.faces.size(); ++f)
        if (r.mesh.neighbour[f] < 0 && r.mesh.facePatch[f] >= 0 &&
            r.mesh.facePatch[f] < (int)nFaces.size())
            nFaces[(size_t)r.mesh.facePatch[f]]++;
    const std::map<std::string, std::string> ptm = patchTypeMap(s.cartConfig.patchTypes);
    for (size_t i = 0; i < r.mesh.patchNames.size(); ++i)
        j["patches"].push_back({{"name", r.mesh.patchNames[i]},
                                {"type", foamPatchTypeWith(r.mesh.patchNames[i], ptm)},
                                {"nFaces", nFaces[i]}});
    return j;
}

// ---------------- 笛卡尔新 schema 解析（SPEC §2.3） ----------------
// 顶层键镜像 snappyHexMeshDict 的 castellatedMeshControls；本引擎扩展键隔离于 meshGenerator；
// 旧式扁平字段（baseLevel/maxLevel/domainPad/balance/buffer/roi/firstLayer 顶层）保持向后兼容。
// 优先级：castellatedMeshControls/meshGenerator 新键 > 顶层旧式扁平字段 > 默认值。

// 与引擎同规则估算根立方体边长 H（bbox 各向外扩 domainPad·maxDim 后取最大维度；
// hasBox 时取 box 最大维度）。firstLayer 兼容换算依赖它。
double estimateRootEdge(const TriSurface& surf, const CartesianConfig& cfg) {
    if (cfg.hasBox) {
        const Vec3 sz = cfg.boxMax - cfg.boxMin;
        // T-2D：根边长只按面内维度（z 向为单胞厚 slab，不参与域尺寸）
        return cfg.mode2d ? std::max(sz.x, sz.y) : std::max({sz.x, sz.y, sz.z});
    }
    const Vec3 sz = surf.bboxMax() - surf.bboxMin();
    const double maxDim = cfg.mode2d ? std::max(sz.x, sz.y)
                                     : std::max({sz.x, sz.y, sz.z});
    return maxDim * (1.0 + 2.0 * cfg.domainPad);
}

struct CartParseOutcome {
    CartesianConfig cfg;              // 默认值=冻结头 CartesianConfig 构造默认
    std::vector<std::string> warnings;
    std::string vtuDefault;           // meshGenerator.exportVtu（--export-vtu 未给时的默认）
    std::string foamDefault;          // meshGenerator.exportFoam（--export-foam 未给时的默认）
    bool newSchema = false;           // 出现 castellatedMeshControls 或 meshGenerator
};

CartParseOutcome parseCartesianConfigJson(const json& j, const TriSurface* surf) {
    CartParseOutcome out;
    CartesianConfig& cfg = out.cfg;
    auto& warn = out.warnings;
    out.newSchema = j.contains("castellatedMeshControls") || j.contains("meshGenerator");

    if (!out.newSchema) {
        // 纯旧式配置：保持旧引擎默认（baseLevel=1/maxLevel=5/buffer=3），保证存量算例行为不变
        cfg.baseLevel = 1;
        cfg.maxLevel = 5;
        cfg.domainPad = 0.2;
        cfg.buffer = 3;
        cfg.balance2to1 = true;
    }

    auto readRoi = [](const json& arr) {
        std::vector<RoiBox> rois;
        for (const auto& r : arr) {
            RoiBox rb;
            rb.mn = vecFrom(r.value("min", json::array({0, 0, 0})));
            rb.mx = vecFrom(r.value("max", json::array({0, 0, 0})));
            rb.size = r.value("size", 0.0);
            rois.push_back(rb);
        }
        return rois;
    };

    // ---- 旧式顶层扁平字段（向后兼容）----
    if (j.contains("baseLevel")) cfg.baseLevel = j["baseLevel"].get<int>();
    if (j.contains("maxLevel")) cfg.maxLevel = j["maxLevel"].get<int>();
    if (j.contains("domainPad")) cfg.domainPad = j["domainPad"].get<double>();
    if (j.contains("balance")) cfg.balance2to1 = j["balance"].get<bool>();
    if (j.contains("buffer")) cfg.buffer = j["buffer"].get<int>();
    if (j.contains("roi") && j["roi"].is_array()) cfg.roi = readRoi(j["roi"]);
    if (j.contains("mode2d")) cfg.mode2d = j["mode2d"].get<bool>();  // T-2D（2D 单胞厚模式）
    // dc17 Step 1：seeded-flood DC 模式旗标（缺省 false = 历史行为）。解析口径与 mode2d
    // 同（顶层旧式扁平 + meshGenerator 新键，新键优先）；引擎读取自 Step 2 起分步接入。
    if (j.contains("dcMode")) cfg.dcMode = j["dcMode"].get<bool>();

    // ---- meshGenerator：本引擎扩展键 ----
    if (j.contains("meshGenerator") && j["meshGenerator"].is_object()) {
        const json& mg = j["meshGenerator"];
        if (mg.contains("baseLevel")) cfg.baseLevel = mg["baseLevel"].get<int>();
        if (mg.contains("maxLevel")) cfg.maxLevel = mg["maxLevel"].get<int>();
        if (mg.contains("domainPad")) cfg.domainPad = mg["domainPad"].get<double>();
        if (mg.contains("mode2d")) cfg.mode2d = mg["mode2d"].get<bool>();  // T-2D
        if (mg.contains("dcMode")) cfg.dcMode = mg["dcMode"].get<bool>();  // dc17 Step 1（meshGenerator.dcMode）
        if (mg.contains("roi") && mg["roi"].is_array()) cfg.roi = readRoi(mg["roi"]);
        if (mg.contains("qualityGate") && mg["qualityGate"].is_string())
            cfg.qualityGateWarn = (mg["qualityGate"].get<std::string>() == "warn");
        if (mg.contains("smallCell") && mg["smallCell"].is_object()) {
            const json& sc = mg["smallCell"];
            if (sc.contains("volumeFraction")) cfg.mergeVolFrac = sc["volumeFraction"].get<double>();
            if (sc.contains("maxPasses")) cfg.mergeMaxPasses = sc["maxPasses"].get<int>();
        }
        if (mg.contains("box") && mg["box"].is_object()) {  // 各向异性域（可选扩展）
            cfg.hasBox = true;
            cfg.boxMin = vecFrom(mg["box"].value("min", json::array({0, 0, 0})));
            cfg.boxMax = vecFrom(mg["box"].value("max", json::array({0, 0, 0})));
        }
        // T-PT：boundary 边界面 type 覆盖映射（可选；缺省 = 现状推断规则）
        if (mg.contains("patchTypes") && mg["patchTypes"].is_object()) {
            for (auto it = mg["patchTypes"].begin(); it != mg["patchTypes"].end(); ++it)
                cfg.patchTypes.emplace_back(it.key(), it.value().get<std::string>());
        }
        // T-CFDRUN：baffle 声明（可选；createBaffles 语义 → 成对 master/slave boundary patch）。
        //   每条：{region, name?, faceType?}；region 必填（= 表面 patchName）；name 缺省=region；
        //   faceType 缺省 "wall"。同时把 master/slave faceType 注入 patchTypes（导出端类型）。
        if (mg.contains("baffles") && mg["baffles"].is_array()) {
            for (const auto& b : mg["baffles"]) {
                if (!b.is_object() || !b.contains("region") || !b["region"].is_string()) continue;
                BaffleSpec bs;
                bs.region = b["region"].get<std::string>();
                bs.name = b.value("name", bs.region);
                bs.faceType = b.value("faceType", std::string("wall"));
                if (bs.name.empty()) bs.name = bs.region;
                cfg.baffles.push_back(bs);
                cfg.patchTypes.emplace_back(bs.name, bs.faceType);
                cfg.patchTypes.emplace_back(bs.name + "_slave", bs.faceType);
            }
        }
        // T-CFDRUN：cellZone 声明（可选；胞心 containment → constant/polyMesh/cellZones）。
        //   每条：{name, min:[x,y,z], max:[x,y,z]}。
        if (mg.contains("cellZones") && mg["cellZones"].is_array()) {
            for (const auto& z : mg["cellZones"]) {
                if (!z.is_object() || !z.contains("name") || !z["name"].is_string()) continue;
                CellZoneSpec zs;
                zs.name = z["name"].get<std::string>();
                zs.mn = vecFrom(z.value("min", json::array({0, 0, 0})));
                zs.mx = vecFrom(z.value("max", json::array({0, 0, 0})));
                cfg.cellZones.push_back(zs);
            }
        }
        out.vtuDefault = mg.value("exportVtu", "");
        out.foamDefault = mg.value("exportFoam", "");
    }

    // ---- 旧式顶层 patchTypes（与 meshGenerator 同语义；新键优先）----
    if (j.contains("patchTypes") && j["patchTypes"].is_object() && cfg.patchTypes.empty()) {
        for (auto it = j["patchTypes"].begin(); it != j["patchTypes"].end(); ++it)
            cfg.patchTypes.emplace_back(it.key(), it.value().get<std::string>());
    }

    // ---- castellatedMeshControls：镜像 snappyHexMeshDict ----
    if (j.contains("castellatedMeshControls") && j["castellatedMeshControls"].is_object()) {
        const json& cm = j["castellatedMeshControls"];
        if (cm.contains("maxGlobalCells")) cfg.maxGlobalCells = cm["maxGlobalCells"].get<long long>();
        if (cm.contains("minRefinementCells")) cfg.minRefineCells = cm["minRefinementCells"].get<int>();
        if (cm.contains("nCellsBetweenLevels")) cfg.nCellsBetweenLevels = cm["nCellsBetweenLevels"].get<int>();
        if (cm.contains("refinementSurfaces") && cm["refinementSurfaces"].is_object()) {
            for (auto it = cm["refinementSurfaces"].begin(); it != cm["refinementSurfaces"].end(); ++it) {
                LevelSpec ls{0, 0};
                const json& rs = it.value();
                // 区分『缺省』与『畸形』：畸形 level（非对象/缺键/非数组/长度≠2/
                // 元素非数）此前被静默当 {0,0} 处理 ⇒ 该表面零加密且无任何告警，
                // 用户得到静默的错误网格。现显式告警（与 keepPoints 缺失提示同级）。
                const bool haveLevel = rs.is_object() && rs.contains("level");
                const bool wellFormed =
                    haveLevel && rs["level"].is_array() && rs["level"].size() == 2 &&
                    rs["level"][0].is_number() && rs["level"][1].is_number();
                if (wellFormed) {
                    ls.lo = rs["level"][0].get<int>();
                    ls.hi = rs["level"][1].get<int>();
                } else if (!rs.is_object()) {
                    warn.push_back("[config] refinementSurfaces.'" + it.key() +
                                   "' 畸形（期望对象 {\"level\": [min, max]}），该表面将不加密");
                } else if (!haveLevel) {
                    warn.push_back("[config] refinementSurfaces.'" + it.key() +
                                   "' 缺少 \"level\" 键（期望 [min, max] 两整数数组），该表面将不加密");
                } else {
                    warn.push_back("[config] refinementSurfaces.'" + it.key() +
                                   "'.level 畸形（期望 [min, max] 两整数数组），该表面将不加密");
                }
                cfg.surfaceLevels.emplace_back(it.key(), ls);
            }
        }
        if (cm.contains("locationInMesh")) cfg.keepPoints.push_back(vecFrom(cm["locationInMesh"]));
        if (cm.contains("outsidePoints") && cm["outsidePoints"].is_array())
            for (const auto& q : cm["outsidePoints"]) cfg.keepPoints.push_back(vecFrom(q));
    }

    // ---- firstLayer 弃用兼容糖：level = ceil(log2(H/h)) − baseLevel（SPEC §2.3）----
    if (j.contains("firstLayer") && j["firstLayer"].is_object() && !j["firstLayer"].empty()) {
        if (cfg.surfaceLevels.empty()) {
            if (surf == nullptr) {
                warn.push_back("[compat] firstLayer 换算依赖表面 bbox（H），表面未装载，本次跳过换算");
            } else {
                const double H = estimateRootEdge(*surf, cfg);
                for (auto it = j["firstLayer"].begin(); it != j["firstLayer"].end(); ++it) {
                    const double h = it.value().get<double>();
                    if (h <= 0 || H <= 0) continue;
                    const int absLv = (int)std::ceil(std::log2(std::max(1.0, H / h)));
                    const int rel = std::max(0, std::min(absLv - cfg.baseLevel,
                                                         cfg.maxLevel - cfg.baseLevel));
                    cfg.surfaceLevels.emplace_back(it.key(), LevelSpec{rel, rel});
                    warn.push_back("[compat] firstLayer 已弃用：'" + it.key() + "' h=" +
                                   std::to_string(h) + " → 换算表面级 " + std::to_string(rel) +
                                   "（绝对级 " + std::to_string(cfg.baseLevel + rel) +
                                   "，level=ceil(log2(H/h))-base，H=" + std::to_string(H) + "）");
                }
            }
        } else {
            warn.push_back("[compat] firstLayer 已弃用：已配置 refinementSurfaces 层级，firstLayer 被忽略");
        }
    }

    // ---- 配置校验提示（SPEC §2.3）----
    // surfaceLevels 的键未匹配任何表面 patch：该条目整体无效（引擎侧静默跳过），
    // 与畸形 level 同属静默零加密家族，显式提示（表面已装载时才能核对）
    if (surf != nullptr && !cfg.surfaceLevels.empty()) {
        for (const auto& sl : cfg.surfaceLevels) {
            bool matched = false;
            for (const auto& nm : surf->patchNames)
                if (nm == sl.first) { matched = true; break; }
            if (matched) continue;
            std::string patches;
            for (const auto& nm : surf->patchNames) patches += (patches.empty() ? "" : ", ") + nm;
            warn.push_back("[config] refinementSurfaces.'" + sl.first +
                           "' 未匹配任何表面 patch（现有: " +
                           (patches.empty() ? std::string("无") : patches) +
                           "），对应层级配置无效");
        }
    }
    if (!cfg.surfaceLevels.empty() && cfg.nCellsBetweenLevels > 0)
        warn.push_back("[hint] 只配置了表面级而 nCellsBetweenLevels>0：本引擎的过渡层对表面锚生效"
                       "（与上游 snappy 差异——上游仅对 refinementRegions distance 带生效，"
                       "表面单独加密不产生额外过渡，只靠 2:1；见 docs/cartesian_design.md §2.1）");
    if (out.newSchema && cfg.keepPoints.empty())
        warn.push_back("[hint] 未配置 locationInMesh/outsidePoints：S7 内外洪泛缺种子点，"
                       "外流算例请提供 locationInMesh");

    return out;
}

// 取路径的父目录（polyMesh 目录 → constant/ 目录，用于落 dynamicMeshDict）
std::string parentDir(const std::string& path) {
    std::string p = path;
    while (p.size() > 1 && p.back() == '/') p.pop_back();
    const size_t pos = p.find_last_of('/');
    if (pos == std::string::npos) return ".";
    if (pos == 0) return "/";
    return p.substr(0, pos);
}

// dc17 Step 2：dcMode 下把引擎装入的 [DC-S7] JSON 报告落 sidecar——
// <exportDir>/../dc_report.json（"next to the export dir"；approach1 openQuestion 的
// JSON-sidecar 建议）。成功与失败路径都调用（失败诊断是 kill-criteria 证据链）。
// flag-OFF / 报告为空零触碰 ⇒ 历史行为零变化。
void writeDcReportSidecar(const CartesianConfig& cfg, const CartesianResult& res,
                          const std::string& exportDir) {
    if (!cfg.dcMode || res.dcReportJson.empty() || exportDir.empty()) return;
    CART_ROOT_BLOCK {
        const std::string dir = parentDir(exportDir);
        std::error_code ec;
        fs::create_directories(dir, ec);
        const std::string rp = dir + "/dc_report.json";
        std::ofstream rf(rp);
        if (rf) {
            rf << res.dcReportJson;
            std::printf("[dc] S7 报告 → %s\n", rp.c_str());
        } else {
            std::fprintf(stderr, "[dc] warn: 无法写 S7 报告 %s\n", rp.c_str());
        }
    }
}

// ---------------- RPC 分发 ----------------

struct RpcError {
    std::string msg;
};

json handleMethod(Session& s, const std::string& method, const json& p) {
    auto require = [&](bool cond, const char* msg) {
        if (!cond) throw RpcError{msg};
    };

    if (method == "ping") return "pong";

    if (method == "system.info")
        return json{{"simd", simd::backendName()},
                    {"threads", globalPool().size()},
                    {"version", "1.0"}};

    // ---- model ----
    if (method == "model.new") {
        s.reset();  // Session 含 std::mutex（不可赋值）→ 逐字段重置
        return modelInfo(s);
    }
    if (method == "model.load") {
        const std::string path = p.value("path", "");
        std::string err;
        require(!path.empty() && s.model.loadJsonFile(path, &err),
                ("加载失败: " + err).c_str());
        s.projectPath = path;
        s.meshValid = false;
        return modelInfo(s);
    }
    if (method == "model.loadJson") {
        std::string err;
        require(s.model.fromJsonString(p.value("json", "{}"), &err),
                ("解析失败: " + err).c_str());
        s.meshValid = false;
        return modelInfo(s);
    }
    if (method == "model.save") {
        const std::string path = p.value("path", s.projectPath);
        std::string err;
        require(!path.empty() && s.model.saveJsonFile(path, &err),
                ("保存失败: " + err).c_str());
        s.projectPath = path;
        return json{{"path", path}};
    }
    if (method == "model.info") return modelInfo(s);
    if (method == "model.json") return json::parse(s.model.toJsonString());
    if (method == "model.validate") return s.model.validate();

    // ---- point ----
    if (method == "point.add") {
        const int id = s.model.addPoint(vecFrom(p), p.value("name", ""));
        s.meshValid = false;
        return json{{"id", id}};
    }
    if (method == "point.set") {
        GeoPoint* g = s.model.point(p.value("id", 0));
        require(g, "点不存在");
        g->pos = vecFrom(p);
        s.meshValid = false;
        return json{{"id", g->id}};
    }

    // ---- curve ----
    if (method == "curve.addLine") {
        const int id = s.model.addLine(p.value("p0", 0), p.value("p1", 0),
                                       p.value("nPoints", 11),
                                       p.contains("dist") ? distFromJson(p["dist"])
                                                          : Distribution{});
        s.meshValid = false;
        return json{{"id", id}};
    }
    if (method == "curve.addArc") {
        // center 为必需键：const json& 缺键裸读触发 JSON_ASSERT（Release 剥离后为 UB，
        // 实测静默生成以原点为中心的退化弧）。显式校验+友好错误（不给默认值）。
        require(p.contains("center"), "curve.addArc 缺少必需键 center");
        const int id = s.model.addArc(p.value("p0", 0), p.value("p1", 0), vecFrom(p.at("center")),
                                      p.value("nPoints", 11),
                                      p.contains("dist") ? distFromJson(p["dist"])
                                                         : Distribution{},
                                      p.contains("normal") ? vecFrom(p["normal"]) : Vec3{});
        s.meshValid = false;
        return json{{"id", id}};
    }
    if (method == "curve.set") {
        Curve* c = s.model.curve(p.value("id", 0));
        require(c, "曲线不存在");
        if (p.contains("nPoints")) c->nPoints = std::max(2, p["nPoints"].get<int>());
        if (p.contains("dist")) c->dist = distFromJson(p["dist"]);
        if (p.contains("reverse") && p["reverse"].get<bool>() && c->type != CurveType::Polyline)
            std::swap(c->p0, c->p1);
        if (p.value("clearCustomSeq", false)) c->customSeq.clear();
        s.meshValid = false;
        return curveInfo(s.model, *c);
    }
    if (method == "curve.info") {
        const Curve* c = s.model.curve(p.value("id", 0));
        require(c, "曲线不存在");
        auto j = curveInfo(s.model, *c);
        if (p.value("includeSeq", false)) {
            j["seq"] = ((int)c->customSeq.size() == c->nPoints && c->nPoints >= 2)
                           ? c->customSeq
                           : distributionSequence(c->dist, c->nPoints);
        }
        return j;
    }

    // ---- block ----
    if (method == "block.add") {
        auto verts = p.value("vertices", std::vector<int>{});
        require(verts.size() == 8, "vertices 需要 8 个点");
        std::array<int, 8> v{};
        std::copy(verts.begin(), verts.begin() + 8, v.begin());
        std::array<std::string, 6> patches{};
        if (p.contains("patches")) {
            auto ps = p["patches"].get<std::vector<std::string>>();
            for (int i = 0; i < 6 && i < (int)ps.size(); ++i) patches[i] = ps[i];
        }
        const int id = s.model.addBlock(v, patches, p.value("nPoints", 11));
        s.meshValid = false;
        return json{{"id", id}};
    }
    if (method == "block.split") {
        const int axis = p.value("axis", std::string("i")) == "j" ? 1
                         : p.value("axis", std::string("i")) == "k" ? 2 : 0;
        auto ratios = p.value("ratios", std::vector<double>{});
        const auto ids =
            s.model.splitBlock(p.value("id", 0), axis, p.value("n", 2), ratios);
        require(!ids.empty(), "切分失败（块不存在或切分参数非法）");
        s.meshValid = false;
        return json{{"ids", ids}};
    }
    if (method == "block.extrude") {
        // vec 为必需键：缺键裸读为 UB，实测静默生成零位移/零体积重复块。显式校验。
        require(p.contains("vec"), "block.extrude 缺少必需键 vec");
        const int id = s.model.extrudeBlock(p.value("id", 0), vecFrom(p.at("vec")));
        require(id, "拉伸失败");
        s.meshValid = false;
        return json{{"id", id}};
    }
    if (method == "block.neighbors") return blockNeighbors(s, p.value("id", 0));
    if (method == "block.list") {
        json arr = json::array();
        for (const auto& b : s.model.blocks)
            arr.push_back({{"id", b.id}, {"name", b.name}, {"enabled", b.enabled},
                           {"vertices", b.verts}, {"patches", b.patches}});
        return arr;
    }
    if (method == "block.setPatches") {
        Block* b = s.model.block(p.value("id", 0));
        require(b, "块不存在");
        auto ps = p.value("patches", std::vector<std::string>{});
        for (int i = 0; i < 6 && i < (int)ps.size(); ++i) b->patches[i] = ps[i];
        return json{{"id", b->id}};
    }

    // ---- delete ----
    if (method == "delete") {
        const std::string type = p.value("type", "");
        const int id = p.value("id", 0);
        bool ok = type == "point"   ? s.model.deletePoint(id)
                  : type == "curve" ? s.model.deleteCurve(id)
                  : type == "block" ? s.model.deleteBlock(id)
                                    : false;
        if (type == "zone") {
            auto& zs = s.model.refineZones;
            zs.erase(std::remove_if(zs.begin(), zs.end(),
                                    [id](const RefineZone& z) { return z.id == id; }),
                     zs.end());
            ok = true;
        }
        s.meshValid = false;
        return json{{"ok", ok}};
    }

    // ---- couplings ----
    if (method == "couplings.report") return couplingsReportJson(s);
    if (method == "couplings.autoFix") {
        auto log = autoFixCouplings(s.model);
        s.meshValid = false;
        return json{{"log", log}};
    }

    // ---- refine（魔法笔） ----
    if (method == "refine.addZone") {
        RefineZone z;
        z.id = s.model.nextZoneId++;
        z.name = p.value("name", "zone" + std::to_string(z.id));
        z.radius = p.value("radius", 0.1);
        z.targetSize = p.value("targetSize", 0.01);
        if (p.contains("stroke"))
            for (const auto& q : p["stroke"]) z.stroke.push_back(vecFrom(q));
        require(z.stroke.size() >= 2, "stroke 至少两个点");
        s.model.refineZones.push_back(std::move(z));
        return json{{"id", s.model.refineZones.back().id}};
    }
    if (method == "refine.apply") {
        auto r = applyRefinement(s.model);
        s.meshValid = false;
        return json{{"curvesModified", r.curvesModified}, {"log", r.log}};
    }
    if (method == "refine.clear") {
        return json{{"cleared", clearRefinement(s.model)}};
    }

    // ---- mesh ----
    if (method == "mesh.generate") {
        if (p.value("autoFix", false)) autoFixCouplings(s.model);
        if (p.value("refine", false)) applyRefinement(s.model);
        std::string err;
        require(generateMesh(s.model, s.mesh, err), ("网格生成失败: " + err).c_str());
        s.meshValid = true;
        QualityStats q = computeQuality(s.mesh);
        return meshReport(s, &q);
    }
    if (method == "mesh.quality") {
        require(s.meshValid, "请先生成网格");
        return json{
            {"quality", [](const QualityStats& q) {
                 return json{{"minSJ", q.minSJ}, {"avgSJ", q.avgSJ}, {"nBadSJ", q.nBadSJ},
                             {"maxNonOrtho", q.maxNonOrtho}, {"avgNonOrtho", q.avgNonOrtho}};
             }(computeQuality(s.mesh))}};
    }
    if (method == "mesh.exportFoam") {
        require(s.meshValid, "请先生成网格");
        std::string err;
        const std::string dir = p.value("dir", "case/constant/polyMesh");
        require(writeOpenFoamPolyMesh(s.mesh, dir, err), ("导出失败: " + err).c_str());
        return json{{"dir", dir}};
    }
    if (method == "mesh.blockMeshDict") {
        std::string err;
        const std::string path = p.value("path", "blockMeshDict");
        require(writeBlockMeshDict(s.model, path, err), ("导出失败: " + err).c_str());
        return json{{"path", path}};
    }
    if (method == "mesh.report") {
        require(s.meshValid, "请先生成网格");
        QualityStats q = computeQuality(s.mesh);
        return meshReport(s, &q);
    }

    // ---- cartesian（cut-cell 引擎；新 schema 见 SPEC §2.3 与 docs/cartesian_design.md）----
    if (method == "cartesian.loadSurface") {
        const std::string path = p.value("path", "");
        require(!path.empty(), "缺少 path");
        std::string err;
        TriSurface surf;
        const bool isStl = path.size() > 4 && path.substr(path.size() - 4) == ".stl";
        require(isStl ? surf.loadSTL(path, err) : surf.loadJSON(path, err),
                ("表面装载失败: " + err).c_str());
        // 防御纵深：入会话前再过一次索引/patch 校验（畸形索引 = 段错误或垃圾网格）
        require(surf.validate(err), ("表面无效: " + err).c_str());
        s.cartSurface = std::move(surf);
        s.surfaceLoaded = true;
        s.cartValid = false;
        const Vec3 bmin = s.cartSurface.bboxMin(), bmax = s.cartSurface.bboxMax();
        return json{{"path", path},
                    {"vertices", s.cartSurface.verts.size()},
                    {"triangles", s.cartSurface.tris.size()},
                    {"patches", s.cartSurface.patchNames},
                    {"bbox", {{"min", {bmin.x, bmin.y, bmin.z}},
                              {"max", {bmax.x, bmax.y, bmax.z}}}}};
    }
    if (method == "cartesian.configure") {
        // 接受与批处理同一份 schema：castellatedMeshControls + meshGenerator（+旧式扁平字段）
        if (p.contains("firstLayer") && p["firstLayer"].is_object() && s.surfaceLoaded) {
            // 同步到 TriSurface：过渡期旧引擎仍读 surf.firstLayer（新引擎只读 surfaceLevels）
            for (auto it = p["firstLayer"].begin(); it != p["firstLayer"].end(); ++it) {
                const int pid = s.cartSurface.patchId(it.key());
                if ((int)s.cartSurface.firstLayer.size() <= pid)
                    s.cartSurface.firstLayer.resize(pid + 1, -1);
                s.cartSurface.firstLayer[pid] = it.value().get<double>();
            }
        }
        CartParseOutcome parsed =
            parseCartesianConfigJson(p, s.surfaceLoaded ? &s.cartSurface : nullptr);
        s.cartConfig = std::move(parsed.cfg);
        s.cartValid = false;
        json sls = json::array();
        for (const auto& sl : s.cartConfig.surfaceLevels)
            sls.push_back({{"region", sl.first}, {"level", {sl.second.lo, sl.second.hi}}});
        return json{{"ok", true},
                    {"warnings", parsed.warnings},
                    {"config",
                     {{"baseLevel", s.cartConfig.baseLevel},
                      {"maxLevel", s.cartConfig.maxLevel},
                      {"domainPad", s.cartConfig.domainPad},
                      {"nCellsBetweenLevels", s.cartConfig.nCellsBetweenLevels},
                      {"maxGlobalCells", s.cartConfig.maxGlobalCells},
                      {"minRefinementCells", s.cartConfig.minRefineCells},
                      {"surfaceLevels", sls},
                      {"roiBoxes", s.cartConfig.roi.size()},
                      {"keepPoints", s.cartConfig.keepPoints.size()}}}};
    }
    if (method == "cartesian.generate") {
        require(s.surfaceLoaded, "请先 cartesian.loadSurface");
        std::string err;
        require(generateCartesian(s.cartSurface, s.cartConfig, s.cartResult, err),
                ("笛卡尔生成失败: " + err).c_str());
        s.cartValid = true;
        return cartesianReport(s);
    }
    if (method == "cartesian.report") {
        require(s.cartValid, "请先 cartesian.generate");
        return cartesianReport(s);
    }
    // ---- T-AMR：运行时加密/粗化（Session 请求级锁由 serveLine 统一持有）----
    auto amrBoxFrom = [&](const json& q) {
        AmrBox box;
        const json& b = q.contains("box") ? q["box"] : q;
        require(b.is_object() && b.contains("min") && b.contains("max"),
                "缺少 box:{min:[x,y,z],max:[x,y,z]}");
        // 严格解析：vecFrom 对畸形值静默取原点 → 盒角点被悄悄替换、在错误位置加密
        // （违反 docs/agent_native.md §box [x,y,z] 契约）。显式报错并给出实际类型。
        std::string vErr;
        require(strictVecFrom(b.at("min"), box.mn, vErr),
                ("box.min 类型错误: " + vErr).c_str());
        require(strictVecFrom(b.at("max"), box.mx, vErr),
                ("box.max 类型错误: " + vErr).c_str());
        box.levels = q.value("levels", 1);
        return box;
    };
    if (method == "cartesian.refine") {
        require(s.cartValid, "请先 cartesian.generate");
        const AmrBox box = amrBoxFrom(p);
        std::string err;
        require(refineCartesian(s.cartResult, box, err), ("加密失败: " + err).c_str());
        return cartesianReport(s);  // 含刷新后的胞数/级域/树统计
    }
    if (method == "cartesian.coarsen") {
        require(s.cartValid, "请先 cartesian.generate");
        const AmrBox box = amrBoxFrom(p);
        std::string err;
        // v1.5 未实现（T-AMR 约定）：诚实报错，不静默
        require(coarsenCartesian(s.cartResult, box, err), ("粗化失败: " + err).c_str());
        return cartesianReport(s);
    }
    if (method == "cartesian.exportFoam") {
        require(s.cartValid, "请先 cartesian.generate");
        std::string err;
        const std::string dir = p.value("dir", "case/constant/polyMesh");
        require(writePolyMeshFoam(s.cartResult.mesh, dir, err,
                                  patchTypeMap(s.cartConfig.patchTypes)),
                ("导出失败: " + err).c_str());
        // AMR 契约文件（SPEC §2.4）：cellLevel/pointLevel/level0Edge 落 polyMesh 目录；
        // dynamicMeshDict 由 writeCartesianAmrFiles 一并落其父目录（即 constant/，
        // 注意不是 system/，见 01_U3 §5；writeDynamicMeshDict 接收完整文件路径，勿传目录）
        require(writeCartesianAmrFiles(s.cartResult, dir, err),
                ("AMR 场导出失败: " + err).c_str());
        return json{{"dir", dir},
                    {"dynamicMeshDict", parentDir(dir) + "/dynamicMeshDict"}};
    }
    if (method == "cartesian.exportVtu") {
        require(s.cartValid, "请先 cartesian.generate");
        std::string err;
        const std::string path = p.value("path", "");
        require(!path.empty(), "缺少 path（.vtu）");
        require(writePolyMeshVtu(s.cartResult.mesh, path, err), ("导出失败: " + err).c_str());
        return json{{"path", path}};
    }

    throw RpcError{"未知方法: " + method};
}

json serveLine(Session& s, const std::string& line) {
    // 共享 Session 请求级串行化：TCP 模式每连接一线程（见 runTcpServer），
    // cartesian.*/mesh.* 均为有状态操作，必须整体加锁（SPEC §2.3）。
    std::lock_guard<std::mutex> lock(s.mutex);
    // id 提到 try 之外：错误应答必须回带请求 id（docs/agent_native.md 契约
    // {"id":N,"error":"..."}），否则多客户端/异步客户端无法关联应答。
    // 用 json 承载以免收窄 JSON-RPC 允许的字符串 id；parse 失败无 id → null（JSON-RPC 惯例）。
    json id = nullptr;
    try {
        json req = json::parse(line);
        id = req.contains("id") ? req["id"] : json(0);  // 保持成功路径“缺省 id=0”行为
        const std::string method = req.value("method", "");
        const json params = req.value("params", json::object());
        json result = handleMethod(s, method, params);
        // R14-项1：成功应答与错误应答同口径回带 id——直接用上方 json 承载的原值
        // （存在即原样、缺失为 0）。此前 req.value("id",0) 在 id 为字符串/浮点/null
        // 时抛 type_error.302，被外层 catch 把【成功】结果误报为 {"error":"异常:…"}。
        return json{{"id", id}, {"result", result}};
    } catch (const RpcError& e) {
        return json{{"id", id}, {"error", e.msg}};
    } catch (const std::exception& e) {
        return json{{"id", id}, {"error", std::string("异常: ") + e.what()}};
    }
}

void runStdioServer(Session& s) {
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        std::cout << serveLine(s, line).dump() << std::endl;
    }
}

#if !defined(_WIN32)
void runTcpServer(Session& s, int port) {
    // 本地加固（仅 127.0.0.1）：① 并发连接上限（无上限每连接一 detach 线程 = 本地资源耗尽面）；
    // ② 每连接行缓冲上限（无换行超大字节流会无限增长内存）。超限均为友好报错+断开，协议语义不变。
    constexpr int kMaxConn = 16;
    constexpr size_t kMaxLineBytes = 1u << 20;  // 1 MiB
    init_sockets();
    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        std::cerr << "socket 创建失败" << std::endl;
        return;
    }
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons((uint16_t)port);
    if (bind(fd, (sockaddr*)&addr, sizeof(addr)) < 0 || listen(fd, 4) < 0) {
        std::cerr << "bind/listen 失败（端口 " << port << "）" << std::endl;
        close(fd);
        return;
    }
    std::cerr << "[meshgen-cli] JSON-RPC 监听 tcp://127.0.0.1:" << port << std::endl;
    std::atomic<int> nConn{0};
    for (;;) {
        const int cfd = accept(fd, nullptr, nullptr);
        if (cfd < 0) continue;
        if (nConn.load() >= kMaxConn) {
            const std::string busy =
                json{{"error", std::string("连接数已达上限(") + std::to_string(kMaxConn) + ")，稍后重试"}}
                    .dump() + "\n";
            // 告警修（合并波 2(d)，mpi_fixes §8-5）：gcc warn_unused_result 不认 (void)
            // 强转——真消费返回值。尽力而为应答：写失败 = 对端已去，无可重试语义。
            const ssize_t wBusy = write(cfd, busy.data(), busy.size());
            (void)wBusy;
            close(cfd);
            continue;
        }
        nConn.fetch_add(1);
        std::thread([cfd, &s, &nConn] {
            std::string buf;
            char chunk[4096];
            for (;;) {
                const ssize_t n = read(cfd, chunk, sizeof(chunk));
                if (n <= 0) break;
                buf.append(chunk, (size_t)n);
                if (buf.size() > kMaxLineBytes) {
                    const std::string resp =
                        json{{"error", std::string("行超长(>") + std::to_string(kMaxLineBytes) +
                                           " 字节)，连接断开"}}
                            .dump() + "\n";
                    // 告警修（合并波 2(d)）：真消费返回值（同上：尽力而为应答）。
                    const ssize_t wResp = write(cfd, resp.data(), resp.size());
                    (void)wResp;
                    break;  // 断开，防无界缓冲
                }
                size_t pos;
                while ((pos = buf.find('\n')) != std::string::npos) {
                    const std::string line = buf.substr(0, pos);
                    buf.erase(0, pos + 1);
                    if (line.empty()) continue;
                    const std::string resp = serveLine(s, line).dump() + "\n";
                    ssize_t off = 0;
                    while (off < (ssize_t)resp.size()) {
                        const ssize_t w = write(cfd, resp.data() + off, resp.size() - off);
                        if (w <= 0) break;
                        off += w;
                    }
                }
            }
            close(cfd);
            nConn.fetch_sub(1);
        }).detach();
    }
}
#endif

// ---------------- 批处理 ----------------

int runBatch(int argc, char** argv) {
    Session s;
    std::string project;
    bool checkOnly = false, autoFix = false, refine = false, quiet = false, jsonOut = false;
    std::string foamDir, bmdPath, reportPath, savePath;
    struct SplitOp {
        int bid, axis, n;
    };
    std::vector<SplitOp> splits;

    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&](const char* what) -> std::string {
            if (i + 1 >= argc) {
                std::cerr << "缺少参数: " << what << std::endl;
                exit(2);
            }
            return argv[++i];
        };
        if (a == "--check") checkOnly = true;
        else if (a == "--auto-fix") autoFix = true;
        else if (a == "--refine") refine = true;
        else if (a == "--quiet") quiet = true;
        else if (a == "--json-out") jsonOut = true;
        else if (a == "--export-foam") foamDir = next("dir");
        else if (a == "--blockmesh-dict") bmdPath = next("path");
        else if (a == "--report") reportPath = next("path");
        else if (a == "--save") savePath = next("path");
        else if (a == "--split") {
            const std::string spec = next("bid:i|j|k:n");
            int bid = 0, n = 2;
            char ax = 'i';
            if (std::sscanf(spec.c_str(), "%d:%c:%d", &bid, &ax, &n) != 3) {
                std::cerr << "--split 语法: <bid>:<i|j|k>:<n>" << std::endl;
                return 2;
            }
            splits.push_back({bid, ax == 'j' ? 1 : ax == 'k' ? 2 : 0, n});
        } else if (a[0] == '-') {
            std::cerr << "未知选项: " << a << std::endl;
            return 2;
        } else {
            project = a;
        }
    }
    if (project.empty()) {
        std::cerr << "用法: meshgen-cli <project.json> [选项]  (或 --server)\n";
        return 2;
    }
    std::string err;
    if (!s.model.loadJsonFile(project, &err)) {
        std::cerr << "[错误] " << err << std::endl;
        return 1;
    }
    s.projectPath = project;
    auto log = [&](const std::string& msg) {
        if (!quiet) std::cout << msg << std::endl;
    };

    for (auto& sp : splits) {
        const auto ids = s.model.splitBlock(sp.bid, sp.axis, sp.n);
        log("[split] 块 " + std::to_string(sp.bid) + " → " + std::to_string(ids.size()) +
            " 块");
    }
    if (autoFix) {
        for (const auto& l : autoFixCouplings(s.model)) log("[auto-fix] " + l);
    }
    if (refine) {
        auto r = applyRefinement(s.model);
        for (const auto& l : r.log) log("[refine] " + l);
    }

    auto errs = s.model.validate();
    if (!errs.empty()) {
        for (const auto& e : errs) std::cerr << "[校验] " << e << std::endl;
        if (checkOnly) return 1;
    } else if (checkOnly) {
        log("[校验] 通过");
        return 0;
    }

    if (!generateMesh(s.model, s.mesh, err)) {
        std::cerr << "[错误] " << err << std::endl;
        return 1;
    }
    s.meshValid = true;
    QualityStats q = computeQuality(s.mesh);
    log("[网格] 点=" + std::to_string(s.mesh.points.size()) +
        " 单元=" + std::to_string(s.mesh.cells.size()) +
        " 内部面=" + std::to_string(s.mesh.nInternalFaces) + " 耗时=" +
        std::to_string(s.mesh.genMs) + "ms");
    char buf[256];
    std::snprintf(buf, sizeof(buf), "[质量] minSJ=%.4f avgSJ=%.4f 坏单元=%ld 最大非正交=%.2f°",
                  q.minSJ, q.avgSJ, q.nBadSJ, q.maxNonOrtho);
    log(buf);

    if (!foamDir.empty()) {
        if (!writeOpenFoamPolyMesh(s.mesh, foamDir, err)) {
            std::cerr << "[错误] " << err << std::endl;
            return 1;
        }
        log("[导出] OpenFOAM polyMesh → " + foamDir);
    }
    if (!bmdPath.empty()) {
        if (!writeBlockMeshDict(s.model, bmdPath, err)) {
            std::cerr << "[错误] " << err << std::endl;
            return 1;
        }
        log("[导出] blockMeshDict → " + bmdPath);
    }
    if (!savePath.empty()) {
        s.model.saveJsonFile(savePath, &err);
        log("[保存] " + savePath);
    }
    if (!reportPath.empty() || jsonOut) {
        json rep = meshReport(s, &q);
        rep["couplings"] = couplingsReportJson(s);
        rep["model"] = modelInfo(s);
        rep["simd"] = simd::backendName();
        rep["threads"] = globalPool().size();
        if (jsonOut) std::cout << rep.dump(2) << std::endl;
        if (!reportPath.empty()) {
            std::ofstream f(reportPath);
            f << rep.dump(2);
            log("[报告] " + reportPath);
        }
    }
    return 0;
}

void printUsage() {
    std::cerr <<
        R"(meshgen-cli — 多块结构网格生成器 + 笛卡尔 cut-cell 引擎（Agent native）
用法:
  meshgen-cli <project.json> [选项]
  meshgen-cli cartesian <config.json> [--export-foam dir] [--export-vtu file.vtu] [--dc-mode]
  meshgen-cli cfdrun <x.cfdrun> [--export-foam dir] [--export-vtu file.vtu] [--surface-dir dir] [--dc-mode]
                                [--dc-raw] [--dc-seeds pre|post] [--report file.json]
                                （--dc-seeds 缺省 pre=seedsPreNudge；post=POST-nudge 全集）
  meshgen-cli --server stdio            JSON-RPC（stdin/stdout，供 Agent 子进程接入）
  meshgen-cli --server tcp:<port>       JSON-RPC（TCP，默认建议 8787）
批处理选项（结构网格）:
  --check                  仅校验
  --auto-fix               自动匹配耦合（点数/布点律传播）
  --refine                 应用魔法笔加密区
  --split <bid>:<i|j|k>:<n> 切分块
  --export-foam <dir>      导出 OpenFOAM polyMesh
  --blockmesh-dict <path>  导出 blockMeshDict（近似）
  --save <path>            保存（可能修改过的）模型
  --report <path>          JSON 报告
  --json-out               报告输出到 stdout
  --quiet                  静默
cartesian 子命令:
  配置 JSON 支持新 schema（castellatedMeshControls + meshGenerator，镜像 snappyHexMeshDict）
  与旧式扁平字段（向后兼容）。导出同时落 AMR 契约文件（cellLevel/pointLevel/level0Edge
  + constant/dynamicMeshDict）。设计详见 docs/cartesian_design.md。
  --dc-mode（cartesian/cfdrun 通用）: dc17 DC 模式总开关——引擎侧（Step 2/3a：S7 报告
  不拒出 + 种子旁路 + side-node 洪泛 + E-A7 dc_report.json；cfdrun 加 --dc-raw 走磁盘
  solid 桥接）+ 出口侧（Step 8/任务书 S3）: DC 黄金 boundary 出口契约——
  VERBATIM patch 命名（无 wall_ 前缀）+ 类型按 ingest 分类 + baffle <zone>_master/_slave
  （穿孔 = cyclic 全元数据）+ faceZones 落盘 + 远场六侧按房间包络改名 + AMR extras 剥除。
  缺省 OFF ⇒ 输出逐字节不变。
)";
}

}  // namespace


int runCartesian(int argc, char** argv) {
#if defined(MESHGEN_WITH_MPI)
    // 生成前的线程池尺寸（仅诊断打印用）：MESHGEN_THREADS 的实际生效值。
    const size_t threadsAtEntry = meshgen::globalPool().size();
#endif
    // meshgen-cli cartesian <config.json> [--export-foam dir] [--export-vtu file.vtu]
    // 新 schema（SPEC §2.3）：castellatedMeshControls{...} + meshGenerator{...}；
    // 旧式顶层扁平字段保持向后兼容。--export-foam/--export-vtu 语义不变（命令行优先于 JSON 默认）。
    std::string cfgPath, exportDir = "case/logger/cartesian/constant/polyMesh", vtuPath;
    bool foamFlag = false, vtuFlag = false, dcModeFlag = false;
    for (int i = 2; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--export-foam" && i + 1 < argc) { exportDir = argv[++i]; foamFlag = true; }
        else if (a == "--export-vtu" && i + 1 < argc) { vtuPath = argv[++i]; vtuFlag = true; }
        else if (a == "--dc-mode") { dcModeFlag = true; }  // dc17 Step 1：CLI 优先于 JSON 键
        else if (a[0] != '-') cfgPath = a;
    }
    if (cfgPath.empty()) {
        CART_ROOT_BLOCK
        std::cerr << "用法: meshgen-cli cartesian <config.json> [--export-foam dir] [--export-vtu file.vtu] [--dc-mode]\n";
        return 2;
    }
    std::ifstream f(cfgPath);
    if (!f) { CART_ROOT_BLOCK std::cerr << "无法打开配置: " << cfgPath << "\n"; return 1; }
    json j;
    try {
        f >> j;
    } catch (const std::exception& e) {
        CART_ROOT_BLOCK
        std::cerr << "配置 JSON 解析失败: " << e.what() << "\n";
        return 1;
    }
    // 顶层非对象（数组/数/字符串等）在后续 value()/get<T> 会抛 type_error：友好报错
    if (!j.is_object()) {
        CART_ROOT_BLOCK
        std::cerr << "[cartesian] 配置 JSON 顶层必须是对象（实际类型: " << j.type_name() << "）\n";
        return 1;
    }
    // 装载后的取值/换算（j.value/get<T>）对类型错误会抛 nlohmann type_error——
    // 此前未捕获 → SIGABRT；与 serveLine 的兜底行为对齐，可读报错并 exit 1。
    try {
        TriSurface surf;
        std::string err;
        std::string surfPath = j.value("surface", "");
        bool ok = (surfPath.size() > 4 && surfPath.substr(surfPath.size() - 4) == ".stl")
                      ? surf.loadSTL(surfPath, err)
                      : surf.loadJSON(surfPath, err);
        if (!ok) { CART_ROOT_BLOCK std::cerr << err << "\n"; return 1; }
        // 防御纵深：任何装载器产物入引擎前再过一次索引/patch 校验
        if (!surf.validate(err)) { CART_ROOT_BLOCK std::cerr << "表面无效: " << err << "\n"; return 1; }
        // firstLayer 同步到 TriSurface：过渡期旧引擎仍读 surf.firstLayer；
        // 新引擎只读下面换算出的 cfg.surfaceLevels（弃用兼容，SPEC §2.3）
        if (j.contains("firstLayer") && j["firstLayer"].is_object()) {
            for (auto it = j["firstLayer"].begin(); it != j["firstLayer"].end(); ++it) {
                int pid = surf.patchId(it.key());
                if ((int)surf.firstLayer.size() <= pid) surf.firstLayer.resize(pid + 1, -1);
                surf.firstLayer[pid] = it.value().get<double>();
            }
        }
        // 新 schema 解析 + 兼容换算 + 校验提示（与 RPC cartesian.configure 同一实现）
        CartParseOutcome parsed = parseCartesianConfigJson(j, &surf);
        CartesianConfig cfg = std::move(parsed.cfg);
        // dc17 Step 1：--dc-mode 命令行旗标（优先于 JSON 的 dcMode 键；同 --export-foam
        // 命令行优先于 JSON 缺省的既有口径）。引擎自 Step 2 起读取；出口契约自 Step 8 起。
        // dc17 Step 8/S3：cartesian 子命令无 ingest 契约——出口只有 VERBATIM 命名 +
        // golden 版式 + faceZones；类型仍走 cfg.patchTypes/缺省推断。
        if (dcModeFlag) cfg.dcMode = true;
        // dc17 Step 5a-i ②：dcMode 下 stderr 行缓冲（防御性——glibc stderr 缺省即
        // 无缓冲；3b §6.1.1 "4KB 块缓冲吃尾行" 后被实证修正为"段行只在 END 打、
        // 段未完成故无行"，引擎侧 [DC-T] START 行 + 逐行 fflush 已根治。此行保证
        // 任何被流包装的 stderr 亦即时落地）。flag-OFF 不触及。
        if (dcModeFlag) std::setvbuf(stderr, nullptr, _IOLBF, 0);
        if (cfg.dcMode) {
            CART_ROOT_BLOCK
            std::printf("[dc] dcMode=ON（dc17 seeded-flood DC 模式；Step 3a：S7 报告不拒出 + 种子旁路 "
                        "+ side-node 洪泛 + E-A7 dc_report.json；Step 8：golden boundary 出口契约）\n");
        }
        // 配置解析告警：全秩同一配置 ⇒ 同一告警，只 rank0 打一份（stderr 红线口径）
        CART_ROOT_BLOCK
        for (const auto& w : parsed.warnings) std::cerr << "[cartesian] " << w << "\n";
        // JSON 默认导出路径仅在未显式给命令行参数时生效
        if (!foamFlag && !parsed.foamDefault.empty()) exportDir = parsed.foamDefault;
        if (!vtuFlag && !parsed.vtuDefault.empty()) vtuPath = parsed.vtuDefault;

        // PERF：CART_DBG_TIME=1 → stderr 打导出各步耗时（缺省零输出，stdout 不变）
        const bool dbgTime = (std::getenv("CART_DBG_TIME") != nullptr);
        using PerfClock = std::chrono::steady_clock;
        const auto perfNow = [] { return PerfClock::now(); };
        const auto perfMs = [](PerfClock::time_point a) {
            return std::chrono::duration<double, std::milli>(PerfClock::now() - a).count();
        };
        CartesianResult res;
        // 生成：全秩同一入口（MPI 构建 + mpirun 下走 §2 分布路径：树复制 + 逐叶场
        // 分布 + rank0 串行重放后半程；否则 = 原单进程路径，逐字节同一）
        if (!generateCartesian(surf, cfg, res, err)) {
            writeDcReportSidecar(cfg, res, exportDir);  // dc17 Step 2：失败路径也落报告
            CART_ROOT_BLOCK std::cerr << err << "\n";
            return 1;
        }
        writeDcReportSidecar(cfg, res, exportDir);      // dc17 Step 2：dcMode 报告 sidecar
        // 统计打印 + 全部落盘：只 rank0（模式 G 下非 rank0 的 res.mesh 为空，
        // 且多秩同写同一目录会互相覆写产物）。CART_ROOT_BLOCK 在 OFF 构建展开为空
        // ⇒ 下面的块只是普通作用域，行为与基线逐字一致。
        CART_ROOT_BLOCK {
        std::printf("[cartesian] 叶=%d 切割=%d 流体=%d 内部面=%d minVol=%.3g 耗时=%.1fms\n",
                    res.nLeaves, res.nCut, res.nFluid, res.mesh.nInternal, res.minFluidVol,
                    res.genMs);
#if defined(MESHGEN_WITH_MPI)
        // 线程/秩取【生成前】的池尺寸：分工点后 rank0 会把池扩回机器全核（§C.3-2），
        // 生成后再读会显示扩容值，掩盖 MESHGEN_THREADS 的实际配置。
        // M1b③：分布=按实果打印（distTailMode 由引擎在分工点/回退点置标签）：
        // 默认（DISTTAIL 未设/0）= "模式G(rank0 汇聚重放)" 与旧硬编码逐字一致；
        // DISTTAIL=1 真开 = 模式G-D；被 mode2d/诊断 env/S8-A2 中途回退拦下 = 带原因的模式G。
        if (CART_MPI_ON)
            std::printf("[cartesian] MPI: ranks=%d 分布=%s 线程/秩=%zu%s\n",
                        meshgen::mpi::size(), meshgen::mpi::distTailMode(), threadsAtEntry,
                        meshgen::mpi::size() > 1 ? "（分工点后 rank0 扩回全核）" : "");
#endif
        if (!res.cellLevel.empty())
            std::printf("[cartesian] AMR: level0Edge=%.6g cellLevel=%zu pointLevel=%zu\n",
                        res.level0Edge, res.cellLevel.size(), res.pointLevel.size());
        auto tFoam = perfNow();
        // dc17 Step 8/S3：dcMode → golden 版式 boundary + faceZones（cartesian 子命令无
        // ingest 契约，DcBoundaryOptions 只开总开关；类型走 cfg.patchTypes/缺省推断）
        DcBoundaryOptions dcOpts;
        dcOpts.dcMode = dcModeFlag;
        if (!writePolyMeshFoam(res.mesh, exportDir, err, patchTypeMap(cfg.patchTypes),
                               dcModeFlag ? &dcOpts : nullptr)) {
            std::cerr << err << "\n";
            return 1;
        }
        if (dcModeFlag)
            for (const auto& r : dcOpts.report) std::cerr << r << "\n";
        if (dbgTime) std::fprintf(stderr, "[TIME] export polyMesh %.1f ms\n", perfMs(tFoam));
        std::printf("[cartesian] 导出 OpenFOAM polyMesh → %s\n", exportDir.c_str());
        // AMR 契约文件（SPEC §2.4，T2 实现）：三场落 polyMesh 目录；
        // dynamicMeshDict 由 writeCartesianAmrFiles 一并落 constant/（polyMesh 的父目录，
        // 不是 system/ —— 01_U3 §5）。writeDynamicMeshDict 单独调用需完整文件路径，此处不必。
        // dcMode：剥除（任务书 S3 EXTRAS——golden polyMesh 只有五件套+zones）。
        if (dcModeFlag) {
            std::printf("[cartesian] dcMode: 跳过 AMR extras + dynamicMeshDict（golden parity）\n");
        } else {
            auto tAmr = perfNow();
            if (!writeCartesianAmrFiles(res, exportDir, err)) { std::cerr << err << "\n"; return 1; }
            if (dbgTime) std::fprintf(stderr, "[TIME] export AMR files %.1f ms\n", perfMs(tAmr));
            std::printf("[cartesian] 导出 AMR 场 (cellLevel/pointLevel/level0Edge) → %s\n",
                        exportDir.c_str());
            std::printf("[cartesian] 导出 dynamicMeshDict → %s/dynamicMeshDict\n",
                        parentDir(exportDir).c_str());
        }
        if (!vtuPath.empty()) {
            auto tVtu = perfNow();
            if (!meshgen::writePolyMeshVtu(res.mesh, vtuPath, err)) { std::cerr << err << "\n"; return 1; }
            if (dbgTime) std::fprintf(stderr, "[TIME] export VTU %.1f ms\n", perfMs(tVtu));
            std::printf("[cartesian] 导出 VTU (ParaView/pyvista) → %s\n", vtuPath.c_str());
        }
        }
        return 0;
    } catch (const nlohmann::json::exception& e) {
        CART_ROOT_BLOCK
        std::cerr << "[cartesian] 配置类型错误（字段类型不符，如数字被写成字符串）: "
                  << e.what() << "\n";
        return 1;
    } catch (const std::exception& e) {
        CART_ROOT_BLOCK
        std::cerr << "[cartesian] 异常: " << e.what() << "\n";
        return 1;
    }
}

// ---------------- T-CFDRUN：cfdrun 子命令（dcdtuni .cfdrun 摄入 + 生成 + 导出） ----------------
namespace {

// 合并装载 cfdrun 引用的全部 STL → 单一 TriSurface（patch 名 = region solidName）。
// baseDir = 清单所在目录（STL 相对路径据此解析）；surfaceDirOverride 非空则优先。
// 单 solid 的 STL 被 loadSTL 归一命名为 "wall"——若与引用它的 region solidName 不符，
// 改名回该 solidName（保证 surfaceLevels/baffles 的 region 键匹配 TriSurface patchName）。
bool loadCfdrunSurface(const CfdrunSpec& spec, const std::string& baseDir,
                       const std::string& surfaceDirOverride, TriSurface& out, std::string& err) {
    out = TriSurface{};
    if (spec.stlFiles.empty()) {
        err = "cfdrun 未引用任何 STL 文件，无法装载表面（该清单可能只含 domain/级；"
              "如需单表面请改用 cartesian 子命令）";
        return false;
    }
    for (const auto& stlRel : spec.stlFiles) {
        std::string path = stlRel;
        const bool abs = !path.empty() && path[0] == '/';
        if (!abs) {
            const std::string& base = surfaceDirOverride.empty() ? baseDir : surfaceDirOverride;
            path = base.empty() ? stlRel : (base + "/" + stlRel);
        }
        // T-CFDRUN 适配（DC 四案）：清单引用的 STL 缺失 ⇒ 告警跳过（DC runner 会重排
        // triSurface 文件名，如 merged_baffle.stl → merged_wall_baffle.stl；适配清单/
        // 域面映射的 region 允许无对应几何）。全部缺失仍按空表面硬失败。
        if (!std::ifstream(path).good()) {
            CART_ROOT_BLOCK
            std::cerr << "[cfdrun] warn: STL 缺失(" << path << ")——引用它的 region 无几何跳过\n";
            continue;
        }
        TriSurface part;
        if (!part.loadSTL(path, err)) { err = "STL 装载失败(" + path + "): " + err; return false; }
        if (!part.validate(err)) { err = "STL 无效(" + path + "): " + err; return false; }
        // 引用该 STL 的 region solidName 集合
        std::vector<std::string> wantSolids;
        for (const auto& r : spec.regions)
            if (r.stlFile == stlRel) wantSolids.push_back(r.solidName);
        // 单 solid（loadSTL 命名 "wall"）且有 region 声明、名字不符 → 改名为首个 region solidName
        if (part.patchNames.size() == 1 && !wantSolids.empty() &&
            std::find(wantSolids.begin(), wantSolids.end(), part.patchNames[0]) == wantSolids.end())
            part.patchNames[0] = wantSolids[0];
        // 合并：顶点/三角形索引偏移 voff；patch 按名去重合并（pmap）
        const int voff = (int)out.verts.size();
        for (const auto& v : part.verts) out.verts.push_back(v);
        std::vector<int> pmap(part.patchNames.size(), -1);
        for (size_t p = 0; p < part.patchNames.size(); ++p) {
            int found = -1;
            for (size_t q = 0; q < out.patchNames.size(); ++q)
                if (out.patchNames[q] == part.patchNames[p]) { found = (int)q; break; }
            if (found < 0) {
                out.patchNames.push_back(part.patchNames[p]);
                found = (int)out.patchNames.size() - 1;
            }
            pmap[p] = found;
        }
        for (const auto& t : part.tris) out.tris.push_back({t[0] + voff, t[1] + voff, t[2] + voff});
        for (size_t i = 0; i < part.patch.size(); ++i)
            out.patch.push_back(pmap[(size_t)part.patch[i]]);
    }
    // region → solid 覆盖核对：声明的 region 在合并表面里无对应 patchName ⇒ 告警
    // （该 region 不产生 patch；域面映射的墙/地板或几何缺失属预期，见适配文档）
    for (const auto& r : spec.regions) {
        if (std::find(out.patchNames.begin(), out.patchNames.end(), r.solidName) ==
            out.patchNames.end()) {
            CART_ROOT_BLOCK
            std::cerr << "[cfdrun] warn: region " << r.name << "（solid " << r.solidName
                      << "）在 STL 中无对应 solid——无 patch 几何（域面映射/缺失跳过）\n";
        }
    }
    out.firstLayer.assign(out.patchNames.size(), -1.0);
    if (out.tris.empty()) { err = "合并表面为空（无三角形）"; return false; }
    return true;
}

// ---------------- dc17 Step 3a（deliverable 1）：dc-raw 映射 → 引擎 TriSurface 桥 ----------------
// 把 DcRawMapping 的逐磁盘 solid 记录装配成单一 TriSurface：
//  · patch 名 = record.patchName（baffle zone 折叠 = snappy 合并语义；非 baffle =
//    geometryExport.patchName 优先否则 solidName 逐字）⇒ 与 map.cfg 的 surfaceLevels/
//    patchTypes/baffles 键【同名对齐】（旧 region-key 摄入的失配根因就此消除）；
//  · 单 solid 文件的 loadSTL "wall" 归一名 → 扫描器记录的真实 solid 名；
//  · 跨文件同名 solid 合并为同 patch（重复名风险，approach1 risks #7）：level/barrier
//    冲突取首 record + [dc-raw] 告警（loud，不静默）；
//  · dcPatchBarrier 按 patch 装配（record.floodBarrier；delivery A2 角色消费）；
//  · 全零三角文件（每个 solid 块零 facet，loadSTL 因此返回 false）→ 逐 solid loud 告警后
//    跳过而非拒载（镜像经典装载对空 solid 块的容忍；step3b §4.1 Polygon_Room 修复）。
//    含任一非零 record 却装载失败的文件仍按损坏硬失败（不掩盖真畸形）。
// 确定性：文件名字典序（scanTriSurfaceDir 口径）× 文件内 solid 序 × loadSTL 三角序。
// nSkippedZeroTriangle（out）：本次桥接因"全零三角文件"而跳过的 solid 计数（step3b §4.1
// 修复的机器可读账面；与 [dc-raw] skippedZeroTriangle 摘要行同源同口径）。
bool buildDcRawSurface(const DcRawMapping& map, const std::string& surfaceDir, TriSurface& out,
                       std::vector<uint8_t>& barrierByPatch, long& nSkippedZeroTriangle,
                       long& nRoleConflicts, std::string& err) {
    out = TriSurface{};
    barrierByPatch.clear();
    nSkippedZeroTriangle = 0;
    nRoleConflicts = 0;  // dc17 Step 5a-ii：角色表冲突计数（首记录优先覆盖次数，静默通道 #6）
    // record 按 stlFile 分组（map.solids 已按文件名字典序+文件内序 ⇒ 首现序即确定序）
    std::vector<std::string> files;
    std::map<std::string, std::vector<const DcRawSolidRecord*>> byFile;
    for (const auto& r : map.solids) {
        if (!byFile.count(r.stlFile)) files.push_back(r.stlFile);
        byFile[r.stlFile].push_back(&r);
    }
    for (const auto& stlFile : files) {
        const std::string path = surfaceDir + "/" + stlFile;
        if (!std::ifstream(path).good()) {
            CART_ROOT_BLOCK
            std::cerr << "[dc-raw] warn: STL 缺失(" << path << ")——该文件固体无几何跳过\n";
            continue;
        }
        TriSurface part;
        if (!part.loadSTL(path, err)) {
            // 经典装载容忍语义镜像（step3b §4.1，Polygon_Room INGEST-REFUSAL 修复）：
            // loadSTL 对"每个 solid 块都零 facet"的 ASCII STL 返回 false（parsed.empty()，
            // Cartesian.cpp:1033）——但它并非损坏，而是"清单声明、磁盘零几何"的空表面。
            // 扫描器已把这些 solid 记为 triCount==0；故一个文件的 record 全为零三角 ⇒
            // 无几何即无屏障，逐 solid 响亮告警后跳过（对齐经典非 dc-raw 路径对空 solid
            // 块的容忍），而不是拒载整次摄入。若该文件存在任一非零 record 却仍装载失败，
            // 则是真正的损坏/畸形 ⇒ 照旧硬失败（不掩盖）。
            const auto& zrecs = byFile[stlFile];
            bool allZero = !zrecs.empty();
            for (const auto* r : zrecs)
                if (r->triCount != 0) { allZero = false; break; }
            if (!allZero) { err = "STL 装载失败(" + path + "): " + err; return false; }
            for (const auto* r : zrecs) {
                ++nSkippedZeroTriangle;
                CART_ROOT_BLOCK
                std::cerr << "[dc-raw] warn: zero-triangle solid " << r->solidName << " in "
                          << stlFile << " skipped — declared patch will be zero-face or absent\n";
            }
            continue;  // 零几何文件不贡献任何三角形/patch（其声明 patch 将零面或缺席）
        }
        if (!part.validate(err)) { err = "STL 无效(" + path + "): " + err; return false; }
        const auto& recs = byFile[stlFile];
        // 单 solid 文件：loadSTL 归一名 "wall" → 扫描器记录的真实 solid 名
        if (part.patchNames.size() == 1 && !recs.empty() && part.patchNames[0] != recs[0]->solidName)
            part.patchNames[0] = recs[0]->solidName;
        // part patch（=solidName）→ record（同文件内按 solidName 匹配）
        for (size_t p = 0; p < part.patchNames.size(); ++p) {
            const DcRawSolidRecord* rec = nullptr;
            for (const auto* r : recs)
                if (r->solidName == part.patchNames[p]) { rec = r; break; }
            if (!rec) {
                CART_ROOT_BLOCK
                std::cerr << "[dc-raw] warn: " << stlFile << " 内 solid " << part.patchNames[p]
                          << " 无扫描记录（装载/扫描口径分歧）——按 solidName 直通\n";
            }
            const std::string target = rec ? rec->patchName : part.patchNames[p];
            // 合并入 out（按 target 名去重；跨文件同名 = 同 patch 合并）
            int found = -1;
            for (size_t q = 0; q < out.patchNames.size(); ++q)
                if (out.patchNames[q] == target) { found = (int)q; break; }
            if (found < 0) {
                found = (int)out.patchNames.size();
                out.patchNames.push_back(target);
                barrierByPatch.push_back(rec ? (uint8_t)(rec->floodBarrier ? 1 : 0) : (uint8_t)1);
            } else if (rec && (barrierByPatch[(size_t)found] ? 1 : 0) != (rec->floodBarrier ? 1 : 0)) {
                ++nRoleConflicts;  // dc17 Step 5a-ii：首记录优先覆盖 = 静默通道 #6 计数
                CART_ROOT_BLOCK
                std::cerr << "[dc-raw] warn: patch " << target << " 的 floodBarrier 声明冲突（"
                          << stlFile << "：" << (int)rec->floodBarrier << " vs 首记录 "
                          << (int)barrierByPatch[(size_t)found] << "）——取首记录\n";
            }
            part.patchNames[p] = target;  // 供下方 pmap
        }
        // 顶点/三角形/patch 合并（loadCfdrunSurface 同款 pmap 口径）
        const int voff = (int)out.verts.size();
        for (const auto& v : part.verts) out.verts.push_back(v);
        std::vector<int> pmap(part.patchNames.size(), -1);
        for (size_t p = 0; p < part.patchNames.size(); ++p) {
            for (size_t q = 0; q < out.patchNames.size(); ++q)
                if (out.patchNames[q] == part.patchNames[p]) { pmap[p] = (int)q; break; }
        }
        for (const auto& t : part.tris) out.tris.push_back({t[0] + voff, t[1] + voff, t[2] + voff});
        for (size_t i = 0; i < part.patch.size(); ++i)
            out.patch.push_back(pmap[(size_t)part.patch[i]]);
    }
    out.firstLayer.assign(out.patchNames.size(), -1.0);
    if (out.tris.empty()) { err = "[dc-raw] 桥接表面为空（无三角形）"; return false; }
    return true;
}

// 房间壳 bbox（E-A7 报告的 kept 体积内/外拆分用；仅报告，不参与任何谓词）。
// V2: meshGeometry.room.{plane,height}；V1: room.{plane,height}。plane = [{x,y}…] 多边形。
// DC room 轴序交叉（ingest-log 已知告警族）：plane 第二轴可能是引擎 y（深度）也可能是
// z（高度），height 对应另一轴——两个候选取向都构造，由调用方按 domain box 尺寸择一。
struct DcRoomPlaneBBox {
    double u0, v0, u1, v1, h;  // plane bbox (u,v) + height
};
static bool dcRoomPlaneFromManifest(const std::string& cfdrunPath, DcRoomPlaneBBox& out) {
    std::ifstream f(cfdrunPath);
    if (!f) return false;
    nlohmann::json j;
    try { f >> j; } catch (...) { return false; }
    CfdrunGeometryView view;
    std::string error;
    if (!CfdrunGeometryView::select(j, view, error)) return false;
    const auto& geometry = view.geometryOrRoot();
    const nlohmann::json* room = geometry.contains("room") ? &geometry["room"] : nullptr;
    if (!room || !room->contains("plane") || !(*room)["plane"].is_array()) return false;
    double x0 = 0, y0 = 0, x1 = 0, y1 = 0;
    bool first = true;
    for (const auto& pt : (*room)["plane"]) {
        if (!pt.contains("x") || !pt.contains("y")) return false;
        const double x = pt["x"].get<double>(), y = pt["y"].get<double>();
        if (first) { x0 = x1 = x; y0 = y1 = y; first = false; }
        else { x0 = std::min(x0, x); x1 = std::max(x1, x); y0 = std::min(y0, y); y1 = std::max(y1, y); }
    }
    if (first) return false;
    double h = 0;
    if (room->contains("height") && (*room)["height"].is_number()) h = (*room)["height"].get<double>();
    if (h <= 0) return false;
    out = DcRoomPlaneBBox{x0, y0, x1, y1, h};
    return true;
}

// 映射报告账面（step3b §4.1）：统计"全零三角文件"里被 dc-raw 桥跳过（而非拒载）的 solid 数。
// 纯从扫描器 record（map.solids 的 triCount）算，无 I/O，与 buildDcRawSurface 的跳过判据同口径
// （一个文件的 record 全为 triCount==0 ⇒ 整文件零几何 ⇒ 跳过）。确定序无关（只计数）。
// 该计数在 writeDcRawMappingJson 之后、dcMode 分支之前打印，故 --dc-raw 纯映射模式（不跑桥）
// 也如实上报（never silently）；--dc-mode 下 buildDcRawSurface 另发逐 solid 的 loud 告警。
static long dcRawCountZeroTriangleSolids(const DcRawMapping& map) {
    std::map<std::string, std::vector<const DcRawSolidRecord*>> byFile;
    for (const auto& r : map.solids) byFile[r.stlFile].push_back(&r);
    long n = 0;
    for (const auto& kv : byFile) {
        const auto& recs = kv.second;
        bool allZero = !recs.empty();
        for (const auto* r : recs)
            if (r->triCount != 0) { allZero = false; break; }
        if (allZero) n += (long)recs.size();
    }
    return n;
}

}  // namespace

// --dc-raw 也可由清单顶层键 dcRaw:true / dcMode:"raw"|"dc-raw" 触发（便利；CLI 旗优先）。
static bool dcRawFromManifest(const std::string& path) {
    std::ifstream f(path);
    if (!f) return false;
    nlohmann::json j;
    try { f >> j; } catch (...) { return false; }
    if (!j.is_object()) return false;
    if (j.contains("dcRaw") && j["dcRaw"].is_boolean() && j["dcRaw"].get<bool>()) return true;
    if (j.contains("dcMode") && j["dcMode"].is_string()) {
        const std::string m = j["dcMode"].get<std::string>();
        if (m == "raw" || m == "dc-raw" || m == "dcRaw") return true;
    }
    return false;
}

int runCfdrun(int argc, char** argv) {
    // meshgen-cli cfdrun <x.cfdrun> [--export-foam dir] [--export-vtu file] [--surface-dir dir]
    //             [--dc-mode] [--dc-raw] [--dc-seeds pre|post] [--report file.json] [--dump-contract file.json]
    std::string cfdrunPath, exportDir = "case/logger/cfdrun/constant/polyMesh", vtuPath, surfaceDir;
    std::string reportPath;
    bool dcModeFlag = false;
    bool dcRaw = false;
    // dc17 种子口径开关（impl log §8.7 合并笔记的"单行切换点"，dc17-ingest 585572f
    // 合并后定案）：DcRawMapping 落两套种子——seedsSynthesized（POST-nudge，DC 精确
    // 对齐；cfg.keepPoints 即其点集）与 seedsPreNudge（PRE-nudge 几何核心——nudge 是
    // DC 对齐网格 snappy findCell 的 workaround，与本引擎 dyadic 八叉树无关）。
    //   pre（缺省）→ map.seedsPreNudge（引擎干净口径，seed_parity 文档 §10 推荐）；
    //   post        → map.cfg.keepPoints（POST-nudge 全集，DC 忠实口径）。
    std::string dcSeeds = "pre";
    for (int i = 2; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--export-foam" && i + 1 < argc) exportDir = argv[++i];
        else if (a == "--export-vtu" && i + 1 < argc) { vtuPath = argv[++i]; }
        else if (a == "--surface-dir" && i + 1 < argc) surfaceDir = argv[++i];
        else if (a == "--dc-mode") dcModeFlag = true;  // dc17 Step 1（清单键 Step 4 再接）
        else if ((a == "--report" || a == "--dump-contract") && i + 1 < argc) reportPath = argv[++i];
        else if (a == "--dc-raw") dcRaw = true;
        else if (a == "--dc-seeds" && i + 1 < argc) dcSeeds = argv[++i];
        else if (a[0] != '-') cfdrunPath = a;
    }
    if (cfdrunPath.empty()) {
        CART_ROOT_BLOCK
        std::cerr << "用法: meshgen-cli cfdrun <x.cfdrun> [--export-foam dir] "
                     "[--export-vtu file] [--surface-dir dir] [--dc-mode] [--dc-raw] "
                     "[--dc-seeds pre|post] [--report file.json]\n";
        return 2;
    }
    if (!dcRaw) dcRaw = dcRawFromManifest(cfdrunPath);

    // ---------------- --dc-raw：RAW DC 摄入（磁盘扫描为主 + cfdrun 分类），不网格化 ----------------
    // 引擎 open-shell 模式（W2）另行落地；本路径只产出映射完备性报告（STEP 4 验收门）。
    if (dcRaw) {
        // surfaceDir 缺省 = <清单目录>/../Mesh/constant/triSurface（DC 算例布局）
        std::string sdir = surfaceDir;
        if (sdir.empty()) {
            std::string baseDir;
            const size_t sl = cfdrunPath.find_last_of('/');
            if (sl != std::string::npos) baseDir = cfdrunPath.substr(0, sl);
            sdir = (baseDir.empty() ? std::string(".") : baseDir) + "/../Mesh/constant/triSurface";
        }
        DcRawMapping map;
        std::string err;
        if (!ingestDcRaw(cfdrunPath, sdir, map, err)) {
            CART_ROOT_BLOCK std::cerr << err << "\n";
            return 1;
        }
        CART_ROOT_BLOCK
        for (const auto& w : map.warnings) std::cerr << w << "\n";
        // 报告路径缺省：case/logger/dc17_ingest/<清单茎>_mapping.json
        std::string rp = reportPath;
        if (rp.empty()) {
            std::string stem = cfdrunPath;
            const size_t sl = stem.find_last_of('/');
            if (sl != std::string::npos) stem = stem.substr(sl + 1);
            const size_t dot = stem.find('.');
            if (dot != std::string::npos) stem = stem.substr(0, dot);
            rp = "case/logger/dc17_ingest/" + stem + "_mapping.json";
        }
        std::string werr;
        if (!writeDcRawMappingJson(map, rp, werr)) {
            CART_ROOT_BLOCK std::cerr << werr << "\n";
            return 1;
        }
        // 人读摘要（stderr，稳定 [dc-raw] 前缀）
        std::map<std::string, long> byRole;
        for (const auto& r : map.solids) byRole[r.role]++;
        CART_ROOT_BLOCK {
            std::cerr << "[dc-raw] case=" << map.name << " manifest=" << map.manifestPath << "\n";
            std::cerr << "[dc-raw] surfaceDir=" << map.surfaceDir << "\n";
            std::cerr << "[dc-raw] domain hasBox=" << (int)map.cfg.hasBox << " box=(" << map.cfg.boxMin.x
                      << "," << map.cfg.boxMin.y << "," << map.cfg.boxMin.z << ")-(" << map.cfg.boxMax.x
                      << "," << map.cfg.boxMax.y << "," << map.cfg.boxMax.z << ") baseLevel="
                      << map.cfg.baseLevel << " maxLevel=" << map.cfg.maxLevel << "\n";
            std::cerr << "[dc-raw] keepPoints=" << map.cfg.keepPoints.size()
                      << " refinementZones=" << map.refineZones.size()
                      << " baffleZones=" << map.baffleZones.size() << "\n";
            std::cerr << "[dc-raw] solids onDisk=" << map.nSolidsOnDisk
                      << " boundToManifest=" << map.nBoundManifest
                      << " stlOnlyClassified=" << map.nStlOnlyClassified
                      << " UNEXPLAINED=" << map.nUnexplained << "\n";
            std::cerr << "[dc-raw] roles:";
            for (const auto& kv : byRole) std::cerr << " " << kv.first << "=" << kv.second;
            std::cerr << "\n";
            // 零三角 solid 账面（step3b §4.1）：dc-raw 桥对"全零 facet 文件"跳过而非拒载，
            // 与经典装载容忍语义对齐。计数 + 逐 solid 明细在此如实上报（never silently），
            // 纯映射模式（无 --dc-mode，不跑桥）亦可见；机器可读键 = skippedZeroTriangle。
            const long nZT = dcRawCountZeroTriangleSolids(map);
            std::cerr << "[dc-raw] skippedZeroTriangle=" << nZT;
            if (nZT > 0) {
                std::cerr << " (全零 facet STL 文件内的声明 solid——无几何即无屏障，dc-raw 桥跳过"
                             "而非拒载；其声明 patch 将零面或缺席):\n";
                std::map<std::string, std::vector<const DcRawSolidRecord*>> zByFile;
                std::vector<std::string> zFiles;
                for (const auto& r : map.solids) {
                    if (!zByFile.count(r.stlFile)) zFiles.push_back(r.stlFile);
                    zByFile[r.stlFile].push_back(&r);
                }
                for (const auto& f : zFiles) {
                    const auto& recs = zByFile[f];
                    bool allZero = !recs.empty();
                    for (const auto* r : recs)
                        if (r->triCount != 0) { allZero = false; break; }
                    if (!allZero) continue;
                    for (const auto* r : recs)
                        std::cerr << "[dc-raw]   zero-triangle solid " << r->solidName << " in "
                                  << f << "\n";
                }
            } else {
                std::cerr << "\n";
            }
            if (map.nUnexplained > 0) {
                std::cerr << "[dc-raw] INFEASIBLE: " << map.nUnexplained
                          << " 个 solid 未能解释（bbox 兜底弱信号）——如实上报，未静默丢弃:\n";
                long shown = 0;
                for (const auto& r : map.solids)
                    if (!r.explained && shown < 40) {
                        std::cerr << "[dc-raw]   " << r.solidName << " (" << r.stlFile << ")\n";
                        ++shown;
                    }
            }
            std::cerr << "[dc-raw] mapping report → " << rp << "\n";
            if (!dcModeFlag) {
                std::cerr << "[dc-raw] 网格化推迟到引擎 open-shell 模式（W2）——本步不生成网格"
                             "（任务书 S2 gate c）\n";
                return 0;
            }
        }
        {
            // ---------------- dc17 Step 3a（deliverable 1）：--dc-mode --dc-raw 组合路径 ----------------
            // dc-raw 映射 → TriSurface（全磁盘 solid、per-patch 名/级/角色）+ CartesianConfig
            // → dcMode 引擎跑。E-A10：'引擎尚未读取该旗标' 回显自本步起失实——引擎已读
            // （S7 报告/种子旁路/Step 3a side-node 洪泛）。
            CART_ROOT_BLOCK {
                std::printf("[dc] dcMode=ON + dc-raw 桥接（dc17 Step 3a：side-node 洪泛 + "
                            "角色消费 + E-A7 报告）\n");
            }
            TriSurface surfRaw;
            std::vector<uint8_t> barrierByPatch;
            long nSkippedZT = 0;
            long nRoleConflicts = 0;
            std::string berr;
            if (!buildDcRawSurface(map, sdir, surfRaw, barrierByPatch, nSkippedZT,
                                   nRoleConflicts, berr)) {
                CART_ROOT_BLOCK std::cerr << berr << "\n";
                return 1;
            }
            if (nSkippedZT > 0)
                CART_ROOT_BLOCK std::cerr << "[dc-raw] bridge skipped " << nSkippedZT
                                          << " zero-triangle solid(s); surface assembled from "
                                             "remaining geometry（无几何即无屏障，洪泛继续）\n";
            CartesianConfig cfgR = map.cfg;
            cfgR.dcMode = true;
            cfgR.dcPatchBarrier = barrierByPatch;
            // 种子口径（--dc-seeds pre|post；585572f 已并入 ⇒ 两档取值不同，见旗标解析处注）
            if (dcSeeds != "pre" && dcSeeds != "post") {
                CART_ROOT_BLOCK std::cerr << "[dc-raw] --dc-seeds 仅接受 pre|post（收到 '"
                                          << dcSeeds << "'）\n";
                return 2;
            }
            if (dcSeeds == "pre") {
                // PRE-nudge 几何核心（引擎缺省）：keepPoints ← seedsPreNudge 点集
                cfgR.keepPoints.clear();
                for (const auto& s : map.seedsPreNudge) cfgR.keepPoints.push_back(s.point);
                CART_ROOT_BLOCK std::cerr << "[dc-raw] --dc-seeds pre: keepPoints="
                                          << cfgR.keepPoints.size() << "（seedsPreNudge）\n";
            } else {
                // POST-nudge 全集（DC 忠实）：map.cfg.keepPoints 原样（= seedsSynthesized 点集）
                CART_ROOT_BLOCK std::cerr << "[dc-raw] --dc-seeds post: keepPoints="
                                          << cfgR.keepPoints.size()
                                          << "（seedsSynthesized，POST-nudge）\n";
            }
            DcRoomPlaneBBox rpb;
            if (dcRoomPlaneFromManifest(cfdrunPath, rpb)) {
                // 取向选择（仅报告口径）：候选 A = plane(u,v)→引擎(x,y)、height→z；
                // 候选 B = plane u→x、height→y、v→z。取尺寸与 domain box 相容者
                //（room ⊆ padded box）；双相容/双不相容 ⇒ A 优先 + 告警（loud）。
                const Vec3 bsz = cfgR.boxMax - cfgR.boxMin;
                auto fits = [&](double ex, double ey, double ez) {
                    return cfgR.hasBox && ex <= bsz.x && ey <= bsz.y && ez <= bsz.z;
                };
                const bool okA = fits(rpb.u1 - rpb.u0, rpb.v1 - rpb.v0, rpb.h);
                const bool okB = fits(rpb.u1 - rpb.u0, rpb.h, rpb.v1 - rpb.v0);
                if (okA || !okB) {
                    cfgR.dcRoomBBoxMin = Vec3{rpb.u0, rpb.v0, 0};
                    cfgR.dcRoomBBoxMax = Vec3{rpb.u1, rpb.v1, rpb.h};
                } else {
                    cfgR.dcRoomBBoxMin = Vec3{rpb.u0, 0, rpb.v0};
                    cfgR.dcRoomBBoxMax = Vec3{rpb.u1, rpb.h, rpb.v1};
                }
                if (okA == okB)
                    CART_ROOT_BLOCK std::cerr << "[dc-raw] warn: room 轴序取向双候选同判（A="
                                              << (int)okA << "）——按 A 优先，报告口径核对\n";
                cfgR.dcHasRoomBBox = true;
            } else {
                CART_ROOT_BLOCK std::cerr << "[dc-raw] warn: 清单无 room.plane/height——"
                                             "kept 体积内/外拆分降级为无房间 bbox 口径\n";
            }
            CART_ROOT_BLOCK {
                long nBarrier = 0;
                for (uint8_t b : cfgR.dcPatchBarrier) nBarrier += b;
                std::printf("[dc-raw] 桥接表面: patches=%zu tris=%zu barrier=%ld transparent=%ld "
                            "keepPoints=%zu roomBBox=%d\n",
                            surfRaw.patchNames.size(), surfRaw.tris.size(), nBarrier,
                            (long)cfgR.dcPatchBarrier.size() - nBarrier,
                            cfgR.keepPoints.size(), (int)cfgR.dcHasRoomBBox);
                // dc17 Step 5a-ii（E1b）：角色表冲突普查（NEW 行，既有桥接表面行字节不动）。
                // roleConflicts>0 = 某 patch 跨文件 floodBarrier 声明冲突、首记录优先覆盖
                // （静默通道 #6 / 假设 4 机理）；0 = 角色表无冲突（coldAisle/DAML 实测 0）。
                std::printf("[dc-raw] roleTable: patches=%zu roleConflicts=%ld\n",
                            surfRaw.patchNames.size(), nRoleConflicts);
            }
            CartesianResult resR;
            std::string rerr;
            if (!generateCartesian(surfRaw, cfgR, resR, rerr)) {
                writeDcReportSidecar(cfgR, resR, exportDir);  // 失败路径也落报告（证据链）
                CART_ROOT_BLOCK std::cerr << rerr << "\n";
                return 1;
            }
            writeDcReportSidecar(cfgR, resR, exportDir);
            CART_ROOT_BLOCK {
                std::printf("[cfdrun] name=%s 叶=%d 切割=%d 流体=%d 内部面=%d 耗时=%.1fms\n",
                            map.name.c_str(), resR.nLeaves, resR.nCut, resR.nFluid,
                            resR.mesh.nInternal, resR.genMs);
                if (!writePolyMeshFoam(resR.mesh, exportDir, rerr,
                                       patchTypeMap(cfgR.patchTypes))) {
                    std::cerr << rerr << "\n";
                    return 1;
                }
                if (!writeCartesianAmrFiles(resR, exportDir, rerr)) {
                    std::cerr << rerr << "\n";
                    return 1;
                }
                std::printf("[cfdrun] 导出 OpenFOAM polyMesh → %s\n", exportDir.c_str());
                if (!vtuPath.empty()) {
                    if (!writePolyMeshVtu(resR.mesh, vtuPath, rerr)) {
                        std::cerr << rerr << "\n";
                        return 1;
                    }
                    std::printf("[cfdrun] 导出 VTU → %s\n", vtuPath.c_str());
                }
                std::vector<long> nFaces(resR.mesh.patchNames.size(), 0);
                for (size_t f = 0; f < resR.mesh.faces.size(); ++f)
                    if (resR.mesh.neighbour[f] < 0 && resR.mesh.facePatch[f] >= 0 &&
                        resR.mesh.facePatch[f] < (int)nFaces.size())
                        nFaces[(size_t)resR.mesh.facePatch[f]]++;
                const std::map<std::string, std::string> ptm = patchTypeMap(cfgR.patchTypes);
                std::printf("[cfdrun] boundary patches (%zu):\n", resR.mesh.patchNames.size());
                for (size_t i = 0; i < resR.mesh.patchNames.size(); ++i)
                    std::printf("    %-28s type=%-14s nFaces=%ld\n",
                                resR.mesh.patchNames[i].c_str(),
                                foamPatchTypeWith(resR.mesh.patchNames[i], ptm).c_str(), nFaces[i]);
            }
            return 0;
        }
        return 0;
    }

    CfdrunSpec spec;
    std::string err;
    if (!loadCfdrunFile(cfdrunPath, spec, err)) {
        CART_ROOT_BLOCK std::cerr << "[cfdrun] " << err << "\n";
        return 1;
    }
    CART_ROOT_BLOCK
    for (const auto& w : spec.warnings) std::cerr << "[cfdrun] " << w << "\n";

    // baseDir = 清单所在目录（STL 相对路径解析基准）
    std::string baseDir;
    const size_t slash = cfdrunPath.find_last_of('/');
    if (slash != std::string::npos) baseDir = cfdrunPath.substr(0, slash);

    TriSurface surf;
    if (!loadCfdrunSurface(spec, baseDir, surfaceDir, surf, err)) {
        CART_ROOT_BLOCK std::cerr << "[cfdrun] " << err << "\n";
        return 1;
    }
    CartesianConfig cfg = spec.cfg;
    // dc17 Step 1：cfdrun 子命令的 --dc-mode 旗标（.cfdrun 清单键覆盖留待 Step 4 的
    // parseCfdrun 重写一并接入；CLI 置位 + 回显）。引擎自 Step 2/3a 起读取（S7 报告 +
    // side-node 洪泛）；出口契约自 Step 8 起（下块）。cfg 保持非 const（两侧都注入钩子）。
    if (dcModeFlag) cfg.dcMode = true;
    // dc17 Step 5a-i ②：dcMode 下 stderr 行缓冲（同 runCartesian 侧注记；flag-OFF
    // 不触及——OFF 字节红线零影响）。
    if (dcModeFlag) std::setvbuf(stderr, nullptr, _IOLBF, 0);
    if (cfg.dcMode) {
        CART_ROOT_BLOCK
        std::printf("[dc] dcMode=ON（dc17 seeded-flood DC 模式；Step 3a：S7 报告不拒出 + 种子旁路 "
                        "+ side-node 洪泛 + E-A7 dc_report.json；Step 8：golden boundary 出口契约）\n");
    }

    // ---------------- dc17 Step 8 / 任务书 S3：--dc-mode 出口契约（缺省 OFF ⇒ 逐字节不变） ----
    // 命名/类型/baffle 对/faceZones/远场命运全部由契约驱动：ingest 分类（磁盘扫描为主）+
    // 清单 domain-face 包络映射 + OFF baffles 基（保 G3 几何同构）。出口层几何语义零改动；
    // 注意：合并 Step 3a 后【引擎侧】洪泛会改变 ON 路径面集（31_dc17_export_log.md §4.3
    // 第 1 条预言的场景已发生——G3 口径按 impl log 合并节的 post-flood 重锚执行）。
    DcExportContract dcContract;
    std::map<std::string, std::string> exportTypes = patchTypeMap(cfg.patchTypes);
    if (dcModeFlag) {
        cfg.dcMode = true;
        // ingest：与 --dc-raw 同一分类器（surfaceDir 缺省 = 清单目录；适配案 STL 与清单同目录）
        const std::string sdir = surfaceDir.empty() ? baseDir : surfaceDir;
        DcRawMapping map;
        if (!ingestDcRaw(cfdrunPath, sdir.empty() ? "." : sdir, map, err)) {
            CART_ROOT_BLOCK std::cerr << "[dc-export] ingest 失败: " << err << "\n";
            return 1;
        }
        nlohmann::json mj;
        {
            std::ifstream f2(cfdrunPath);
            try { f2 >> mj; } catch (const std::exception& e) {
                CART_ROOT_BLOCK
                std::cerr << "[dc-export] 清单重解析失败: " << e.what() << "\n";
                return 1;
            }
        }
        if (!buildDcExportContract(mj, map, spec, surf, dcContract, err)) {
            CART_ROOT_BLOCK std::cerr << "[dc-export] " << err << "\n";
            return 1;
        }
        cfg.dcFarFieldNames = dcContract.farFieldNames;
        cfg.dcPatchRename = dcContract.patchRename;
        cfg.baffles = dcContract.baffles;
        exportTypes = dcContract.patchTypes;
        CART_ROOT_BLOCK {
            std::cerr << "[dc-mode] export parity ON: verbatim naming + golden boundary 版式 + "
                         "faceZones；AMR extras/dynamicMeshDict 剥除（任务书 S3）\n";
            std::cerr << "[dc-mode] envelopeDeclared=" << (int)dcContract.envelopeDeclared
                      << " baffles=" << cfg.baffles.size()
                      << " renames=" << dcContract.patchRename.size()
                      << " types=" << exportTypes.size()
                      << " suppressed=" << dcContract.opts.suppressedPatches.size()
                      << " zeroFaceDeclared=" << dcContract.opts.zeroFacePatches.size() << "\n";
        }
    }

    CartesianResult res;
    if (!generateCartesian(surf, cfg, res, err)) {
        writeDcReportSidecar(cfg, res, exportDir);  // dc17 Step 2：失败路径也落报告
        CART_ROOT_BLOCK std::cerr << err << "\n";
        return 1;
    }
    writeDcReportSidecar(cfg, res, exportDir);      // dc17 Step 2：dcMode 报告 sidecar
    CART_ROOT_BLOCK {
        std::printf("[cfdrun] name=%s 叶=%d 切割=%d 流体=%d 内部面=%d 耗时=%.1fms\n",
                    spec.name.c_str(), res.nLeaves, res.nCut, res.nFluid, res.mesh.nInternal,
                    res.genMs);
        std::printf("[cfdrun] domain hasBox=%d box=(%g,%g,%g)-(%g,%g,%g) baseLevel=%d "
                    "maxLevel=%d regions=%zu baffles=%zu cellZones=%zu\n",
                    (int)cfg.hasBox, cfg.boxMin.x, cfg.boxMin.y, cfg.boxMin.z, cfg.boxMax.x,
                    cfg.boxMax.y, cfg.boxMax.z, cfg.baseLevel, cfg.maxLevel, spec.regions.size(),
                    cfg.baffles.size(), cfg.cellZones.size());
        if (dcModeFlag) {
            if (!writePolyMeshFoam(res.mesh, exportDir, err, exportTypes, &dcContract.opts)) {
                for (const auto& r : dcContract.opts.report) std::cerr << r << "\n";
                std::cerr << "[dc-export] " << err << "\n";
                return 1;
            }
            // 任务书 S3 EXTRAS：golden polyMesh = 五件套 + zones/sets——dcMode 剥除
            // cellLevel/pointLevel/level0Edge/refinementHistory 与 constant/dynamicMeshDict。
            std::printf("[cfdrun] dcMode: 跳过 AMR extras + dynamicMeshDict（golden parity）\n");
        } else {
            if (!writePolyMeshFoam(res.mesh, exportDir, err, exportTypes)) {
                std::cerr << err << "\n";
                return 1;
            }
            if (!writeCartesianAmrFiles(res, exportDir, err)) { std::cerr << err << "\n"; return 1; }
        }
        std::printf("[cfdrun] 导出 OpenFOAM polyMesh → %s\n", exportDir.c_str());
        if (!vtuPath.empty()) {
            if (!writePolyMeshVtu(res.mesh, vtuPath, err)) { std::cerr << err << "\n"; return 1; }
            std::printf("[cfdrun] 导出 VTU → %s\n", vtuPath.c_str());
        }
        // patch 列表打印（验收要求）：名/类型/面数（dcMode 用契约类型表——报告 ≡ 落盘）
        std::vector<long> nFaces(res.mesh.patchNames.size(), 0);
        for (size_t f = 0; f < res.mesh.faces.size(); ++f)
            if (res.mesh.neighbour[f] < 0 && res.mesh.facePatch[f] >= 0 &&
                res.mesh.facePatch[f] < (int)nFaces.size())
                nFaces[(size_t)res.mesh.facePatch[f]]++;
        std::printf("[cfdrun] boundary patches (%zu):\n", res.mesh.patchNames.size());
        for (size_t i = 0; i < res.mesh.patchNames.size(); ++i)
            std::printf("    %-28s type=%-14s nFaces=%ld\n", res.mesh.patchNames[i].c_str(),
                        foamPatchTypeWith(res.mesh.patchNames[i], exportTypes).c_str(), nFaces[i]);
        if (dcModeFlag) {
            // 远场泄漏诊断（任务书 S3：包络已声明而 xMin..zMax 仍有面 = 洪泛穿过开放壳，
            // 引擎 Steps 3/5 落地前在适配案不会触发——六侧全部改名包络 golden 名）
            if (dcContract.envelopeDeclared) {
                static const char* far6[6] = {"xMin", "xMax", "yMin", "yMax", "zMin", "zMax"};
                for (int k = 0; k < 6; ++k) {
                    for (size_t i = 0; i < res.mesh.patchNames.size(); ++i)
                        if (res.mesh.patchNames[i] == far6[k] && nFaces[i] > 0)
                            std::cerr << "[dc-export] CONTRACT VIOLATION: 远场 patch " << far6[k]
                                      << " 在已声明房间包络下仍有 " << nFaces[i]
                                      << " 面——洪泛穿过开放壳（任务书 S3 诊断；引擎 Steps 3/5 待落地）\n";
                }
            }
            for (const auto& r : dcContract.opts.report) std::cerr << r << "\n";
        }
    }
    return 0;
}

#if defined(MESHGEN_WITH_MPI)
// MPI 预检（只在 mpirun 拉起时生效）：初始化 + 子命令白名单。
// 非 mpirun 直接执行 ON 构建 ⇒ 不初始化 ⇒ 引擎走原串行路径（行为与 OFF 构建逐字节同）。
// 批处理/RPC 服务子命令在 mpirun 下显式拒绝：它们含会话状态与网络监听，多秩语义
// 未定义（M1a 范围外），静默跑 P 份只会互相覆写产物。
static int mpiPreflight(int argc, char** argv) {
    std::string err;
    const bool on = meshgen::mpi::init(err);
    if (!err.empty()) {
        std::cerr << "[mpi] " << err << "\n";
        return 1;
    }
    if (!on) return 0;   // 未被 mpirun 拉起 ⇒ 串行路径
    // T-CFDRUN：cfdrun 子命令与 cartesian 同走分布生成路径（同一 generateCartesian 入口）
    if (argc >= 2 && (std::string(argv[1]) == "cartesian" || std::string(argv[1]) == "cfdrun"))
        return 0;
    if (meshgen::mpi::root())
        std::cerr << "[mpi] mpirun 下仅支持 cartesian/cfdrun 子命令（M1a 范围；批处理/RPC 服务"
                     "无多秩语义，会互相覆写产物）——请单进程运行，或 MESHGEN_MPI=0 关闭\n";
    meshgen::mpi::shutdown();
    return 2;
}
#endif

int mainDispatch(int argc, char** argv) {
    if (argc >= 2 && std::string(argv[1]) == "cartesian") return runCartesian(argc, argv);
    if (argc >= 2 && std::string(argv[1]) == "cfdrun") return runCfdrun(argc, argv);
    if (argc >= 2 && std::string(argv[1]) == "--server") {
        const std::string mode = argc >= 3 ? argv[2] : "stdio";
        Session s;
        if (mode == "stdio") {
            runStdioServer(s);
            return 0;
        }
        if (mode.rfind("tcp:", 0) == 0) {
#if !defined(_WIN32)
            runTcpServer(s, std::atoi(mode.c_str() + 4));
            return 0;
#else
            std::cerr << "Windows 暂不支持 TCP 模式" << std::endl;
            return 2;
#endif
        }
        std::cerr << "--server 需要 stdio 或 tcp:<port>" << std::endl;
        return 2;
    }
    if (argc >= 2 && (std::string(argv[1]) == "-h" || std::string(argv[1]) == "--help")) {
        printUsage();
        return 0;
    }
    return runBatch(argc, argv);
}

int main(int argc, char** argv) {
#if defined(MESHGEN_WITH_MPI)
    const int pre = mpiPreflight(argc, argv);
    if (pre != 0) return pre;
    const int rc = mainDispatch(argc, argv);
    // 全秩一致退出码：rank0 的 rc 广播（模式 G 下只有 rank0 装配/落盘，其失败必须
    // 让所有秩同码退出，否则 mpirun 报"部分秩 0 退出"而脚本误判成功）。
    const int rcAll = meshgen::mpi::consensusExit(rc);
    meshgen::mpi::shutdown();
    return rcAll;
#else
    return mainDispatch(argc, argv);
#endif
}
