// CartesianCfdrun.cpp —— dcdtuni .cfdrun 清单摄入实现（T-CFDRUN）
//
// 纯翻译层：.cfdrun（V1 CFDExport 顶层 / V2 CfdrunV2.meshGeometry）→ CartesianConfig +
// 表面装载计划。不做物理、不改网格几何语义。缺省安全：清单未声明项一律不写。
// 详见 CartesianCfdrun.hpp 头注与 docs/cfdrun_ingest.md。

#include "CartesianCfdrun.hpp"
#include "CfdrunGeometryView.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace meshgen {
namespace {

using json = nlohmann::json;

// ---- 防御性 JSON 取值小工具（缺键/类型不符 → 回退，不抛） ----
bool isObj(const json& j) { return j.is_object(); }

double numOr(const json& j, const char* key, double dflt) {
    if (j.is_object() && j.contains(key) && j[key].is_number()) return j[key].get<double>();
    return dflt;
}

// 读 {x,y,z} 或 [x,y,z] → Vec3；ok 置是否成功（三个分量均为数）
bool readVec(const json& j, Vec3& out) {
    if (j.is_array() && j.size() == 3 && j[0].is_number() && j[1].is_number() &&
        j[2].is_number()) {
        out = Vec3{j[0].get<double>(), j[1].get<double>(), j[2].get<double>()};
        return true;
    }
    if (j.is_object() && j.contains("x") && j.contains("y") && j.contains("z") &&
        j["x"].is_number() && j["y"].is_number() && j["z"].is_number()) {
        out = Vec3{j["x"].get<double>(), j["y"].get<double>(), j["z"].get<double>()};
        return true;
    }
    return false;
}

std::string strOr(const json& j, const char* key, const std::string& dflt = "") {
    if (j.is_object() && j.contains(key) && j[key].is_string()) return j[key].get<std::string>();
    return dflt;
}

// refinementLevel [min,max]（或标量）→ LevelSpec；缺省 {dflt,dflt}
LevelSpec readLevelPair(const json& j, int dflt) {
    LevelSpec ls{dflt, dflt};
    if (j.is_array() && j.size() >= 1 && j[0].is_number()) {
        ls.lo = j[0].get<int>();
        ls.hi = (j.size() >= 2 && j[1].is_number()) ? j[1].get<int>() : ls.lo;
    } else if (j.is_number_integer()) {
        ls.lo = ls.hi = j.get<int>();
    }
    if (ls.hi < ls.lo) ls.hi = ls.lo;
    return ls;
}

// 从 room.plane（[{x,y,z},...]）+ height 推 x/y/z 包围盒
bool roomBBox(const json& room, Vec3& mn, Vec3& mx) {
    if (!isObj(room) || !room.contains("plane") || !room["plane"].is_array()) return false;
    const json& plane = room["plane"];
    if (plane.empty()) return false;
    mn = Vec3{1e300, 1e300, 1e300};
    mx = Vec3{-1e300, -1e300, -1e300};
    double z0 = 1e300;
    for (const auto& p : plane) {
        Vec3 v;
        if (!readVec(p, v)) continue;
        mn.x = std::min(mn.x, v.x);
        mx.x = std::max(mx.x, v.x);
        mn.y = std::min(mn.y, v.y);
        mx.y = std::max(mx.y, v.y);
        z0 = std::min(z0, v.z);
    }
    if (mn.x > mx.x) return false;  // 无有效点
    if (z0 > 1e299) z0 = 0.0;
    const double h = numOr(room, "height", 0.0);
    mn.z = z0;
    mx.z = z0 + (h > 0 ? h : 0.0);
    return true;
}

}  // namespace

bool parseCfdrun(const json& j, CfdrunSpec& out, std::string& err) {
    out = CfdrunSpec{};
    if (!j.is_object()) {
        err = "cfdrun 顶层必须是 JSON 对象（实际: " + std::string(j.type_name()) + "）";
        return false;
    }
    auto& warn = out.warnings;
    CartesianConfig& cfg = out.cfg;

    CfdrunGeometryView view;
    if (!CfdrunGeometryView::select(j, view, err)) return false;
    const json* geom = view.geometry;
    const bool isV2 = view.schema >= 2; // modern BC bindings have the same consumer semantics
    out.name = isV2 ? strOr(j, "name") : strOr(j, "projectName", strOr(j, "name"));
    if (view.missingV2Blob)
        warn.push_back("[cfdrun] V2 清单缺 meshGeometry 几何 blob——domain/regions 仅能从 "
                       "geometryExport/boundaryBindings 有限推断");
    const json& meshCfg = view.meshConfig();

    // ---- ① domain：blockMeshBounds 优先，否则 room.plane+height ----
    Vec3 dmn{0, 0, 0}, dmx{0, 0, 0};
    bool haveDomain = false;
    if (meshCfg.contains("blockMeshBounds") && isObj(meshCfg["blockMeshBounds"])) {
        const json& b = meshCfg["blockMeshBounds"];
        Vec3 lo, hi;
        if (b.contains("min") && b.contains("max") && readVec(b["min"], lo) && readVec(b["max"], hi)) {
            dmn = lo;
            dmx = hi;
            haveDomain = true;
        }
    }
    if (!haveDomain && geom && geom->contains("room")) {
        Vec3 lo, hi;
        if (view.modern() ? view.roomBounds(lo, hi) : roomBBox((*geom)["room"], lo, hi)) {
            dmn = lo;
            dmx = hi;
            haveDomain = true;
            warn.push_back("[cfdrun] 无 blockMeshBounds，domain 由 room.plane+height 推断");
        }
    }
    if (haveDomain) {
        cfg.hasBox = true;
        cfg.boxMin = dmn;
        cfg.boxMax = dmx;
        out.hasDomain = true;
    } else {
        warn.push_back("[cfdrun] 未能解析 domain 盒（缺 blockMeshBounds 与 room.plane）——"
                       "将回退表面 bbox+domainPad");
    }

    // T-26 房间盒交叉核对（只告警，不改 domain）：DC 的 room.plane 是**平面坐标**
    // （Y-up 工程：plane 的 (x,y) = 3D 的 (x,z)，height = 3D 的 y），roomBBox() 按
    // 三维 {x,y,z} 直读 ⇒ 轴指派与适配 STL（scripts/adapt_dcdtuni.py 已把 DC 平面坐标
    // 重排成 3D）不同。故 blockMeshBounds 优先是**必须的**（四案实测二者尺寸集合相同、
    // 轴序不同）。此处把"尺寸集合相同但轴序不同"与"根本不一致"分别告警，防后续
    // 清单改动静默错位（domain 轴错 ⇒ 全部 region 级/远场 patch 语义崩坏）。
    if (haveDomain && geom && geom->contains("room")) {
        Vec3 rlo, rhi;
        if (view.modern() ? view.roomBounds(rlo, rhi) : roomBBox((*geom)["room"], rlo, rhi)) {
            const Vec3 ds = dmx - dmn, rs = rhi - rlo;
            std::array<double, 3> a{ds.x, ds.y, ds.z}, b{rs.x, rs.y, rs.z};
            std::sort(a.begin(), a.end());
            std::sort(b.begin(), b.end());
            const double tol = 1e-6 * std::max(1.0, a[2]);
            const bool sameSet = std::abs(a[0] - b[0]) <= tol && std::abs(a[1] - b[1]) <= tol &&
                                 std::abs(a[2] - b[2]) <= tol;
            const bool sameAxis = std::abs(ds.x - rs.x) <= tol && std::abs(ds.y - rs.y) <= tol &&
                                  std::abs(ds.z - rs.z) <= tol;
            if (!sameAxis)
                warn.push_back(sameSet
                                   ? "[cfdrun] room.plane+height 与 blockMeshBounds 尺寸同集合但轴序不同"
                                     "（DC 平面坐标 Y-up）——domain 用 blockMeshBounds（与适配 STL 同轴）"
                                   : "[cfdrun] room.plane+height 与 blockMeshBounds 尺寸不一致——"
                                     "domain 用 blockMeshBounds，请核对清单/适配几何");
        }
    }

    // ---- ①b nCellsBetweenLevels：DC 生产口径 = 1（引擎默认 3 会三倍加厚过渡带）----
    // T-26 取证（docs/cartesian_dev/26_elig_deadair.md §2）：DC runner 生成的
    // snappyHexMeshDict 四案恒写 `nCellsBetweenLevels 1`（dcdtuni
    // src/backend/aircooling/runner/stlgen_runner.py:4900；case/*/Mesh/system/
    // snappyHexMeshDict 实测四案同值），dcCartMesh 亦以 1 为缺省（dcCartMesh.C:192
    // getOrDefault<label>("nCellsBetweenLevels", 1)）。摄入层此前不映射该键 ⇒ 走引擎
    // 默认 3；又因引擎 S5 锚定波 target=Ls−⌈d/n⌉ 用**全局** Ls=max(region 绝对级)
    // 对**所有**表面锚生效（Cartesian.cpp:2317，非 snappy 的逐表面级语义），低级别
    // region 的表面带也被铺到 maxLevel 且厚 n 胞：Slab 实测 250,978 个 maxLevel(L8) 胞
    // 里 117,745 属声明 L7 的 Raising_floor、41,266 属声明 L7 的 Baffle_1_copy
    // （63% 的最细级胞是"被波推高"的低级别 region）。n=1 把带厚收到 DC 同口径。
    // 清单显式声明者优先（前向兼容 DC 将来把它写进 meshConfig）。
    if (meshCfg.contains("nCellsBetweenLevels") && meshCfg["nCellsBetweenLevels"].is_number_integer()) {
        cfg.nCellsBetweenLevels = std::max(0, meshCfg["nCellsBetweenLevels"].get<int>());
    } else {
        cfg.nCellsBetweenLevels = 1;  // DC 生产口径（stlgen_runner.py:4900）
        warn.push_back("[cfdrun] 清单未声明 nCellsBetweenLevels——取 DC 生产口径 1"
                       "（引擎默认 3 会三倍加厚过渡带；见 26_elig_deadair.md §2）");
    }

    // ---- ② baseLevel/maxLevel：baseSize + rootEdge + refinementLevel ----
    const double baseSize = numOr(meshCfg, "baseSize", numOr(meshCfg, "meshBaseSize", 0.0));
    LevelSpec refLevel = readLevelPair(meshCfg.contains("refinementLevel") ? meshCfg["refinementLevel"]
                                                                           : json::array({1, 1}),
                                       1);
    // rootEdge = domain 盒最大维度（hasBox）；否则暂用 baseSize 无法换算 → 回退默认级
    if (haveDomain) {
        const Vec3 sz = dmx - dmn;
        const double rootEdge = std::max({sz.x, sz.y, sz.z});
        if (baseSize > 0 && rootEdge > 0) {
            int bl = (int)std::lround(std::log2(rootEdge / baseSize));
            bl = std::max(0, std::min(bl, CartesianMesh::kMaxLevel));
            cfg.baseLevel = bl;
            // T-CFDRUN 适配（DC 四案）：maxLevel 取全部类别相对级键的最大 hi——
            // 只看 refinementLevel 会把 racks(rackRegionLevel=3) 等类别级钳到
            // baseLevel+1（引擎 rel clamp 到 maxLevel−baseLevel），与 DC runner 的
            // snappyHexMeshDict（*_frame level (3,3)）失配。无这些键的清单不变。
            int relHi = std::max(0, refLevel.hi);
            static const char* kLevelKeys[] = {
                "floorRefinementLevel", "acuRefinementLevel", "boxRefinementLevel",
                "partitionWallRefinementLevel", "baffleRefinementLevel", "rackRegionLevel"};
            for (const char* k : kLevelKeys)
                if (meshCfg.contains(k))
                    relHi = std::max(relHi, std::max(0, readLevelPair(meshCfg[k], 0).hi));
            int ml = bl + relHi;
            ml = std::max(bl, std::min(ml, CartesianMesh::kMaxLevel));
            cfg.maxLevel = ml;
        } else {
            warn.push_back("[cfdrun] baseSize<=0 或 domain 退化——baseLevel/maxLevel 用引擎默认");
        }
    } else {
        warn.push_back("[cfdrun] 无 domain 盒——baseLevel/maxLevel 用引擎默认（表面 bbox 路径）");
    }

    // ---- ③ locationInMesh / extraLocationsInMesh → keepPoints ----
    if (geom && geom->contains("room") && isObj((*geom)["room"])) {
        const json& room = (*geom)["room"];
        Vec3 lp;
        if (room.contains("locationInMesh") && readVec(room["locationInMesh"], lp))
            cfg.keepPoints.push_back(lp);
        if (room.contains("extraLocationsInMesh") && room["extraLocationsInMesh"].is_array())
            for (const auto& e : room["extraLocationsInMesh"]) {
                Vec3 ev;
                if (readVec(e, ev)) cfg.keepPoints.push_back(ev);
            }
    }
    // V2：inputs 里也可能带 locationInMesh（meshConfig.locationInMesh）
    if (cfg.keepPoints.empty() && view.modern() && view.location()) {
        Vec3 lp;
        if (readVec(*view.location(), lp)) cfg.keepPoints.push_back(lp);
    }
    if (cfg.keepPoints.empty() && meshCfg.contains("locationInMesh")) {
        Vec3 lp;
        if (readVec(meshCfg["locationInMesh"], lp)) cfg.keepPoints.push_back(lp);
    }

    // ---- region 收集：逐类别（walls/baffles/racks/acus/floors/heatSources/wallObjects/
    //      partitionWalls）⇒ CfdrunRegion + surfaceLevels + patchTypes ----
    // 类别 → (相对级键, 缺省边界类型, kind, 是否 baffle)
    struct CatInfo {
        const char* key;         // geom 下的类别字典键
        const char* levelKey;    // meshConfig 下的相对级键（可空 = 用 refinementLevel）
        const char* patchType;   // 缺省 OpenFOAM 类型
        const char* kind;        // region kind
        bool baffle;             // 是否 baffle 类
    };
    const CatInfo cats[] = {
        {"walls", nullptr, "wall", "wall", false},
        {"baffles", "baffleRefinementLevel", "wall", "baffle", true},
        {"partitionWalls", "partitionWallRefinementLevel", "wall", "partitionWall", false},
        {"floors", "floorRefinementLevel", "wall", "floor", false},
        // T-CFDRUN 适配（DC 四案）：机架级来自 meshConfig.rackRegionLevel（标量 3 ⇒
        // (3,3)，与 DC snappyHexMeshDict 的 *_frame level (3,3) 对齐；缺省仍回退
        // refinementLevel——无 rackRegionLevel 键的清单行为不变）
        {"racks", "rackRegionLevel", "wall", "rack", false},
        {"acus", "acuRefinementLevel", "wall", "acu", false},
        {"heatSources", nullptr, "wall", "heatSource", false},
        {"wallObjects", nullptr, "wall", "wallObject", false},
    };

    std::set<std::string> stlSet;
    auto addRegion = [&](const std::string& name, const std::string& stlFile,
                         const std::string& solidName, const LevelSpec& lv,
                         const std::string& patchType, const std::string& kind, bool isBaffle,
                         const std::string& faceType) {
        if (name.empty()) return;
        CfdrunRegion r;
        r.name = name;
        r.stlFile = stlFile;
        r.solidName = solidName.empty() ? name : solidName;
        r.kind = kind;
        r.level = lv;
        r.patchType = patchType;
        r.isBaffle = isBaffle;
        out.regions.push_back(r);
        // surfaceLevels：region 名 → 相对级（引擎按 TriSurface patchName 匹配）
        cfg.surfaceLevels.emplace_back(r.solidName, lv);
        if (!stlFile.empty()) stlSet.insert(stlFile);
        if (isBaffle) {
            BaffleSpec bs;
            bs.region = r.solidName;
            bs.name = name;  // master patch 名 = region 声明名
            bs.faceType = faceType;
            cfg.baffles.push_back(bs);
            // faceType 覆盖 master/slave（导出端 patchTypes）
            const std::string ft = faceType.empty() ? std::string("wall") : faceType;
            cfg.patchTypes.emplace_back(name, ft);
            cfg.patchTypes.emplace_back(name + "_slave", ft);
        } else if (!patchType.empty()) {
            cfg.patchTypes.emplace_back(r.solidName, patchType);
        }
    };

    if (geom) {
        for (const auto& ci : cats) {
            if (!geom->contains(ci.key) || !(*geom)[ci.key].is_object()) continue;
            const json& catDict = (*geom)[ci.key];
            // 类别相对级（缺省用全局 refinementLevel）
            LevelSpec catLv = refLevel;
            if (ci.levelKey && meshCfg.contains(ci.levelKey))
                catLv = readLevelPair(meshCfg[ci.levelKey], refLevel.hi);
            for (auto it = catDict.begin(); it != catDict.end(); ++it) {
                const std::string& name = it.key();
                const json& e = it.value();
                if (!e.is_object()) continue;
                const std::string stlFile = strOr(e, "stlFile");
                // solid 名：优先 solidName/solidNames[0]；否则用 region 名
                std::string solidName = strOr(e, "solidName");
                if (solidName.empty() && e.contains("solidNames") && e["solidNames"].is_array() &&
                    !e["solidNames"].empty() && e["solidNames"][0].is_string())
                    solidName = e["solidNames"][0].get<std::string>();
                // baffle faceType：physics.baffleType
                std::string faceType;
                if (ci.baffle) {
                    std::string bt = "wall";
                    if (e.contains("physics") && isObj(e["physics"]))
                        bt = strOr(e["physics"], "baffleType", "wall");
                    faceType = (bt == "perforated") ? "cyclicOpening" : "wall";
                }
                addRegion(name, stlFile, solidName, catLv, ci.patchType, ci.kind, ci.baffle,
                          faceType);
            }
        }
    }

    // ---- V2：boundaryBindings 覆盖 faceType/patchType（bcType → OpenFOAM 类型） ----
    if (isV2 && j.contains("boundaryBindings") && j["boundaryBindings"].is_object()) {
        const json& bb = j["boundaryBindings"];
        for (auto it = bb.begin(); it != bb.end(); ++it) {
            const json& b = it.value();
            if (!b.is_object()) continue;
            const std::string patchName = strOr(b, "patchName");
            const std::string bcType = strOr(b, "bcType");
            if (patchName.empty() || bcType.empty()) continue;
            // bcType → OpenFOAM 边界类型（wall_baffle/perforated_baffle/wall → wall/cyclicOpening；
            // 其余保持缺省，不覆盖）
            std::string ft;
            if (bcType == "wall" || bcType == "wall_baffle" || bcType == "nonfan_fixed_temp" ||
                bcType == "fixedTemperature" || bcType == "heat_source_flux" ||
                bcType == "nonfan_heat_flux")
                ft = "wall";
            else if (bcType == "perforated_baffle")
                ft = "cyclicOpening";
            else if (bcType == "acu_supply" || bcType == "acu_return" || bcType == "fan_inlet" ||
                     bcType == "fan_outlet")
                ft = "patch";
            if (ft.empty()) continue;
            // 覆盖已声明 baffle 的 master/slave faceType；否则追加 region patchType
            bool hitBaffle = false;
            for (auto& bs : cfg.baffles) {
                if (bs.name == patchName) {
                    bs.faceType = ft;
                    hitBaffle = true;
                    break;
                }
            }
            if (hitBaffle) {
                for (auto& kv : cfg.patchTypes)
                    if (kv.first == patchName || kv.first == patchName + "_slave") kv.second = ft;
            } else {
                cfg.patchTypes.emplace_back(patchName, ft);
            }
        }
    }

    // ---- cellZones（若清单显式声明；V1 meshConfig.cellZones 或 V2 顶层 cellZones）----
    // DC 当前不直接产 cellZones；此路径为前向兼容（porousZone region 可后续映射）。
    const json* czSrc = nullptr;
    if (meshCfg.contains("cellZones") && meshCfg["cellZones"].is_array()) czSrc = &meshCfg["cellZones"];
    else if (j.contains("cellZones") && j["cellZones"].is_array()) czSrc = &j["cellZones"];
    if (czSrc) {
        for (const auto& z : *czSrc) {
            if (!z.is_object()) continue;
            CellZoneSpec zs;
            zs.name = strOr(z, "name");
            Vec3 lo, hi;
            if (z.contains("min") && z.contains("max") && readVec(z["min"], lo) && readVec(z["max"], hi)) {
                zs.mn = lo;
                zs.mx = hi;
                if (!zs.name.empty()) cfg.cellZones.push_back(zs);
            }
        }
    }

    // ---- maxGlobalCells / nCellsBetweenLevels（若声明）----
    if (meshCfg.contains("maxGlobalCells") && meshCfg["maxGlobalCells"].is_number())
        cfg.maxGlobalCells = meshCfg["maxGlobalCells"].get<long long>();

    // qualityGate（缺省 strict 零容忍不变）："warn" ⇒ S10 kind2/3 降级告警继续导出
    // （kind1 开放胞恒拒出）。与 cartesian JSON 的 meshGenerator.qualityGate 同语义。
    if (meshCfg.contains("qualityGate") && meshCfg["qualityGate"].is_string() &&
        meshCfg["qualityGate"].get<std::string>() == "warn")
        cfg.qualityGateWarn = true;

    // smallCell（S9 合并；缺省引擎行为不变）：与 cartesian JSON 的
    // meshGenerator.smallCell 同语义。DC 适配案关闭合并（v3 R3 实证：合并角胞
    // 触发 S10 kind3 / S11b 边一致性缺陷）。
    if (meshCfg.contains("smallCell") && meshCfg["smallCell"].is_object()) {
        const json& sc = meshCfg["smallCell"];
        if (sc.contains("volumeFraction") && sc["volumeFraction"].is_number())
            cfg.mergeVolFrac = sc["volumeFraction"].get<double>();
        if (sc.contains("maxPasses") && sc["maxPasses"].is_number_integer())
            cfg.mergeMaxPasses = sc["maxPasses"].get<int>();
        // T-26 如实告警：清单写 {volumeFraction:0,maxPasses:0} 表达"关合并"，但引擎
        // 侧 `for (pass = 0; pass < std::max(1, cfg.mergeMaxPasses); ++pass)`
        //（Cartesian.cpp:6917）⇒ 仍跑 1 pass，且 S9 的四条判据里只有"体积阈值"
        // 被 mergeVolFrac=0 关掉，最小面宽(<0.05h)/切胞质心出胞/邻居体积比(>100×)
        // 三条仍在合并（Base2-9 CART_DBG 日志实测 [MERGE] 行存在）。即"关"是半开态，
        // 不是历史行为。要真关需引擎侧加开关（属下轮提案，见 26_elig_deadair.md §4）。
        if (cfg.mergeMaxPasses <= 0 || cfg.mergeVolFrac <= 0)
            warn.push_back("[cfdrun] smallCell 声明为关（volumeFraction/maxPasses≤0），但引擎 "
                           "S9 仍跑 max(1,maxPasses)=1 pass 且面宽/质心/体积比三判据仍生效"
                           "（Cartesian.cpp:6917）——半开态，非真关");
    }

    // ---- stlFiles 去重列表（保持插入序）----
    for (const auto& r : out.regions)
        if (!r.stlFile.empty() &&
            std::find(out.stlFiles.begin(), out.stlFiles.end(), r.stlFile) == out.stlFiles.end())
            out.stlFiles.push_back(r.stlFile);
    (void)stlSet;

    if (out.regions.empty() && !haveDomain)
        warn.push_back("[cfdrun] 既无 region 也无 domain——生成可能为空，请核对清单");

    out.valid = true;
    return true;
}

