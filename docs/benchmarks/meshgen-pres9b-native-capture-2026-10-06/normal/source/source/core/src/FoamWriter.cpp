#include "meshgen/FoamWriter.hpp"

#include <algorithm>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <sstream>

namespace fs = std::filesystem;

namespace meshgen {

namespace {

void writeHeader(std::ostream& os, const char* className, const char* object,
                 const char* location = "constant/polyMesh") {
    os << "/*--------------------------------*- C++ -*----------------------------------*\\\n"
          "| =========                 |                                                 |\n"
          "| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |\n"
          "|  \\\\    /   O peration     | Version:  v2412                                 |\n"
          "|   \\\\  /    A nd           | Website:  www.openfoam.com                      |\n"
          "|    \\\\/     M anipulation  |                                                 |\n"
          "\\*---------------------------------------------------------------------------*/\n"
          "FoamFile\n{\n    version     2.0;\n    format      ascii;\n    class       "
       << className << ";\n    location    \"" << location << "\";\n    object      " << object
       << ";\n}\n"
       << "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n";
}

void writeFooter(std::ostream& os) {
    os << "\n\n// **************************************************"
          "*********************** //\n";
}

}  // namespace

std::string foamPatchType(const std::string& name) {
    auto contains = [&](const char* s) { return name.find(s) != std::string::npos; };
    if (contains("empty") || contains("front") || contains("back")) return "empty";
    if (contains("symm")) return "symmetryPlane";
    if (contains("wall")) return "wall";
    if (contains("inlet") || contains("outlet") || contains("patch")) return "patch";
    return "patch";
}

bool writeOpenFoamPolyMesh(const MeshResult& mesh, const std::string& dir, std::string& err) {
    std::error_code ec;
    fs::create_directories(dir, ec);
    if (ec) {
        err = "无法创建目录: " + dir;
        return false;
    }
    char buf[128];

    // ---- points ----
    {
        std::ofstream f(dir + "/points");
        if (!f) { err = "无法写入 points"; return false; }
        writeHeader(f, "vectorField", "points");
        f << mesh.points.size() << "\n(\n";
        for (const auto& p : mesh.points) {
            std::snprintf(buf, sizeof(buf), "(%.15g %.15g %.15g)\n", p.x, p.y, p.z);
            f << buf;
        }
        f << ")\n";
        writeFooter(f);
    }
    // ---- faces ----
    {
        std::ofstream f(dir + "/faces");
        if (!f) { err = "无法写入 faces"; return false; }
        writeHeader(f, "faceList", "faces");
        f << mesh.faces.size() << "\n(\n";
        for (const auto& fc : mesh.faces)
            f << "4(" << fc[0] << ' ' << fc[1] << ' ' << fc[2] << ' ' << fc[3] << ")\n";
        f << ")\n";
        writeFooter(f);
    }
    // ---- owner ----
    {
        std::ofstream f(dir + "/owner");
        if (!f) { err = "无法写入 owner"; return false; }
        writeHeader(f, "labelList", "owner");
        f << mesh.owner.size() << "\n(\n";
        for (int o : mesh.owner) f << o << '\n';
        f << ")\n";
        writeFooter(f);
    }
    // ---- neighbour ----
    {
        std::ofstream f(dir + "/neighbour");
        if (!f) { err = "无法写入 neighbour"; return false; }
        writeHeader(f, "labelList", "neighbour");
        f << mesh.neighbour.size() << "\n(\n";
        for (int n : mesh.neighbour) f << n << '\n';
        f << ")\n";
        writeFooter(f);
    }
    // ---- boundary ----
    {
        std::ofstream f(dir + "/boundary");
        if (!f) { err = "无法写入 boundary"; return false; }
        writeHeader(f, "polyBoundaryMesh", "boundary");
        // E5c W-SB-4(iii)（solve-blocker 工作单）：patch 名 [A-Za-z0-9_]+ 消毒
        //（dcdtuni 解析器契约；defence-in-depth——引擎侧 M4a 已不产非法名，本层
        // 兜任何来源；合法名 = 恒等映射 ⇒ OFF/既有案逐字节不变）。
        auto wsb4Sanitize = [](const std::string& nm) {
            std::string s2 = nm;
            for (auto& ch : s2)
                if (!(std::isalnum((unsigned char)ch) || ch == '_')) ch = '_';
            return s2;
        };
        f << mesh.patchNames.size() << "\n(\n";
        for (size_t pi = 0; pi < mesh.patchNames.size(); ++pi) {
            const std::string nmW = wsb4Sanitize(mesh.patchNames[pi]);
            f << "    " << (nmW.empty() ? "default" : nmW)
              << "\n    {\n        type        " << foamPatchType(nmW)
              << ";\n        nFaces      " << mesh.patchNFaces[pi] << ";\n        startFace   "
              << mesh.patchStart[pi] << ";\n    }\n";
        }
        f << ")\n";
        writeFooter(f);
    }
    return true;
}

bool writeBlockMeshDict(const Model& m, const std::string& path, std::string& err) {
    const fs::path parent = fs::path(path).parent_path();
    if (!parent.empty()) {
        std::error_code ec;
        fs::create_directories(parent, ec);
    }
    std::ofstream f(path);
    if (!f) {
        err = "无法写入: " + path;
        return false;
    }
    // 唯一点（块角点）
    std::map<int, int> vIdx;
    for (const auto& b : m.blocks)
        if (b.enabled)
            for (int v : b.verts)
                if (!vIdx.count(v)) vIdx[v] = (int)vIdx.size();

    writeHeader(f, "dictionary", "blockMeshDict", "system");
    f << "convertToMeters 1;\n\nvertices\n(\n";
    // 按索引顺序输出
    std::vector<std::pair<int, int>> order(vIdx.begin(), vIdx.end());
    std::sort(order.begin(), order.end(),
              [](auto& a, auto& b) { return a.second < b.second; });
    char buf[256];
    for (auto& kv : order) {
        const GeoPoint* p = m.point(kv.first);
        if (!p) continue;
        std::snprintf(buf, sizeof(buf), "    (%.15g %.15g %.15g) // %d\n", p->pos.x, p->pos.y,
                      p->pos.z, kv.first);
        f << buf;
    }
    f << ");\n\nblocks\n(\n";
    for (const auto& b : m.blocks) {
        if (!b.enabled) continue;
        f << "    hex (";
        for (int v = 0; v < 8; ++v) f << (v ? " " : "") << vIdx.at(b.verts[v]);
        f << ") ";
        int div[3] = {1, 1, 1};
        double grade[3] = {1, 1, 1};
        for (int axis = 0; axis < 3; ++axis) {
            int e0 = -1;
            for (int e = 0; e < 12; ++e)
                if (hex::kEdgeAxis[e] == axis) { e0 = e; break; }
            const Curve* c = m.curve(b.absCurve(e0));
            if (!c) continue;
            div[axis] = std::max(1, c->nPoints - 1);
            std::vector<double> seq = ((int)c->customSeq.size() == c->nPoints && c->nPoints >= 2)
                                          ? c->customSeq
                                          : distributionSequence(c->dist, c->nPoints);
            grade[axis] = sequenceExpansionRatio(seq);
        }
        std::snprintf(buf, sizeof(buf), "(%d %d %d) simpleGrading (%.6g %.6g %.6g)\n", div[0],
                      div[1], div[2], grade[0], grade[1], grade[2]);
        f << buf;
    }
    f << ");\n\nedges\n(\n";
    std::set<int> emitted;
    for (const auto& b : m.blocks) {
        if (!b.enabled) continue;
        for (int e = 0; e < 12; ++e) {
            const Curve* c = m.curve(b.absCurve(e));
            if (!c || c->type != CurveType::Arc || emitted.count(c->id)) continue;
            if (!vIdx.count(c->p0) || !vIdx.count(c->p1)) continue;
            const Vec3 mid = c->at(m, 0.5);
            std::snprintf(buf, sizeof(buf), "    arc %d %d (%.15g %.15g %.15g)\n", vIdx[c->p0],
                          vIdx[c->p1], mid.x, mid.y, mid.z);
            f << buf;
            emitted.insert(c->id);
        }
    }
    f << ");\n\nboundary\n(\n";
    // 共享面（出现两次的角点集合）视为内部面，不输出
    std::map<std::array<int, 4>, int> faceCount;
    for (const auto& b : m.blocks)
        if (b.enabled)
            for (int fa = 0; fa < 6; ++fa) {
                std::array<int, 4> key;
                for (int c = 0; c < 4; ++c) key[c] = b.verts[hex::kFaces[fa][c]];
                std::sort(key.begin(), key.end());
                faceCount[key]++;
            }
    std::map<std::string, std::vector<std::pair<const Block*, int>>> patchFaces;
    for (const auto& b : m.blocks)
        if (b.enabled)
            for (int fa = 0; fa < 6; ++fa) {
                std::array<int, 4> key;
                for (int c = 0; c < 4; ++c) key[c] = b.verts[hex::kFaces[fa][c]];
                std::sort(key.begin(), key.end());
                if (faceCount[key] >= 2) continue;
                patchFaces[b.patches[fa]].push_back({&b, fa});
            }
    for (auto& kv : patchFaces) {
        f << "    " << (kv.first.empty() ? "default" : kv.first) << "\n    {\n        type        "
          << foamPatchType(kv.first) << ";\n        faces\n        (\n";
        for (auto& bf : kv.second) {
            f << "            (";
            for (int c = 0; c < 4; ++c)
                f << (c ? " " : "") << vIdx.at(bf.first->verts[hex::kFaces[bf.second][c]]);
            f << ")\n";
        }
        f << "        );\n    }\n";
    }
    f << ");\n";
    f << "\n// 注意：simpleGrading 仅能精确表达几何级数布点；\n"
         "// tanh/对称指数等布点以总体拉伸率近似，如需精确请改用 polyMesh 导出。\n";
    writeFooter(f);
    return true;
}

}  // namespace meshgen
