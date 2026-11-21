#include "meshgen/Refine.hpp"

#include <algorithm>
#include <cmath>

#include "meshgen/Coupling.hpp"

namespace meshgen {

namespace {

// 曲线在参数 t 处的基准网格尺寸（由当前序列得出）
double baseSpacingAt(const std::vector<double>& seq, double L, double t) {
    if (seq.size() < 2 || L <= 0) return L;
    t = std::clamp(t, 0.0, 1.0);
    size_t k = 0;
    while (k + 2 < seq.size() && seq[k + 1] < t) ++k;
    return std::max(L * (seq[k + 1] - seq[k]), L * 1e-6);
}

double distToStroke(const Vec3& p, const std::vector<Vec3>& stroke) {
    double best = 1e300;
    for (size_t i = 1; i < stroke.size(); ++i) {
        const Vec3 a = stroke[i - 1], b = stroke[i];
        const Vec3 ab = b - a;
        const double L2 = ab.norm2();
        double t = L2 > 1e-30 ? (p - a).dot(ab) / L2 : 0.0;
        t = std::clamp(t, 0.0, 1.0);
        best = std::min(best, (p - (a + ab * t)).norm());
    }
    return best;
}

}  // namespace

RefineResult applyRefinement(Model& m) {
    RefineResult res;
    std::vector<RefineZone*> zones;
    for (auto& z : m.refineZones)
        if (z.enabled && z.stroke.size() >= 2) zones.push_back(&z);
    if (zones.empty()) {
        res.log.push_back("没有启用的加密区");
        return res;
    }

    // ---- 1) 每条曲线自适应布点 ----
    for (auto& c : m.curves) {
        const double L = c.length(m);
        if (L <= 1e-15) continue;
        std::vector<double> base =
            ((int)c.customSeq.size() == c.nPoints && c.nPoints >= 2)
                ? c.customSeq
                : distributionSequence(c.dist, c.nPoints);
        // 采样检测与各加密区的距离
        const int S = std::max(64, 4 * c.nPoints);
        bool affected = false;
        for (auto* z : zones) {
            for (int i = 0; i <= S && !affected; ++i) {
                const Vec3 p = c.at(m, double(i) / S);
                if (distToStroke(p, z->stroke) < z->radius) affected = true;
            }
        }
        if (!affected) continue;

        // 间距推进
        std::vector<double> seq2;
        seq2.reserve(c.nPoints * 2);
        double s = 0.0;
        seq2.push_back(0.0);
        int guard = 0;
        const int maxPts = 4096;
        while (s < L && (int)seq2.size() < maxPts && ++guard < 100000) {
            const double t = s / L;
            double h = baseSpacingAt(base, L, t);
            for (auto* z : zones) {
                const double d = distToStroke(c.at(m, t), z->stroke);
                const double k = std::clamp(d / z->radius, 0.0, 1.0);
                h = std::min(h, z->targetSize + (h - z->targetSize) * k);
            }
            h = std::max(h, L / (maxPts - 1));
            s += h;
            if (s >= L - 0.25 * h) break;
            seq2.push_back(s / L);
        }
        seq2.push_back(1.0);
        if (seq2.size() < 3) continue;
        if ((int)seq2.size() == c.nPoints) continue;  // 无变化
        c.customSeq = seq2;
        c.nPoints = (int)seq2.size();
        res.curvesModified++;
        res.log.push_back("曲线 C" + std::to_string(c.id) + " 自适应加密：n=" +
                          std::to_string(c.nPoints));
    }

    // ---- 2) 块内各轴一致性（同轴任一边被加密 → 全轴对齐） ----
    for (auto& b : m.blocks) {
        if (!b.enabled) continue;
        for (int axis = 0; axis < 3; ++axis) {
            Curve* master = nullptr;
            std::vector<Curve*> es;
            for (int e = 0; e < 12; ++e) {
                if (hex::kEdgeAxis[e] != axis) continue;
                Curve* c = m.curve(b.absCurve(e));
                if (!c) continue;
                es.push_back(c);
                if (!master || c->nPoints > master->nPoints) master = c;
            }
            if (!master) continue;
            for (Curve* c : es) {
                if (c == master || c->nPoints == master->nPoints) continue;
                c->nPoints = master->nPoints;
                c->dist = master->dist;
                c->customSeq = ((int)master->customSeq.size() == master->nPoints)
                                   ? master->customSeq
                                   : std::vector<double>{};
                res.curvesModified++;
            }
        }
    }

    // ---- 3) 耦合传播（点数/序列对齐） ----
    for (int round = 0; round < 3; ++round) {
        bool changed = false;
        auto syncPair = [&](int ida, int idb) {
            Curve* a = m.curve(ida);
            Curve* b = m.curve(idb);
            if (!a || !b || a->id == b->id) return;
            Curve *master = a, *slave = b;
            if (b->nPoints > a->nPoints ||
                (b->nPoints == a->nPoints && !b->customSeq.empty() && a->customSeq.empty()))
                std::swap(master, slave);
            if (slave->nPoints != master->nPoints ||
                (slave->customSeq.empty() != master->customSeq.empty())) {
                slave->nPoints = master->nPoints;
                slave->dist = master->dist;
                slave->customSeq = ((int)master->customSeq.size() == master->nPoints)
                                       ? master->customSeq
                                       : std::vector<double>{};
                changed = true;
            }
        };
        CouplingReport rep = analyzeCouplings(m);
        for (const auto& ec : rep.edges) syncPair(ec.curveA, ec.curveB);
        for (const auto& fc : rep.faces) {
            if (fc.countMatch && fc.distMatch) continue;
            const Block* BA = m.block(fc.blockA);
            const Block* BB = m.block(fc.blockB);
            if (!BA || !BB) continue;
            for (int d = 0; d < 2; ++d) {
                auto faceEdgeId = [&](const Block& B, int f, int dl) {
                    const hex::FaceInfo& I = hex::faceInfo(f);
                    const int axis = dl == 0 ? I.axisU : I.axisV;
                    const int other = dl == 0 ? I.axisV : I.axisU;
                    const int lvl = dl == 0 ? I.v0 : I.u0;
                    return B.absCurve(hex::edgeWithFixedLevels(axis, other, lvl, I.wAxis, I.wLvl));
                };
                syncPair(faceEdgeId(*BA, fc.faceA, d), faceEdgeId(*BB, fc.faceB, fc.mapDir[d]));
            }
        }
        res.rounds = round + 1;
        if (!changed) break;
    }
    if (res.curvesModified == 0) res.log.push_back("加密区未影响任何曲线");
    return res;
}

int clearRefinement(Model& m) {
    int n = 0;
    for (auto& c : m.curves)
        if (!c.customSeq.empty()) {
            c.customSeq.clear();
            ++n;
        }
    return n;
}

}  // namespace meshgen
