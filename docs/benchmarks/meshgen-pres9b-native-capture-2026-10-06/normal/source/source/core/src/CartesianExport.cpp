// CartesianExport.cpp —— cut-cell 多面体网格导出 + AMR 契约落盘
//
// 基线：T0 机械搬运自 core/src/Cartesian.cpp（writeHeader+:928-995 writePolyMeshFoam、
// :1000-1116 writePolyMeshVtu）。所有权：T2（导出+算例）。
// T2 扩展（SPEC §2.4）：
//   1. 五件套 header 补 location "constant/polyMesh" + OpenFOAM banner（仿 FoamWriter.cpp:16-29）；
//      废 :922-925 的裸 header。
//   2. boundary 按引擎给的 patchNames 直写（新引擎远场拆为六 patch），导出端不重命名；
//      补"同 patch 连续段"断言（U1 §8-10：原实现依赖该假设但无断言）。
//   3. AMR 三件套 + constant/dynamicMeshDict：格式照 01_U3_amr_contract.md §5/§6.1
//      实测样例；唯一修正：level0Edge 用 v2412 关键字形式
//      （dimensions/value，UniformDimensionedField.C:116-133 强制，
//      本机 snappyHexMesh v2412 真实输出同形；U3 §6.1 的裸量纲式不被读取）。
//   4. T-HIST（AMR v1.5，roadmap A.2）：refinementHistory 完整森林史落盘
//      （01_U3 §4 方案 C：8 子齐全节点 `parent (i0..i7)`，不完整组各为根
//      `-1 0()`，visibleCells[胞元]=条目下标；导出前 checkIndices 等价自检，
//      失败拒写）。胞元→叶映射由终网格几何在导出端重建（不改引擎）：
//      洁净胞元（纯六面/切割胞，整体含于单叶盒）按顶点均值定位+包含验证；
//      跨叶合并胞（S9 吸收邻居）按面内点内移定位，分得合成根条目。
//      运行时语义见 refinementHistory.H:30-66 / hexRef8.C:5180-5275：
//      完整 8 兄弟组共享 parentIndex 可粗化合并回生成器层级；合并/切割胞
//      受 dynamicRefineFvMesh protectedCell_ 保护，永不参与运行时加密/粗化。

#include "meshgen/CartesianExport.hpp"

#include "meshgen/Cartesian.hpp"  // PolyMesh / CartesianResult 完整定义
#include "meshgen/ThreadPool.hpp" // PERF-R15：导出格式化并行

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <future>
#include <ostream>
#include <sstream>
#include <cctype>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace meshgen {

// ================= OpenFOAM 文件头/尾 =================
namespace {

// OpenFOAM 标准 banner（仿 core/src/FoamWriter.cpp:18-24，Version v2412）
void writeBanner(std::ostream& os) {
    os << "/*--------------------------------*- C++ -*----------------------------------*\\\n"
          "| =========                 |                                                 |\n"
          "| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |\n"
          "|  \\\\    /   O peration     | Version:  v2412                                 |\n"
          "|   \\\\  /    A nd           | Website:  www.openfoam.com                      |\n"
          "|    \\\\/     M anipulation  |                                                 |\n"
          "\\*---------------------------------------------------------------------------*/\n";
}

// FoamFile header：banner + version/format/class/location/object + 分隔线。
// location 默认 "constant/polyMesh"（五件套与 AMR 三件套）；dynamicMeshDict 传 "constant"。
void writeHeader(std::ostream& os, const char* cls, const char* obj,
                 const char* location = "constant/polyMesh") {
    writeBanner(os);
    os << "FoamFile\n{\n    version     2.0;\n    format      ascii;\n    class       " << cls
       << ";\n    location    \"" << location << "\";\n    object      " << obj << ";\n}\n"
       << "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n";
}

void writeFooter(std::ostream& os) {
    os << "\n// ************************************************************************* //\n";
}

// labelList 文件体：<n> ( 每行一个整数 )。
// 结尾不写 ';'（01_U3 §6.1：v2412 的 UList 输出不带终止符）。
// PERF-R15 注：五件套/AMR 大列表已改走 writeListFileParallel（并行块写），本 ostream
// 版仅供小列表/兼容保留（[[maybe_unused]] 免未引用告警）。
[[maybe_unused]] void writeLabelListBody(std::ostream& f, const std::vector<int>& v) {
    f << v.size() << "\n(\n";
    for (int x : v) f << x << '\n';
    f << ")\n";
}

// ============ PERF-R15：并行缓冲格式化 + fwrite 块写 ============
// 十进制整数追加（std::to_chars 最短表示；与 ostream<<int / %d 在 C locale 逐字节
// 一致：无分隔符/无前导零/负号 '-'）。
inline void appendInt(std::string& s, long long v) {
    char b[24];
    const auto r = std::to_chars(b, b + sizeof(b), v);
    s.append(b, (size_t)(r.ptr - b));
}

// 并行写一个 "<header><count>\n(\n<body>)\n<footer>" 形态的列表文件：
// body(lo,hi,out) 把 [lo,hi) 行格式化进 out（线程池按块并行）；块缓冲按序 fwrite
// ⇒ 落盘字节与串行逐行写完全一致。header/footer 复用既有 ostream 构造（逐字节同源）。
template <class BodyFn>
bool writeListFileParallel(const std::string& path, const char* cls, const char* obj,
                           size_t count, std::string& err, const BodyFn& body) {
    // "w"（文本模式）：Unix 下与 "wb" 同；Windows 下 \n→\r\n，与原 std::ofstream
    // 文本写逐字节一致（跨平台基线不变）。
    FILE* fp = std::fopen(path.c_str(), "w");
    if (!fp) { err = "无法写入: " + path; return false; }
    {
        std::ostringstream hs;
        writeHeader(hs, cls, obj);
        const std::string h = hs.str();
        std::fwrite(h.data(), 1, h.size(), fp);
    }
    {
        char b[64];
        const int n = std::snprintf(b, sizeof(b), "%zu\n(\n", count);
        std::fwrite(b, 1, (size_t)n, fp);
    }
    const size_t W = std::max<size_t>(1, globalPool().size());
    const size_t chunk = count > 0 ? std::max<size_t>(1, (count + W - 1) / W) : 1;
    const size_t nChunk = count > 0 ? (count + chunk - 1) / chunk : 0;
    std::vector<std::string> bufs(nChunk);
    {
        std::vector<std::future<void>> futs;
        futs.reserve(nChunk);
        for (size_t w = 0; w < nChunk; ++w) {
            const size_t lo = w * chunk, hi = std::min(count, lo + chunk);
            futs.push_back(
                globalPool().submit([&bufs, &body, w, lo, hi] { body(lo, hi, bufs[w]); }));
        }
        for (auto& f : futs) f.get();
    }
    for (const auto& s : bufs) std::fwrite(s.data(), 1, s.size(), fp);
    std::fwrite(")\n", 1, 2, fp);
    {
        std::ostringstream fs;
        writeFooter(fs);
        const std::string ft = fs.str();
        std::fwrite(ft.data(), 1, ft.size(), fp);
    }
    if (std::fclose(fp) != 0) { err = "写入失败(fclose): " + path; return false; }
    return true;
}

}  // namespace

// 缺省 patch 类型推断的【单一真源】（R14-项8）：本文件的 boundary 写出与
// cli/main.cpp 报告侧 foamPatchTypeWith 共同调用，报告 ≡ 落盘。
// 规则 = 历史缺省行为（逐字保持）："wall"/"wall*" → wall；front/back（T-2D
// mode2d 的 z-/z+ 域壁，引擎侧命名契约）→ empty；其余（远场六拆分等）→ patch。
// 注：CartesianExport.hpp 属 T2 所有权（本任务不改头文件），cli 侧以外部声明链接。
std::string cartesianDefaultPatchType(const std::string& name) {
    if (name == "wall" || name.rfind("wall", 0) == 0) return "wall";
    if (name == "front" || name == "back") return "empty";
    return "patch";
}