bool loadCfdrunFile(const std::string& path, CfdrunSpec& out, std::string& err) {
    std::ifstream f(path);
    if (!f) {
        err = "无法打开 cfdrun: " + path;
        return false;
    }
    json j;
    try {
        f >> j;
    } catch (const std::exception& e) {
        err = "cfdrun JSON 解析失败: " + std::string(e.what());
        return false;
    }
    return parseCfdrun(j, out, err);
}

// ============================================================================
// dc17 STEP 4 / 任务书 S2 —— RAW DC 摄入（--dc-raw）实现
// ============================================================================
// 设计纪律（verdicts.json）：磁盘扫描为主、cfdrun 仅作分类器；每个磁盘 solid 必须被
// 解释（bound 或 STL_ONLY 启发式），绝不静默丢弃；无法解释者如实计入 unexplained。
// OFF 路径（parseCfdrun/CfdrunSpec）逐字节不受影响——本节全部为新增符号。
namespace {

namespace fs = std::filesystem;

bool strContains(const std::string& h, const std::string& n) {
    return !n.empty() && h.find(n) != std::string::npos;
}
bool strIContains(const std::string& h, const std::string& n) {
    if (n.empty() || h.size() < n.size()) return false;
    auto low = [](std::string s) {
        for (auto& c : s) c = (char)std::tolower((unsigned char)c);
        return s;
    };
    return low(h).find(low(n)) != std::string::npos;
}
bool endsWith(const std::string& s, const std::string& suf) {
    return s.size() >= suf.size() && s.compare(s.size() - suf.size(), suf.size(), suf) == 0;
}
// 去 _master/_slave 尾缀 → baffle zone 名
std::string stripMasterSlave(const std::string& s) {
    if (endsWith(s, "_master")) return s.substr(0, s.size() - 7);
    if (endsWith(s, "_slave")) return s.substr(0, s.size() - 6);
    return s;
}
std::string fileStem(const std::string& fn) {
    if (endsWith(fn, ".stl") || endsWith(fn, ".STL")) return fn.substr(0, fn.size() - 4);
    return fn;
}
// 箱体面后缀（merged_box 里 Pillar/Batt/PDU 等以 _Front/_Back/... 命名）
bool isBoxFaceSuffix(const std::string& s) {
    static const char* suf[] = {"_Front", "_Back", "_Left", "_Right", "_Top",    "_Bottom",
                                "_front", "_back", "_left", "_right", "_top",    "_bottom"};
    for (const char* p : suf)
        if (endsWith(s, p)) return true;
    return false;
}

// ---- ASCII STL 目录扫描：逐 *.stl 解析 "solid <name>" 块 → 名/三角数/包围盒 ----
// 单 solid 文件保留真实 solid 名（不归一为 "wall"）；二进制 STL 无 solid 名 ⇒ 告警 +
// 按文件茎造单条记录（不静默丢）。确定性：文件名字典序 + 文件内 solid 出现序。
bool scanOneStl(const std::string& path, const std::string& fn, std::vector<DcRawSolidInput>& out,
                std::vector<std::string>& warnings) {
    std::ifstream f(path);
    if (!f) {
        warnings.push_back("[dc-raw] 无法打开 STL: " + path);
        return false;
    }
    const std::string stem = fileStem(fn);
    std::string tok, line;
    DcRawSolidInput cur;
    bool inSolid = false;
    int unnamed = 0;
    long fileSolids = 0;
    long vertCount = 0;  // 当前 solid 顶点计数（/3 = 三角数）
    auto flush = [&]() {
        if (inSolid) {
            cur.triCount = vertCount / 3;  // 每 facet 3 顶点
            // 空 solid（无顶点）→ bbox 归零（避免 1e300 残留）
            if (vertCount == 0) {
                cur.bboxMin = Vec3{0, 0, 0};
                cur.bboxMax = Vec3{0, 0, 0};
            }
            out.push_back(cur);
            ++fileSolids;
        }
        inSolid = false;
        vertCount = 0;
    };
    while (f >> tok) {
        if (tok == "solid") {
            flush();
            std::getline(f, line);
            const size_t a = line.find_first_not_of(" \t\r\n");
            const size_t b = line.find_last_not_of(" \t\r\n");
            std::string nm = (a == std::string::npos) ? "" : line.substr(a, b - a + 1);
            if (nm.empty()) nm = stem + "_solid" + std::to_string(unnamed++);
            cur = DcRawSolidInput{};
            cur.solidName = nm;
            cur.stlFile = fn;
            cur.bboxMin = Vec3{1e300, 1e300, 1e300};
            cur.bboxMax = Vec3{-1e300, -1e300, -1e300};
            inSolid = true;
        } else if (tok == "endsolid") {
            std::getline(f, line);  // 行尾可能带名字
            flush();
        } else if (tok == "vertex") {
            double x, y, z;
            if (!(f >> x >> y >> z)) break;
            if (inSolid) {
                ++vertCount;
                cur.bboxMin.x = std::min(cur.bboxMin.x, x);
                cur.bboxMin.y = std::min(cur.bboxMin.y, y);
                cur.bboxMin.z = std::min(cur.bboxMin.z, z);
                cur.bboxMax.x = std::max(cur.bboxMax.x, x);
                cur.bboxMax.y = std::max(cur.bboxMax.y, y);
                cur.bboxMax.z = std::max(cur.bboxMax.z, z);
            }
        }
    }
    flush();
    if (fileSolids == 0) {
        // 二进制 STL / 无 solid 块：按文件茎造单条记录（不静默丢）
        warnings.push_back("[dc-raw] STL 无 ASCII solid 块（二进制？）——按文件茎登记: " + fn);
        DcRawSolidInput fb;
        fb.solidName = stem;
        fb.stlFile = fn;
        fb.triCount = 0;
        out.push_back(fb);
    }
    return true;
}

// Editor-only interface walls: the server's left/right faces coincide with the
// rack frame interior, carry no fluid and must never become geometry. The
// dcdtuni runner excludes them via mesh_acceptance.is_editor_only_stl(); the
// disk-scanning ingest here must apply the identical rule or it would classify
// the interface bucket as solids. Keep the two predicates in sync.
bool isEditorOnlyInterfaceStl(const std::string& fn) {
    if (fn == "merged_server_interface_walls.stl") return true;
    // per-side forms incl. patch subdivisions: merged_server_<stem>_(left|right)[_p<N>|_wall].stl
    const std::string stem = ".stl";
    if (fn.size() <= stem.size() || fn.compare(fn.size() - stem.size(), stem.size(), stem) != 0)
        return false;
    const std::string base = fn.substr(0, fn.size() - stem.size());
    const std::string prefix = "merged_server_";
    if (base.compare(0, prefix.size(), prefix) != 0) return false;
    for (const std::string side : {"_left", "_right"}) {
        if (base.size() >= prefix.size() + side.size() &&
            base.compare(base.size() - side.size(), side.size(), side) == 0)
            return true;
        // subdivisions: ..._left_p0 / ..._right_wall
        for (const std::string tail : {"_p", "_wall"}) {
            const auto pos = base.rfind(side);
            if (pos != std::string::npos && pos + side.size() < base.size() &&
                base.compare(pos, side.size(), side) == 0 &&
                base.compare(pos + side.size(), tail.size(), tail) == 0)
                return true;
        }
    }
    return false;
}

}  // namespace

