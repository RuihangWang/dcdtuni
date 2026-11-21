#include "meshgen/Mesher.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <numeric>
#include <unordered_map>

#include "meshgen/Simd.hpp"
#include "meshgen/ThreadPool.hpp"

namespace meshgen {

using hex::edgeWithFixedLevels;

// ===================== BlockEval =====================
//
// 体插值采用 OpenFOAM blockMesh 的加权混合算法（参考
// Ref_code/OpenFOAM-dev/src/mesh/blockMesh/blocks/block/blockCreate.C）：
//   P = [Σ w_q·S_q]/3 + Σ w_q·(E_q − S_q)
// 其中 S_q 为直线边插值、E_q 为曲线边精确采样、w_q 为归一化角点形函数权重。
// 该算法在边上精确重现曲线采样点，且允许同轴各边持有不同布点序列。

bool BlockEval::build(const Model& m, const Block& b, std::string* err) {
    model = &m;
    blockCopy = b;  // 值副本，防止外部 vector 扩容导致悬空
    block = &blockCopy;
    if (!m.blockCorners(b, corners.data())) {
        if (err) *err = "块 " + b.name + " 顶点缺失";
        return false;
    }
    for (int e = 0; e < 12; ++e) {
        const Curve* c = m.curve(b.absCurve(e));
        if (!c) {
            if (err) *err = "块 " + b.name + " 边 " + std::to_string(e) + " 曲线缺失";
            return false;
        }
        std::vector<double> seq;
        if ((int)c->customSeq.size() == c->nPoints && c->nPoints >= 2)
            seq = c->customSeq;
        else
            seq = distributionSequence(c->dist, c->nPoints);
        edgeSeqs[e] = seq;
        auto& out = edgeSamples[e];
        out.resize(seq.size());
        for (size_t i = 0; i < seq.size(); ++i) {
            const double t = b.curveReversed(e) ? 1.0 - seq[i] : seq[i];
            out[i] = c->at(m, t);
        }
    }
    for (int axis = 0; axis < 3; ++axis) {
        int ref = -1, e0 = -1;
        for (int e = 0; e < 12; ++e) {
            if (hex::kEdgeAxis[e] != axis) continue;
            if (e0 < 0) e0 = e;
            const int n = (int)edgeSamples[e].size();
            if (ref < 0) ref = n;
            else if (n != ref) {
                if (err)
                    *err = "块 " + b.name + " 轴 " + ("ijk"[axis]) +
                           " 各边点数不一致，请先运行自动匹配";
                return false;
            }
        }
        nPts[axis] = ref;
        seqs[axis] = edgeSeqs[e0];
    }
    return true;
}

Vec3 BlockEval::edgeSample(int e, double t) const {
    const Curve* c = model->curve(block->absCurve(e));
    const double tc = block->curveReversed(e) ? 1.0 - t : t;
    return c->at(*model, tc);
}

namespace {

// 12 条边的直线插值端点（起点、终点）
inline void straightEdgeEnds(const std::array<Vec3, 8>& c, int e, Vec3& a, Vec3& b) {
    a = c[hex::kEdges[e][0]];
    b = c[hex::kEdges[e][1]];
}

}  // namespace

Vec3 BlockEval::atParam(double u, double v, double w) const {
    // 单参数版（各轴共用一个逻辑坐标）：权重退化为标准边形函数
    const double wx[4] = {(1 - v) * (1 - w), v * (1 - w), v * w, (1 - v) * w};
    const double wy[4] = {(1 - u) * (1 - w), u * (1 - w), u * w, (1 - u) * w};
    const double wz[4] = {(1 - u) * (1 - v), u * (1 - v), u * v, (1 - u) * v};
    const double par[3] = {u, v, w};
    Vec3 base{0, 0, 0}, corr{0, 0, 0};
    for (int e = 0; e < 12; ++e) {
        const int axis = hex::kEdgeAxis[e];
        const double wt = (axis == 0 ? wx : axis == 1 ? wy : wz)[e - (axis == 0 ? 0 : axis == 1 ? 4 : 8)];
        Vec3 a, b;
        straightEdgeEnds(corners, e, a, b);
        const Vec3 s = a + (b - a) * par[axis];
        const Vec3 cur = edgeSample(e, par[axis]);
        base += s * wt;
        corr += (cur - s) * wt;
    }
    return base / 3.0 + corr;
}

Vec3 BlockEval::gridPoint(int i, int j, int k) const {
    // 通用版：每条边用自己的布点序列（与 OpenFOAM blockCreate.C 相同的权重）
    const double* uE[4] = {edgeSeqs[0].data(), edgeSeqs[1].data(), edgeSeqs[2].data(),
                           edgeSeqs[3].data()};
    const double* vE[4] = {edgeSeqs[4].data(), edgeSeqs[5].data(), edgeSeqs[6].data(),
                           edgeSeqs[7].data()};
    const double* wE[4] = {edgeSeqs[8].data(), edgeSeqs[9].data(), edgeSeqs[10].data(),
                           edgeSeqs[11].data()};
    const double u0 = uE[0][i], u1 = uE[1][i], u2 = uE[2][i], u3 = uE[3][i];
    const double v0 = vE[0][j], v1 = vE[1][j], v2 = vE[2][j], v3 = vE[3][j];
    const double w0 = wE[0][k], w1 = wE[1][k], w2 = wE[2][k], w3 = wE[3][k];

    double wx[4], wy[4], wz[4];
    wx[0] = (1 - u0) * (1 - v0) * (1 - w0) + u0 * (1 - v1) * (1 - w1);
    wx[1] = (1 - u1) * v0 * (1 - w3) + u1 * v1 * (1 - w2);
    wx[2] = (1 - u2) * v3 * w3 + u2 * v2 * w2;
    wx[3] = (1 - u3) * (1 - v3) * w0 + u3 * (1 - v2) * w1;
    wy[0] = (1 - v0) * (1 - u0) * (1 - w0) + v0 * (1 - u1) * (1 - w3);
    wy[1] = (1 - v1) * u0 * (1 - w1) + v1 * u1 * (1 - w2);
    wy[2] = (1 - v2) * u3 * w1 + v2 * u2 * w2;
    wy[3] = (1 - v3) * (1 - u3) * w0 + v3 * (1 - u2) * w3;
    wz[0] = (1 - w0) * (1 - u0) * (1 - v0) + w0 * (1 - u3) * (1 - v3);
    wz[1] = (1 - w1) * u0 * (1 - v1) + w1 * u3 * (1 - v2);
    wz[2] = (1 - w2) * u1 * v1 + w2 * u2 * v2;
    wz[3] = (1 - w3) * (1 - u1) * v0 + w3 * (1 - u2) * v3;
    auto normalize = [](double t[4]) {
        const double s = t[0] + t[1] + t[2] + t[3];
        if (s > 1e-300)
            for (int q = 0; q < 4; ++q) t[q] /= s;
    };
    normalize(wx);
    normalize(wy);
    normalize(wz);

    const Vec3& c000 = corners[0];
    const Vec3& c100 = corners[1];
    const Vec3& c110 = corners[2];
    const Vec3& c010 = corners[3];
    const Vec3& c001 = corners[4];
    const Vec3& c101 = corners[5];
    const Vec3& c111 = corners[6];
    const Vec3& c011 = corners[7];

    const Vec3 sx[4] = {c000 + (c100 - c000) * u0, c010 + (c110 - c010) * u1,
                        c011 + (c111 - c011) * u2, c001 + (c101 - c001) * u3};
    const Vec3 sy[4] = {c000 + (c010 - c000) * v0, c100 + (c110 - c100) * v1,
                        c101 + (c111 - c101) * v2, c001 + (c011 - c001) * v3};
    const Vec3 sz[4] = {c000 + (c001 - c000) * w0, c100 + (c101 - c100) * w1,
                        c110 + (c111 - c110) * w2, c010 + (c011 - c010) * w3};

    Vec3 base{0, 0, 0}, corr{0, 0, 0};
    for (int q = 0; q < 4; ++q) {
        const Vec3 ex = edgeSamples[q][i];
        const Vec3 ey = edgeSamples[4 + q][j];
        const Vec3 ez = edgeSamples[8 + q][k];
        base += sx[q] * wx[q] + sy[q] * wy[q] + sz[q] * wz[q];
        corr += (ex - sx[q]) * wx[q] + (ey - sy[q]) * wy[q] + (ez - sz[q]) * wz[q];
    }
    return base / 3.0 + corr;
}

Vec3 blockPointAt(const Model& m, const Block& b, double u, double v, double w, bool* ok) {
    BlockEval ev;
    std::string err;
    if (!ev.build(m, b, &err)) {
        if (ok) *ok = false;
        return {};
    }
    if (ok) *ok = true;
    return ev.atParam(u, v, w);
}

// ===================== SIMD 网格点生成 =====================

namespace {

void genBlockPoints(const BlockEval& ev, std::vector<Vec3>& out) {
    const int ni = ev.n(0), nj = ev.n(1), nk = ev.n(2);
    out.resize(size_t(ni) * nj * nk);
    if (ni < 2 || nj < 2 || nk < 2) return;

    // 序列一致性检测（魔法笔/失配时可能不一致 → 走通用标量路径）
    bool uniformSeqs = true;
    for (int axis = 0; axis < 3 && uniformSeqs; ++axis)
        for (int q = 1; q < 4; ++q) {
            const auto& a = ev.edgeSeq(axis * 4);
            const auto& b = ev.edgeSeq(axis * 4 + q);
            if (a.size() != b.size() ||
                !std::equal(a.begin(), a.end(), b.begin(),
                            [](double x, double y) { return std::abs(x - y) < 1e-15; }))
                uniformSeqs = false;
        }

    if (!uniformSeqs) {
        for (int k = 0; k < nk; ++k)
            for (int j = 0; j < nj; ++j)
                for (int i = 0; i < ni; ++i)
                    out[(size_t(k) * nj + j) * ni + i] = ev.gridPoint(i, j, k);
        return;
    }

    // ---- SIMD 快速路径（各轴序列一致） ----
    // P[i] = C0 + u·C1 + Σ_q wx_q·( Ex_q[i] − (2/3)(A_q + u·B_q) )
    alignas(32) std::array<std::vector<double>, 3> EX[4];
    for (int q = 0; q < 4; ++q) {
        const auto& src = ev.edgePts(q);
        for (int a = 0; a < 3; ++a) EX[q][a].resize(ni);
        for (int i = 0; i < ni; ++i) {
            EX[q][0][i] = src[i].x;
            EX[q][1][i] = src[i].y;
            EX[q][2][i] = src[i].z;
        }
    }
    const double* s0 = ev.seq(0).data();
    const double* s1 = ev.seq(1).data();
    const double* s2 = ev.seq(2).data();

    // x 向直线边端点：S_q(u) = A_q + u·B_q
    Vec3 A[4], B[4];
    for (int q = 0; q < 4; ++q) {
        Vec3 a0, a1;
        straightEdgeEnds(ev.cornersArr(), q, a0, a1);
        A[q] = a0;
        B[q] = a1 - a0;
    }

    using V = simd::dvec;
    constexpr int W = V::width;
    double tmp[3][W > 0 ? W : 1];

    for (int k = 0; k < nk; ++k) {
        const double w = s2[k];
        for (int j = 0; j < nj; ++j) {
            const double v = s1[j];
            // y/z 贡献在行内为 u 的仿射函数：wy_q(u) = wyA[q] + wyB[q]·u
            const double wyA[4] = {1 - w, 0, 0, w};
            const double wyB[4] = {-(1 - w), (1 - w), w, -w};
            const double wzA[4] = {1 - v, 0, 0, v};
            const double wzB[4] = {-(1 - v), (1 - v), v, -v};
            Vec3 C0{0, 0, 0}, C1{0, 0, 0};
            for (int q = 0; q < 4; ++q) {
                Vec3 ya, yb;
                straightEdgeEnds(ev.cornersArr(), 4 + q, ya, yb);
                const Vec3 sy = ya + (yb - ya) * v;
                const Vec3 Ky = (ev.edgePts(4 + q)[j] * 3.0 - sy * 2.0) / 3.0;
                C0 += Ky * wyA[q];
                C1 += Ky * wyB[q];
                Vec3 za, zb;
                straightEdgeEnds(ev.cornersArr(), 8 + q, za, zb);
                const Vec3 sz = za + (zb - za) * w;
                const Vec3 Kz = (ev.edgePts(8 + q)[k] * 3.0 - sz * 2.0) / 3.0;
                C0 += Kz * wzA[q];
                C1 += Kz * wzB[q];
            }
            const double wx[4] = {(1 - v) * (1 - w), v * (1 - w), v * w, (1 - v) * w};
            Vec3* row = out.data() + (size_t(k) * nj + j) * ni;

            int i = 0;
            for (; i + W <= ni; i += W) {
                const V u = V::load(s0 + i);
                for (int a = 0; a < 3; ++a) {
                    V r = V::fmadd(V::splat((&C1.x)[a]), u, V::splat((&C0.x)[a]));
                    for (int q = 0; q < 4; ++q) {
                        // D = Ex − (2/3)(A + u·B)
                        V d = V::load(EX[q][a].data() + i) -
                              (V::splat((2.0 / 3.0) * (&A[q].x)[a]) +
                               V::splat((2.0 / 3.0) * (&B[q].x)[a]) * u);
                        r = V::fmadd(V::splat(wx[q]), d, r);
                    }
                    r.store(tmp[a]);
                }
                for (int t = 0; t < W; ++t) {
                    row[i + t].x = tmp[0][t];
                    row[i + t].y = tmp[1][t];
                    row[i + t].z = tmp[2][t];
                }
            }
            for (; i < ni; ++i) {  // 尾部标量
                Vec3 p = C0 + C1 * s0[i];
                for (int q = 0; q < 4; ++q) {
                    const Vec3 s = A[q] + B[q] * s0[i];
                    p += (ev.edgePts(q)[i] * 3.0 - s * 2.0) * (wx[q] / 3.0);
                }
                row[i] = p;
            }
        }
    }
}

// ---------- 并查集 ----------
struct DSU {
    std::vector<int> p;
    explicit DSU(size_t n) : p(n) { std::iota(p.begin(), p.end(), 0); }
    int find(int x) {
        while (p[x] != x) {
            p[x] = p[p[x]];
            x = p[x];
        }
        return x;
    }
    void unite(int a, int b) {
        a = find(a);
        b = find(b);
        if (a == b) return;
        if (a > b) std::swap(a, b);
        p[b] = a;
    }
};

struct FaceCornerKey {
    std::array<int, 4> v;
    bool operator==(const FaceCornerKey& o) const { return v == o.v; }
};
struct FaceCornerKeyHash {
    size_t operator()(const FaceCornerKey& k) const {
        uint64_t h = 1469598103934665603ULL;
        for (int x : k.v) h ^= uint64_t(uint32_t(x)) + 0x9e3779b97f4a7c15ULL + (h << 6) + (h >> 2);
        return (size_t)h;
    }
};

struct FaceRef {
    int bi;
    int face;
};

void faceDims(const BlockEval& ev, int f, int& nU, int& nV) {
    const hex::FaceInfo& I = hex::faceInfo(f);
    nU = ev.n(I.axisU);
    nV = ev.n(I.axisV);
}

long localIndex(const BlockEval& ev, int i, int j, int k) {
    return (long(k) * ev.n(1) + j) * ev.n(0) + i;
}

}  // namespace

// ===================== 主流程 =====================

bool generateMesh(const Model& m, MeshResult& out, std::string& err) {
    using Clock = std::chrono::steady_clock;
    out.clear();
    const auto t0 = Clock::now();

    std::vector<const Block*> enabled;
    for (const auto& b : m.blocks)
        if (b.enabled) enabled.push_back(&b);
    if (enabled.empty()) {
        err = "没有可用的块";
        return false;
    }
    const int nB = (int)enabled.size();

    // ---------- 1) 并行：每块 BlockEval ----------
    std::vector<BlockEval> evals(nB);
    std::vector<std::string> errs(nB);
    globalPool().parallelRanges(nB, [&](size_t lo, size_t hi) {
        for (size_t bi = lo; bi < hi; ++bi)
            evals[bi].build(m, *enabled[bi], &errs[bi]);
    });
    for (const auto& e : errs)
        if (!e.empty()) {
            err = e;
            return false;
        }

    std::vector<long> off(nB + 1, 0);
    for (int bi = 0; bi < nB; ++bi)
        off[bi + 1] = off[bi] + (long)evals[bi].n(0) * evals[bi].n(1) * evals[bi].n(2);
    const long totalPts = off[nB];

    // ---------- 2) 块间耦合面节点归一（DSU，仅依赖拓扑/布点数） ----------
    std::unordered_map<FaceCornerKey, std::vector<FaceRef>, FaceCornerKeyHash> faceMap;
    faceMap.reserve(nB * 6);
    for (int bi = 0; bi < nB; ++bi) {
        for (int f = 0; f < 6; ++f) {
            FaceCornerKey key;
            for (int c = 0; c < 4; ++c) key.v[c] = enabled[bi]->verts[hex::kFaces[f][c]];
            std::sort(key.v.begin(), key.v.end());
            faceMap[key].push_back({bi, f});
        }
    }
    DSU dsu((size_t)totalPts);
    std::vector<std::pair<int, int>> unitedPairs;
    for (auto& kv : faceMap) {
        auto& refs = kv.second;
        if (refs.size() < 2) continue;
        const FaceRef& A = refs[0];
        const hex::FaceInfo& IA = hex::faceInfo(A.face);
        int nAu, nAv;
        faceDims(evals[A.bi], A.face, nAu, nAv);
        for (size_t t = 1; t < refs.size(); ++t) {
            const FaceRef& B = refs[t];
            int nBu, nBv;
            faceDims(evals[B.bi], B.face, nBu, nBv);
            if (nAu != nBu || nAv != nBv) continue;
            const hex::FaceInfo& IB = hex::faceInfo(B.face);
            int p0 = -1, p1 = -1, p3 = -1;
            for (int q = 0; q < 4; ++q) {
                if (enabled[B.bi]->verts[IB.corners[q]] == enabled[A.bi]->verts[IA.corners[0]])
                    p0 = q;
                if (enabled[B.bi]->verts[IB.corners[q]] == enabled[A.bi]->verts[IA.corners[1]])
                    p1 = q;
                if (enabled[B.bi]->verts[IB.corners[q]] == enabled[A.bi]->verts[IA.corners[3]])
                    p3 = q;
            }
            if (p0 < 0 || p1 < 0 || p3 < 0) continue;
            auto gridCoord = [](int q, int nU, int nV, int& r, int& s) {
                switch (q) {
                    case 0: r = 0; s = 0; break;
                    case 1: r = nU - 1; s = 0; break;
                    case 2: r = nU - 1; s = nV - 1; break;
                    default: r = 0; s = nV - 1; break;
                }
            };
            int g0r, g0s, g1r, g1s, g3r, g3s;
            gridCoord(p0, nBu, nBv, g0r, g0s);
            gridCoord(p1, nBu, nBv, g1r, g1s);
            gridCoord(p3, nBu, nBv, g3r, g3s);
            const double stepR[2] = {double(g1r - g0r) / (nAu - 1),
                                     double(g1s - g0s) / (nAu - 1)};
            const double stepS[2] = {double(g3r - g0r) / (nAv - 1),
                                     double(g3s - g0s) / (nAv - 1)};
            const long baseA = off[A.bi], baseB = off[B.bi];
            auto faceNodeA = [&](int r, int s, int g[3]) {
                const int ru = IA.signU > 0 ? r : (nAu - 1 - r);
                const int sv = IA.signV > 0 ? s : (nAv - 1 - s);
                g[IA.axisU] = ru;
                g[IA.axisV] = sv;
                g[IA.wAxis] = IA.wLvl ? (evals[A.bi].n(IA.wAxis) - 1) : 0;
            };
            auto faceNodeB = [&](int r, int s, int g[3]) {
                const int ru = IB.signU > 0 ? r : (nBu - 1 - r);
                const int sv = IB.signV > 0 ? s : (nBv - 1 - s);
                g[IB.axisU] = ru;
                g[IB.axisV] = sv;
                g[IB.wAxis] = IB.wLvl ? (evals[B.bi].n(IB.wAxis) - 1) : 0;
            };
            for (int s = 0; s < nAv; ++s) {
                for (int r = 0; r < nAu; ++r) {
                    int ga[3], gb[3];
                    faceNodeA(r, s, ga);
                    const int rb = (int)std::lround(g0r + stepR[0] * r + stepS[0] * s);
                    const int sb = (int)std::lround(g0s + stepR[1] * r + stepS[1] * s);
                    faceNodeB(rb, sb, gb);
                    const int ia = (int)(baseA + localIndex(evals[A.bi], ga[0], ga[1], ga[2]));
                    const int ib = (int)(baseB + localIndex(evals[B.bi], gb[0], gb[1], gb[2]));
                    dsu.unite(ia, ib);
                    unitedPairs.push_back({ia, ib});
                }
            }
        }
    }

    // ---------- 2.5) 并行：块网格点生成 ----------
    std::vector<std::vector<Vec3>> blockPts(nB);
    globalPool().parallelRanges(nB, [&](size_t lo, size_t hi) {
        for (size_t bi = lo; bi < hi; ++bi) genBlockPoints(evals[bi], blockPts[bi]);
    });

    // ---------- 2.6) 目标化 untangle：仅平滑邻接负体积单元的非边节点（界面 DSU 同步） ----------
    {
        std::unordered_map<int, std::vector<std::pair<int, long>>> groups;
        for (auto& pr : unitedPairs) groups[dsu.find(pr.first)];
        for (auto& pr : unitedPairs) {
            auto add = [&](int idx) {
                int bi = (int)(std::upper_bound(off.begin(), off.end(), idx) - off.begin()) - 1;
                groups[dsu.find(idx)].push_back({bi, idx - off[bi]});
            };
            add(pr.first);
            add(pr.second);
        }
        auto isEdgeNode = [&](const BlockEval& ev, int i, int j, int k) {
            int bnd = (i == 0 || i == ev.n(0) - 1) + (j == 0 || j == ev.n(1) - 1) +
                      (k == 0 || k == ev.n(2) - 1);
            return bnd >= 2;
        };
        auto cellSJ = [](const Vec3 p[8]) {
            double sj = 1.0;
            for (int v = 0; v < 8; ++v) {
                const Vec3 e1 = p[hex::kCornerNb[v][0]] - p[v];
                const Vec3 e2 = p[hex::kCornerNb[v][1]] - p[v];
                const Vec3 e3 = p[hex::kCornerNb[v][2]] - p[v];
                const double dn = e1.norm() * e2.norm() * e3.norm();
                const double j = dn > 1e-300 ? e1.dot(e2.cross(e3)) / dn : 0.0;
                sj = std::min(sj, j);
            }
            return sj;
        };
        std::vector<std::vector<Vec3>> tmp(nB);
        for (int iter = 0; iter < 200; ++iter) {
            // 找负体积单元邻接节点
            std::vector<std::vector<char>> mark(nB);
            bool anyBad = false;
            for (int bi = 0; bi < nB; ++bi) {
                const BlockEval& ev = evals[bi];
                const int ni = ev.n(0), nj = ev.n(1), nk = ev.n(2);
                mark[bi].assign(blockPts[bi].size(), 0);
                for (int k = 0; k + 1 < nk; ++k)
                    for (int j = 0; j + 1 < nj; ++j)
                        for (int i = 0; i + 1 < ni; ++i) {
                            Vec3 p[8];
                            const int cc[8][3] = {{i, j, k},         {i + 1, j, k},
                                                  {i + 1, j + 1, k}, {i, j + 1, k},
                                                  {i, j, k + 1},     {i + 1, j, k + 1},
                                                  {i + 1, j + 1, k + 1}, {i, j + 1, k + 1}};
                            for (int v = 0; v < 8; ++v)
                                p[v] = blockPts[bi][(size_t(cc[v][2]) * nj + cc[v][1]) * ni + cc[v][0]];
                            if (cellSJ(p) < 0.0) {
                                anyBad = true;
                                for (int v = 0; v < 8; ++v)
                                    mark[bi][(size_t(cc[v][2]) * nj + cc[v][1]) * ni + cc[v][0]] = 1;
                            }
                        }
            }
            if (!anyBad) break;
            // 标记膨胀一层
            {
                std::vector<std::vector<char>> dil(nB);
                for (int bi = 0; bi < nB; ++bi) {
                    const BlockEval& ev = evals[bi];
                    const int ni = ev.n(0), nj = ev.n(1), nk = ev.n(2);
                    dil[bi] = mark[bi];
                    for (int k = 0; k < nk; ++k)
                        for (int j = 0; j < nj; ++j)
                            for (int i = 0; i < ni; ++i) {
                                const size_t id = (size_t(k) * nj + j) * ni + i;
                                if (!mark[bi][id]) continue;
                                if (i > 0) dil[bi][id - 1] = 1;
                                if (i < ni - 1) dil[bi][id + 1] = 1;
                                if (j > 0) dil[bi][id - ni] = 1;
                                if (j < nj - 1) dil[bi][id + ni] = 1;
                                if (k > 0) dil[bi][id - (size_t)ni * nj] = 1;
                                if (k < nk - 1) dil[bi][id + (size_t)ni * nj] = 1;
                            }
                }
                mark = std::move(dil);
            }
            // 平滑被标记的非边节点
            globalPool().parallelRanges(nB, [&](size_t lo, size_t hi) {
                for (size_t bi = lo; bi < hi; ++bi) {
                    const BlockEval& ev = evals[bi];
                    const int ni = ev.n(0), nj = ev.n(1), nk = ev.n(2);
                    tmp[bi] = blockPts[bi];
                    for (int k = 0; k < nk; ++k)
                        for (int j = 0; j < nj; ++j)
                            for (int i = 0; i < ni; ++i) {
                                const size_t id = (size_t(k) * nj + j) * ni + i;
                                if (!mark[bi][id] || isEdgeNode(ev, i, j, k)) continue;
                                Vec3 s{0, 0, 0};
                                int c = 0;
                                if (i > 0) s += blockPts[bi][id - 1], ++c;
                                if (i < ni - 1) s += blockPts[bi][id + 1], ++c;
                                if (j > 0) s += blockPts[bi][id - ni], ++c;
                                if (j < nj - 1) s += blockPts[bi][id + ni], ++c;
                                if (k > 0) s += blockPts[bi][id - (size_t)ni * nj], ++c;
                                if (k < nk - 1) s += blockPts[bi][id + (size_t)ni * nj], ++c;
                                if (c) tmp[bi][id] = s / double(c);
                            }
                }
            });
            for (auto& gv : groups) {
                auto& mem = gv.second;
                if (mem.size() < 2) continue;
                Vec3 avg{0, 0, 0};
                int c = 0;
                for (auto& mm : mem) avg += tmp[mm.first][mm.second], ++c;
                if (!c) continue;
                avg = avg / double(c);
                for (auto& mm : mem) tmp[mm.first][mm.second] = avg;
            }
            globalPool().parallelRanges(nB, [&](size_t lo, size_t hi) {
                for (size_t bi = lo; bi < hi; ++bi) blockPts[bi] = tmp[bi];
            });
        }
    }

    // 块级网格点缓存（可视化用）
    out.blockPoints = std::move(blockPts);
    out.blockDims.resize(nB);
    out.blockIds.resize(nB);
    for (int bi = 0; bi < nB; ++bi) {
        out.blockDims[bi] = {evals[bi].n(0), evals[bi].n(1), evals[bi].n(2)};
        out.blockIds[bi] = enabled[bi]->id;
    }
    std::vector<std::vector<Vec3>>& blockPtsRef = out.blockPoints;

    // ---------- 3) 位置哈希去重（捕捉几何重合但拓扑未共享的节点） ----------
    const double diag = m.diag();
    const double quant = std::max(diag * 1e-9, 1e-30);
    struct QKey {
        long long x, y, z;
        bool operator==(const QKey& o) const { return x == o.x && y == o.y && z == o.z; }
    };
    struct QHash {
        size_t operator()(const QKey& k) const {
            uint64_t h = uint64_t(k.x) * 73856093ULL ^ uint64_t(k.y) * 19349663ULL ^
                         uint64_t(k.z) * 83492791ULL;
            return (size_t)h;
        }
    };
    std::vector<int> remap((size_t)totalPts, -1);
    std::unordered_map<QKey, int, QHash> posMap;
    posMap.reserve((size_t)(totalPts * 1.3));
    for (long idx = 0; idx < totalPts; ++idx) {
        const int root = dsu.find((int)idx);
        if (remap[root] >= 0) {
            remap[idx] = remap[root];
            continue;
        }
        int bi = (int)(std::upper_bound(off.begin(), off.end(), idx) - off.begin()) - 1;
        const Vec3& p = blockPtsRef[bi][(size_t)(idx - off[bi])];
        QKey key{std::llround(p.x / quant), std::llround(p.y / quant),
                 std::llround(p.z / quant)};
        auto it = posMap.find(key);
        if (it != posMap.end()) {
            remap[root] = it->second;
            remap[idx] = it->second;
        } else {
            const int id = (int)out.points.size();
            out.points.push_back(p);
            posMap.emplace(key, id);
            remap[root] = id;
            remap[idx] = id;
        }
    }

    // ---------- 4) 并行：单元 ----------
    std::vector<long> cellOff(nB + 1, 0);
    for (int bi = 0; bi < nB; ++bi) {
        cellOff[bi + 1] = cellOff[bi] +
                          (long)(evals[bi].n(0) - 1) * (evals[bi].n(1) - 1) * (evals[bi].n(2) - 1);
    }
    out.cells.resize((size_t)cellOff[nB]);
    out.cellBlock.resize((size_t)cellOff[nB]);
    globalPool().parallelRanges(nB, [&](size_t lo, size_t hi) {
        for (size_t bi = lo; bi < hi; ++bi) {
            const BlockEval& ev = evals[bi];
            const int ni = ev.n(0), nj = ev.n(1), nk = ev.n(2);
            const long base = off[bi];
            long ci = cellOff[bi];
            for (int k = 0; k < nk - 1; ++k)
                for (int j = 0; j < nj - 1; ++j)
                    for (int i = 0; i < ni - 1; ++i) {
                        const int c[8][3] = {{i, j, k},         {i + 1, j, k},
                                             {i + 1, j + 1, k}, {i, j + 1, k},
                                             {i, j, k + 1},     {i + 1, j, k + 1},
                                             {i + 1, j + 1, k + 1}, {i, j + 1, k + 1}};
                        for (int v = 0; v < 8; ++v)
                            out.cells[(size_t)ci][v] =
                                remap[(size_t)(base + localIndex(ev, c[v][0], c[v][1], c[v][2]))];
                        out.cellBlock[(size_t)ci] = enabled[bi]->id;
                        ++ci;
                    }
        }
    });

    // ---------- 5) 面装配 ----------
    // 块内部面直接生成（每层面一次）；块外表面进入配对哈希，重合者转为内部面。
    struct FaceRec {
        std::array<int, 4> key;
        std::array<int, 4> quad;
        int owner = -1, neighbour = -1;
        int bi = -1;
        int face = -1;
    };
    std::vector<std::vector<FaceRec>> perThreadB, perThreadI;
    {
        const size_t nWorkers = globalPool().size();
        perThreadB.resize(nWorkers);
        perThreadI.resize(nWorkers);
        std::atomic<size_t> ticket{0};
        globalPool().parallelRanges(nB, [&](size_t lo, size_t hi) {
            std::vector<FaceRec> bnd, intl;
            for (size_t bi = lo; bi < hi; ++bi) {
                const BlockEval& ev = evals[bi];
                const int dims[3] = {ev.n(0), ev.n(1), ev.n(2)};
                const long base = off[bi];
                auto nodeId = [&](int i, int j, int k) {
                    return remap[(size_t)(base + localIndex(ev, i, j, k))];
                };
                auto cellId = [&](int ci, int cj, int ck) {
                    return (int)(cellOff[bi] +
                                 (long(ck) * (dims[1] - 1) + cj) * (dims[0] - 1) + ci);
                };
                for (int axis = 0; axis < 3; ++axis) {
                    const int axB = (axis + 1) % 3, axC = (axis + 2) % 3;
                    for (int L = 0; L < dims[axis]; ++L) {
                        const bool isBoundary = (L == 0 || L == dims[axis] - 1);
                        int fIdx = -1;
                        if (isBoundary)
                            for (int f = 0; f < 6; ++f)
                                if (hex::kFaceNormalAxis[f] == axis &&
                                    hex::kFaceLow[f] == (L == 0 ? 1 : 0))
                                    fIdx = f;
                        for (int s = 0; s < dims[axC] - 1; ++s)
                            for (int r = 0; r < dims[axB] - 1; ++r) {
                                int g[4][3];
                                const int dr[4] = {0, 1, 1, 0}, ds[4] = {0, 0, 1, 1};
                                for (int c = 0; c < 4; ++c) {
                                    g[c][0] = g[c][1] = g[c][2] = 0;
                                    g[c][axis] = L;
                                    g[c][axB] = r + dr[c];
                                    g[c][axC] = s + ds[c];
                                }
                                FaceRec rec;
                                for (int c = 0; c < 4; ++c)
                                    rec.quad[c] = nodeId(g[c][0], g[c][1], g[c][2]);
                                if (isBoundary && L == 0)
                                    std::swap(rec.quad[1], rec.quad[3]);  // 外法向 -axis
                                int cc[3] = {0, 0, 0};
                                cc[axB] = r;
                                cc[axC] = s;
                                if (isBoundary) {
                                    cc[axis] = L == 0 ? 0 : L - 1;
                                    rec.owner = cellId(cc[0], cc[1], cc[2]);
                                    rec.neighbour = -1;
                                    rec.bi = (int)bi;
                                    rec.face = fIdx;
                                    rec.key = rec.quad;
                                    std::sort(rec.key.begin(), rec.key.end());
                                    bnd.push_back(rec);
                                } else {
                                    cc[axis] = L - 1;
                                    rec.owner = cellId(cc[0], cc[1], cc[2]);
                                    cc[axis] = L;
                                    rec.neighbour = cellId(cc[0], cc[1], cc[2]);
                                    intl.push_back(rec);
                                }
                            }
                    }
                }
            }
            const size_t slot = ticket.fetch_add(1);
            if (slot < perThreadB.size()) {
                perThreadB[slot] = std::move(bnd);
                perThreadI[slot] = std::move(intl);
            }
        });
    }
    struct FaceKeyHash {
        size_t operator()(const std::array<int, 4>& k) const {
            uint64_t h = 1469598103934665603ULL;
            for (int x : k) h ^= uint64_t(uint32_t(x)) + 0x9e3779b97f4a7c15ULL + (h << 6) + (h >> 2);
            return (size_t)h;
        }
    };
    std::unordered_map<std::array<int, 4>, int, FaceKeyHash> seen;
    std::vector<FaceRec> internal, boundary;
    for (auto& v : perThreadI)
        for (auto& rec : v) internal.push_back(std::move(rec));
    {
        size_t totalB = 0;
        for (auto& v : perThreadB) totalB += v.size();
        seen.reserve(totalB);
        for (auto& v : perThreadB)
            for (auto& rec : v) {
                auto it = seen.find(rec.key);
                if (it == seen.end()) {
                    seen.emplace(rec.key, (int)boundary.size());
                    boundary.push_back(rec);
                } else {
                    const size_t pos = (size_t)it->second;
                    FaceRec& first = boundary[pos];
                    FaceRec merged;
                    merged.key = rec.key;
                    if (first.owner < rec.owner) {
                        merged.quad = first.quad;
                        merged.owner = first.owner;
                        merged.neighbour = rec.owner;
                        merged.bi = first.bi;
                    } else {
                        merged.quad = rec.quad;
                        merged.owner = rec.owner;
                        merged.neighbour = first.owner;
                        merged.bi = rec.bi;
                    }
                    merged.face = -1;
                    internal.push_back(merged);
                    boundary[pos] = boundary.back();
                    boundary.pop_back();
                    if (pos < boundary.size()) seen[boundary[pos].key] = (int)pos;
                }
            }
    }
    std::sort(internal.begin(), internal.end(), [](const FaceRec& a, const FaceRec& b) {
        if (a.owner != b.owner) return a.owner < b.owner;
        return a.neighbour < b.neighbour;
    });

    std::vector<std::string> patchOrder;
    std::unordered_map<std::string, int> patchIdx;
    for (const auto& rec : boundary) {
        const std::string& pn = enabled[rec.bi]->patches[rec.face];
        if (!patchIdx.count(pn)) {
            patchIdx[pn] = (int)patchOrder.size();
            patchOrder.push_back(pn);
        }
    }
    std::sort(boundary.begin(), boundary.end(), [&](const FaceRec& a, const FaceRec& b) {
        const int pa = patchIdx[enabled[a.bi]->patches[a.face]];
        const int pb = patchIdx[enabled[b.bi]->patches[b.face]];
        if (pa != pb) return pa < pb;
        if (a.owner != b.owner) return a.owner < b.owner;
        return a.bi < b.bi;
    });

    out.nInternalFaces = (int)internal.size();
    out.patchNames = patchOrder;
    const size_t nF = internal.size() + boundary.size();
    out.faces.resize(nF);
    out.owner.resize(nF);
    out.neighbour.resize(nF);
    out.faceBlock.resize(nF);
    size_t fi = 0;
    for (const auto& rec : internal) {
        out.faces[fi] = rec.quad;
        out.owner[fi] = rec.owner;
        out.neighbour[fi] = rec.neighbour;
        out.faceBlock[fi] = -1;
        ++fi;
    }
    out.patchStart.resize(patchOrder.size());
    out.patchNFaces.resize(patchOrder.size(), 0);
    for (const auto& rec : boundary) {
        out.faces[fi] = rec.quad;
        out.owner[fi] = rec.owner;
        out.neighbour[fi] = -1;
        out.faceBlock[fi] = enabled[rec.bi]->id;
        const int pi = patchIdx[enabled[rec.bi]->patches[rec.face]];
        if (out.patchNFaces[pi] == 0) out.patchStart[pi] = (int)fi;
        out.patchNFaces[pi]++;
        ++fi;
    }

    // ---------- 6) 加密区标记（魔法笔） ----------
    if (!m.refineZones.empty()) {
        out.cellRefined.assign(out.cells.size(), 0);
        std::vector<Vec3> zoneMin(m.refineZones.size()), zoneMax(m.refineZones.size());
        for (size_t z = 0; z < m.refineZones.size(); ++z) {
            const auto& zn = m.refineZones[z];
            Vec3 lo{1e300, 1e300, 1e300}, hi{-1e300, -1e300, -1e300};
            for (const auto& p : zn.stroke) {
                lo.x = std::min(lo.x, p.x); lo.y = std::min(lo.y, p.y); lo.z = std::min(lo.z, p.z);
                hi.x = std::max(hi.x, p.x); hi.y = std::max(hi.y, p.y); hi.z = std::max(hi.z, p.z);
            }
            zoneMin[z] = lo - Vec3{zn.radius, zn.radius, zn.radius};
            zoneMax[z] = hi + Vec3{zn.radius, zn.radius, zn.radius};
        }
        auto distToZone = [&](const Vec3& p, const RefineZone& zn) {
            double best = 1e300;
            for (size_t i = 1; i < zn.stroke.size(); ++i) {
                const Vec3 a = zn.stroke[i - 1], b = zn.stroke[i];
                const Vec3 ab = b - a;
                const double L2 = ab.norm2();
                double t = L2 > 1e-30 ? (p - a).dot(ab) / L2 : 0.0;
                t = std::clamp(t, 0.0, 1.0);
                best = std::min(best, (p - (a + ab * t)).norm());
            }
            return best;
        };
        globalPool().parallelRanges(out.cells.size(), [&](size_t lo, size_t hi) {
            for (size_t ci = lo; ci < hi; ++ci) {
                for (int v = 0; v < 8 && !out.cellRefined[ci]; ++v) {
                    const Vec3& p = out.points[(size_t)out.cells[ci][v]];
                    for (size_t z = 0; z < m.refineZones.size(); ++z) {
                        if (!m.refineZones[z].enabled) continue;
                        if (p.x < zoneMin[z].x || p.x > zoneMax[z].x || p.y < zoneMin[z].y ||
                            p.y > zoneMax[z].y || p.z < zoneMin[z].z || p.z > zoneMax[z].z)
                            continue;
                        if (distToZone(p, m.refineZones[z]) <= m.refineZones[z].radius) {
                            out.cellRefined[ci] = 1;
                            break;
                        }
                    }
                }
            }
        });
    }

    out.genMs = std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
    return true;
}

}  // namespace meshgen