// ================= T-CFDRUN：cellZones（OpenFOAM zoneMesh ASCII） =================
// 仅当 m.cellZone 非空（= 装配期声明了 cellZones）时落盘；缺省空 ⇒ 不写文件、
// 五件套逐字节不变（六基线红线）。格式 = zoneMesh 列表：`<n> ( <name> { cells <list>; } )`，
// header class regIOobject / object cellZones（对齐 v2412 真实落盘）。列表恒用多行式
// （OpenFOAM 读端空白不敏感，size 后 `(` 逐行元素 `)` 再 `;`）。
static bool writeCellZones(const PolyMesh& m, const std::string& dir, std::string& err) {
    if (m.cellZone.empty()) return true;  // 无 zone：保持历史行为（不写文件）
    std::vector<std::vector<int>> zones(m.cellZoneNames.size());
    for (size_t ci = 0; ci < m.cellZone.size(); ++ci) {
        const int z = m.cellZone[ci];
        if (z >= 0 && z < (int)zones.size()) zones[(size_t)z].push_back((int)ci);
    }
    const std::string path = dir + "/cellZones";
    std::ofstream f(path);
    if (!f) { err = "无法写入: " + path; return false; }
    writeHeader(f, "regIOobject", "cellZones");
    f << zones.size() << "\n(\n";
    for (size_t z = 0; z < zones.size(); ++z) {
        f << "\n" << m.cellZoneNames[z] << "\n{\n";
        f << "    cells\n" << zones[z].size() << "\n(\n";
        for (int c : zones[z]) f << c << "\n";
        f << ")\n;\n}\n";
    }
    f << ")\n";
    writeFooter(f);
    return true;
}

// ================= dc17 Step 8 / 任务书 S3：DC 黄金 boundary 契约（dcMode） =================
// 全部只在 dc->dcMode 时启用；dc==nullptr / dcMode=false ⇒ 既有路径逐字节不变（G2 红线）。
// golden 版式实测源（只读取证）：dcdtuni case/Base_leakage/Mesh/constant/polyMesh/boundary
// （cyclic 对 Raised_Floor_copy_patch_master/slave、wall 对 merged_wall_baffle_master/slave、
// inGroups 1(wall)/2(cyclic <zone>)、值列对齐第 24 列）与 case/Slab_dataset/case_0813/
// Mesh/constant/polyMesh/faceZones（ASCII zoneMesh 全结构）。
namespace {

// golden key 字段宽 16：8 空格缩进 + key + 补齐到 16 列 ⇒ 值起于第 24 列。
// 'type            cyclic;' 的字面 12 空格是 DC 求解侧 fix_cyclic_match_tolerance
// （openfoam_case.py:37-90）的匹配硬契约，禁改窄。
void dcKey(std::ostream& os, const char* k) {
    os << "        " << k;
    for (size_t n = std::strlen(k); n < 16; ++n) os.put(' ');
}

// dcMode FLUID_VOLUME（exportKind=none）抑制：扫描边界面统计命中 suppressedPatches 的
// 面；命中 >0 时构建过滤副本（边界面恒在面表尾段，内部面/胞元/点表不动；被抑制面删除后
// 邻胞在 polyMesh 意义上开口——洪泛透明语义由引擎 Step 3/5 落地，出口端只保证
// 『不发射 patch、如实计数』，见 31_dc17_export_log.md 决策 D4）。
void dcFilterSuppressed(const PolyMesh& m, const std::vector<std::string>& names, PolyMesh& out,
                        long& nSup, std::map<std::string, long>& perName) {
    nSup = 0;
    perName.clear();
    std::vector<char> sup(m.faces.size(), 0);
    for (size_t fi = (size_t)m.nInternal; fi < m.faces.size(); ++fi) {
        if (m.neighbour[fi] >= 0) continue;
        const int pi = (fi < m.facePatch.size()) ? m.facePatch[fi] : -1;
        if (pi < 0 || pi >= (int)m.patchNames.size()) continue;
        const std::string& nm = m.patchNames[(size_t)pi];
        for (const auto& s : names)
            if (s == nm) {
                sup[fi] = 1;
                ++nSup;
                ++perName[nm];
                break;
            }
    }
    if (nSup == 0) return;
    out = PolyMesh{};
    out.points = m.points;
    out.patchNames = m.patchNames;
    out.faceZoneNames = m.faceZoneNames;
    out.cellZone = m.cellZone;
    out.cellZoneNames = m.cellZoneNames;
    out.nInternal = m.nInternal;
    out.faces.reserve(m.faces.size() - (size_t)nSup);
    for (size_t fi = 0; fi < m.faces.size(); ++fi) {
        if (sup[fi]) continue;
        out.faces.push_back(m.faces[fi]);
        out.owner.push_back(m.owner[fi]);
        out.neighbour.push_back(m.neighbour[fi]);
        out.facePatch.push_back((fi < m.facePatch.size()) ? m.facePatch[fi] : -1);
        if (!m.faceZone.empty()) out.faceZone.push_back(m.faceZone[fi]);
    }
}

// faceZones（OpenFOAM v2412 ASCII zoneMesh；golden = Slab_dataset/case_0813 实测结构）：
//   header：class regIOobject + meta { names N ( z1 … ); }（MG ASCII 无 arch 行——与
//   binary golden 的唯一头部差异，记录于 31_dc17_export_log.md 决策 D7）
//   body：N 行 + `(z1\n{ type faceZone; faceLabels List<label> <n> (…)\n; flipMap
//   List<bool> <n> (…)\n; }\nz2\n{…}\n)\n` + footer。
//   flipMap：属 <zone>_slave patch 的面 = 1（createBaffles 语义，golden 实测 master 段 0 /
//   slave 段 1），其余 = 0。faceLabels 升序（面表序）。
// m.faceZone 空（无 baffle 装配）⇒ 不写文件（DC 无 zone 算例同行为），返回 true。
bool writeFaceZonesDc(const PolyMesh& m, const std::string& dir, std::string& err,
                      std::vector<std::string>& report) {
    if (m.faceZone.empty() || m.faceZoneNames.empty()) return true;
    const size_t nz = m.faceZoneNames.size();
    std::vector<std::vector<int>> labels(nz);
    std::vector<std::vector<char>> flips(nz);
    long total = 0;
    const size_t nf = std::min(m.faces.size(), m.faceZone.size());
    for (size_t fi = 0; fi < nf; ++fi) {
        const int z = m.faceZone[fi];
        if (z < 0 || z >= (int)nz) continue;
        labels[(size_t)z].push_back((int)fi);
        char flip = 0;
        const int pi = (fi < m.facePatch.size()) ? m.facePatch[fi] : -1;
        if (pi >= 0 && pi < (int)m.patchNames.size() &&
            m.patchNames[(size_t)pi] == m.faceZoneNames[(size_t)z] + "_slave")
            flip = 1;
        flips[(size_t)z].push_back(flip);
        ++total;
    }
    const std::string path = dir + "/faceZones";
    std::ofstream f(path);
    if (!f) { err = "无法写入: " + path; return false; }
    writeBanner(f);
    f << "FoamFile\n{\n    version     2.0;\n    format      ascii;\n    class       regIOobject;\n"
      << "    location    \"constant/polyMesh\";\n    object      faceZones;\n    meta\n    {\n"
      << "        names           " << nz << " (";
    for (size_t z = 0; z < nz; ++z) f << (z ? " " : " ") << m.faceZoneNames[z];
    f << " );\n    }\n}\n"
      << "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n";
    f << nz << "\n(";
    for (size_t z = 0; z < nz; ++z) {
        f << m.faceZoneNames[z] << "\n{\n";
        f << "    type            faceZone;\n";
        f << "    faceLabels      List<label> \n" << labels[z].size() << "\n(\n";
        for (int l : labels[z]) f << l << "\n";
        f << ")\n;\n";
        f << "    flipMap         List<bool> \n" << flips[z].size() << "\n(\n";
        for (char fl : flips[z]) f << (int)fl << "\n";
        f << ")\n;\n";
        f << "}\n";
    }
    f << ")\n";
    writeFooter(f);
    report.push_back("[dc-export] faceZones: " + std::to_string(nz) + " zones / " +
                     std::to_string(total) + " faces → " + path);
    return (bool)f;
}

}  // namespace