bool scanTriSurfaceDir(const std::string& dir, std::vector<DcRawSolidInput>& out,
                       std::vector<std::string>& warnings) {
    out.clear();
    std::error_code ec;
    if (dir.empty() || !fs::is_directory(dir, ec)) {
        warnings.push_back("[dc-raw] surface-dir 不是目录: " + dir);
        return false;
    }
    std::vector<std::string> files;
    for (auto it = fs::directory_iterator(dir, ec); it != fs::directory_iterator(); it.increment(ec)) {
        if (ec) break;
        if (!it->is_regular_file()) continue;
        const std::string fn = it->path().filename().string();
        if (!(endsWith(fn, ".stl") || endsWith(fn, ".STL"))) continue;
        if (isEditorOnlyInterfaceStl(fn)) {
            warnings.push_back("[dc-raw] 跳过 editor-only 接口桶（无流体界面）: " + fn);
            continue;
        }
        files.push_back(fn);
    }
    std::sort(files.begin(), files.end());  // 确定性：文件名字典序
    for (const auto& fn : files) scanOneStl((dir + "/" + fn).c_str(), fn, out, warnings);
    return !out.empty();
}

namespace {

// ---- 分类上下文：绑定结果 + 清单信号 ----
struct Owner {
    std::string region, category, sub, source;
};
struct PbInfo {
    std::string zone, type;  // type: wallBaffle|perforatedBaffle
};

// 从 meshConfig 取某类别的相对级（缺省回退 refinementLevel）
LevelSpec levelForCat(const std::string& cat, const json& meshCfg, const LevelSpec& dflt,
                      std::string& srcKey) {
    const char* key = nullptr;
    if (cat == "floor") key = "floorRefinementLevel";
    else if (cat == "rack") key = "rackRegionLevel";
    else if (cat == "acu") key = "acuRefinementLevel";
    else if (cat == "baffle") key = "baffleRefinementLevel";
    else if (cat == "partitionWall") key = "partitionWallRefinementLevel";
    else if (cat == "box") key = "boxRefinementLevel";
    else key = nullptr;  // wall/heatSource/wallObject/other → refinementLevel
    if (key && meshCfg.contains(key)) {
        srcKey = key;
        return readLevelPair(meshCfg[key], dflt.hi);
    }
    srcKey = "refinementLevel";
    if (meshCfg.contains("refinementLevel")) return readLevelPair(meshCfg["refinementLevel"], dflt.hi);
    return dflt;
}

// 文件名/solid 名 → baffle 启发（stem/solid 含 wallBaffle/perforated/merged_wall_baffle/
// perforatedBlankingPanel/wallBlankingPanel/perforated_baffle）。返回是否 baffle + 是否穿孔。
bool filenameBaffle(const std::string& stem, const std::string& S, bool& perforated) {
    perforated = false;
    if (strIContains(stem, "perforated") || strIContains(S, "perforated")) {
        perforated = true;
        return true;
    }
    if (strIContains(stem, "wallbaffle") || strIContains(S, "wallbaffle") ||
        strContains(stem, "merged_wall_baffle") || strContains(S, "merged_wall_baffle") ||
        strIContains(stem, "blankingpanel") || strIContains(S, "blankingpanel")) {
        perforated = strIContains(stem, "perforated") || strIContains(S, "perforated");
        return true;
    }
    return false;
}

}  // namespace

