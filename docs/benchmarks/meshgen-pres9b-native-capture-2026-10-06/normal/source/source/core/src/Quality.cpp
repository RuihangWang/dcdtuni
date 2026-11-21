#include "meshgen/Quality.hpp"

#include <algorithm>
#include <cmath>
#include <mutex>
#include <vector>

#include "meshgen/ThreadPool.hpp"

namespace meshgen {

QualityStats computeQuality(MeshResult& mesh) {
    QualityStats st;
    const size_t nC = mesh.cells.size();
    st.nCells = (long)nC;
    if (nC == 0) return st;
    mesh.cellQuality.assign(nC, 1.0);
    mesh.cellCenters.assign(nC, Vec3{});

    struct Acc {
        double sum = 0, mn = 1.0;
        long bad = 0;
    };
    Acc total;
    std::mutex mu;

    // ---- 缩放雅可比（8 角点最小值）+ 单元中心 ----
    globalPool().parallelRanges(nC, [&](size_t lo, size_t hi) {
        Acc local;
        for (size_t ci = lo; ci < hi; ++ci) {
            const auto& c = mesh.cells[ci];
            Vec3 p[8];
            Vec3 ctr{0, 0, 0};
            for (int v = 0; v < 8; ++v) {
                p[v] = mesh.points[(size_t)c[v]];
                ctr += p[v];
            }
            mesh.cellCenters[ci] = ctr * 0.125;
            double sj = 1.0;
            for (int v = 0; v < 8; ++v) {
                const Vec3 e1 = p[hex::kCornerNb[v][0]] - p[v];
                const Vec3 e2 = p[hex::kCornerNb[v][1]] - p[v];
                const Vec3 e3 = p[hex::kCornerNb[v][2]] - p[v];
                const double denom = e1.norm() * e2.norm() * e3.norm();
                const double j = denom > 1e-300 ? e1.dot(e2.cross(e3)) / denom : 0.0;
                sj = std::min(sj, j);
            }
            mesh.cellQuality[ci] = sj;
            local.sum += sj;
            local.mn = std::min(local.mn, sj);
            if (sj < st.sjThreshold) local.bad++;
        }
        std::lock_guard<std::mutex> lk(mu);
        total.sum += local.sum;
        total.mn = std::min(total.mn, local.mn);
        total.bad += local.bad;
    });
    st.minSJ = total.mn;
    st.avgSJ = total.sum / double(nC);
    st.nBadSJ = total.bad;

    // ---- 内部面非正交角 ----
    const size_t nIF = (size_t)mesh.nInternalFaces;
    if (nIF > 0) {
        double sumAng = 0, maxAng = 0;
        std::mutex mu2;
        globalPool().parallelRanges(nIF, [&](size_t lo, size_t hi) {
            double s = 0, mx = 0;
            for (size_t fi = lo; fi < hi; ++fi) {
                const auto& f = mesh.faces[fi];
                const Vec3& a = mesh.points[(size_t)f[0]];
                const Vec3& b = mesh.points[(size_t)f[1]];
                const Vec3& c = mesh.points[(size_t)f[2]];
                const Vec3& d = mesh.points[(size_t)f[3]];
                Vec3 n = (c - a).cross(d - b);
                const double na = n.norm();
                if (na < 1e-300) continue;
                n = n / na;
                Vec3 dir = mesh.cellCenters[(size_t)mesh.neighbour[fi]] -
                           mesh.cellCenters[(size_t)mesh.owner[fi]];
                const double dl = dir.norm();
                if (dl < 1e-300) continue;
                dir = dir / dl;
                if (n.dot(dir) < 0) n = n * -1.0;
                const double ang =
                    std::acos(std::clamp(n.dot(dir), -1.0, 1.0)) * 180.0 / M_PI;
                s += ang;
                mx = std::max(mx, ang);
            }
            std::lock_guard<std::mutex> lk(mu2);
            sumAng += s;
            maxAng = std::max(maxAng, mx);
        });
        st.avgNonOrtho = sumAng / double(nIF);
        st.maxNonOrtho = maxAng;
    }
    return st;
}

}  // namespace meshgen