// ================= OpenFOAM polyMesh 五件套 =================
bool writePolyMeshFoam(const PolyMesh& m, const std::string& dir, std::string& err,
                       const std::map<std::string, std::string>& patchTypes,
                       DcBoundaryOptions* dc) {
    const bool dcm = dc && dc->dcMode;
    // ---- dcMode：FLUID_VOLUME 抑制（有命中才过滤重入；适配案无此类 ⇒ 零成本直通） ----
    if (dcm && !dc->suppressedPatches.empty()) {
        PolyMesh fm;
        long nSup = 0;
        std::map<std::string, long> perName;
        dcFilterSuppressed(m, dc->suppressedPatches, fm, nSup, perName);
        if (nSup > 0) {
            for (const auto& kv : perName)
                dc->report.push_back(
                    "[dc-export] suppressed " + std::to_string(kv.second) +
                    " boundary faces of FLUID_VOLUME patch '" + kv.first +
                    "' (exportKind=none; flood-transparent semantics pending engine Steps 3/5)");
            DcBoundaryOptions dc2 = *dc;
            dc2.suppressedPatches.clear();
            dc2.report.clear();
            const bool ok = writePolyMeshFoam(fm, dir, err, patchTypes, &dc2);
            for (const auto& r : dc2.report) dc->report.push_back(r);
            return ok;
        }
        dc->report.push_back(
            "[dc-export] note: suppressedPatches declared (" +
            std::to_string(dc->suppressedPatches.size()) +
            " names) but no boundary faces carry them — nothing suppressed");
    }
    std::error_code ec;
    fs::create_directories(dir, ec);
    if (ec) { err = "无法创建目录: " + dir; return false; }
    // PERF-R15：四件大文件改『并行格式化到块缓冲 + fwrite 顺序块写』。字节流与
    // 串行逐行 ostream 写完全一致：points 保持 %.15g snprintf；faces/owner/neighbour
    // 整数经 std::to_chars（C locale 下与 ostream<<int 逐字节同）。
    if (!writeListFileParallel(dir + "/points", "vectorField", "points", m.points.size(),
                               err,
                               [&](size_t lo, size_t hi, std::string& out) {
                                   out.reserve((hi - lo) * 48);
                                   // OPT-CODE 项4：%.15g snprintf（vfprintf→dtoa，
                                   // 实测 420ms/1.9M 值）→ std::to_chars(general,15)。
                                   // C++17 [utility.to.chars]：带精度 general 形态与
                                   // printf %.*g 同串（本机 libc++ 实测 1e6 随机+格点
                                   // 双精度 0 mismatch，含 ±0/次正规/1e±300）；实测
                                   // 5.7× 提速。
                                   char b[128];
                                   auto g15 = [&](double v, char*& w) {
                                       const auto r = std::to_chars(
                                           w, b + sizeof(b), v,
                                           std::chars_format::general, 15);
                                       w = r.ptr;
                                   };
                                   for (size_t i = lo; i < hi; ++i) {
                                       const Vec3& p = m.points[i];
                                       char* w = b;
                                       *w++ = '(';
                                       g15(p.x, w);
                                       *w++ = ' ';
                                       g15(p.y, w);
                                       *w++ = ' ';
                                       g15(p.z, w);
                                       *w++ = ')';
                                       *w++ = '\n';
                                       out.append(b, (size_t)(w - b));
                                   }
                               }))
        return false;
    if (!writeListFileParallel(dir + "/faces", "faceList", "faces", m.faces.size(), err,
                               [&](size_t lo, size_t hi, std::string& out) {
                                   out.reserve((hi - lo) * 32);
                                   for (size_t i = lo; i < hi; ++i) {
                                       const std::vector<int>& fc = m.faces[i];
                                       appendInt(out, (long long)fc.size());
                                       out.push_back('(');
                                       for (size_t k = 0; k < fc.size(); ++k) {
                                           if (k) out.push_back(' ');
                                           appendInt(out, fc[k]);
                                       }
                                       out.push_back(')');
                                       out.push_back('\n');
                                   }
                               }))
        return false;
    if (!writeListFileParallel(dir + "/owner", "labelList", "owner", m.owner.size(), err,
                               [&](size_t lo, size_t hi, std::string& out) {
                                   out.reserve((hi - lo) * 10);
                                   for (size_t i = lo; i < hi; ++i) {
                                       appendInt(out, m.owner[i]);
                                       out.push_back('\n');
                                   }
                               }))
        return false;
    // 修复（承原注）：只写 nInternal 条 neighbour（OpenFOAM 规范），不写边界面的 -1
    if (!writeListFileParallel(dir + "/neighbour", "labelList", "neighbour",
                               (size_t)m.nInternal, err,
                               [&](size_t lo, size_t hi, std::string& out) {
                                   out.reserve((hi - lo) * 10);
                                   for (size_t i = lo; i < hi; ++i) {
                                       appendInt(out, m.neighbour[i]);
                                       out.push_back('\n');
                                   }
                               }))
        return false;
    {
        // boundary：边界面已按 patch 连续排列，扫描连续段。
        // 导出端不重命名（引擎负责远场六拆分 xMin/.../zMax + wall*，SPEC §2.4），
        // 仅断言同一 patch 名的边界面构成单一连续段：重名段会生成非法
        // （重复 patch 名）的 boundary，必须显式失败而非静默输出。
        // dcMode（S3-1）：拒出保持武装，但先把【全部】重名段以 [dc-export] error 响亮
        // 列到 stderr 再拒（跨文件同名 solid = 战役级 bug，如实上报不粉饰；S0 只合并
        // 文件内同名 solid）。预检在开文件前跑 ⇒ 失败不落半截 boundary。
        if (dcm) {
            std::vector<std::string> segName;
            std::vector<int> segStart;
            std::vector<std::string> dups;
            for (size_t fi = (size_t)m.nInternal; fi < m.faces.size(); ++fi) {
                if (m.neighbour[fi] >= 0) continue;
                const int pi = (fi < m.facePatch.size()) ? m.facePatch[fi] : -1;
                if (pi < 0 || pi >= (int)m.patchNames.size()) continue;  // 越界由主扫描报错
                const std::string& nm = m.patchNames[(size_t)pi];
                if (!segName.empty() && segName.back() == nm) continue;
                bool dup = false;
                for (const auto& s : segName)
                    if (s == nm) { dup = true; break; }
                if (dup) {
                    bool listed = false;
                    for (const auto& d : dups)
                        if (d == nm) { listed = true; break; }
                    if (!listed) dups.push_back(nm);
                }
                segName.push_back(nm);
                segStart.push_back((int)fi);
            }
            if (!dups.empty()) {
                std::string list;
                for (const auto& d : dups) {
                    std::fprintf(stderr,
                                 "[dc-export] error: duplicate/non-contiguous boundary patch '%s'"
                                 "（跨文件同名 solid？S0 只合并文件内同名 solid——如实上报）\n",
                                 d.c_str());
                    if (!list.empty()) list += ", ";
                    list += d;
                }
                err = "boundary: dcMode 重名段预检失败（" + std::to_string(dups.size()) +
                      " 个 patch 多段出现）: " + list;
                return false;
            }
        }
        std::ofstream f(dir + "/boundary");
        if (!f) { err = "无法写入: " + dir + "/boundary"; return false; }
        writeHeader(f, "polyBoundaryMesh", "boundary");
        struct P { std::string name; int n, start; };
        std::vector<P> patches;
        for (size_t fi = (size_t)m.nInternal; fi < m.faces.size(); ++fi) {
            if (m.neighbour[fi] >= 0) continue;  // 内部面
            int pi = (fi < m.facePatch.size()) ? m.facePatch[fi] : -1;
            if (pi < 0 || pi >= (int)m.patchNames.size()) {
                err = "boundary: 边界面 " + std::to_string(fi) + " 的 facePatch(" +
                      std::to_string(pi) + ") 越界";
                return false;
            }
            // E5c W-SB-4(iii)：patch 名 [A-Za-z0-9_]+ 消毒（dcdtuni 解析器契约；
            // 合法名恒等 ⇒ 逐字节不变；类型/契约查表仍用原名——声明名恒合法，
            // 消毒仅作用于落盘文本）。
            std::string nm = m.patchNames[pi];
            for (auto& chW : nm)
                if (!(std::isalnum((unsigned char)chW) || chW == '_')) chW = '_';
            if (!patches.empty() && patches.back().name == nm) {
                patches.back().n++;
            } else {
                for (const auto& q : patches) {
                    if (q.name == nm) {
                        err = "boundary: patch '" + nm + "' 的边界面不连续（段起于面 " +
                              std::to_string(q.start) + " 与面 " + std::to_string(fi) +
                              "）；请保证引擎按 patch 连续输出边界面";
                        return false;
                    }
                }
                patches.push_back({nm, 1, (int)fi});
            }
        }
        // dcMode：zero-face 契约名（声明而在网格里无面者）按契约序附在真实段后
        // （fields.py 严格门要求注册名在场，任务书 S3；startFace = 边界面总数）。
        std::vector<std::string> zeroNames;
        if (dcm) {
            for (const auto& zn : dc->zeroFacePatches) {
                bool have = false;
                for (const auto& p : patches)
                    if (p.name == zn) { have = true; break; }
                if (have) continue;
                bool dup = false;
                for (const auto& z : zeroNames)
                    if (z == zn) { dup = true; break; }
                if (!dup) zeroNames.push_back(zn);
            }
        }
        // dcMode 类型解析：cyclic 元数据命中 → cyclic；否则 patchTypes 覆盖表；
        // 都未命中 → 缺省推断 + 如实报告（dc 契约应覆盖全部发射名，漏网即告警）。
        auto resolveTypeDc = [&](const std::string& name) -> std::string {
            if (dc->cyclic.count(name)) return "cyclic";
            const auto it = patchTypes.find(name);
            if (it != patchTypes.end() && !it->second.empty()) return it->second;
            const std::string t = cartesianDefaultPatchType(name);
            dc->report.push_back("[dc-export] warn: patch '" + name +
                                 "' 无声明类型——回退缺省推断 '" + t + "'");
            return t;
        };
        // golden 条目版式（字段序/缩进/值列 = dcdtuni 实测 boundary 逐字节同）
        auto emitDcEntry = [&](const std::string& name, const std::string& type, int n, int start) {
            f << "    " << name << "\n    {\n";
            dcKey(f, "type");
            f << type << ";\n";
            const auto cyc = dc->cyclic.find(name);
            if (cyc != dc->cyclic.end()) {
                dcKey(f, "inGroups");
                f << "2(cyclic " << cyc->second.zone << ");\n";
            } else if (type == "wall") {
                dcKey(f, "inGroups");
                f << "1(wall);\n";
            }
            dcKey(f, "nFaces");
            f << n << ";\n";
            dcKey(f, "startFace");
            f << start << ";\n";
            if (cyc != dc->cyclic.end()) {
                dcKey(f, "matchTolerance");
                f << cyc->second.matchTolerance << ";\n";
                dcKey(f, "transform");
                f << "coincidentFullMatch;\n";
                dcKey(f, "neighbourPatch");
                f << cyc->second.neighbour << ";\n";
            }
            f << "    }\n";
        };
        // dcMode：类型一次解析（真实段 + zero-face 段；回退告警只报一次）
        std::vector<std::string> resolvedTypes;
        if (dcm) {
            resolvedTypes.reserve(patches.size() + zeroNames.size());
            for (const auto& p : patches) resolvedTypes.push_back(resolveTypeDc(p.name));
            for (const auto& zn : zeroNames) resolvedTypes.push_back(resolveTypeDc(zn));
        }
        f << (patches.size() + zeroNames.size()) << "\n(\n";
        for (size_t pi = 0; pi < patches.size(); ++pi) {
            auto& p = patches[pi];
            if (dcm) {
                emitDcEntry(p.name, resolvedTypes[pi], p.n, p.start);
                continue;
            }
            // T-PT：patchTypes 覆盖映射优先；未命中走缺省推断（历史行为，
            // 单一真源 cartesianDefaultPatchType，报告侧同调——R14-项8）
            const auto ovIt = patchTypes.find(p.name);
            std::string type;
            if (ovIt != patchTypes.end() && !ovIt->second.empty()) {
                type = ovIt->second;
            } else {
                type = cartesianDefaultPatchType(p.name);
            }
            f << "    " << p.name << "\n    {\n        type        " << type
              << ";\n        nFaces      " << p.n << ";\n        startFace   " << p.start
              << ";\n    }\n";
        }
        if (dcm) {
            const int zeroStart = (int)m.faces.size();
            for (size_t k = 0; k < zeroNames.size(); ++k) {
                const std::string& t = resolvedTypes[patches.size() + k];
                emitDcEntry(zeroNames[k], t, 0, zeroStart);
                dc->report.push_back("[dc-export] zero-face contract patch emitted: '" +
                                     zeroNames[k] + "' (type " + t + ")");
            }
            long nw = 0, np = 0, nc = 0, no = 0;
            for (const auto& t : resolvedTypes) {
                if (t == "wall") ++nw;
                else if (t == "patch") ++np;
                else if (t == "cyclic") ++nc;
                else ++no;
            }
            dc->report.push_back(
                "[dc-export] boundary: " + std::to_string(patches.size() + zeroNames.size()) +
                " patches (" + std::to_string(patches.size()) + " with faces, " +
                std::to_string(zeroNames.size()) + " zero-face); census wall=" +
                std::to_string(nw) + " patch=" + std::to_string(np) + " cyclic=" +
                std::to_string(nc) + " other=" + std::to_string(no));
        }
        f << ")\n";
        writeFooter(f);
    }
    // dc17 Step 8：faceZones（dcMode 专属；m.faceZone 空 ⇒ 不写）
    if (dcm && dc->writeFaceZones) {
        if (!writeFaceZonesDc(m, dir, err, dc->report)) return false;
    }
    // T-CFDRUN：cellZones（可选；m.cellZone 空 ⇒ 不写，五件套逐字节不变）
    if (!writeCellZones(m, dir, err)) return false;
    return true;
}