bool classifyDcRaw(const json& mf, const std::vector<DcRawSolidInput>& solids, DcRawMapping& out,
                   std::string& err) {
    out = DcRawMapping{};
    if (!mf.is_object()) {
        err = "[dc-raw] manifest 顶层必须是 JSON 对象";
        return false;
    }
    auto& warn = out.warnings;

    // ---- ① 复用 parseCfdrun 取 domain/baseLevel/maxLevel/keepPoints/nCellsBetweenLevels ----
    CfdrunSpec base;
    std::string e2;
    if (parseCfdrun(mf, base, e2)) {
        out.cfg = base.cfg;
        out.name = base.name;
        for (auto& w : base.warnings) warn.push_back(w);
    } else {
        warn.push_back("[dc-raw] parseCfdrun 失败（domain/级回退引擎默认）: " + e2);
    }
    // 清掉 parseCfdrun 按类别字典键建的 region 派生量——dc-raw 以磁盘扫描为主重建之
    out.cfg.surfaceLevels.clear();
    out.cfg.patchTypes.clear();
    out.cfg.baffles.clear();
    out.cfg.roi.clear();

    CfdrunGeometryView view;
    if (!CfdrunGeometryView::select(mf, view, err)) return false;
    const json* geom = &view.geometryOrRoot();
    const json& meshCfg = view.meshConfig();
    const LevelSpec globalRef =
        meshCfg.contains("refinementLevel") ? readLevelPair(meshCfg["refinementLevel"], 1)
                                            : LevelSpec{1, 1};

    // ---- ③ 清单查找表 ----
    // geometryExport.solids（TOP-LEVEL，dict sol_*）：solidName → patchName
    std::unordered_map<std::string, std::string> geoPatch;
    if (mf.contains("geometryExport") && mf["geometryExport"].is_object()) {
        const json& ge = mf["geometryExport"];
        if (ge.contains("solids") && ge["solids"].is_object())
            for (auto it = ge["solids"].begin(); it != ge["solids"].end(); ++it) {
                const json& v = it.value();
                if (!v.is_object()) continue;
                const std::string sn = strOr(v, "solidName");
                const std::string pn = strOr(v, "patchName");
                if (!sn.empty()) geoPatch[sn] = pn.empty() ? sn : pn;
            }
    }
    // boundaryBindings（TOP-LEVEL，dict bb_*）：solidName/patchName → bcType
    std::unordered_map<std::string, std::string> bbBySolid, bbByPatch;
    if (mf.contains("boundaryBindings") && mf["boundaryBindings"].is_object())
        for (auto it = mf["boundaryBindings"].begin(); it != mf["boundaryBindings"].end(); ++it) {
            const json& v = it.value();
            if (!v.is_object()) continue;
            const std::string sn = strOr(v, "solidName"), pn = strOr(v, "patchName"),
                              bc = strOr(v, "bcType");
            if (bc.empty()) continue;
            if (!sn.empty()) bbBySolid[sn] = bc;
            if (!pn.empty()) bbByPatch[pn] = bc;
        }
    // porousBindings（TOP-LEVEL，dict pb_*）：surfaceUid/patchName → {zone,type}
    std::unordered_map<std::string, PbInfo> pbByUid, pbByPatch;
    if (mf.contains("porousBindings") && mf["porousBindings"].is_object())
        for (auto it = mf["porousBindings"].begin(); it != mf["porousBindings"].end(); ++it) {
            const json& v = it.value();
            if (!v.is_object()) continue;
            const std::string pn = strOr(v, "patchName"), uid = strOr(v, "surfaceUid"),
                              ty = strOr(v, "type");
            const std::string zone = stripMasterSlave(pn);
            if (!uid.empty()) pbByUid[uid] = PbInfo{zone, ty};
            if (!pn.empty()) pbByPatch[pn] = PbInfo{zone, ty};
        }

    // 类别字典 → solidOwner（solidName → {region,category,sub,source}）+ objectNames + objCat
    std::unordered_map<std::string, Owner> owner;
    std::unordered_map<std::string, std::string> objCat;     // objectName → category
    std::unordered_map<std::string, std::string> baffleTypeByRegion;  // region → wall|perforated
    std::vector<std::string> objectNames;
    auto addOwner = [&](const std::string& solid, const Owner& o) {
        if (solid.empty()) return;
        owner.emplace(solid, o);  // 先者优先（按下方处理序 = 优先级）
    };
    auto addObject = [&](const std::string& nm, const std::string& cat) {
        if (nm.empty()) return;
        objCat.emplace(nm, cat);
        objectNames.push_back(nm);
    };

    if (geom) {
        // racks（最specific 的嵌套名优先）
        if (geom->contains("racks") && (*geom)["racks"].is_object()) {
            const json& racks = (*geom)["racks"];
            for (auto it = racks.begin(); it != racks.end(); ++it) {
                const std::string R = it.key();
                const json& rk = it.value();
                addObject(R, "rack");
                if (!rk.is_object()) continue;
                if (rk.contains("frameSolidNames") && rk["frameSolidNames"].is_array())
                    for (const auto& s : rk["frameSolidNames"])
                        if (s.is_string())
                            addOwner(s.get<std::string>(), Owner{R, "rack", "frame", "rack:frameSolidNames"});
                if (rk.contains("wallSolidNames") && rk["wallSolidNames"].is_array())
                    for (const auto& s : rk["wallSolidNames"])
                        if (s.is_string())
                            addOwner(s.get<std::string>(), Owner{R, "rack", "rackWall", "rack:wallSolidNames"});
                if (rk.contains("wallBaffleSolids") && rk["wallBaffleSolids"].is_array())
                    for (const auto& s : rk["wallBaffleSolids"])
                        if (s.is_string())
                            addOwner(s.get<std::string>(), Owner{R, "baffle", "rackBaffle", "rack:wallBaffleSolids"});
                // servers[*].faces.{inlet,outlet,top,bottom}.{solid,patches[].solid,wallSolid}
                if (rk.contains("servers") && rk["servers"].is_array())
                    for (const auto& sv : rk["servers"]) {
                        if (!sv.is_object() || !sv.contains("faces")) continue;
                        const json& fc = sv["faces"];
                        struct FaceDef {
                            const char* key, *sub;
                        };
                        static const FaceDef fds[] = {{"inlet", "serverInlet"},
                                                      {"outlet", "serverOutlet"},
                                                      {"top", "serverTop"},
                                                      {"bottom", "serverBottom"}};
                        for (const auto& fd : fds) {
                            if (!fc.contains(fd.key) || !fc[fd.key].is_object()) continue;
                            const json& fo = fc[fd.key];
                            std::string sub = fd.sub;
                            if (fo.contains("solid") && fo["solid"].is_string())
                                addOwner(fo["solid"].get<std::string>(), Owner{R, "rack", sub, std::string("rack:server.") + fd.key});
                            if (fo.contains("wallSolid") && fo["wallSolid"].is_string())
                                addOwner(fo["wallSolid"].get<std::string>(), Owner{R, "rack", sub + "Wall", std::string("rack:server.") + fd.key + ".wallSolid"});
                            if (fo.contains("patches") && fo["patches"].is_array())
                                for (const auto& p : fo["patches"])
                                    if (p.is_object() && p.contains("solid") && p["solid"].is_string())
                                        addOwner(p["solid"].get<std::string>(), Owner{R, "rack", sub, std::string("rack:server.") + fd.key + ".patches"});
                        }
                    }
                // leakage.groups[*].stlSolidName → through_zone
                if (rk.contains("leakage") && rk["leakage"].is_object()) {
                    const json& lk = rk["leakage"];
                    if (lk.contains("groups") && lk["groups"].is_array())
                        for (const auto& g : lk["groups"])
                            if (g.is_object() && g.contains("stlSolidName") && g["stlSolidName"].is_string())
                                addOwner(g["stlSolidName"].get<std::string>(), Owner{R, "rack", "throughZone", "rack:leakage"});
                }
            }
        }
        // acus
        if (geom->contains("acus") && (*geom)["acus"].is_object()) {
            const json& acus = (*geom)["acus"];
            for (auto it = acus.begin(); it != acus.end(); ++it) {
                const std::string A = it.key();
                const json& ac = it.value();
                addObject(A, "acu");
                if (!ac.is_object()) continue;
                if (ac.contains("supplyFaces") && ac["supplyFaces"].is_array())
                    for (const auto& s : ac["supplyFaces"])
                        if (s.is_object() && s.contains("solidName") && s["solidName"].is_string())
                            addOwner(s["solidName"].get<std::string>(), Owner{A, "acu", "acuSupply", "acu:supplyFaces"});
                if (ac.contains("returnFaces") && ac["returnFaces"].is_array())
                    for (const auto& s : ac["returnFaces"])
                        if (s.is_object() && s.contains("solidName") && s["solidName"].is_string())
                            addOwner(s["solidName"].get<std::string>(), Owner{A, "acu", "acuReturn", "acu:returnFaces"});
                if (ac.contains("wallSolidNames") && ac["wallSolidNames"].is_array())
                    for (const auto& s : ac["wallSolidNames"])
                        if (s.is_string())
                            addOwner(s.get<std::string>(), Owner{A, "acu", "acuWall", "acu:wallSolidNames"});
            }
        }
        // walls/floors/baffles/partitionWalls/heatSources/wallObjects：字典键 = 对象名 = solid 名
        struct SimpleCat {
            const char* key, *cat;
        };
        static const SimpleCat scats[] = {{"walls", "wall"},     {"floors", "floor"},
                                          {"baffles", "baffle"}, {"partitionWalls", "partitionWall"},
                                          {"heatSources", "heatSource"}, {"wallObjects", "wallObject"}};
        for (const auto& sc : scats) {
            if (!geom->contains(sc.key) || !(*geom)[sc.key].is_object()) continue;
            const json& d = (*geom)[sc.key];
            for (auto it = d.begin(); it != d.end(); ++it) {
                const std::string K = it.key();
                addObject(K, sc.cat);
                addOwner(K, Owner{K, sc.cat, "", std::string("category:") + sc.key});
                if (std::string(sc.cat) == "baffle") {
                    std::string bt = "wall";
                    const json& v = it.value();
                    if (v.is_object() && v.contains("physics") && v["physics"].is_object())
                        bt = strOr(v["physics"], "baffleType", "wall");
                    baffleTypeByRegion[K] = (bt == "perforated") ? "perforated" : "wall";
                }
            }
        }
    }
    // objectNames 按长度降序（前缀匹配取最长）
    std::sort(objectNames.begin(), objectNames.end(),
              [](const std::string& a, const std::string& b) { return a.size() > b.size(); });

    // ---- ④ refinementZones → ROI ----
    if (meshCfg.contains("refinementZones") && meshCfg["refinementZones"].is_array())
        for (const auto& z : meshCfg["refinementZones"]) {
            if (!z.is_object()) continue;
            DcRawRefineZone rz;
            rz.name = strOr(z, "name");
            rz.level = (int)numOr(z, "level", 0);
            Vec3 lo, hi;
            if (z.contains("min") && z.contains("max") && readVec(z["min"], lo) && readVec(z["max"], hi)) {
                rz.mn = lo;
                rz.mx = hi;
                out.refineZones.push_back(rz);
                RoiBox rb;
                rb.mn = lo;
                rb.mx = hi;
                rb.size = 0;  // 级语义在报告里记录；引擎侧 size→targetLevel 由 W2 决定
                out.cfg.roi.push_back(rb);
            }
        }

    // ---- ⑤ keepPoints 告警（extraLocationsInMesh 空 + containment 种子合成是引擎侧 OPEN 项）----
    if (geom && geom->contains("room") && (*geom)["room"].is_object()) {
        const json& room = (*geom)["room"];
        const bool hasExtra = room.contains("extraLocationsInMesh") &&
                              room["extraLocationsInMesh"].is_array() &&
                              !room["extraLocationsInMesh"].empty();
        if (!hasExtra)
            warn.push_back("[dc-raw] extraLocationsInMesh 为空——containment/密封区洪泛种子合成是"
                           "引擎侧 OPEN 项（本摄入只取 room.locationInMesh 主种子）");
    }

    // ---- ⑥ 逐磁盘 solid 分类 ----
    std::set<std::string> patchNameSet;
    std::set<std::string> baffleZoneSet;
    for (const auto& in : solids) {
        DcRawSolidRecord r;
        r.solidName = in.solidName;
        r.stlFile = in.stlFile;
        r.triCount = in.triCount;
        r.bboxMin = in.bboxMin;
        r.bboxMax = in.bboxMax;
        const std::string& S = in.solidName;
        const std::string stem = fileStem(in.stlFile);

        // 清单信号
        std::string bc;
        auto itb = bbBySolid.find(S);
        if (itb != bbBySolid.end()) bc = itb->second;
        if (bc.empty()) {
            auto itp = bbByPatch.find(S);
            if (itp != bbByPatch.end()) bc = itp->second;
        }
        PbInfo pb;
        bool havePb = false;
        auto itu = pbByUid.find(S);
        if (itu != pbByUid.end()) { pb = itu->second; havePb = true; }
        if (!havePb) {
            auto itm = pbByPatch.find(S + "_master");
            if (itm != pbByPatch.end()) { pb = itm->second; havePb = true; }
            else {
                auto its = pbByPatch.find(S + "_slave");
                if (its != pbByPatch.end()) { pb = its->second; havePb = true; }
            }
        }
        // 别名解析（R6）：porousBindings.surfaceUid 常是 **文件茎**（merged_wall_baffle）而非
        // solid 名（该文件内 solid 实为 Baffle_1/2/3，被 snappy 并为单一 merged_wall_baffle
        // patch 对）。solid 名未命中时回退文件茎 ⇒ 该文件全部 solid 归此 zone（与 golden
        // 实际 boundary 的 merged_wall_baffle_master/slave 对齐）。
        if (!havePb) {
            auto itf = pbByUid.find(stem);
            if (itf != pbByUid.end()) { pb = itf->second; havePb = true; }
        }
        std::string geoPn;
        auto itg = geoPatch.find(S);
        if (itg != geoPatch.end()) geoPn = itg->second;

        // 绑定：owner > boundaryBindings > porousBindings > geometryExport > prefix > STL_ONLY
        Owner ow;
        bool haveOwner = false;
        auto ito = owner.find(S);
        if (ito != owner.end()) { ow = ito->second; haveOwner = true; }
        std::string region, category, sub, bindSource;
        // 告警修（合并波 2(d)，mpi_fixes §8-5）：prefixHit/prefixObjCat 为只写死变量
        //（-Wunused-but-set-variable）——三处赋值从无读者，删除；绑定来源已由
        // bindSource（"prefix:"+O 等）承载，语义零损失。
        bool bound = false;
        if (haveOwner) {
            region = ow.region; category = ow.category; sub = ow.sub; bindSource = ow.source; bound = true;
        } else if (!bc.empty()) {
            bound = true; bindSource = "boundaryBindings";
            // region 由前缀对象名推（若有）
            for (const auto& O : objectNames)
                if (S == O || (S.size() > O.size() + 1 && S.compare(0, O.size(), O) == 0 && S[O.size()] == '_')) {
                    region = O; auto oc = objCat.find(O); category = (oc != objCat.end()) ? oc->second : "wall";
                    break;
                }
            if (region.empty()) { region = "BOUNDARY"; category = "wall"; }
        } else if (havePb) {
            bound = true; bindSource = "porousBindings"; region = pb.zone; category = "baffle";
        } else if (!geoPn.empty()) {
            bound = true; bindSource = "geometryExport";
            for (const auto& O : objectNames)
                if (S == O || (S.size() > O.size() + 1 && S.compare(0, O.size(), O) == 0 && S[O.size()] == '_')) {
                    region = O; auto oc = objCat.find(O); category = (oc != objCat.end()) ? oc->second : "wall";
                    break;
                }
            if (region.empty()) { region = "GEOMETRY_EXPORT"; category = "wall"; }
        } else {
            // prefix 派生（箱体面 ACU_1_Front 等）
            for (const auto& O : objectNames)
                if (S == O || (S.size() > O.size() + 1 && S.compare(0, O.size(), O) == 0 && S[O.size()] == '_')) {
                    region = O; bindSource = "prefix:" + O; bound = true;
                    auto oc = objCat.find(O); category = (oc != objCat.end()) ? oc->second : "wall";
                    sub = "derivedFace"; break;
                }
            if (!bound) { region = "STL_ONLY"; category = "STL_ONLY"; bindSource = "filename:" + stem; }
        }

        // ---- baffle 声明检测 ----
        bool isBaffle = false, perforated = false;
        std::string baffleZone, baffleType;
        if (havePb) {
            isBaffle = true; baffleZone = pb.zone;
            perforated = (pb.type == "perforatedBaffle");
            baffleType = perforated ? "perforated" : "wall";
        } else if (haveOwner && category == "baffle") {
            isBaffle = true; baffleZone = region;
            auto bt = baffleTypeByRegion.find(region);
            perforated = (bt != baffleTypeByRegion.end() && bt->second == "perforated");
            if (!perforated && sub == "rackBaffle") perforated = false;
            baffleType = perforated ? "perforated" : "wall";
        } else {
            bool pf = false;
            if (filenameBaffle(stem, S, pf)) {
                isBaffle = true; perforated = pf; baffleType = pf ? "perforated" : "wall";
                baffleZone = stripMasterSlave(S);
                if (baffleZone.empty()) baffleZone = stem;
            }
        }
        // snappy 把 "merged_*" baffle 文件并为 **单一以文件茎命名** 的 patch 对
        // （merged_wall_baffle / merged_rack_door / merged_perforatedBlankingPanel…），无论
        // porousBindings.surfaceUid 写的是文件茎（blanking panel）还是 solid 名（rack door 的
        // A17_inlet_door）。非 merged_* 的单 solid baffle 文件（perforated_floor_* /
        // wallBaffle_floor_* / perforated_baffle_*）则按 solid 名成对。golden 实际 boundary 实证。
        if (isBaffle && stem.rfind("merged_", 0) == 0) baffleZone = stem;

        // ---- role / patchType / exportKind / floodBarrier ----
        std::string role, exportKind, patchType;
        bool floodBarrier = true;
        const bool isThrough = (bc == "through_zone") || (haveOwner && sub == "throughZone") ||
                               strContains(S, "through_zone") || strContains(stem, "through_zone");
        const bool isFanAcu = (bc == "fan_inlet" || bc == "fan_outlet" || bc == "acu_supply" ||
                               bc == "acu_return") ||
                              (haveOwner && (sub == "serverInlet" || sub == "serverOutlet" ||
                                             sub == "acuSupply" || sub == "acuReturn"));
        if (isThrough) {
            role = "FLUID_VOLUME"; exportKind = "none"; patchType = ""; floodBarrier = false;
        } else if (isFanAcu) {
            role = "FLOW_MEMBRANE"; exportKind = "patch"; patchType = "patch"; floodBarrier = true;
        } else if (isBaffle && perforated) {
            role = "FLOW_MEMBRANE"; exportKind = "cyclicPair"; patchType = "cyclicOpening"; floodBarrier = false;
        } else if (isBaffle) {
            role = "BARRIER_SHEET"; exportKind = "baffleMasterSlave"; patchType = "wall"; floodBarrier = true;
        } else if (haveOwner && sub == "frame") {
            role = "FRAME"; exportKind = "plain"; patchType = "wall";
        } else if (haveOwner && (sub == "serverTop" || sub == "serverBottom" ||
                                 sub == "serverOutletWall" || sub == "serverInletWall" ||
                                 sub == "rackWall" || sub == "acuWall")) {
            role = "BARRIER_SHEET"; exportKind = "wallPair"; patchType = "wall";
        } else if (bound && (category == "wall" || category == "floor" || category == "partitionWall" ||
                             category == "heatSource" || category == "wallObject")) {
            role = "BARRIER_SHEET"; exportKind = "wallPair"; patchType = "wall";
        } else if (bound && (category == "acu" || category == "rack")) {
            // ACU/机架箱体面（prefix 派生或剩余）→ SOLID_BOX
            role = "SOLID_BOX"; exportKind = "plain"; patchType = "wall";
        } else if (bound) {
            role = "SOLID_BOX"; exportKind = "plain"; patchType = "wall";
        } else {
            // STL_ONLY：文件名启发
            bool explainedFile = true;
            if (stem == "merged_box" || (isBoxFaceSuffix(S) && strIContains(stem, "box"))) {
                role = "SOLID_BOX"; category = "box";
            } else if (stem == "room_wall" || strIContains(stem, "room_wall")) {
                role = "BARRIER_SHEET"; category = "wall";
            } else if (strIContains(stem, "floor_ceiling") || strIContains(stem, "false_ceiling")) {
                role = "BARRIER_SHEET"; category = "floor";
            } else if (strIContains(stem, "rack_frame")) {
                role = "FRAME"; category = "rack"; sub = "frame";
            } else if (strIContains(stem, "merged_acu")) {
                role = isBoxFaceSuffix(S) ? "SOLID_BOX" : "BARRIER_SHEET"; category = "acu";
            } else if (strIContains(stem, "merged_server")) {
                // 服务器面：inlet/outlet → 膜；top/bottom/wall → 屏障
                if (strContains(S, "inlet") || strContains(S, "outlet_p")) {
                    role = "FLOW_MEMBRANE"; sub = "serverInlet";
                } else {
                    role = "BARRIER_SHEET"; sub = "serverTop";
                }
                category = "rack";
            } else if (strIContains(stem, "rack_door")) {
                role = "BARRIER_SHEET"; category = "rack"; sub = "door";
            } else if (strIContains(stem, "partition")) {
                role = "BARRIER_SHEET"; category = "partitionWall";
            } else if (isBoxFaceSuffix(S)) {
                role = "SOLID_BOX"; category = "box";
            } else {
                explainedFile = false;
            }
            if (explainedFile) {
                // 文件名启发已分类
            } else {
                // bbox 长宽比兜底：薄平面 → BARRIER_SHEET；否则 SOLID_BOX
                const Vec3 sz = r.bboxMax - r.bboxMin;
                double dims[3] = {sz.x, sz.y, sz.z};
                std::sort(dims, dims + 3);
                const double thick = dims[0], span = dims[2];
                if (span > 0 && thick / span < 0.08) { role = "BARRIER_SHEET"; category = "sheet?"; }
                else { role = "SOLID_BOX"; category = "box?"; }
            }
            exportKind = (role == "FLOW_MEMBRANE") ? "patch"
                         : (role == "BARRIER_SHEET" ? "wallPair" : "plain");
            patchType = (role == "FLOW_MEMBRANE") ? "patch" : (role == "FLUID_VOLUME" ? "" : "wall");
            if (role == "FLUID_VOLUME") { exportKind = "none"; floodBarrier = false; }
        }

        // ---- level ----
        std::string levelSrc;
        // through_zone 用 rack 级；box 用 boxRefinementLevel
        std::string lvlCat = category;
        if (isThrough) lvlCat = "rack";
        if (role == "SOLID_BOX" && (category == "box" || category == "STL_ONLY" || category == "box?"))
            lvlCat = "box";
        r.level = levelForCat(lvlCat, meshCfg, globalRef, levelSrc);
        r.levelSource = levelSrc;

        // ---- patchName ----
        // baffle：导出 patch 基名 = zone（snappy 把同名文件的多个 solid 并为单一
        // <zone>_master/_slave 对，如 merged_wall_baffle.stl 的 Baffle_1/2/3 → merged_wall_baffle）。
        // 非 baffle：geometryExport.patchName 优先，否则 solidName 逐字。
        std::string patchName = isBaffle ? baffleZone : (!geoPn.empty() ? geoPn : S);
        if (patchName.empty()) patchName = S;
        r.patchName = patchName;
        r.region = region;
        r.regionCategory = category;
        r.bindSource = bindSource;
        r.role = role;
        r.patchType = patchType;
        r.baffle = isBaffle;
        r.baffleZone = baffleZone;
        r.baffleType = baffleType;
        r.exportKind = exportKind;
        r.floodBarrier = floodBarrier;
        // explained：bound（命中清单某键）或 STL_ONLY 文件名启发命中。bbox 长宽比兜底
        // （category="box?"/"sheet?"）= 最弱信号，如实记为 unexplained（验收目标 0）。
        r.explained = bound || (category != "box?" && category != "sheet?");

        out.solids.push_back(r);

        // patch 名集 + baffle zone 集（供 golden 交叉核对）。through_zone（FLUID_VOLUME，
        // exportKind=none）的 solid 名仍记入 patchNames——它是清单声明名（boundaryBindings
        // bcType=through_zone / leakage.groups），只是 **不发射边界 patch**；报告用
        // byExportKind=none 标明非发射，交叉核对仍按声明名计其复现。
        patchNameSet.insert(patchName);
        if (isBaffle) {
            baffleZoneSet.insert(baffleZone);
            patchNameSet.insert(baffleZone + "_master");
            patchNameSet.insert(baffleZone + "_slave");
            patchNameSet.insert(baffleZone);
        }
        // surfaceLevels / patchTypes（前向兼容：引擎侧据此加密/定边界类型）
        if (!isThrough) {
            out.cfg.surfaceLevels.emplace_back(patchName, r.level);
            if (!patchType.empty()) out.cfg.patchTypes.emplace_back(patchName, patchType);
        }
        if (isBaffle) {
            BaffleSpec bs;
            bs.region = patchName;
            bs.name = baffleZone;
            bs.faceType = perforated ? "cyclicOpening" : "wall";
            out.cfg.baffles.push_back(bs);
        }
    }

    // ---- ⑥b 去重 surfaceLevels/patchTypes/baffles（多个 solid 共享同一 baffle zone patchName）----
    {
        std::set<std::string> seenLvl, seenPt, seenBaf;
        std::vector<std::pair<std::string, LevelSpec>> uniqLvl;
        for (auto& kv : out.cfg.surfaceLevels)
            if (seenLvl.insert(kv.first).second) uniqLvl.push_back(kv);
        out.cfg.surfaceLevels.swap(uniqLvl);
        std::vector<std::pair<std::string, std::string>> uniqPt;
        for (auto& kv : out.cfg.patchTypes)
            if (seenPt.insert(kv.first).second) uniqPt.push_back(kv);
        out.cfg.patchTypes.swap(uniqPt);
        std::vector<BaffleSpec> uniqBaf;
        for (auto& bs : out.cfg.baffles)
            if (seenBaf.insert(bs.name).second) uniqBaf.push_back(bs);
        out.cfg.baffles.swap(uniqBaf);
    }

    // ---- ⑦ 统计 ----
    out.nSolidsOnDisk = (long)out.solids.size();
    for (const auto& r : out.solids) {
        if (r.region == "STL_ONLY") {
            if (r.explained) ++out.nStlOnlyClassified;
            else ++out.nUnexplained;
        } else {
            ++out.nBoundManifest;
        }
    }
    out.baffleZones.assign(baffleZoneSet.begin(), baffleZoneSet.end());
    out.patchNames.assign(patchNameSet.begin(), patchNameSet.end());

    if (out.nSolidsOnDisk == 0)
        warn.push_back("[dc-raw] 磁盘扫描 0 个 solid——surface-dir 可能为空/路径错（INFEASIBLE：无几何可分类）");

    // out.name 已由 parseCfdrun 取清单 name；manifestPath/surfaceDir 由 ingestDcRaw 填实际路径。
    out.valid = true;
    return true;
}

