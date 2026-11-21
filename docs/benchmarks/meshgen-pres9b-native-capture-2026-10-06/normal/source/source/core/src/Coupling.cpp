#include "meshgen/Coupling.hpp"

#include <algorithm>
#include <map>
#include <set>

namespace meshgen {

namespace {

struct Usage {
    int count = 0;
};

// 面沿其局部 u/v 方向的边编号
int faceEdge(int f, int dirLocal /*0=u,1=v*/, int atOne /*0/1*/) {
    const hex::FaceInfo& I = hex::faceInfo(f);
    const int axis = dirLocal == 0 ? I.axisU : I.axisV;
    const int other = dirLocal == 0 ? I.axisV : I.axisU;
    const int lvlOther = dirLocal == 0 ? (atOne ? I.v0 ^ 1 : I.v0) : (atOne ? I.u0 ^ 1 : I.u0);
    return hex::edgeWithFixedLevels(axis, other, lvlOther, I.wAxis, I.wLvl);
}

int faceCount(const Model& m, const Block& b, int f, int dirLocal) {
    const Curve* c = m.curve(b.absCurve(faceEdge(f, dirLocal, 0)));
    return c ? c->nPoints : 0;
}

}  // namespace

CouplingReport analyzeCouplings(const Model& m) {
    CouplingReport rep;

    // ---- 面耦合：按 4 角点集合分组 ----
    std::map<std::array<int, 4>, std::vector<std::pair<int, int>>> faceMap;
    for (const auto& b : m.blocks) {
        if (!b.enabled) continue;
        for (int f = 0; f < 6; ++f) {
            std::array<int, 4> key;
            for (int c = 0; c < 4; ++c) key[c] = b.verts[hex::kFaces[f][c]];
            std::sort(key.begin(), key.end());
            faceMap[key].push_back({b.id, f});
        }
    }
    for (auto& kv : faceMap) {
        auto& refs = kv.second;
        for (size_t i = 0; i < refs.size(); ++i)
            for (size_t j = i + 1; j < refs.size(); ++j) {
                const Block* BA = m.block(refs[i].first);
                const Block* BB = m.block(refs[j].first);
                if (!BA || !BB) continue;
                FaceCoupling fc;
                fc.blockA = BA->id;
                fc.faceA = refs[i].second;
                fc.blockB = BB->id;
                fc.faceB = refs[j].second;
                fc.nA[0] = faceCount(m, *BA, fc.faceA, 0);
                fc.nA[1] = faceCount(m, *BA, fc.faceA, 1);
                fc.nB[0] = faceCount(m, *BB, fc.faceB, 0);
                fc.nB[1] = faceCount(m, *BB, fc.faceB, 1);
                // 在 B 面环上定位 A 的角点
                const hex::FaceInfo& IA = hex::faceInfo(fc.faceA);
                const hex::FaceInfo& IB = hex::faceInfo(fc.faceB);
                int pos[4] = {-1, -1, -1, -1};
                for (int c = 0; c < 4; ++c)
                    for (int q = 0; q < 4; ++q)
                        if (BB->verts[IB.corners[q]] == BA->verts[IA.corners[c]]) pos[c] = q;
                if (pos[0] < 0 || pos[1] < 0 || pos[3] < 0) continue;
                auto ringMap = [&](int from, int to, int& dirOut, int& signOut) {
                    if ((from + 1) % 4 == to) {
                        dirOut = (from % 2 == 0) ? 0 : 1;
                        signOut = (from < 2) ? 1 : -1;
                    } else {
                        const int q = (from + 3) % 4;
                        dirOut = (q % 2 == 0) ? 0 : 1;
                        signOut = -((q < 2) ? 1 : -1);
                    }
                };
                ringMap(pos[0], pos[1], fc.mapDir[0], fc.mapSign[0]);
                ringMap(pos[0], pos[3], fc.mapDir[1], fc.mapSign[1]);
                fc.countMatch = (fc.nA[0] == fc.nB[fc.mapDir[0]]) &&
                                (fc.nA[1] == fc.nB[fc.mapDir[1]]);
                // 布点律一致性
                fc.distMatch = fc.countMatch;
                if (fc.countMatch) {
                    for (int d = 0; d < 2 && fc.distMatch; ++d) {
                        const Curve* ca = m.curve(BA->absCurve(faceEdge(fc.faceA, d, 0)));
                        const Curve* cb =
                            m.curve(BB->absCurve(faceEdge(fc.faceB, fc.mapDir[d], 0)));
                        if (!ca || !cb) {
                            fc.distMatch = false;
                        } else if (!ca->dist.isSameAs(cb->dist)) {
                            fc.distMatch = false;
                        } else if (!ca->customSeq.empty() != !cb->customSeq.empty()) {
                            fc.distMatch = false;
                        }
                    }
                }
                rep.faces.push_back(fc);
                if (!fc.countMatch || !fc.distMatch) rep.nMismatch++;
                // 高亮集合
                for (int d = 0; d < 2; ++d) {
                    auto add = [&](const Block& B, int f, int dl) {
                        const int cid = B.absCurve(faceEdge(f, dl, 0));
                        auto& vec = (fc.countMatch && fc.distMatch) ? rep.matchedCurves
                                                                    : rep.mismatchedCurves;
                        if (std::find(vec.begin(), vec.end(), cid) == vec.end())
                            vec.push_back(cid);
                    };
                    add(*BA, fc.faceA, d);
                    add(*BB, fc.faceB, fc.mapDir[d]);
                }
            }
    }

    // ---- 边耦合：端点重合的不同曲线 ----
    std::map<std::pair<int, int>, std::vector<int>> endMap;
    for (const auto& c : m.curves) {
        if (c.type == CurveType::Polyline || c.p0 < 0) continue;
        auto key = std::minmax(c.p0, c.p1);
        endMap[{key.first, key.second}].push_back(c.id);
    }
    for (auto& kv : endMap) {
        auto& ids = kv.second;
        for (size_t i = 0; i < ids.size(); ++i)
            for (size_t j = i + 1; j < ids.size(); ++j) {
                const Curve* ca = m.curve(ids[i]);
                const Curve* cb = m.curve(ids[j]);
                EdgeCoupling ec;
                ec.curveA = ca->id;
                ec.curveB = cb->id;
                ec.countMatch = ca->nPoints == cb->nPoints;
                ec.distMatch = ec.countMatch && ca->dist.isSameAs(cb->dist) &&
                               (ca->customSeq.empty() == cb->customSeq.empty());
                rep.edges.push_back(ec);
                if (!ec.countMatch || !ec.distMatch) {
                    rep.nMismatch++;
                    for (int cid : {ca->id, cb->id})
                        if (std::find(rep.mismatchedCurves.begin(), rep.mismatchedCurves.end(),
                                      cid) == rep.mismatchedCurves.end())
                            rep.mismatchedCurves.push_back(cid);
                } else {
                    for (int cid : {ca->id, cb->id})
                        if (std::find(rep.matchedCurves.begin(), rep.matchedCurves.end(), cid) ==
                            rep.matchedCurves.end())
                            rep.matchedCurves.push_back(cid);
                }
            }
    }

    // ---- 共享曲线（拓扑共享，天然同点数） ----
    std::map<int, int> usage;
    for (const auto& b : m.blocks)
        if (b.enabled)
            for (int e = 0; e < 12; ++e) usage[b.absCurve(e)]++;
    for (auto& kv : usage)
        if (kv.second >= 2) {
            rep.sharedCurves.push_back(kv.first);
            if (std::find(rep.matchedCurves.begin(), rep.matchedCurves.end(), kv.first) ==
                rep.matchedCurves.end())
                rep.matchedCurves.push_back(kv.first);
        }

    return rep;
}

std::vector<std::string> autoFixCouplings(Model& m) {
    std::vector<std::string> log;
    // 单调递增（只增不减）+ id 重查 + 收敛循环：
    // 每轮先做块内轴一致，再做耦合传播；直到无变化。结束时必然轴一致。
    for (int iter = 0; iter < 8; ++iter) {
        bool changed = false;
        auto sync = [&](int ida, int idb) {
            Curve* ca = m.curve(ida);
            Curve* cb = m.curve(idb);
            if (!ca || !cb || ca->id == cb->id) return;
            if (ca->nPoints == cb->nPoints && ca->dist.isSameAs(cb->dist) &&
                (ca->customSeq.empty() == cb->customSeq.empty()))
                return;
            Curve* master = (ca->nPoints >= cb->nPoints) ? ca : cb;
            Curve* slave = (master == ca) ? cb : ca;
            slave->nPoints = master->nPoints;
            slave->dist = master->dist;
            slave->customSeq = ((int)master->customSeq.size() == master->nPoints)
                                   ? master->customSeq
                                   : std::vector<double>{};
            changed = true;
            log.push_back("耦合传播：C" + std::to_string(slave->id) + " ← C" +
                          std::to_string(master->id) + "（n=" +
                          std::to_string(master->nPoints) + "）");
        };
        // 1) 块内各轴一致（master = 轴上最大 nPoints）
        for (auto& b : m.blocks) {
            if (!b.enabled) continue;
            for (int axis = 0; axis < 3; ++axis) {
                int masterId = -1, maxN = -1;
                for (int e = 0; e < 12; ++e) {
                    if (hex::kEdgeAxis[e] != axis) continue;
                    Curve* c = m.curve(b.absCurve(e));
                    if (!c) continue;
                    if (c->nPoints > maxN) {
                        maxN = c->nPoints;
                        masterId = c->id;
                    }
                }
                if (masterId < 0) continue;
                for (int e = 0; e < 12; ++e) {
                    if (hex::kEdgeAxis[e] != axis) continue;
                    Curve* c = m.curve(b.absCurve(e));
                    if (!c || c->id == masterId) continue;
                    Curve* master = m.curve(masterId);
                    if (c->nPoints != master->nPoints ||
                        !c->dist.isSameAs(master->dist)) {
                        c->nPoints = master->nPoints;
                        c->dist = master->dist;
                        c->customSeq = ((int)master->customSeq.size() == master->nPoints)
                                           ? master->customSeq
                                           : std::vector<double>{};
                        changed = true;
                        log.push_back("块 " + b.name + " 轴 " + ("ijk"[axis]) + "：C" +
                                      std::to_string(c->id) + " ← C" +
                                      std::to_string(masterId) + "（n=" +
                                      std::to_string(master->nPoints) + "）");
                    }
                }
            }
        }
        // 2) 耦合传播（几何边耦合 + 面耦合）
        CouplingReport rep = analyzeCouplings(m);
        for (const auto& ec : rep.edges)
            if (!(ec.countMatch && ec.distMatch)) sync(ec.curveA, ec.curveB);
        for (const auto& fc : rep.faces) {
            if (fc.countMatch && fc.distMatch) continue;
            Block* BA = m.block(fc.blockA);
            Block* BB = m.block(fc.blockB);
            if (!BA || !BB) continue;
            for (int d = 0; d < 2; ++d)
                sync(BA->absCurve(faceEdge(fc.faceA, d, 0)),
                     BB->absCurve(faceEdge(fc.faceB, fc.mapDir[d], 0)));
        }
        if (!changed) break;
    }
    if (log.empty()) log.push_back("无需修复：所有耦合均已匹配");
    return log;
}

}  // namespace meshgen