// ================= AMR 输出契约（SPEC §2.4 / 01_U3 §6.1） =================

bool writeDynamicMeshDict(const std::string& path, std::string& err) {
    const fs::path parent = fs::path(path).parent_path();
    if (!parent.empty()) {
        std::error_code ec;
        fs::create_directories(parent, ec);
    }
    std::ofstream f(path);
    if (!f) { err = "无法写入: " + path; return false; }
    // 01_U3 §5：文件必须放 constant/（dynamicFvMeshNew.C:45），不是 system/；
    // 官方模板的 minLevel/maxLevel 键是错的，代码读 lowerRefineLevel/upperRefineLevel。
    // 系数按 SPEC §2.4 定稿（maxRefinement 为 cellLevel 绝对上限）。
    writeHeader(f, "dictionary", "dynamicMeshDict", "constant");
    f << "dynamicFvMesh      dynamicRefineFvMesh;\n\n"
         "dynamicRefineFvMeshCoeffs\n{\n"
         "    refineInterval   5;\n"
         "    field            magVorticity;\n"
         "    lowerRefineLevel 5;\n"
         "    upperRefineLevel 1e10;\n"
         "    unrefineLevel    2;\n"
         "    nBufferLayers    2;\n"
         "    maxRefinement    8;\n"
         "    maxCells         4000000;\n"
         "    correctFluxes    ((phi U));\n"
         "    dumpLevel        true;\n"
         "}\n";
    writeFooter(f);
    return true;
}