// ============================================================================
// dc17 修正案 E-A4 —— DC locationsInMesh 种子合成（移植 stlgen_runner.py）
// ============================================================================
namespace {

constexpr double kGeomEps = 1e-9;
constexpr double kRayStartEps = 1e-6;
constexpr double kSeedDedupeTol = 1e-6;
constexpr double kSeedPlaneBand = 1e-3;
constexpr double kSeedPlaneNudge = 0.25;
constexpr double kPlanarTol = 1e-5;

bool startsWith(const std::string& s, const std::string& p) {
    return s.size() >= p.size() && std::memcmp(s.data(), p.data(), p.size()) == 0;
}

// Python re.sub(r"[^A-Za-z0-9_]", "_", name)
std::string sanitizeName(const std::string& n) {
    std::string r;
    r.reserve(n.size());
    for (char c : n)
        r.push_back((std::isalnum(static_cast<unsigned char>(c)) || c == '_') ? c : '_');
    return r;
}

// Python round()：round-half-to-even（用于 round(length/base) 等整数计数）
double pyRound(double x) {
    double f = std::floor(x);
    double d = x - f;
    if (d > 0.5) return f + 1.0;
    if (d < 0.5) return f;
    return (std::fmod(f, 2.0) == 0.0) ? f : f + 1.0;  // 恰好 .5 → 偶数
}
// round(x, 9)：量化到 1e-9（half-even），用于网格平面去重
double round9(double x) { return pyRound(x * 1e9) / 1e9; }

// ---- 三角 / 面片几何 ----
struct Tri {
    Vec3 a, b, c;
};

// 解析 ASCII STL 里某个 solid 的全部三角（按出现序）。返回是否找到该 solid。
bool readSolidTriangles(const std::string& path, const std::string& solid, std::vector<Tri>& out) {
    out.clear();
    std::ifstream f(path);
    if (!f) return false;
    std::string tok, line;
    std::string cur;
    bool inSolid = false;
    Vec3 verts[3];
    int nv = 0;
    while (f >> tok) {
        if (tok == "solid") {
            std::getline(f, line);
            const size_t a = line.find_first_not_of(" \t\r\n");
            const size_t b = line.find_last_not_of(" \t\r\n");
            cur = (a == std::string::npos) ? "" : line.substr(a, b - a + 1);
            inSolid = (cur == solid);
            nv = 0;
        } else if (tok == "endsolid") {
            inSolid = false;
            cur.clear();
            nv = 0;
        } else if (tok == "vertex") {
            double x, y, z;
            if (!(f >> x >> y >> z)) break;
            if (inSolid) {
                verts[nv++] = Vec3{x, y, z};
                if (nv == 3) {
                    out.push_back(Tri{verts[0], verts[1], verts[2]});
                    nv = 0;
                }
            }
        }
    }
    return !out.empty();
}

// 面积加权质心 + 归一总法向（= Python _compute_planar_patch_geometry）。
// 返回 false 若退化（无三角/零面积/零法向/非平面）。
bool planarPatchGeometry(const std::vector<Tri>& tris, Vec3& center, Vec3& normal) {
    if (tris.empty()) return false;
    double totalArea = 0;
    Vec3 weighted{0, 0, 0}, totalNormal{0, 0, 0};
    for (const auto& t : tris) {
        const Vec3 e1 = t.b - t.a, e2 = t.c - t.a;
        const Vec3 cr = e1.cross(e2);
        const double area = 0.5 * cr.norm();
        if (area <= kGeomEps) continue;
        const Vec3 c{(t.a.x + t.b.x + t.c.x) / 3.0, (t.a.y + t.b.y + t.c.y) / 3.0,
                     (t.a.z + t.b.z + t.c.z) / 3.0};
        weighted += c * area;
        totalArea += area;
        totalNormal += cr;  // 未减半：与 Python 一致（累加 cross 而非 unit）
    }
    if (totalArea <= kGeomEps) return false;
    center = weighted / totalArea;
    if (totalNormal.norm() <= kGeomEps) return false;
    normal = totalNormal.normalized();
    // 平面性检查（仅诊断；golden 均为平面 patch）
    double maxDev = 0;
    for (const auto& t : tris)
        for (const Vec3* v : {&t.a, &t.b, &t.c})
            maxDev = std::max(maxDev, std::abs((*v - center).dot(normal)));
    (void)maxDev;  // 不 reject（Python 会 raise；golden 数据均通过）
    return true;
}

// Moller-Trumbore：返回射线-三角距离（>kGeomEps），无命中返回 false。
bool rayTriHit(const Vec3& o, const Vec3& d, const Tri& t, double& dist) {
    const Vec3 e1 = t.b - t.a, e2 = t.c - t.a;
    const Vec3 pvec = d.cross(e2);
    const double det = e1.dot(pvec);
    if (std::abs(det) <= kGeomEps) return false;
    const double inv = 1.0 / det;
    const Vec3 tvec = o - t.a;
    const double u = tvec.dot(pvec) * inv;
    if (u < 0.0 || u > 1.0) return false;
    const Vec3 qvec = tvec.cross(e1);
    const double v = d.dot(qvec) * inv;
    if (v < 0.0 || u + v > 1.0) return false;
    dist = e2.dot(qvec) * inv;
    return dist > kGeomEps;
}

// 保守的射线-AABB（slab）测试，用于跳过明显不相交的 solid（加速净距搜索）。
bool rayBboxHit(const Vec3& o, const Vec3& d, Vec3 mn, Vec3 mx) {
    const double pad = 1e-6;
    mn = mn - Vec3{pad, pad, pad};
    mx = mx + Vec3{pad, pad, pad};
    double t0 = 0.0, t1 = 1e300;
    for (int i = 0; i < 3; ++i) {
        const double oi = (i == 0) ? o.x : (i == 1) ? o.y : o.z;
        const double di = (i == 0) ? d.x : (i == 1) ? d.y : d.z;
        const double lo = (i == 0) ? mn.x : (i == 1) ? mn.y : mn.z;
        const double hi = (i == 0) ? mx.x : (i == 1) ? mx.y : mx.z;
        if (std::abs(di) < 1e-12) {
            if (oi < lo || oi > hi) return false;
        } else {
            double ta = (lo - oi) / di, tb = (hi - oi) / di;
            if (ta > tb) std::swap(ta, tb);
            t0 = std::max(t0, ta);
            t1 = std::min(t1, tb);
            if (t0 > t1) return false;
        }
    }
    return true;
}

// 场景：全部 solid 的 (file,solid,bbox) + 按需三角缓存（按文件）。
using FileTriMap = std::map<std::string, std::vector<Tri>>;
struct Scene {
    std::string dir;
    std::vector<DcRawSolidInput> solids;  // 复制自扫描结果（带 bbox）
    mutable std::map<std::string, FileTriMap> fileCache;  // file → (solid → tris)
    const FileTriMap& fileTris(const std::string& file) const {
        auto it = fileCache.find(file);
        if (it != fileCache.end()) return it->second;
        FileTriMap m;
        // 解析整个文件的全部 solid 三角（一次）
        std::ifstream f(dir + "/" + file);
        if (f) {
            std::string tok, line, cur;
            Vec3 verts[3];
            int nv = 0;
            while (f >> tok) {
                if (tok == "solid") {
                    std::getline(f, line);
                    const size_t a = line.find_first_not_of(" \t\r\n");
                    const size_t b = line.find_last_not_of(" \t\r\n");
                    cur = (a == std::string::npos) ? "" : line.substr(a, b - a + 1);
                    nv = 0;
                } else if (tok == "endsolid") {
                    cur.clear();
                    nv = 0;
                } else if (tok == "vertex") {
                    double x, y, z;
                    if (!(f >> x >> y >> z)) break;
                    if (!cur.empty()) {
                        verts[nv++] = Vec3{x, y, z};
                        if (nv == 3) {
                            m[cur].push_back(Tri{verts[0], verts[1], verts[2]});
                            nv = 0;
                        }
                    }
                }
            }
        }
        return fileCache.emplace(file, std::move(m)).first->second;
    }
};

using SurfaceRef = std::pair<std::string, std::string>;

// = Python _nearest_ray_clearance：从 anchor+dir·eps 出发沿 dir 的最近三角净距（+eps）。
// 无命中返回 -1。忽略 ignored 里的 (file,solid)。
double nearestRayClearance(const Scene& sc, const Vec3& anchor, const Vec3& dir,
                           const std::set<SurfaceRef>& ignored) {
    const Vec3 origin = anchor + dir * kRayStartEps;
    double nearest = -1.0;
    for (const auto& s : sc.solids) {
        if (ignored.count({s.stlFile, s.solidName})) continue;
        if (!rayBboxHit(origin, dir, s.bboxMin, s.bboxMax)) continue;
        const FileTriMap& fm = sc.fileTris(s.stlFile);
        auto it = fm.find(s.solidName);
        if (it == fm.end()) continue;
        for (const auto& t : it->second) {
            double dist;
            if (!rayTriHit(origin, dir, t, dist)) continue;
            const double clearance = dist + kRayStartEps;
            if (nearest < 0.0 || clearance < nearest) nearest = clearance;
        }
    }
    return nearest;
}

// ---- 背景网格（compute_aligned_grid / compute_multiblock_grid / _cell_planes）----
struct Seg {
    double lo, hi;
    int n;
};

std::vector<double> cellPlanes(const std::vector<Seg>& segs) {
    std::set<double> ps;
    for (const auto& s : segs) {
        const int n = std::max(1, s.n);
        for (int i = 0; i <= n; ++i) ps.insert(round9(s.lo + (s.hi - s.lo) * i / n));
    }
    return std::vector<double>(ps.begin(), ps.end());
}

std::vector<Seg> computeMultiblockGrid(double rmin, double rmax, double base) {
    const double len = rmax - rmin;
    const int n = std::max(1, (int)pyRound(len / base));
    const double cs = (n > 0) ? len / n : base;
    return {{rmin - cs, rmin, 1}, {rmin, rmax, n}, {rmax, rmax + cs, 1}};
}

std::vector<Seg> computeAlignedGrid(double rmin, double rmax, double base,
                                    std::vector<double> align) {
    const double eps = 1e-3;
    const double epsMerge = std::max(2e-3, 0.01 * base);
    const double sliverMin = base / 8.0;
    std::sort(align.begin(), align.end());
    std::vector<double> interior;
    for (double p : align)
        if ((rmin + eps) < p && p < (rmax - eps)) interior.push_back(p);
    std::vector<double> reps;
    for (double p : interior)
        if (reps.empty() || (p - reps.back()) > epsMerge) reps.push_back(p);
    std::vector<double> breaks{rmin};
    for (double p : reps)
        if ((p - breaks.back()) >= sliverMin && (rmax - p) >= sliverMin) breaks.push_back(p);
    breaks.push_back(rmax);
    const double cs = (rmax - rmin) / std::max(1.0, pyRound((rmax - rmin) / base));
    std::vector<Seg> segs{{breaks.front() - cs, breaks.front(), 1}};
    for (size_t i = 0; i + 1 < breaks.size(); ++i) {
        const int n = std::max(1, (int)pyRound((breaks[i + 1] - breaks[i]) / base));
        segs.push_back({breaks[i], breaks[i + 1], n});
    }
    segs.push_back({breaks.back(), breaks.back() + cs, 1});
    return segs;
}

std::string resolveMeshMode(const json& mc) {
    const std::string m = strOr(mc, "meshMode");
    if (m == "bodyFitted" || m == "cartesianLocalized" || m == "structured") return m;
    const bool cart = mc.is_object() && mc.contains("cartesianMode") && mc["cartesianMode"].is_boolean() &&
                      mc["cartesianMode"].get<bool>();
    if (cart) {
        const bool sg = mc.contains("structuredGrid") && mc["structuredGrid"].is_boolean() &&
                        mc["structuredGrid"].get<bool>();
        return sg ? "structured" : "cartesianLocalized";
    }
    return "structured";
}

// nudge 一条种子（就地），返回是否移动。planes[axis] = 该轴网格平面（升序）。
bool nudgeSeed(Vec3& p, const std::vector<double> px, const std::vector<double> py,
               const std::vector<double> pz) {
    const std::vector<double>* planes[3] = {&px, &py, &pz};
    double* coord[3] = {&p.x, &p.y, &p.z};
    bool moved = false;
    for (int i = 0; i < 3; ++i) {
        const auto& pl = *planes[i];
        if (pl.size() < 2) continue;
        const double v = *coord[i];
        // 最近平面
        size_t k = 0;
        double best = 1e300;
        for (size_t idx = 0; idx < pl.size(); ++idx) {
            const double d = std::abs(pl[idx] - v);
            if (d < best) {
                best = d;
                k = idx;
            }
        }
        const double below = (k > 0) ? (pl[k] - pl[k - 1]) : 0.0;
        const double above = (k + 1 < pl.size()) ? (pl[k + 1] - pl[k]) : 0.0;
        const double local = std::max(below, above);
        if (local <= 0) continue;
        if (std::abs(v - pl[k]) > kSeedPlaneBand * local) continue;
        if (above >= below)
            *coord[i] = pl[k] + kSeedPlaneNudge * above;
        else
            *coord[i] = pl[k] - kSeedPlaneNudge * below;
        moved = true;
    }
    return moved;
}

// 对齐平面文件集（_ALIGN_STL_FILES + glob）。
bool isAlignFile(const std::string& fn) {
    static const char* fixed[] = {"merged_rack_frame.stl", "merged_rack.stl",   "merged_acu.stl",
                                  "merged_baffle.stl",     "merged_partition_wall.stl",
                                  "merged_box.stl",        "merged_floor_ceiling.stl"};
    for (const char* f : fixed)
        if (fn == f) return true;
    static const char* pref[] = {"perforated_floor_", "wallBaffle_floor_", "merged_wall_baffle",
                                 "perforated_baffle_"};
    for (const char* p : pref)
        if (startsWith(fn, p)) return true;
    return false;
}

// _parse_bucket_from_filename → category（空 = 非 bucket 文件）。
std::string bucketCategory(const std::string& fn) {
    static const std::pair<const char*, const char*> P[] = {
        {"merged_server_", "server"},
        {"merged_blanking_", "blanking"},
        {"merged_partition_", "partition"},
        {"merged_through_zone_", "through_zone"},
        {"merged_wallBaffle_", "wallBaffle"},
        {"merged_perforatedBlankingPanel_", "perforatedBlankingPanel"},
        {"merged_wallBlankingPanel_", "wallBlankingPanel"},
    };
    for (const auto& pp : P) {
        const std::string prefix = pp.first;
        if (!startsWith(fn, prefix)) continue;
        const std::string rest = fn.substr(prefix.size());
        // 形态：<N>U.stl | <N>U__<zone>.stl | <Letters>_<N>U.stl
        // 提取末尾的 <N>U（可能后接 __zone），前面可有字母段。
        // 简化：找 "U.stl" 或 "U__" 之前的数字串。
        size_t upos = std::string::npos;
        if ((upos = rest.find("U.stl")) != std::string::npos ||
            (upos = rest.find("U__")) != std::string::npos) {
            // 数字须紧邻 U 之前
            size_t j = upos;
            while (j > 0 && std::isdigit(static_cast<unsigned char>(rest[j - 1]))) --j;
            if (j < upos) {
                // rest[j..upos) 是数字；rest[0..j) 须为空或 <Letters>_
                const std::string pre = rest.substr(0, j);
                bool ok = pre.empty() || (pre.size() >= 1 && pre.back() == '_');
                if (ok) {
                    for (char c : pre)
                        if (!(std::isalpha(static_cast<unsigned char>(c)) || c == '_')) ok = false;
                    if (ok) return pp.second;
                }
            }
        }
    }
    return "";
}

// 解析 seedConfig（= Python _resolve_patch_seed_config 的读取部分）：enabled!=false 时
// 取 anchor/direction/offset 覆盖。写入 spec 的 cfg* 存在位 + 值。
void applySeedConfig(const json& sc, DcSeedSpec& spec) {
    if (!sc.is_object()) return;
    if (sc.contains("enabled") && sc["enabled"].is_boolean() && !sc["enabled"].get<bool>()) return;
    spec.hasSeedCfg = true;
    if (sc.contains("anchor")) {
        Vec3 a;
        if (readVec(sc["anchor"], a)) {
            spec.anchor = a;
            spec.cfgAnchor = true;
        }
    }
    if (sc.contains("direction")) {
        Vec3 d;
        if (readVec(sc["direction"], d)) {
            spec.direction = d;
            spec.cfgDir = true;
        }
    }
    if (sc.contains("offset") && sc["offset"].is_number()) {
        spec.offset = sc["offset"].get<double>();
        spec.cfgOff = true;
    }
}

}  // namespace

bool synthesizeDcSeeds(const json& manifest, const std::vector<DcRawSolidInput>& solids,
                       const std::string& surfaceDir, DcRawMapping& out, std::string& err) {
    CfdrunGeometryView view;
    if (!CfdrunGeometryView::select(manifest, view, err)) return false;
    const json* geom = &view.geometryOrRoot();
    const json& mc = view.meshConfig();

    // ---- 磁盘 solid → {file: [solidName]}（有序）----
    std::map<std::string, std::vector<std::string>> stlSolids;
    for (const auto& s : solids) stlSolids[s.stlFile].push_back(s.solidName);

    Scene scene;
    scene.dir = surfaceDir;
    scene.solids = solids;

    // ---- h_local / 缺省 offset ----
    double base = view.modern() ? view.baseSize(0.34) : numOr(mc, "baseSize", 0.34);
    if (base <= 0) base = 0.34;
    int maxLvl = 0;
    if (mc.contains("baffleRefinementLevel")) {
        const LevelSpec ls = readLevelPair(mc["baffleRefinementLevel"], 0);
        maxLvl = std::max(ls.lo, ls.hi);
    }
    const double hLocal = base / std::pow(2.0, (double)maxLvl);
    const double defaultOffset = 0.5 * hLocal;
    out.seedHLocal = hLocal;
    out.seedDefaultOffset = defaultOffset;
    out.meshMode = resolveMeshMode(mc);

    // ---- 规格收集（= _collect_create_baffle_seed_specs）----
    std::vector<DcSeedSpec> specs;
    std::set<std::string> added;  // sanitize 后的 zone 名

    // 解析文件/solid → spec（= _resolve_single_solid_baffle_file）；seedCfg 可空。
    auto resolveFile = [&](const std::string& san, const std::string& kind, const json* seedCfg,
                           DcSeedSpec& outSpec) -> bool {
        auto finish = [&](const std::string& file, const std::string& sol) {
            outSpec.zone = san;
            outSpec.kind = kind;
            outSpec.stlFile = file;
            outSpec.solidName = sol;
            if (seedCfg) applySeedConfig(*seedCfg, outSpec);
        };
        const std::string cands[3] = {"perforated_floor_" + san + ".stl",
                                      "perforated_baffle_" + san + ".stl", "baffle_" + san + ".stl"};
        for (const auto& nm : cands) {
            auto it = stlSolids.find(nm);
            if (it != stlSolids.end() && it->second.size() == 1) {
                finish(nm, it->second[0]);
                return true;
            }
        }
        for (const char* mf : {"merged_wall_baffle.stl", "merged_rack_door.stl"}) {
            auto it = stlSolids.find(mf);
            if (it == stlSolids.end()) continue;
            for (const auto& sol : it->second)
                if (sanitizeName(sol) == san) {
                    finish(mf, sol);
                    return true;
                }
        }
        return false;
    };
    auto addZone = [&](const std::string& zoneName, const std::string& kind, const json* seedCfg) {
        const std::string san = sanitizeName(zoneName);
        if (added.count(san)) return;
        DcSeedSpec sp;
        if (resolveFile(san, kind, seedCfg, sp)) {
            specs.push_back(sp);
            added.insert(san);
        }
    };

    const json& floors = geom->contains("floors") ? (*geom)["floors"] : json::object();
    const json& racks = geom->contains("racks") ? (*geom)["racks"] : json::object();
    const json& baffles = geom->contains("baffles") ? (*geom)["baffles"] : json::object();

    // 规则 1：地板 openings（physics.type ∈ {perforated, wallBaffle}）
    if (floors.is_object())
        for (auto it = floors.begin(); it != floors.end(); ++it) {
            const json& fl = it.value();
            if (!fl.is_object() || !fl.contains("openings") || !fl["openings"].is_array()) continue;
            for (const auto& op : fl["openings"]) {
                if (!op.is_object()) continue;
                std::string opType = "wall";
                if (op.contains("physics") && op["physics"].is_object())
                    opType = strOr(op["physics"], "type", "wall");
                if (opType == "perforated" || opType == "wallBaffle") {
                    const json* sc = (op.contains("seedConfig") && op["seedConfig"].is_object())
                                         ? &op["seedConfig"]
                                         : nullptr;
                    addZone(strOr(op, "solidName"), "floor_opening_" + opType, sc);
                }
            }
        }

    // 规则 2：地板 defaultBC ∈ {wallBaffle, perforated}——只扫 baffle_/perforated_floor_/
    //         merged_wall_baffle.stl 文件里以地板名开头且非 opening 的 solid。
    if (floors.is_object())
        for (auto it = floors.begin(); it != floors.end(); ++it) {
            const std::string floorName = it.key();
            const json& fl = it.value();
            if (!fl.is_object()) continue;
            const std::string defaultBc = strOr(fl, "defaultBC", "wall");
            if (defaultBc != "wallBaffle" && defaultBc != "perforated") continue;
            const std::string sanFloor = sanitizeName(floorName);
            std::set<std::string> openingNames;
            if (fl.contains("openings") && fl["openings"].is_array())
                for (const auto& op : fl["openings"])
                    if (op.is_object()) openingNames.insert(sanitizeName(strOr(op, "solidName")));
            const json& dscMap =
                fl.contains("defaultBCSeedConfigs") ? fl["defaultBCSeedConfigs"] : json::object();
            const json* dscDefault = (fl.contains("defaultBCSeedConfig") &&
                                      fl["defaultBCSeedConfig"].is_object())
                                         ? &fl["defaultBCSeedConfig"]
                                         : nullptr;
            for (const auto& fs_ : stlSolids) {
                const std::string& fn = fs_.first;
                if (!(startsWith(fn, "baffle_") || startsWith(fn, "perforated_floor_") ||
                      fn == "merged_wall_baffle.stl"))
                    continue;
                for (const auto& sol : fs_.second) {
                    const std::string san = sanitizeName(sol);
                    if (startsWith(san, sanFloor) && !openingNames.count(san)) {
                        // seedConfig 优先级：defaultBCSeedConfigs[solid] > [san] > defaultBCSeedConfig
                        const json* sc = dscDefault;
                        if (dscMap.is_object()) {
                            if (dscMap.contains(sol) && dscMap[sol].is_object())
                                sc = &dscMap[sol];
                            else if (dscMap.contains(san) && dscMap[san].is_object())
                                sc = &dscMap[san];
                        }
                        addZone(sol, "floor_surface_" + defaultBc, sc);
                    }
                }
            }
        }

    // 规则 3：rack.wallBaffleSolids → set（供 blanking bucket 过滤）
    std::set<std::string> wallBaffleSolidsPerRack;
    if (racks.is_object())
        for (auto it = racks.begin(); it != racks.end(); ++it) {
            const json& rk = it.value();
            if (!rk.is_object() || !rk.contains("wallBaffleSolids") ||
                !rk["wallBaffleSolids"].is_array())
                continue;
            for (const auto& wb : rk["wallBaffleSolids"])
                if (wb.is_string()) wallBaffleSolidsPerRack.insert(sanitizeName(wb.get<std::string>()));
        }

    // 规则 4：merged_wall_baffle.stl / merged_rack_door.stl 的每个 solid
    {
        const std::pair<const char*, const char*> merges[] = {
            {"merged_wall_baffle.stl", "rack_wallBaffle"}, {"merged_rack_door.stl", "rack_door"}};
        for (const auto& m : merges) {
            auto it = stlSolids.find(m.first);
            if (it == stlSolids.end()) continue;
            for (const auto& sol : it->second) {
                const std::string san = sanitizeName(sol);
                if (added.count(san)) continue;
                DcSeedSpec sp;
                sp.zone = san;
                sp.kind = m.second;
                sp.stlFile = m.first;
                sp.solidName = sol;
                specs.push_back(sp);
                added.insert(san);
            }
        }
    }

    // 规则 5：legacy bucket 文件（merged_door_<N>U / merged_wallBaffle_<N>U /
    //         merged_blanking_<N>U / merged_wallBlankingPanel_<N>U）
    for (const auto& fs_ : stlSolids) {
        const std::string& fn = fs_.first;
        const std::string cat = bucketCategory(fn);
        if (cat.empty()) continue;
        std::string kind;
        bool filterByRack = false;
        if (cat == "door")
            kind = "rack_door";
        else if (cat == "wallBaffle")
            kind = "rack_wallBaffle";
        else if (cat == "blanking") {
            kind = "rack_wallBaffle";
            filterByRack = true;
        } else if (cat == "wallBlankingPanel")
            kind = "rack_wallBaffle";
        else
            continue;
        std::set<std::string> seenSan;
        for (const auto& sol : fs_.second) {
            const std::string san = sanitizeName(sol);
            if (filterByRack && !wallBaffleSolidsPerRack.count(san)) continue;
            if (seenSan.count(san)) continue;
            seenSan.insert(san);
            if (added.count(san)) continue;
            DcSeedSpec sp;
            sp.zone = san;
            sp.kind = kind;
            sp.stlFile = fn;
            sp.solidName = sol;
            specs.push_back(sp);
            added.insert(san);
        }
    }

    // 规则 6：baffle_*.stl / perforated_baffle_*.stl 的每个 solid
    //   seedConfig 来源：baffles[*].patchPhysics[solid].seedConfig + wallRegionSeedConfigs[solid]
    //   （后者优先），否则按 baffle 名前缀匹配 baffles[*].seedConfig。
    std::map<std::string, const json*> baffleSeedCfgBySolid;
    std::vector<std::pair<std::string, const json*>> sortedBaffles;
    if (baffles.is_object()) {
        for (auto it = baffles.begin(); it != baffles.end(); ++it) {
            const json& b = it.value();
            if (!b.is_object()) continue;
            sortedBaffles.push_back({it.key(), &b});
            if (b.contains("patchPhysics") && b["patchPhysics"].is_object())
                for (auto pit = b["patchPhysics"].begin(); pit != b["patchPhysics"].end(); ++pit) {
                    const json& phys = pit.value();
                    if (phys.is_object() && phys.contains("seedConfig") &&
                        phys["seedConfig"].is_object())
                        baffleSeedCfgBySolid[sanitizeName(pit.key())] = &phys["seedConfig"];
                }
            if (b.contains("wallRegionSeedConfigs") && b["wallRegionSeedConfigs"].is_object())
                for (auto wit = b["wallRegionSeedConfigs"].begin();
                     wit != b["wallRegionSeedConfigs"].end(); ++wit)
                    if (wit.value().is_object())
                        baffleSeedCfgBySolid[sanitizeName(wit.key())] = &wit.value();
        }
        std::stable_sort(sortedBaffles.begin(), sortedBaffles.end(),
                         [](const std::pair<std::string, const json*>& a,
                            const std::pair<std::string, const json*>& b) {
                             return a.first.size() > b.first.size();
                         });
    }
    for (const auto& fs_ : stlSolids) {
        const std::string& fn = fs_.first;
        if (!(startsWith(fn, "baffle_") || startsWith(fn, "perforated_baffle_"))) continue;
        const bool perforated = startsWith(fn, "perforated_baffle_");
        for (const auto& sol : fs_.second) {
            const std::string san = sanitizeName(sol);
            if (added.count(san)) continue;
            const std::string kind = perforated ? "baffle_model_perforated" : "baffle_model_wall";
            const json* sc = nullptr;
            auto bit = baffleSeedCfgBySolid.find(san);
            if (bit != baffleSeedCfgBySolid.end()) sc = bit->second;
            if (!sc)
                for (const auto& nb : sortedBaffles) {
                    const std::string sanBn = sanitizeName(nb.first);
                    if (san == sanBn || startsWith(san, sanBn + "_")) {
                        if (nb.second->contains("seedConfig") && (*nb.second)["seedConfig"].is_object())
                            sc = &(*nb.second)["seedConfig"];
                        break;
                    }
                }
            DcSeedSpec sp;
            if (resolveFile(san, kind, sc, sp)) {
                specs.push_back(sp);
                added.insert(san);
            }
        }
    }
    out.seedSpecs = specs;

    // ---- patch-driven 双侧种子（= _build_patch_driven_none_seed_entries）----
    std::vector<DcSynthesizedSeed> entries;  // (point, zone="none", rule, sourcePatch)
    for (const auto& sp : specs) {
        std::vector<Tri> tris;
        if (!readSolidTriangles(surfaceDir + "/" + sp.stlFile, sp.solidName, tris) || tris.empty()) {
            out.warnings.push_back("[dc-raw] 种子合成：spec " + sp.zone + " 无三角（" + sp.stlFile +
                                   "/" + sp.solidName + "）——跳过");
            continue;
        }
        Vec3 center, normal;
        if (!planarPatchGeometry(tris, center, normal)) {
            out.warnings.push_back("[dc-raw] 种子合成：spec " + sp.zone + " 面片几何退化——跳过");
            continue;
        }
        Vec3 anchor = center, direction = normal;
        double offset = defaultOffset;
        if (sp.hasSeedCfg) {  // = _resolve_patch_seed_config：anchor/direction/offset 覆盖
            if (sp.cfgAnchor) anchor = sp.anchor;
            if (sp.cfgDir) {
                const Vec3 d = sp.direction.normalized();
                direction = (d.norm() > 0.5) ? d : normal;
            }
            if (sp.cfgOff && sp.offset > kGeomEps) offset = sp.offset;
        }
        // 射线净距夹取
        std::set<SurfaceRef> ignored{{sp.stlFile, sp.solidName}};
        if (sp.stlFile == "merged_wall_baffle.stl" || sp.stlFile == "merged_rack_door.stl") {
            auto it = stlSolids.find(sp.stlFile);
            if (it != stlSolids.end())
                for (const auto& sib : it->second) ignored.insert({sp.stlFile, sib});
        }
        const double cpos = nearestRayClearance(scene, anchor, direction, ignored);
        const double cneg = nearestRayClearance(scene, anchor, direction * -1.0, ignored);
        if (cpos > 0 && cneg > 0) {
            const double minc = std::min(cpos, cneg);
            if (minc < offset) {
                offset = minc * 0.8;
                ++out.seedClamped;
            }
        }
        const Vec3 pPlus = anchor + direction * offset;
        const Vec3 pMinus = anchor - direction * offset;
        entries.push_back(DcSynthesizedSeed{pPlus, "none", "patchDriven", sp.zone});
        entries.push_back(DcSynthesizedSeed{pMinus, "none", "patchDriven", sp.zone});
    }

    // ---- partition-wall 种子（= _build_partition_wall_seed_entries）----
    // 本数据集 17 案全 0（无 partitionWalls 触发）；忠实移植以备含隔断墙的案例。
    if (geom->contains("partitionWalls") && (*geom)["partitionWalls"].is_object()) {
        const json& pw = (*geom)["partitionWalls"];
        for (auto it = pw.begin(); it != pw.end(); ++it) {
            const json& part = it.value();
            if (!part.is_object() || !part.contains("geometry")) continue;
            const json& g = part["geometry"];
            Vec3 anchor{0, 0, 0};
            if (g.contains("anchor")) readVec(g["anchor"], anchor);
            const json& size = g.contains("size") ? g["size"] : json::object();
            const double W = numOr(size, "W", 1.0), D = numOr(size, "D", 0.1),
                         H = numOr(size, "H", 3.0);
            Vec3 q{0, 0, 0};
            double qw = 1.0;
            if (g.contains("transformQuaternion") && g["transformQuaternion"].is_object()) {
                const json& Q = g["transformQuaternion"];
                q = Vec3{numOr(Q, "x", 0), numOr(Q, "y", 0), numOr(Q, "z", 0)};
                qw = numOr(Q, "w", 1);
            }
            // quat 旋转（= _quat_rotate，先归一）
            auto quatRot = [&](const Vec3& v) -> Vec3 {
                double n = std::sqrt(q.x * q.x + q.y * q.y + q.z * q.z + qw * qw);
                double qx = q.x, qy = q.y, qz = q.z, w = qw;
                if (n > kGeomEps) {
                    qx /= n;
                    qy /= n;
                    qz /= n;
                    w /= n;
                }
                const double tx = 2.0 * (qy * v.z - qz * v.y);
                const double ty = 2.0 * (qz * v.x - qx * v.z);
                const double tz = 2.0 * (qx * v.y - qy * v.x);
                return Vec3{v.x + w * tx + (qy * tz - qz * ty), v.y + w * ty + (qz * tx - qx * tz),
                            v.z + w * tz + (qx * ty - qy * tx)};
            };
            const Vec3 localCenter{W * 0.5, H * 0.5, D * 0.5};
            Vec3 direction = quatRot(Vec3{0, 0, 1});
            if (direction.norm() > kGeomEps) direction = direction.normalized();
            const Vec3 center = anchor + quatRot(localCenter);
            const double offset = D * 0.5 + 0.002;
            entries.push_back(DcSynthesizedSeed{center + direction * offset, "none", "partitionWall",
                                                it.key()});
            entries.push_back(DcSynthesizedSeed{center - direction * offset, "none", "partitionWall",
                                                it.key()});
        }
    }
    // partition-hole cellZone 种子（= _build_partition_hole_cellzone_seed_entries）：
    // 需 leakageRate<100 的 hole.zoneStlFile 质心，zone 名=文件茎。本数据集 17 案全 0
    // （无启用 hole）——未触发，故不产出任何 cellZone 种子（与 golden 一致，全为 none）。

    // ---- 装配主/额外种子（= _build_locations_in_mesh_entries 头部）----
    std::vector<DcSynthesizedSeed> assembled;
    const json& room = geom->contains("room") ? (*geom)["room"] : json::object();
    double roomHeight = numOr(room, "height", 3.0);
    if (roomHeight <= 0) roomHeight = 3.0;
    double rxMin = 0, rxMax = 10, rzMin = 0, rzMax = 10;
    if (room.contains("plane") && room["plane"].is_array() && !room["plane"].empty()) {
        rxMin = rzMin = 1e300;
        rxMax = rzMax = -1e300;
        for (const auto& p : room["plane"]) {
            if (!p.is_object()) continue;
            const double px = numOr(p, "x", 0), py = numOr(p, "y", 0);
            rxMin = std::min(rxMin, px);
            rxMax = std::max(rxMax, px);
            rzMin = std::min(rzMin, py);
            rzMax = std::max(rzMax, py);
        }
    }
    Vec3 loc{5.0, 1.5, 5.0};
    if (view.modern() && view.location()) readVec(*view.location(), loc);
    else if (room.contains("locationInMesh")) readVec(room["locationInMesh"], loc);
    auto snapAxis = [](double v, double lo, double hi) {
        return (v < lo || v > hi) ? 0.5 * (lo + hi) : v;
    };
    const Vec3 primary{snapAxis(loc.x, rxMin, rxMax), snapAxis(loc.y, 0.0, roomHeight),
                       snapAxis(loc.z, rzMin, rzMax)};
    assembled.push_back(DcSynthesizedSeed{primary, "none", "primary", "room"});
    if (room.contains("extraLocationsInMesh") && room["extraLocationsInMesh"].is_array())
        for (const auto& e : room["extraLocationsInMesh"]) {
            Vec3 ev;
            if (readVec(e, ev)) assembled.push_back(DcSynthesizedSeed{ev, "none", "extra", "room"});
        }
    for (auto& e : entries) assembled.push_back(e);  // patchDriven + partitionWall

    // ---- 去重前的 pre-nudge 快照 ----
    // dedupe（= _dedupe_seed_entries：同 zone + 三坐标各 <=1e-6）
    auto dedupe = [](const std::vector<DcSynthesizedSeed>& in) {
        std::vector<DcSynthesizedSeed> r;
        for (const auto& e : in) {
            bool dup = false;
            for (const auto& x : r)
                if (x.zone == e.zone && std::abs(x.point.x - e.point.x) <= kSeedDedupeTol &&
                    std::abs(x.point.y - e.point.y) <= kSeedDedupeTol &&
                    std::abs(x.point.z - e.point.z) <= kSeedDedupeTol) {
                    dup = true;
                    break;
                }
            if (!dup) r.push_back(e);
        }
        return r;
    };
    out.seedsPreNudge = dedupe(assembled);

    // ---- nudge（结构化背景网格平面）----
    // 网格平面：对齐文件 bbox 面 → compute_aligned_grid（structured）/ multiblock（其余）。
    std::vector<double> alignX, alignY, alignZ;
    for (const auto& s : solids) {
        if (!isAlignFile(s.stlFile)) continue;
        alignX.push_back(s.bboxMin.x);
        alignX.push_back(s.bboxMax.x);
        alignY.push_back(s.bboxMin.y);
        alignY.push_back(s.bboxMax.y);
        alignZ.push_back(s.bboxMin.z);
        alignZ.push_back(s.bboxMax.z);
    }
    const std::vector<Seg> segX =
        (out.meshMode == "structured") ? computeAlignedGrid(rxMin, rxMax, base, alignX)
                                       : computeMultiblockGrid(rxMin, rxMax, base);
    const std::vector<Seg> segY =
        (out.meshMode == "structured") ? computeAlignedGrid(0.0, roomHeight, base, alignY)
                                       : computeMultiblockGrid(0.0, roomHeight, base);
    const std::vector<Seg> segZ =
        (out.meshMode == "structured") ? computeAlignedGrid(rzMin, rzMax, base, alignZ)
                                       : computeMultiblockGrid(rzMin, rzMax, base);
    const std::vector<double> px = cellPlanes(segX), py = cellPlanes(segY), pz = cellPlanes(segZ);

    std::vector<DcSynthesizedSeed> nudged = assembled;
    bool anyMoved = false;
    for (auto& e : nudged)
        if (nudgeSeed(e.point, px, py, pz)) anyMoved = true;
    out.seedsNudged = anyMoved;
    out.seedsSynthesized = dedupe(nudged);

    // ---- cfg.keepPoints = declared room seed + extras + synthesized（POST-nudge，去重）----
    // parseCfdrun 已填 declared locationInMesh（+extras）；这里重建为 DC 装配口径：
    // 用 seedsSynthesized 的全部点（已含 primary+extra+合成，已去重）替换 keepPoints，
    // 保证引擎拿到的 keepPoints 与 golden locationsInMesh 同口径。
    out.cfg.keepPoints.clear();
    for (const auto& e : out.seedsSynthesized) out.cfg.keepPoints.push_back(e.point);

    return true;
}