bool writeCartesianAmrFiles(const CartesianResult& res, const std::string& polyMeshDir,
                            std::string& err) {
    const PolyMesh& m = res.mesh;
    const long nC = m.nCells();
    const long nP = (long)m.points.size();
    if (nC <= 0) { err = "AMR 导出: 空网格（无单元）"; return false; }
    if (res.cellLevel.empty() || (long)res.cellLevel.size() != nC) {
        err = "AMR 导出: cellLevel 尺寸 " + std::to_string(res.cellLevel.size()) +
              " != 单元数 " + std::to_string(nC) + "（引擎须按 SPEC §2.1 填充 AMR 字段）";
        return false;
    }
    if ((long)res.pointLevel.size() != nP) {
        err = "AMR 导出: pointLevel 尺寸 " + std::to_string(res.pointLevel.size()) +
              " != 点数 " + std::to_string(nP);
        return false;
    }
    if (!(res.level0Edge > 0)) {
        err = "AMR 导出: level0Edge 无效（" + std::to_string(res.level0Edge) +
              "）；必须显式给根边长 H，禁止自动推算（hexRef8.C:2784）";
        return false;
    }
    for (size_t c = 0; c < res.cellLevel.size(); ++c) {
        if (res.cellLevel[c] < 0) {
            err = "AMR 导出: cellLevel[" + std::to_string(c) + "] 为负（" +
                  std::to_string(res.cellLevel[c]) + "）";
            return false;
        }
    }
    // 2:1 自检：内部面两侧 level 差 <= 1（hexRef8 checkRefinementLevels 硬约束，
    // hexRef8.C:4901-4919；SPEC §2.8-5 要求导出端自检断言）。
    for (int fi = 0; fi < m.nInternal; ++fi) {
        const int o = m.owner[fi], nb = m.neighbour[fi];
        if (nb < 0) {
            err = "AMR 导出: 内部面 " + std::to_string(fi) + " 无 neighbour";
            return false;
        }
        const int d = std::abs(res.cellLevel[o] - res.cellLevel[nb]);
        if (d > 1) {
            err = "AMR 导出: 2:1 约束破坏——内部面 " + std::to_string(fi) + " 两侧单元 (" +
                  std::to_string(o) + ", " + std::to_string(nb) + ") 的 cellLevel 差 " +
                  std::to_string(d) + " > 1";
            return false;
        }
    }

    std::error_code ec;
    fs::create_directories(polyMeshDir, ec);
    if (ec) { err = "无法创建目录: " + polyMeshDir; return false; }

    // ---- cellLevel（class labelList）---- PERF-R15：并行格式化 + 块写
    if (!writeListFileParallel(polyMeshDir + "/cellLevel", "labelList", "cellLevel",
                               res.cellLevel.size(), err,
                               [&](size_t lo, size_t hi, std::string& out) {
                                   out.reserve((hi - lo) * 4);
                                   for (size_t i = lo; i < hi; ++i) {
                                       appendInt(out, res.cellLevel[i]);
                                       out.push_back('\n');
                                   }
                               }))
        return false;
    // ---- pointLevel（class labelList；hexRef8 加点规则：max{cellLevel(c): p∈c}）----
    if (!writeListFileParallel(polyMeshDir + "/pointLevel", "labelList", "pointLevel",
                               res.pointLevel.size(), err,
                               [&](size_t lo, size_t hi, std::string& out) {
                                   out.reserve((hi - lo) * 4);
                                   for (size_t i = lo; i < hi; ++i) {
                                       appendInt(out, res.pointLevel[i]);
                                       out.push_back('\n');
                                   }
                               }))
        return false;
    // ---- level0Edge（uniformDimensionedScalarField，dimensions [0 1 0 0 0 0 0]） ----
    // 格式必须是 v2412 关键字形式：readData 以 dictionary 解析，硬性要求
    // `dimensions`/`value` 两个字面键（UniformDimensionedField.C:116-133）；
    // 裸 "[0 1 0 0 0 0 0] <值>" 旧式会解析失败 → READ_IF_PRESENT 静默回退
    // getLevel0EdgeLength() 自动推算——正是 SPEC §2.4 明令禁止的行为。
    // 对齐本机验证过的 snappyHexMesh v2412 真实落盘格式（见 case/Sphere3D/
    // SnappyReference/constant/polyMesh/level0Edge）。
    {
        std::ofstream f(polyMeshDir + "/level0Edge");
        if (!f) { err = "无法写入: " + polyMeshDir + "/level0Edge"; return false; }
        writeHeader(f, "uniformDimensionedScalarField", "level0Edge");
        char buf[64];
        std::snprintf(buf, sizeof(buf), "%.15g", res.level0Edge);
        f << "dimensions      [0 1 0 0 0 0 0];\n"
          << "value           " << buf << ";\n";
        writeFooter(f);
    }
    // ---- refinementHistory（T-HIST，AMR v1.5）：有持久树即写完整森林史 ----
    // 无树（旧式调用方/合成网格测试）保持 v1 行为：跳过，不报错。
    // 有树但自检失败（几何映射不自洽/父链不闭合）则拒绝整个 AMR 导出。
    if (res.tree) {
        if (!writeRefinementHistory(res, polyMeshDir, err)) return false;
    }

    // ---- constant/dynamicMeshDict（父目录；U3 §5：不是 system/） ----
    {
        fs::path pm = fs::path(polyMeshDir).lexically_normal();
        fs::path constantDir = pm.parent_path();
        if (constantDir.empty()) constantDir = ".";
        if (!writeDynamicMeshDict((constantDir / "dynamicMeshDict").string(), err)) return false;
    }
    return true;
}

// ================= refinementHistory 完整森林史（T-HIST，AMR v1.5） =================
//
// 语义（01_U3 §4 方案 C / 03_SPEC §2.4）：
//   · splitCells 条目 = 八叉树节点子集（拓扑序编号，父先于子）：
//       叶节点有条目 ⟺ 该叶是"洁净可见胞元"（终网格胞元整体含于该叶盒内）；
//       内部节点有条目 ⟺ 8 子节点全有条目（整棵子树全为洁净可见叶）；
//       完整节点条目 `parent (i0..i7)`；不完整组成员各自为根 `-1 0()`。
//   · 跨叶合并胞（S9 产物，几何跨多叶）分得合成根条目 `-1 0()`。
//   · visibleCells[胞元] = 其条目下标；尺寸==单元数（hexRef8.C:1980 硬约束）。
// 胞元→叶映射由终网格几何重建（不改引擎）：先"顶点均值定位 + 全顶点含于叶盒"
// 验证（纯六面/切割胞恒满足——两者均为盒与半空间之交，凸且含于盒）；验证失败
// 者判为合并胞，用"最大面三角形质心沿内法向内移微量"的必内点定位其跨叶之一。
// 随后跑 checkIndices 等价自检（更严：双向父子闭合、父链终止、可见覆盖、无孤儿
// 条目），失败即拒写。

namespace {

// Newell 法向（对平面多边形 = 2·面积·单位法向；绕向按右手法则）。
Vec3 newellNormal(const PolyMesh& m, size_t fi) {
    Vec3 n{0, 0, 0};
    const auto& f = m.faces[fi];
    const size_t k = f.size();
    for (size_t i = 0; i < k; ++i) {
        const Vec3& a = m.points[(size_t)f[i]];
        const Vec3& b = m.points[(size_t)f[(i + 1) % k]];
        n = n + a.cross(b);
    }
    return n;
}

struct CellProbe {
    Vec3 avg{0, 0, 0};       // 顶点均值（凸胞必在胞内、必在其叶盒内）
    Vec3 inner{0, 0, 0};     // 最大面内点沿内法向内移（合并胞的必内点）
    bool hasInner = false;
};

// 单元探针点：顶点均值 + 最大面内移点（确定性：面按存储序、法向按存储绕向）。
// R14-项2：cfaces = 本胞面号逆索引（buildRefinementHistory 一次 O(F) 建好，
// 升序 = 存储序），替代原逐胞全表扫描；遍历序与旧实现逐面一致 ⇒ 结果逐位相同。
CellProbe cellProbe(const PolyMesh& m, long ci, double rootEdge,
                    const std::vector<int>& cfaces) {
    CellProbe pr;
    std::vector<int> vids;
    double bestArea2 = -1;
    size_t bestFace = SIZE_MAX;
    int bestSide = 1;
    for (int fiIdx : cfaces) {
        const size_t fi = (size_t)fiIdx;
        const bool own = (m.owner[fi] == ci);
        for (int v : m.faces[fi]) vids.push_back(v);
        const Vec3 nw = newellNormal(m, fi);  // 存储绕向 = owner 外法向
        const Vec3 outward = own ? nw : nw * -1.0;
        const double a2 = outward.norm2();
        if (a2 > bestArea2) { bestArea2 = a2; bestFace = fi; bestSide = own ? 1 : -1; }
    }
    std::sort(vids.begin(), vids.end());
    vids.erase(std::unique(vids.begin(), vids.end()), vids.end());
    for (int v : vids) pr.avg = pr.avg + m.points[(size_t)v];
    if (!vids.empty()) pr.avg = pr.avg * (1.0 / (double)vids.size());
    if (bestFace != SIZE_MAX && m.faces[bestFace].size() >= 3) {
        const auto& f = m.faces[bestFace];
        const Vec3& p0 = m.points[(size_t)f[0]];
        const Vec3& p1 = m.points[(size_t)f[1]];
        const Vec3& p2 = m.points[(size_t)f[2]];
        const Vec3 t = (p0 + p1 + p2) / 3.0;
        const Vec3 nw = newellNormal(m, bestFace);
        const double nrm = nw.norm();
        if (nrm > 1e-300) {
            // 面内点沿【胞内】方向（外法向反向）内移 1e-6·面尺度：严格进胞，
            // 且远离面边（三角形质心到面边距离 > 0），不会越过邻面。
            const double delta = 1e-6 * std::sqrt(nrm * 0.5) + 1e-9 * rootEdge;
            Vec3 inward = nw * (1.0 / nrm);
            if (bestSide < 0) inward = inward * -1.0;
            pr.inner = t - inward * delta;
            pr.hasInner = true;
        }
    }
    return pr;
}

}  // namespace

// OPT-CODE 项4 仪表：CART_DBG_TIME 门控分相账（与引擎侧 StageTimer 同口径：
// 缺省零取钟零输出；本 TU 独立缓存键值）。
namespace {
bool envDbgTimeX() {
    static const bool v = (std::getenv("CART_DBG_TIME") != nullptr);
    return v;
}
double msSinceX(const std::chrono::steady_clock::time_point& t0) {
    return envDbgTimeX()
               ? std::chrono::duration<double, std::milli>(
                     std::chrono::steady_clock::now() - t0)
                     .count()
               : 0.0;
}
}  // namespace

bool buildRefinementHistory(const CartesianResult& res, RefinementHistoryData& out,
                            std::string& err) {
    out = RefinementHistoryData{};
    const auto tBuild0 = envDbgTimeX() ? std::chrono::steady_clock::now()
                                       : std::chrono::steady_clock::time_point{};
    if (!res.tree) { err = "refinementHistory: 无持久树（需先成功 generateCartesian）"; return false; }
    const CartesianMesh& tree = *res.tree;
    const PolyMesh& m = res.mesh;
    const long nC = m.nCells();
    if (nC <= 0) { err = "refinementHistory: 空网格（无单元）"; return false; }
    if ((long)res.cellLevel.size() != nC) {
        err = "refinementHistory: cellLevel 尺寸 " + std::to_string(res.cellLevel.size()) +
              " != 单元数 " + std::to_string(nC);
        return false;
    }
    const long long nLeaves = tree.nLeaves();
    const long long nNodes = tree.nodeCount();
    if (nLeaves <= 0 || nNodes <= 0) { err = "refinementHistory: 空树"; return false; }
    const double tolBox = tree.rootEdge() * 1e-9;  // 与 leafOfPoint 容差带同量级

    // ---- 节点号 → 叶表下标（-1 = 内部节点；叶表恒按规范 Morton 键有序） ----
    std::vector<int> leafOfNode((size_t)nNodes, -1);
    for (long long li = 0; li < nLeaves; ++li) leafOfNode[(size_t)tree.leafNode(li)] = (int)li;

    // ---- 胞元 → 面号逆索引（R14-项2）：一次 O(F) 建表，替代原 cellProbe 与
    // 叶盒包含性验证的逐胞 O(F) 全表扫描（总复杂度 O(nC·F) → O(F + Σ胞面数)；
    // 62k 胞 / 23 万面实测导出 ~11.9s → ~0.3s）。每胞面号升序 = 存储序，与旧
    // 全表扫描的命中序逐面一致 ⇒ 探针点/包含性判定结果逐位相同（确定性不变）。
    // owner/neighbour 同一面双挂（自邻接退化面在旧扫描中只命中一次，新索引挂两次；
    // vids 去重后无差、bestFace 严格 > 比较不受重复影响——语义等价）。
    std::vector<std::vector<int>> cellFaces((size_t)nC);
    for (size_t fi = 0; fi < m.faces.size(); ++fi) {
        const int o = m.owner[fi];
        if (o >= 0 && o < nC) cellFaces[(size_t)o].push_back((int)fi);
        const int nb = m.neighbour[fi];
        if (nb >= 0 && nb < nC) cellFaces[(size_t)nb].push_back((int)fi);
    }
    const auto tInv = envDbgTimeX() ? std::chrono::steady_clock::now()
                                    : std::chrono::steady_clock::time_point{};

    // ---- 胞元 → 叶映射（几何重建；确定性；两遍：定位 → 碎裂判定） ----
    // 一遍定位：每胞元找一个候选叶（顶点均值定位 + 全顶点包含验证；失败者用面内点
    // 内移定位）。允许多胞元映到同一叶——S9b 越界胞拆分会把单叶的裁剪胞拆成多个
    // 凸块胞元（各 ⊆ 该叶盒），二遍判定处理。
    std::vector<long long> cellLeaf((size_t)nC, -1);
    std::vector<uint8_t> cellClean((size_t)nC, 0);
    // OPT-CODE 项4：胞元→叶映射循环并行化（cellProbe/leafOfPoint/包含性验证皆纯
    // 只读；cellLeaf/cellClean 互不重叠写；leafHit 原子自增）。失败语义保持
    // 「升序首个失败胞元」：每段记录本段最小 ci 失败，段间取全局最小 ci 复现
    // 串行 err 文案；成功路径产物逐位同一（映射为逐胞纯函数，与执行序无关）。
    std::vector<std::atomic<int>> leafHit((size_t)nLeaves);
    for (long long i = 0; i < nLeaves; ++i) leafHit[(size_t)i].store(0, std::memory_order_relaxed);
    struct MapFail {
        long ci = -1;      // -1 = 无失败
        int kind = 0;      // 0 无法定位 / 1 级不符
        long long lvl = 0;
    };
    {
        const size_t W = std::max<size_t>(1, globalPool().size());
        const size_t chunk = (size_t)std::max<long>(
            1, (nC + (long)W - 1) / (long)W);
        const size_t nSeg = (size_t)((nC + (long)chunk - 1) / (long)chunk) + 1;
        std::vector<MapFail> segFail(nSeg);
        globalPool().parallelRanges((size_t)nC, [&](size_t lo, size_t hi) {
            MapFail& mf = segFail[lo / chunk];
            for (size_t ciU = lo; ciU < hi; ++ciU) {
                const long ci = (long)ciU;
                const CellProbe pr = cellProbe(m, ci, tree.rootEdge(), cellFaces[(size_t)ci]);
                long long pick = -1;
                bool clean = false;
                const long long cand[2] = {tree.leafOfPoint(pr.avg),
                                           pr.hasInner ? tree.leafOfPoint(pr.inner) : -1};
                for (int k = 0; k < 2 && !clean; ++k) {
                    const long long L = cand[k];
                    if (L < 0 || L >= nLeaves) continue;
                    if (tree.leafLevel(L) != res.cellLevel[(size_t)ci])
                        continue;  // 级不符即弃此候选
                    Vec3 bmn, bmx;
                    tree.leafBox(L, bmn, bmx);
                    bool contains = true;
                    // R14-项2：包含性验证改查逆索引（原逐胞全表扫描 O(F)）；命中面集与
                    // 遍历序同旧实现 ⇒ contains 判定逐位相同
                    for (int fiIdx : cellFaces[(size_t)ci]) {
                        if (!contains) break;
                        const size_t fi = (size_t)fiIdx;
                        for (int v : m.faces[fi]) {
                            const Vec3& p = m.points[(size_t)v];
                            if (p.x < bmn.x - tolBox || p.x > bmx.x + tolBox ||
                                p.y < bmn.y - tolBox || p.y > bmx.y + tolBox ||
                                p.z < bmn.z - tolBox || p.z > bmx.z + tolBox) {
                                contains = false;
                                break;
                            }
                        }
                    }
                    pick = L;
                    clean = contains;
                }
                if (pick < 0) {
                    if (mf.ci < 0) mf = MapFail{ci, 0, 0};
                    continue;
                }
                if (tree.leafLevel(pick) != res.cellLevel[(size_t)ci]) {
                    if (mf.ci < 0) mf = MapFail{ci, 1, tree.leafLevel(pick)};
                    continue;
                }
                cellLeaf[(size_t)ci] = pick;
                cellClean[(size_t)ci] = clean ? 1 : 0;
                leafHit[(size_t)pick].fetch_add(1, std::memory_order_relaxed);
            }
        });
        MapFail first;
        for (const auto& mf : segFail)
            if (mf.ci >= 0 && (first.ci < 0 || mf.ci < first.ci)) first = mf;
        if (first.ci >= 0) {
            if (first.kind == 0)
                err = "refinementHistory: 胞元 " + std::to_string(first.ci) +
                      " 无法定位到任何同级八叉树叶（几何映射失败，拒绝导出）";
            else
                err = "refinementHistory: 胞元 " + std::to_string(first.ci) + " 的叶级 (" +
                      std::to_string(first.lvl) + ") 与 cellLevel (" +
                      std::to_string(res.cellLevel[(size_t)first.ci]) + ") 不符（映射不自洽）";
            return false;
        }
    }
    const auto tMap = envDbgTimeX() ? std::chrono::steady_clock::now()
                                    : std::chrono::steady_clock::time_point{};
    // 二遍判定：叶"洁净可见" ⟺ 恰一个胞元映射且该胞元整体含于叶盒（非跨叶合并）。
    // 其余（跨叶合并胞、碎裂叶的多个凸块胞元）各分合成根条目 `-1 0()`，其叶不计
    // 入森林完备性（等价"不完整组成员各自为根"——它们不构成可粗化的 8 兄弟组）。
    std::vector<uint8_t> cellMerged((size_t)nC, 0);
    std::vector<long long> leafCell((size_t)nLeaves, -1);  // 洁净可见叶 → 胞元
    for (long ci = 0; ci < nC; ++ci) {
        const long long L = cellLeaf[(size_t)ci];
        if (cellClean[(size_t)ci] &&
            leafHit[(size_t)L].load(std::memory_order_relaxed) == 1) {
            leafCell[(size_t)L] = ci;
        } else {
            cellMerged[(size_t)ci] = 1;
        }
    }

    // ---- 自底向上完备性：叶条目 ⟺ 洁净可见；内部条目 ⟺ 8 子全有条目 ----
    // 节点号恒为拓扑序（父先于子，8 子连续）→ 逆序一遍即得不动点。
    std::vector<uint8_t> complete((size_t)nNodes, 0);
    for (long long ni = nNodes - 1; ni >= 0; --ni) {
        if (tree.nodeIsLeaf(ni)) {
            const int li = leafOfNode[(size_t)ni];
            complete[(size_t)ni] = (li >= 0 && leafCell[(size_t)li] >= 0) ? 1 : 0;
            continue;
        }
        bool all = true;
        for (int k = 0; k < 8; ++k) {
            const int ch = tree.nodeChild(ni, k);
            if (ch < 0 || ch >= nNodes || !complete[(size_t)ch]) { all = false; break; }
        }
        complete[(size_t)ni] = all ? 1 : 0;
    }

    // ---- 条目编号（节点拓扑序 → 条目拓扑序；父条目先于子条目） ----
    std::vector<int> entryOfNode((size_t)nNodes, -1);
    for (long long ni = 0; ni < nNodes; ++ni) {
        if (!complete[(size_t)ni]) continue;
        entryOfNode[(size_t)ni] = (int)out.parent.size();
        out.parent.push_back(-1);  // 占位，随后回填
        out.children.push_back({-1, -1, -1, -1, -1, -1, -1, -1});
        out.entryNode.push_back(ni);
    }
    for (long long ni = 0; ni < nNodes; ++ni) {
        const int e = entryOfNode[(size_t)ni];
        if (e < 0) continue;
        const int p = tree.nodeParent(ni);
        if (p >= 0 && entryOfNode[(size_t)p] >= 0) out.parent[(size_t)e] = entryOfNode[(size_t)p];
        if (!tree.nodeIsLeaf(ni)) {
            for (int k = 0; k < 8; ++k) {
                const int ce = entryOfNode[(size_t)tree.nodeChild(ni, k)];
                if (ce < 0) {
                    err = "refinementHistory: 内部条目 " + std::to_string(e) + " 的子 " +
                          std::to_string(k) + " 无条目（完备性计算不自洽）";
                    return false;
                }
                out.children[(size_t)e][(size_t)k] = ce;
            }
        }
    }
    // 合并胞合成根条目（`-1 0()`；运行时加密可挂新根，粗化本就被 protectedCell_ 禁）
    std::vector<int> mergedEntry((size_t)nC, -1);
    for (long ci = 0; ci < nC; ++ci) {
        if (!cellMerged[(size_t)ci]) continue;
        mergedEntry[(size_t)ci] = (int)out.parent.size();
        out.parent.push_back(-1);
        out.children.push_back({-1, -1, -1, -1, -1, -1, -1, -1});
        out.entryNode.push_back(-1);
    }

    // ---- visibleCells：每胞元 → 其条目下标（尺寸==单元数，hexRef8.C:1980） ----
    out.visibleCells.assign((size_t)nC, -1);
    for (long ci = 0; ci < nC; ++ci) {
        out.visibleCells[(size_t)ci] =
            cellMerged[(size_t)ci] ? mergedEntry[(size_t)ci]
                                   : entryOfNode[(size_t)tree.leafNode(cellLeaf[(size_t)ci])];
    }

    // ---- 自检（checkIndices 等价 + 更严；失败即拒写） ----
    const int nE = (int)out.parent.size();
    auto fail = [&](const std::string& msg) { err = "refinementHistory 自检失败: " + msg; return false; };
    if ((long)out.visibleCells.size() != nC) return fail("visibleCells 尺寸 != 单元数");
    // visibleCells 覆盖全部胞元、值域合法、非负值互异、指向叶条目（无子）
    std::vector<uint8_t> referenced((size_t)nE, 0);
    for (long ci = 0; ci < nC; ++ci) {
        const int v = out.visibleCells[(size_t)ci];
        if (v < -1 || v >= nE)
            return fail("visibleCells[" + std::to_string(ci) + "]=" + std::to_string(v) +
                        " 越界（条目数 " + std::to_string(nE) + "）");
        if (v >= 0) {
            if (referenced[(size_t)v])
                return fail("visibleCells[" + std::to_string(ci) + "] 与先前胞元共指条目 " +
                            std::to_string(v) + "（映射非单射）");
            referenced[(size_t)v] = 1;
            for (int k = 0; k < 8; ++k)
                if (out.children[(size_t)v][(size_t)k] >= 0)
                    return fail("visibleCells[" + std::to_string(ci) + "] 指向非叶条目 " +
                                std::to_string(v));
        }
    }
    for (int e = 0; e < nE; ++e) {
        const int p = out.parent[(size_t)e];
        if (p < -1 || p >= nE)
            return fail("条目 " + std::to_string(e) + " 父下标 " + std::to_string(p) + " 越界");
        if (p >= e)
            return fail("条目 " + std::to_string(e) + " 父下标 " + std::to_string(p) +
                        " 不早于自身（拓扑序破坏 ⇒ 父链可成环）");
        const int c0 = out.children[(size_t)e][0];
        const bool hasKids = c0 >= 0;
        if (hasKids) {
            for (int k = 0; k < 8; ++k) {
                const int c = out.children[(size_t)e][(size_t)k];
                if (c < 0 || c >= nE)
                    return fail("条目 " + std::to_string(e) + " 子下标[" + std::to_string(k) +
                                "]=" + std::to_string(c) + " 越界");
                if (out.parent[(size_t)c] != e)
                    return fail("条目 " + std::to_string(c) + " 之父 != " + std::to_string(e) +
                                "（父子不闭合）");
                for (int j = 0; j < k; ++j)
                    if (out.children[(size_t)e][(size_t)j] == c)
                        return fail("条目 " + std::to_string(e) + " 子下标重复 " +
                                    std::to_string(c));
            }
        } else {
            for (int k = 1; k < 8; ++k)
                if (out.children[(size_t)e][(size_t)k] >= 0)
                    return fail("条目 " + std::to_string(e) + " 子列表不完整（部分 -1）");
            if (!referenced[(size_t)e])
                return fail("叶条目 " + std::to_string(e) +
                            " 无可见胞元指向（孤儿条目，可见性覆盖不全）");
        }
    }
    // 父向闭合：凡有父者必在其父的子列表中（上已验子→父；此验父→子）
    for (int e = 0; e < nE; ++e) {
        const int p = out.parent[(size_t)e];
        if (p < 0) continue;
        bool found = false;
        for (int k = 0; k < 8; ++k)
            if (out.children[(size_t)p][(size_t)k] == e) found = true;
        if (!found) return fail("条目 " + std::to_string(e) + " 不在其父 " +
                                std::to_string(p) + " 的子列表中（父链不闭合）");
    }
    // 全条目可达：自可见条目沿父链上标记，未标记者 = 与现存胞元脱节的孤儿森林
    {
        std::vector<uint8_t> reach((size_t)nE, 0);
        for (int v : out.visibleCells)
            for (int e = v; e >= 0 && !reach[(size_t)e]; e = out.parent[(size_t)e])
                reach[(size_t)e] = 1;
        for (int e = 0; e < nE; ++e)
            if (!reach[(size_t)e])
                return fail("条目 " + std::to_string(e) + " 不可达（与可见胞元脱节）");
    }
    // 真实节点条目的级一致：子级 = 父级 + 1
    for (int e = 0; e < nE; ++e) {
        const long long nd = out.entryNode[(size_t)e];
        if (nd < 0 || out.parent[(size_t)e] < 0) continue;
        const long long pn = out.entryNode[(size_t)out.parent[(size_t)e]];
        if (pn < 0) continue;
        if (tree.nodeLevel(nd) != tree.nodeLevel(pn) + 1)
            return fail("条目 " + std::to_string(e) + " 的层级与其父不连续");
    }
    if (envDbgTimeX())
        std::fprintf(stderr, "[TIME] AMR hist build sub: invIdx=%.1f map=%.1f rest=%.1f ms\n",
                     msSinceX(tBuild0) - msSinceX(tInv), msSinceX(tInv) - msSinceX(tMap),
                     msSinceX(tMap));
    return true;
}