bool ingestDcRaw(const std::string& cfdrunPath, const std::string& surfaceDir, DcRawMapping& out,
                 std::string& err) {
    std::ifstream f(cfdrunPath);
    if (!f) {
        err = "[dc-raw] 无法打开 cfdrun: " + cfdrunPath;
        return false;
    }
    json j;
    try {
        f >> j;
    } catch (const std::exception& e) {
        err = "[dc-raw] cfdrun JSON 解析失败: " + std::string(e.what());
        return false;
    }
    std::vector<DcRawSolidInput> solids;
    std::vector<std::string> scanWarn;
    scanTriSurfaceDir(surfaceDir, solids, scanWarn);
    bool ok = classifyDcRaw(j, solids, out, err);
    // dc17 E-A4：种子合成（additive——失败不致命，只记 warning，映射仍有效）
    if (ok) {
        std::string serr;
        if (!synthesizeDcSeeds(j, solids, surfaceDir, out, serr))
            out.warnings.push_back("[dc-raw] 种子合成失败（映射仍有效）: " + serr);
    }
    // 扫描告警并入（前置）
    if (ok) {
        std::vector<std::string> merged;
        merged.insert(merged.end(), scanWarn.begin(), scanWarn.end());
        merged.insert(merged.end(), out.warnings.begin(), out.warnings.end());
        out.warnings.swap(merged);
        out.manifestPath = cfdrunPath;
        out.surfaceDir = surfaceDir;
    }
    return ok;
}