bool writeRefinementHistory(const CartesianResult& res, const std::string& polyMeshDir,
                            std::string& err) {
    const auto tW0 = envDbgTimeX() ? std::chrono::steady_clock::now()
                                   : std::chrono::steady_clock::time_point{};
    RefinementHistoryData d;
    if (!buildRefinementHistory(res, d, err)) return false;
    const auto tW1 = envDbgTimeX() ? std::chrono::steady_clock::now()
                                   : std::chrono::steady_clock::time_point{};

    std::error_code ec;
    fs::create_directories(polyMeshDir, ec);
    if (ec) { err = "无法创建目录: " + polyMeshDir; return false; }
    // PERF-R15：并行格式化 splitCells / visibleCells 两段体到块缓冲 + fwrite 顺序块写。
    // 磁盘格式 = 01_U3 §3 / refinementHistory.H:41-58 实测样例（逐字节同原 ofstream）：
    //   // splitCells  <N> ( 逐条 `parent (i0..i7)` 或 `-1 0()` )
    //   // visibleCells <nCells> ( 逐行一个条目下标 )
    const std::string path = polyMeshDir + "/refinementHistory";
    FILE* fp = std::fopen(path.c_str(), "w");
    if (!fp) { err = "无法写入: " + path; return false; }
    {
        std::ostringstream hs;
        writeHeader(hs, "refinementHistory", "refinementHistory");
        const std::string h = hs.str();
        std::fwrite(h.data(), 1, h.size(), fp);
    }
    const size_t W = std::max<size_t>(1, globalPool().size());
    // 并行格式化 [0,n) 到 bufs 后按序 fwrite（字节流 = 串行）
    auto emitParallel = [&](size_t n, const std::function<void(size_t, size_t, std::string&)>& fmt) {
        const size_t chunk = n > 0 ? std::max<size_t>(1, (n + W - 1) / W) : 1;
        const size_t nC = n > 0 ? (n + chunk - 1) / chunk : 0;
        std::vector<std::string> bufs(nC);
        std::vector<std::future<void>> futs;
        futs.reserve(nC);
        for (size_t w = 0; w < nC; ++w) {
            const size_t lo = w * chunk, hi = std::min(n, lo + chunk);
            futs.push_back(globalPool().submit([&bufs, &fmt, w, lo, hi] { fmt(lo, hi, bufs[w]); }));
        }
        for (auto& fu : futs) fu.get();
        for (const auto& s : bufs) std::fwrite(s.data(), 1, s.size(), fp);
    };
    {
        char b[64];
        const int ln = std::snprintf(b, sizeof(b), "// splitCells\n%zu\n(\n", d.parent.size());
        std::fwrite(b, 1, (size_t)ln, fp);
    }
    emitParallel(d.parent.size(), [&](size_t lo, size_t hi, std::string& out) {
        out.reserve((hi - lo) * 32);
        for (size_t e = lo; e < hi; ++e) {
            appendInt(out, d.parent[e]);
            if (d.children[e][0] >= 0) {
                out += " (";
                for (int k = 0; k < 8; ++k) {
                    if (k) out.push_back(' ');
                    appendInt(out, d.children[e][(size_t)k]);
                }
                out += ")\n";
            } else {
                out += " 0()\n";
            }
        }
    });
    {
        std::ostringstream ss;
        ss << ")\n\n// visibleCells\n" << d.visibleCells.size() << "\n(\n";
        const std::string s = ss.str();
        std::fwrite(s.data(), 1, s.size(), fp);
    }
    emitParallel(d.visibleCells.size(), [&](size_t lo, size_t hi, std::string& out) {
        out.reserve((hi - lo) * 8);
        for (size_t i = lo; i < hi; ++i) {
            appendInt(out, d.visibleCells[i]);
            out.push_back('\n');
        }
    });
    std::fwrite(")\n", 1, 2, fp);
    {
        std::ostringstream fs;
        writeFooter(fs);
        const std::string ft = fs.str();
        std::fwrite(ft.data(), 1, ft.size(), fp);
    }
    if (std::fclose(fp) != 0) { err = "写入失败(fclose): " + path; return false; }
    if (envDbgTimeX())
        std::fprintf(stderr, "[TIME] AMR hist sub: build=%.1f write=%.1f ms\n",
                     msSinceX(tW0) - msSinceX(tW1),
                     std::chrono::duration<double, std::milli>(
                         std::chrono::steady_clock::now() - tW1)
                         .count());
    return true;
}