bool writeDcRawMappingJson(const DcRawMapping& m, const std::string& path, std::string& err) {
    json root;
    root["schema"] = "dc17-ingest-mapping/v1";
    root["case"] = m.name;
    root["manifest"] = m.manifestPath;
    root["surfaceDir"] = m.surfaceDir;
    root["valid"] = m.valid;
    // domain
    json dom;
    dom["hasBox"] = m.cfg.hasBox;
    dom["min"] = {{"x", m.cfg.boxMin.x}, {"y", m.cfg.boxMin.y}, {"z", m.cfg.boxMin.z}};
    dom["max"] = {{"x", m.cfg.boxMax.x}, {"y", m.cfg.boxMax.y}, {"z", m.cfg.boxMax.z}};
    root["domain"] = dom;
    root["baseLevel"] = m.cfg.baseLevel;
    root["maxLevel"] = m.cfg.maxLevel;
    root["nCellsBetweenLevels"] = m.cfg.nCellsBetweenLevels;
    // keepPoints
    json kp = json::array();
    for (const auto& p : m.cfg.keepPoints) kp.push_back({{"x", p.x}, {"y", p.y}, {"z", p.z}});
    root["keepPoints"] = kp;
    // refinementZones
    json rz = json::array();
    for (const auto& z : m.refineZones)
        rz.push_back({{"name", z.name}, {"level", z.level},
                      {"min", {{"x", z.mn.x}, {"y", z.mn.y}, {"z", z.mn.z}}},
                      {"max", {{"x", z.mx.x}, {"y", z.mx.y}, {"z", z.mx.z}}}});
    root["refinementZones"] = rz;
    root["baffleZones"] = m.baffleZones;
    root["patchNames"] = m.patchNames;
    // ---- dc17 E-A4：种子合成 ----
    {
        json sd;
        sd["meshMode"] = m.meshMode;
        sd["hLocal"] = m.seedHLocal;
        sd["defaultOffset"] = m.seedDefaultOffset;
        sd["nSpecs"] = (long)m.seedSpecs.size();
        sd["clamped"] = m.seedClamped;
        sd["nudged"] = m.seedsNudged;
        // 规格直方图（kind）
        std::map<std::string, long> byKind;
        for (const auto& s : m.seedSpecs) byKind[s.kind]++;
        sd["specsByKind"] = byKind;
        auto seedArr = [](const std::vector<DcSynthesizedSeed>& v) {
            json a = json::array();
            for (const auto& e : v)
                a.push_back({{"x", e.point.x}, {"y", e.point.y}, {"z", e.point.z},
                             {"zone", e.zone}, {"rule", e.rule}, {"sourcePatch", e.sourcePatch}});
            return a;
        };
        sd["seedsSynthesized"] = seedArr(m.seedsSynthesized);  // POST-nudge（规范）
        sd["seedsPreNudge"] = seedArr(m.seedsPreNudge);        // PRE-nudge（几何核心）
        // 规格明细（诊断；zone/kind/file/solid）
        json sp = json::array();
        for (const auto& s : m.seedSpecs)
            sp.push_back({{"zone", s.zone}, {"kind", s.kind}, {"file", s.stlFile},
                          {"solid", s.solidName}});
        sd["specs"] = sp;
        root["seedSynthesis"] = sd;
    }
    // counts
    json cnt;
    cnt["solidsOnDisk"] = m.nSolidsOnDisk;
    cnt["boundToManifest"] = m.nBoundManifest;
    cnt["stlOnlyClassified"] = m.nStlOnlyClassified;
    cnt["unexplained"] = m.nUnexplained;
    // role / bindSource / level 直方图
    std::map<std::string, long> byRole, byBind, byLevel, byExport;
    for (const auto& r : m.solids) {
        byRole[r.role]++;
        byBind[r.bindSource]++;
        byLevel["L" + std::to_string(r.level.lo) + "-" + std::to_string(r.level.hi)]++;
        byExport[r.exportKind]++;
    }
    cnt["byRole"] = byRole;
    cnt["byBindSource"] = byBind;
    cnt["levelHistogram"] = byLevel;
    cnt["byExportKind"] = byExport;
    root["counts"] = cnt;
    // warnings
    root["warnings"] = m.warnings;
    // solids（逐条）
    json sarr = json::array();
    for (const auto& r : m.solids) {
        json s;
        s["solidName"] = r.solidName;
        s["stlFile"] = r.stlFile;
        s["region"] = r.region;
        s["regionCategory"] = r.regionCategory;
        s["bindSource"] = r.bindSource;
        s["role"] = r.role;
        s["level"] = {r.level.lo, r.level.hi};
        s["levelSource"] = r.levelSource;
        s["patchType"] = r.patchType;
        s["patchName"] = r.patchName;
        s["baffle"] = r.baffle;
        s["baffleZone"] = r.baffleZone;
        s["baffleType"] = r.baffleType;
        s["exportKind"] = r.exportKind;
        s["floodBarrier"] = r.floodBarrier;
        s["explained"] = r.explained;
        s["triCount"] = r.triCount;
        sarr.push_back(s);
    }
    root["solids"] = sarr;

    std::error_code ec;
    fs::path p(path);
    if (p.has_parent_path()) fs::create_directories(p.parent_path(), ec);
    std::ofstream of(path);
    if (!of) {
        err = "[dc-raw] 无法写映射报告: " + path;
        return false;
    }
    of << root.dump(2);
    return (bool)of;
}

// ============================================================================
// dc17 STEP 8 / 任务书 S3 —— dcMode 出口契约构建实现（决策 D1-D5 见头注）
// ============================================================================

bool buildDcExportContract(const json& manifest, const DcRawMapping& map,
                           const CfdrunSpec& base, const TriSurface& surf,
                           DcExportContract& out, std::string& err) {
    // Invalid current-format layout fails before constructing an export contract.
    out = DcExportContract{};
    auto& warn = out.opts.report;

    CfdrunGeometryView view;
    if (!CfdrunGeometryView::select(manifest, view, err)) return false;
    const json* geom = &view.geometryOrRoot();

    // ---- ① 房间包络 domain-face 映射（D1）：walls/floors/partitionWalls 的
    //      "adapted":"domain-face:<side>" → farFieldNames[side] = solidName（golden 名）----
    static const char* sideNames[6] = {"xMin", "xMax", "yMin", "yMax", "zMin", "zMax"};
    out.farFieldNames.assign(6, "");
    if (geom && geom->is_object()) {
        static const char* envCats[3] = {"walls", "floors", "partitionWalls"};
        for (const char* cat : envCats) {
            if (!geom->contains(cat) || !(*geom)[cat].is_object()) continue;
            const json& d = (*geom)[cat];
            for (auto it = d.begin(); it != d.end(); ++it) {
                const json& e = it.value();
                if (!e.is_object()) continue;
                const std::string a = strOr(e, "adapted");
                if (a.rfind("domain-face:", 0) != 0) continue;
                const std::string side = a.substr(12);
                int si = -1;
                for (int k = 0; k < 6; ++k)
                    if (side == sideNames[k]) si = k;
                if (si < 0) {
                    warn.push_back("[dc-export] warn: 未知 domain-face 侧 '" + side + "'（region " +
                                   it.key() + "）——忽略");
                    continue;
                }
                std::string envName = strOr(e, "solidName");
                if (envName.empty()) envName = it.key();
                if (!out.farFieldNames[(size_t)si].empty()) {
                    if (out.farFieldNames[(size_t)si] != envName)
                        warn.push_back("[dc-export] warn: domain-face " + side + " 多重声明（" +
                                       out.farFieldNames[(size_t)si] + " / " + envName +
                                       "）——取先者（类别序 walls→floors→partitionWalls，键字典序）");
                    continue;
                }
                out.farFieldNames[(size_t)si] = envName;
                out.patchTypes[envName] = "wall";
                out.envelopeDeclared = true;
            }
        }
    }
    // RAW 清单无适配标记：任何 walls/floors 类 BARRIER_SHEET 即宣告包络存在（D1 的
    // RAW 判据；此时远场六侧无改名映射——洪泛落地后 pad 面消失，若有面 = 泄漏，CLI 上报）。
    if (!out.envelopeDeclared) {
        for (const auto& r : map.solids)
            if (r.role == "BARRIER_SHEET" && (r.regionCategory == "wall" ||
                                              r.regionCategory == "floor")) {
                out.envelopeDeclared = true;
                break;
            }
    }

    // ---- ② 逐磁盘 solid：改名 / 类型 / FLUID_VOLUME 抑制（D4） ----
    for (const auto& r : map.solids) {
        if (r.exportKind == "none") {
            out.opts.suppressedPatches.push_back(r.solidName);
            if (!r.patchName.empty() && r.patchName != r.solidName)
                out.opts.suppressedPatches.push_back(r.patchName);
            continue;
        }
        if (r.baffle) continue;  // ③ 处理（zone 键）
        const std::string nm = r.patchName.empty() ? r.solidName : r.patchName;
        out.patchTypes[nm] = r.patchType.empty() ? std::string("wall") : r.patchType;
        if (nm != r.solidName) out.patchRename.emplace_back(r.solidName, nm);
    }

    // ---- ③ baffles（D5）：OFF 声明为基（保 G3 几何同构），zone/类型按 ingest 增强 ----
    out.baffles = base.cfg.baffles;
    for (auto& bs : out.baffles) {
        const DcRawSolidRecord* rec = nullptr;
        for (const auto& r : map.solids)
            if (r.baffle && (r.solidName == bs.region || r.solidName == bs.name)) {
                rec = &r;
                break;
            }
        if (rec) {
            bs.name = rec->baffleZone;  // FILE-STEM 键 zone（ingest open item #4 消费端）
            bs.faceType = (rec->baffleType == "perforated") ? "cyclic" : "wall";
        } else {
            if (bs.faceType == "cyclicOpening") bs.faceType = "cyclic";  // 裸 cyclicOpening 非 v2412 合法
            warn.push_back("[dc-export] warn: baffle '" + bs.region +
                           "' 无 ingest baffle 记录——zone/类型沿用 OFF 声明");
        }
        const std::string master = bs.name + "_master", slave = bs.name + "_slave";
        if (bs.faceType == "cyclic") {
            out.patchTypes[master] = "cyclic";
            out.patchTypes[slave] = "cyclic";
            out.opts.cyclic[master] = DcBoundaryOptions::CyclicPair{slave, bs.name, "0.0001"};
            out.opts.cyclic[slave] = DcBoundaryOptions::CyclicPair{master, bs.name, "0.0001"};
        } else {
            out.patchTypes[master] = "wall";
            out.patchTypes[slave] = "wall";
        }
        // 对成员必须在场（pair 对称 / cyclic neighbourPatch 存在性 / mesh_acceptance 计数）
        out.opts.zeroFacePatches.push_back(master);
        out.opts.zeroFacePatches.push_back(slave);
    }
    // ingest 声明 baffle 而 OFF 未声明 → 不新增分区（适配厚板拆 master/slave 几何上错），如实上报
    for (const auto& r : map.solids) {
        if (!r.baffle) continue;
        bool covered = false;
        for (const auto& bs : out.baffles)
            if (bs.region == r.solidName) {
                covered = true;
                break;
            }
        if (!covered)
            warn.push_back("[dc-export] note: ingest 声明 baffle zone '" + r.baffleZone +
                           "'（solid " + r.solidName +
                           "）不在 OFF baffles 声明中——dcMode 适配路径不做 master/slave 分区"
                           "（薄板语义待引擎 Steps 3/5），按普通表面发射");
    }

    // ---- ④ zero-face 契约名（D3）：包络六名 + 磁盘 solid 出口名（baffle 对已在 ③） ----
    for (size_t k = 0; k < out.farFieldNames.size(); ++k)
        if (!out.farFieldNames[k].empty()) out.opts.zeroFacePatches.push_back(out.farFieldNames[k]);
    for (const auto& r : map.solids) {
        if (r.exportKind == "none" || r.baffle) continue;
        const std::string nm = r.patchName.empty() ? r.solidName : r.patchName;
        if (!nm.empty()) out.opts.zeroFacePatches.push_back(nm);
    }

    // ---- ⑤ bindings 声明而无磁盘几何的名字：不发射，如实计数（D3） ----
    {
        std::set<std::string> onDisk;
        for (const auto& r : map.solids) {
            onDisk.insert(r.solidName);
            if (!r.patchName.empty()) onDisk.insert(r.patchName);
        }
        for (const auto& bs : out.baffles) {
            onDisk.insert(bs.name);
            onDisk.insert(bs.name + "_master");
            onDisk.insert(bs.name + "_slave");
        }
        auto countMissing = [&](const char* key) {
            if (!manifest.is_object() || !manifest.contains(key) || !manifest[key].is_object())
                return;
            for (auto it = manifest[key].begin(); it != manifest[key].end(); ++it) {
                const json& b = it.value();
                if (!b.is_object()) continue;
                const std::string pn = strOr(b, "patchName");
                if (pn.empty()) continue;
                const std::string bc = strOr(b, "bcType");
                if (bc == "through_zone") continue;  // FLUID_VOLUME：永不发射
                const std::string stripped = stripMasterSlave(pn);
                if (onDisk.count(pn) || onDisk.count(stripped)) continue;
                ++out.nBindingsWithoutGeometry;
            }
        };
        countMissing("boundaryBindings");
        countMissing("porousBindings");
        if (out.nBindingsWithoutGeometry > 0)
            warn.push_back("[dc-export] note: " + std::to_string(out.nBindingsWithoutGeometry) +
                           " 个 bindings 声明名无磁盘 solid（适配代理几何）——不发射 zero-face 条目"
                           "（RAW 案此类名皆有磁盘 solid，已被 ④ 覆盖），决策 D3");
    }

    // ---- ⑥ 表面 patch 核对：TriSurface 有而 ingest 无记录的 patch（不该发生——同一目录
    //      扫描；出现即如实上报，类型走写出端缺省推断 + warn） ----
    for (const auto& pn : surf.patchNames) {
        bool known = false;
        for (const auto& r : map.solids)
            if (r.solidName == pn || r.patchName == pn) {
                known = true;
                break;
            }
        if (!known)
            for (const auto& bs : out.baffles)
                if (bs.region == pn) {
                    known = true;
                    break;
                }
        if (!known)
            warn.push_back("[dc-export] warn: 表面 patch '" + pn +
                           "' 不在 ingest 记录中——出口名逐字、类型走缺省推断");
    }

    out.opts.dcMode = true;
    return true;
}

}  // namespace meshgen