// ================= VTK XML (.vtu) 多面体导出 =================
// legacy 5 数组布局（connectivity/offsets/types/faces/faceoffsets），
// VTK_POLYHEDRON=42；ParaView 可拖拽打开、pyvista 可读。绕向统一为"外法向"。
bool writePolyMeshVtu(const PolyMesh& m, const std::string& path, std::string& err) {
    long nC = m.nCells();
    if (nC <= 0 || m.points.empty()) { err = "空网格，无法导出 VTU"; return false; }

    // 每个单元的面：(faceIdx, 该面属于本单元的侧)；随后按外法向统一绕向
    std::vector<std::vector<int>> cellFaces(nC);
    for (size_t fi = 0; fi < m.faces.size(); ++fi) {
        int o = m.owner[fi];
        if (o >= 0 && o < nC) cellFaces[o].push_back((int)fi);
        int nb = m.neighbour[fi];
        if (nb >= 0 && nb < nC) cellFaces[nb].push_back((int)fi);
    }
    // 外法向判定用引擎不变量（零几何判定）：存储面绕向恒为【owner 外法向】
    // （Cartesian.cpp S11：内部面上三角序且交换时同步反转绕向；边界面由 owner
    // 侧发射）。旧实现的顶点均值参考点在合并非凸胞上可落在胞外/面平面外侧，
    // 按 n·(fc−ref) 判向会误翻面（共享面两侧同绕向、ΣSf≠0 的自相矛盾网格）。
    // 现改为本胞是 owner 即原样、否则反序——不依赖任何参考点位置。

    std::ofstream f(path);
    if (!f) { err = "无法写入: " + path; return false; }
    char buf[160];
    f << "<?xml version=\"1.0\"?>\n";
    f << "<VTKFile type=\"UnstructuredGrid\" version=\"1.0\" byte_order=\"LittleEndian\" "
         "header_type=\"UInt64\">\n";
    f << "  <UnstructuredGrid>\n";
    f << "    <Piece NumberOfPoints=\"" << m.points.size() << "\" NumberOfCells=\"" << nC
      << "\">\n";
    f << "      <Points>\n";
    f << "        <DataArray type=\"Float64\" NumberOfComponents=\"3\" format=\"ascii\">\n";
    for (auto& p : m.points) {
        std::snprintf(buf, sizeof(buf), "          %.15g %.15g %.15g\n", p.x, p.y, p.z);
        f << buf;
    }
    f << "        </DataArray>\n      </Points>\n";
    f << "      <Cells>\n";

    // connectivity（每单元去重点集）与 offsets
    std::vector<long long> offsets;
    offsets.reserve(nC);
    f << "        <DataArray type=\"Int64\" Name=\"connectivity\" format=\"ascii\">\n";
    long long run = 0;
    std::string connLine;
    for (long ci = 0; ci < nC; ++ci) {
        std::vector<int> vids;
        for (int fi : cellFaces[ci])
            for (int v : m.faces[fi]) vids.push_back(v);
        std::sort(vids.begin(), vids.end());
        vids.erase(std::unique(vids.begin(), vids.end()), vids.end());
        for (int v : vids) { connLine += std::to_string(v); connLine += ' '; }
        run += (long long)vids.size();
        offsets.push_back(run);
    }
    f << connLine << "\n        </DataArray>\n";
    f << "        <DataArray type=\"Int64\" Name=\"offsets\" format=\"ascii\">\n";
    for (long long o : offsets) f << o << ' ';
    f << "\n        </DataArray>\n";

    // types：全部按多面体
    f << "        <DataArray type=\"UInt8\" Name=\"types\" format=\"ascii\">\n";
    for (long ci = 0; ci < nC; ++ci) f << "42 ";
    f << "\n        </DataArray>\n";

    // faces / faceoffsets：逐面外法向绕向
    std::vector<long long> faceoffsets;
    faceoffsets.reserve(nC);
    f << "        <DataArray type=\"Int64\" Name=\"faces\" format=\"ascii\">\n";
    long long frun = 0;
    std::string faceLine;
    for (long ci = 0; ci < nC; ++ci) {
        long long nInt = 1;  // nFaces
        faceLine += std::to_string(cellFaces[ci].size());
        faceLine += ' ';
        for (int fi : cellFaces[ci]) {
            const auto& fc = m.faces[fi];
            // 引擎不变量：存储绕向 = owner 外法向（见上注）
            const bool outward = (m.owner[fi] == ci);
            faceLine += std::to_string(fc.size());
            faceLine += ' ';
            nInt += 1 + (long long)fc.size();
            if (outward) {
                for (int v : fc) { faceLine += std::to_string(v); faceLine += ' '; }
            } else {
                for (int k = (int)fc.size() - 1; k >= 0; --k) {
                    faceLine += std::to_string(fc[k]); faceLine += ' ';
                }
            }
        }
        frun += nInt;
        faceoffsets.push_back(frun);
    }
    f << faceLine << "\n        </DataArray>\n";
    f << "        <DataArray type=\"Int64\" Name=\"faceoffsets\" format=\"ascii\">\n";
    for (long long o : faceoffsets) f << o << ' ';
    f << "\n        </DataArray>\n";

    f << "      </Cells>\n";
    f << "    </Piece>\n  </UnstructuredGrid>\n</VTKFile>\n";
    return true;
}

}  // namespace meshgen
