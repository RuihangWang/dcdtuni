#include "meshgen/Model.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <tuple>

#include "meshgen/Mesher.hpp"

using json = nlohmann::json;

namespace meshgen {

// ============================== Curve ==============================

Vec3 Curve::endpointPos(const Model& m, bool end) const {
    if (type == CurveType::Polyline && pts.size() >= 2)
        return end ? pts.back() : pts.front();
    const GeoPoint* p = m.point(end ? p1 : p0);
    return p ? p->pos : Vec3{};
}

Vec3 Curve::at(const Model& m, double t) const {
    t = std::clamp(t, 0.0, 1.0);
    switch (type) {
        case CurveType::Line: {
            return lerp(endpointPos(m, false), endpointPos(m, true), t);
        }
        case CurveType::Arc: {
            const Vec3 C = center;
            const Vec3 P0 = endpointPos(m, false), P1 = endpointPos(m, true);
            const Vec3 v0 = P0 - C, v1 = P1 - C;
            Vec3 n = normalHint.norm() > 1e-30 ? normalHint.normalized() : Vec3{};
            if (n.norm2() < 0.5) {
                Vec3 c = v0.cross(v1);
                n = c.norm() > 1e-30 ? c.normalized() : Vec3{0, 0, 1};
            }
            const double ang =
                std::atan2(n.dot(v0.cross(v1)), v0.dot(v1));
            double theta = ang <= 1e-12 ? ang + 2.0 * M_PI : ang;
            const double a = theta * t;
            // Rodrigues 旋转（v0 ⊥ n）
            const double ca = std::cos(a), sa = std::sin(a);
            const Vec3 v = v0 * ca + n.cross(v0) * sa + n * (n.dot(v0)) * (1.0 - ca);
            return C + v;
        }
        case CurveType::Polyline: {
            const int N = (int)pts.size();
            if (N < 2) return pts.empty() ? Vec3{} : pts[0];
            const double f = t * double(N - 1);  // 顶点索引参数化
            int i = (int)std::floor(f);
            if (i >= N - 1) return pts[N - 1];
            return lerp(pts[i], pts[i + 1], f - double(i));
        }
    }
    return {};
}

Vec3 Curve::tangent(const Model& m, double t) const {
    const double eps = 1e-5;
    const Vec3 a = at(m, std::max(0.0, t - eps));
    const Vec3 b = at(m, std::min(1.0, t + eps));
    const Vec3 d = b - a;
    return d.norm() > 1e-30 ? d.normalized() : Vec3{1, 0, 0};
}

double Curve::length(const Model& m) const {
    if (type == CurveType::Polyline) {
        double L = 0;
        for (size_t i = 1; i < pts.size(); ++i) L += (pts[i] - pts[i - 1]).norm();
        return L;
    }
    const int S = 24;
    double L = 0;
    Vec3 prev = at(m, 0.0);
    for (int i = 1; i <= S; ++i) {
        const Vec3 p = at(m, double(i) / S);
        L += (p - prev).norm();
        prev = p;
    }
    return L;
}

// ============================== Model 基础 ==============================

GeoPoint* Model::point(int id) {
    for (auto& p : points)
        if (p.id == id) return &p;
    return nullptr;
}
const GeoPoint* Model::point(int id) const {
    return const_cast<Model*>(this)->point(id);
}
Curve* Model::curve(int id) {
    if (id < 0) id = -id;
    for (auto& c : curves)
        if (c.id == id) return &c;
    return nullptr;
}
const Curve* Model::curve(int id) const { return const_cast<Model*>(this)->curve(id); }
Block* Model::block(int id) {
    for (auto& b : blocks)
        if (b.id == id) return &b;
    return nullptr;
}
const Block* Model::block(int id) const { return const_cast<Model*>(this)->block(id); }

int Model::addPoint(const Vec3& p, const std::string& name) {
    GeoPoint g;
    g.id = nextPointId++;
    g.name = name.empty() ? ("P" + std::to_string(g.id)) : name;
    g.pos = p;
    points.push_back(g);
    return g.id;
}

int Model::addLine(int p0, int p1, int nPoints, const Distribution& dist) {
    Curve c;
    c.id = nextCurveId++;
    c.name = "L" + std::to_string(c.id);
    c.type = CurveType::Line;
    c.p0 = p0;
    c.p1 = p1;
    c.nPoints = std::max(2, nPoints);
    c.dist = dist;
    curves.push_back(std::move(c));
    return curves.back().id;
}

int Model::addArc(int p0, int p1, const Vec3& center, int nPoints, const Distribution& dist,
                  const Vec3& normalHint) {
    Curve c;
    c.id = nextCurveId++;
    c.name = "A" + std::to_string(c.id);
    c.type = CurveType::Arc;
    c.p0 = p0;
    c.p1 = p1;
    c.center = center;
    c.normalHint = normalHint;
    c.nPoints = std::max(2, nPoints);
    c.dist = dist;
    curves.push_back(std::move(c));
    return curves.back().id;
}

int Model::addPolyline(std::vector<Vec3> pts, int nPoints, const Distribution& dist) {
    Curve c;
    c.id = nextCurveId++;
    c.name = "PL" + std::to_string(c.id);
    c.type = CurveType::Polyline;
    c.pts = std::move(pts);
    c.nPoints = std::max(2, nPoints);
    c.dist = dist;
    curves.push_back(std::move(c));
    return curves.back().id;
}

int Model::findCurve(int pa, int pb) const {
    for (const auto& c : curves) {
        if (c.type == CurveType::Polyline) continue;
        if ((c.p0 == pa && c.p1 == pb) || (c.p0 == pb && c.p1 == pa)) return c.id;
    }
    return 0;
}

int Model::ensureCurve(int pa, int pb, int nPoints, const Distribution& dist) {
    const int id = findCurve(pa, pb);
    if (id) {
        const Curve* c = curve(id);
        return (c->p0 == pa) ? id : -id;
    }
    return addLine(pa, pb, nPoints, dist);
}

int Model::addBlock(const std::array<int, 8>& verts,
                    const std::array<std::string, 6>& patches, int defaultN) {
    Block b;
    b.id = nextBlockId++;
    b.name = "B" + std::to_string(b.id);
    b.verts = verts;
    for (int e = 0; e < 12; ++e) {
        const int va = hex::kEdges[e][0], vb = hex::kEdges[e][1];
        b.curves[e] = ensureCurve(verts[va], verts[vb], defaultN);
    }
    bool anyPatch = false;
    for (const auto& s : patches)
        if (!s.empty()) anyPatch = true;
    if (anyPatch) b.patches = patches;
    blocks.push_back(std::move(b));
    return blocks.back().id;
}

// ============================== 切分 ==============================

std::vector<int> Model::splitBlock(int blockId, int dir, int n,
                                   const std::vector<double>& ratios) {
    std::vector<int> result;
    Block* b0 = block(blockId);
    if (!b0 || dir < 0 || dir > 2 || n < 2) return result;

    std::vector<double> ts;
    ts.push_back(0.0);
    if (ratios.empty()) {
        for (int i = 1; i < n; ++i) ts.push_back(double(i) / n);
    } else {
        if ((int)ratios.size() != n - 1) return result;
        for (double r : ratios) {
            if (r <= ts.back() || r >= 1.0) return result;
            ts.push_back(r);
        }
    }
    ts.push_back(1.0);

    BlockEval ev;
    std::string err;
    if (!ev.build(*this, *b0, &err)) return result;

    Block orig = *b0;  // 快照

    // 切平面上的横向曲线：cutPlanes[cutIdx][axis a≠dir][thirdAxisLevel] = curveId（+a 方向采样）
    const int a1 = (dir + 1) % 3, a2 = (dir + 2) % 3;
    std::vector<std::map<std::pair<int, int>, int>> cutCurves(ts.size());
    for (int ci = 1; ci < (int)ts.size() - 1; ++ci) {
        const double tc = ts[ci];
        for (int a : {a1, a2}) {
            const int other = 3 - dir - a;  // 第三轴
            for (int lvl = 0; lvl <= 1; ++lvl) {
                std::vector<Vec3> poly;
                const int nA = ev.n(a);
                for (int i = 0; i < nA; ++i) {
                    double p[3] = {0, 0, 0};
                    p[dir] = tc;
                    p[a] = ev.seq(a)[i];
                    p[other] = double(lvl);
                    poly.push_back(ev.atParam(p[0], p[1], p[2]));
                }
                const int cid = addPolyline(std::move(poly), nA, Distribution{});
                curve(cid)->name = "split" + std::to_string(cid);
                cutCurves[ci][{a, lvl}] = cid;
            }
        }
    }

    // 沿 dir 轴的子段曲线：segEdgeCurves[seg][edgeIdx]（仅 axis==dir 的边需要新曲线）
    auto makeDirSubCurve = [&](int seg, int e) -> int {
        const double tA = ts[seg], tB = ts[seg + 1];
        if (tA <= 1e-15 && tB >= 1.0 - 1e-15) return orig.curves[e] < 0 ? -orig.curves[e]
                                                                        : orig.curves[e];
        // 收集 [tA, tB] 内的全局序列点
        std::vector<double> tv;
        tv.push_back(tA);
        for (double s : ev.seq(dir))
            if (s > tA + 1e-12 && s < tB - 1e-12) tv.push_back(s);
        tv.push_back(tB);
        std::vector<Vec3> poly;
        poly.reserve(tv.size());
        for (double t : tv) poly.push_back(ev.edgeSample(e, t));
        const int npts = (int)poly.size();
        const int cid = addPolyline(std::move(poly), npts, Distribution{});
        curve(cid)->name = "split" + std::to_string(cid);
        return cid;
    };

    // 切平面角点：相邻段共享同一批点 id（耦合检测依赖点 id）
    std::vector<std::map<int, int>> cutCorners(ts.size());
    int lowPartner[8];
    for (int v = 0; v < 8; ++v) {
        lowPartner[v] = v;
        if (hex::kVerts[v][dir] == 1)
            for (int u = 0; u < 8; ++u) {
                bool same = true;
                for (int a = 0; a < 3; ++a)
                    if (a != dir && hex::kVerts[u][a] != hex::kVerts[v][a]) same = false;
                if (same && hex::kVerts[u][dir] == 0) lowPartner[v] = u;
            }
    }
    for (int ci = 1; ci < (int)ts.size() - 1; ++ci) {
        for (int v = 0; v < 8; ++v) {
            if (hex::kVerts[v][dir] != 0) continue;
            double p[3];
            for (int a = 0; a < 3; ++a)
                p[a] = (a == dir) ? ts[ci] : double(hex::kVerts[v][a]);
            cutCorners[ci][v] = addPoint(ev.atParam(p[0], p[1], p[2]),
                                         orig.name + "_c" + std::to_string(ci) + "_v" +
                                             std::to_string(v));
        }
    }

    for (int seg = 0; seg < n; ++seg) {
        std::array<int, 8> newVerts{};
        // 顶点：外表面复用原始角点，内切面使用共享切平面角点
        for (int v = 0; v < 8; ++v) {
            const int lvl = hex::kVerts[v][dir];
            const int ci = lvl ? seg + 1 : seg;
            if (ci == 0 || ci == (int)ts.size() - 1) {
                newVerts[v] = orig.verts[v];
            } else {
                newVerts[v] = cutCorners[ci][lowPartner[v]];
            }
        }

        std::array<int, 12> newCurves{};
        for (int e = 0; e < 12; ++e) {
            const int axis = hex::kEdgeAxis[e];
            if (axis == dir) {
                const int cid = makeDirSubCurve(seg, e);
                newCurves[e] = orig.curves[e] < 0 ? -cid : cid;
            } else {
                // 垂直于 dir 的边：位于切平面 ci=seg（低端）或 ci=seg+1（高端）
                const int lvlDir = hex::kVerts[hex::kEdges[e][0]][dir];  // 0 低 / 1 高
                const int ci = lvlDir ? seg + 1 : seg;
                if (ci == 0) {
                    // 原块低端面：复用原曲线
                    newCurves[e] = orig.curves[e];
                } else if (ci == (int)ts.size() - 1) {
                    newCurves[e] = orig.curves[e];
                } else {
                    const int other = 3 - dir - axis;
                    const int lvlOther = hex::kVerts[hex::kEdges[e][0]][other];
                    const int cid = cutCurves[ci].at({axis, lvlOther});
                    // 方向：新边 va→vb 沿 axis 的方向
                    const int sgn = hex::kVerts[hex::kEdges[e][1]][axis] -
                                    hex::kVerts[hex::kEdges[e][0]][axis];
                    newCurves[e] = sgn > 0 ? cid : -cid;
                }
            }
        }

        if (seg == 0) {
            // 原块更新为第一段
            b0 = block(blockId);
            b0->verts = newVerts;
            b0->curves = newCurves;
            result.push_back(blockId);
        } else {
            Block nb;
            nb.id = nextBlockId++;
            nb.name = orig.name + "_s" + std::to_string(seg);
            nb.verts = newVerts;
            nb.curves = newCurves;
            nb.patches = orig.patches;
            blocks.push_back(std::move(nb));
            result.push_back(blocks.back().id);
        }
    }
    return result;
}

int Model::extrudeBlock(int blockId, const Vec3& vec) {
    const Block* b = block(blockId);
    if (!b) return 0;
    Block orig = *b;
    std::map<int, int> ptMap;
    for (int v : orig.verts) {
        if (ptMap.count(v)) continue;
        const GeoPoint* p = point(v);
        if (!p) return 0;
        ptMap[v] = addPoint(p->pos + vec, p->name + "_ex");
    }
    Block nb;
    nb.id = nextBlockId++;
    nb.name = orig.name + "_ex";
    nb.patches = orig.patches;
    for (int i = 0; i < 8; ++i) nb.verts[i] = ptMap[orig.verts[i]];
    for (int e = 0; e < 12; ++e) {
        const Curve* c = curve(orig.absCurve(e));
        if (!c) return 0;
        int nc = 0;
        if (c->type == CurveType::Polyline) {
            std::vector<Vec3> tp;
            tp.reserve(c->pts.size());
            for (const auto& q : c->pts) tp.push_back(q + vec);
            nc = addPolyline(std::move(tp), c->nPoints, c->dist);
        } else {
            const int np0 = ptMap.count(c->p0) ? ptMap[c->p0] : c->p0;
            const int np1 = ptMap.count(c->p1) ? ptMap[c->p1] : c->p1;
            if (c->type == CurveType::Line) {
                nc = addLine(np0, np1, c->nPoints, c->dist);
            } else {
                nc = addArc(np0, np1, c->center + vec, c->nPoints, c->dist, c->normalHint);
            }
        }
        nb.curves[e] = orig.curves[e] < 0 ? -nc : nc;
    }
    blocks.push_back(std::move(nb));
    return blocks.back().id;
}

// ============================== 删除 ==============================

bool Model::deleteBlock(int id) {
    for (auto it = blocks.begin(); it != blocks.end(); ++it)
        if (it->id == id) {
            blocks.erase(it);
            return true;
        }
    return false;
}

bool Model::deleteCurve(int id) {
    bool removed = false;
    for (auto it = curves.begin(); it != curves.end();) {
        if (it->id == id) {
            it = curves.erase(it);
            removed = true;
        } else {
            ++it;
        }
    }
    if (removed) {
        // 级联删除引用该曲线的块
        std::vector<int> dead;
        for (const auto& b : blocks)
            for (int e = 0; e < 12; ++e)
                if (b.absCurve(e) == id) dead.push_back(b.id);
        for (int d : dead) deleteBlock(d);
    }
    return removed;
}

bool Model::deletePoint(int id) {
    bool removed = false;
    for (auto it = points.begin(); it != points.end(); ++it)
        if (it->id == id) {
            points.erase(it);
            removed = true;
            break;
        }
    if (!removed) return false;
    std::vector<int> deadCurves;
    for (const auto& c : curves)
        if (c.p0 == id || c.p1 == id) deadCurves.push_back(c.id);
    for (int d : deadCurves) deleteCurve(d);
    return true;
}

// ============================== 其他 ==============================

bool Model::blockCorners(const Block& b, Vec3 out[8]) const {
    for (int i = 0; i < 8; ++i) {
        const GeoPoint* p = point(b.verts[i]);
        if (!p) return false;
        out[i] = p->pos;
    }
    return true;
}

double Model::diag() const {
    Vec3 lo{1e300, 1e300, 1e300}, hi{-1e300, -1e300, -1e300};
    bool any = false;
    auto eat = [&](const Vec3& p) {
        any = true;
        lo.x = std::min(lo.x, p.x); lo.y = std::min(lo.y, p.y); lo.z = std::min(lo.z, p.z);
        hi.x = std::max(hi.x, p.x); hi.y = std::max(hi.y, p.y); hi.z = std::max(hi.z, p.z);
    };
    for (const auto& p : points) eat(p.pos);
    for (const auto& c : curves)
        if (c.type == CurveType::Polyline)
            for (const auto& q : c.pts) eat(q);
    return any ? (hi - lo).norm() : 1.0;
}

std::vector<std::string> Model::validate() const {
    std::vector<std::string> errs;
    for (const auto& c : curves) {
        if (c.type != CurveType::Polyline) {
            if (!point(c.p0) || !point(c.p1))
                errs.push_back("曲线 C" + std::to_string(c.id) + " 端点缺失");
            else if (point(c.p0)->pos.approxEq(point(c.p1)->pos, 1e-15))
                errs.push_back("曲线 C" + std::to_string(c.id) + " 两端点重合");
        } else if (c.pts.size() < 2) {
            errs.push_back("折线 C" + std::to_string(c.id) + " 顶点不足");
        }
    }
    for (const auto& b : blocks) {
        if (!b.enabled) continue;
        for (int v = 0; v < 8; ++v)
            if (!point(b.verts[v])) {
                errs.push_back("块 " + b.name + " 顶点缺失");
                break;
            }
        // 各轴 4 条边点数必须一致
        for (int axis = 0; axis < 3; ++axis) {
            int ref = -1;
            for (int e = 0; e < 12; ++e) {
                if (hex::kEdgeAxis[e] != axis) continue;
                const Curve* c = curve(b.absCurve(e));
                if (!c) {
                    errs.push_back("块 " + b.name + " 边 " + std::to_string(e) + " 曲线缺失");
                    continue;
                }
                if (ref < 0) ref = c->nPoints;
                else if (c->nPoints != ref) {
                    errs.push_back("块 " + b.name + " 轴 " + ("ijk"[axis]) +
                                   " 各边点数不一致（" + std::to_string(ref) + " vs " +
                                   std::to_string(c->nPoints) + "），请运行自动匹配");
                    break;
                }
            }
        }
    }
    return errs;
}

void Model::clear() {
    points.clear();
    curves.clear();
    blocks.clear();
    refineZones.clear();
    nextPointId = nextCurveId = nextBlockId = nextZoneId = 1;
}

// ============================== JSON ==============================

static json distToJson(const Distribution& d) {
    return json{{"type", Distribution::name(d.type)}, {"p1", d.p1}, {"p2", d.p2}};
}
static Distribution distFromJson(const json& j) {
    Distribution d;
    if (j.contains("type")) d.type = Distribution::fromName(j["type"].get<std::string>());
    if (j.contains("p1")) d.p1 = j["p1"].get<double>();
    if (j.contains("p2")) d.p2 = j["p2"].get<double>();
    return d;
}
static Vec3 vec3FromJson(const json& j) {
    return Vec3{j.at(0).get<double>(), j.at(1).get<double>(), j.at(2).get<double>()};
}

std::string Model::toJsonString(int indent) const {
    json j;
    j["version"] = 1;
    j["generator"] = "MeshGenerator";
    j["points"] = json::array();
    for (const auto& p : points)
        j["points"].push_back({{"id", p.id}, {"name", p.name},
                               {"x", p.pos.x}, {"y", p.pos.y}, {"z", p.pos.z}});
    j["curves"] = json::array();
    for (const auto& c : curves) {
        json cj;
        cj["id"] = c.id;
        cj["name"] = c.name;
        cj["type"] = c.type == CurveType::Line     ? "line"
                     : c.type == CurveType::Arc    ? "arc"
                                                   : "polyline";
        if (c.type != CurveType::Polyline) {
            cj["p0"] = c.p0;
            cj["p1"] = c.p1;
        }
        if (c.type == CurveType::Arc) {
            cj["center"] = {c.center.x, c.center.y, c.center.z};
            if (c.normalHint.norm2() > 0)
                cj["normal"] = {c.normalHint.x, c.normalHint.y, c.normalHint.z};
        }
        if (c.type == CurveType::Polyline) {
            cj["pts"] = json::array();
            for (const auto& q : c.pts) cj["pts"].push_back({q.x, q.y, q.z});
        }
        cj["nPoints"] = c.nPoints;
        cj["dist"] = distToJson(c.dist);
        if (!c.customSeq.empty()) cj["customSeq"] = c.customSeq;
        j["curves"].push_back(cj);
    }
    j["refineZones"] = json::array();
    for (const auto& z : refineZones) {
        json zj;
        zj["id"] = z.id;
        zj["name"] = z.name;
        zj["radius"] = z.radius;
        zj["targetSize"] = z.targetSize;
        zj["enabled"] = z.enabled;
        zj["stroke"] = json::array();
        for (const auto& p : z.stroke) zj["stroke"].push_back({p.x, p.y, p.z});
        j["refineZones"].push_back(zj);
    }
    j["blocks"] = json::array();
    for (const auto& b : blocks) {
        json bj;
        bj["id"] = b.id;
        bj["name"] = b.name;
        bj["enabled"] = b.enabled;
        bj["vertices"] = b.verts;
        bj["curves"] = b.curves;
        bj["patches"] = b.patches;
        j["blocks"].push_back(bj);
    }
    return j.dump(indent);
}

bool Model::fromJsonString(const std::string& s, std::string* err) {
    try {
        json j = json::parse(s);
        Model m;
        if (j.contains("points"))
            for (const auto& pj : j["points"]) {
                GeoPoint p;
                p.id = pj.at("id").get<int>();
                p.name = pj.value("name", "P" + std::to_string(p.id));
                p.pos = Vec3{pj.at("x").get<double>(), pj.at("y").get<double>(),
                             pj.at("z").get<double>()};
                m.points.push_back(p);
                m.nextPointId = std::max(m.nextPointId, p.id + 1);
            }
        if (j.contains("curves"))
            for (const auto& cj : j["curves"]) {
                Curve c;
                c.id = cj.at("id").get<int>();
                c.name = cj.value("name", "C" + std::to_string(c.id));
                const std::string t = cj.value("type", "line");
                c.type = t == "arc" ? CurveType::Arc
                         : t == "polyline" ? CurveType::Polyline
                                           : CurveType::Line;
                c.p0 = cj.value("p0", -1);
                c.p1 = cj.value("p1", -1);
                if (cj.contains("center")) c.center = vec3FromJson(cj["center"]);
                if (cj.contains("normal")) c.normalHint = vec3FromJson(cj["normal"]);
                if (cj.contains("pts"))
                    for (const auto& q : cj["pts"]) c.pts.push_back(vec3FromJson(q));
                c.nPoints = cj.value("nPoints", 11);
                if (cj.contains("dist")) c.dist = distFromJson(cj["dist"]);
                if (cj.contains("customSeq"))
                    c.customSeq = cj["customSeq"].get<std::vector<double>>();
                m.curves.push_back(std::move(c));
                m.nextCurveId = std::max(m.nextCurveId, m.curves.back().id + 1);
            }
        if (j.contains("refineZones"))
            for (const auto& zj : j["refineZones"]) {
                RefineZone z;
                z.id = zj.value("id", 0);
                z.name = zj.value("name", "zone" + std::to_string(z.id));
                z.radius = zj.value("radius", 0.1);
                z.targetSize = zj.value("targetSize", 0.01);
                z.enabled = zj.value("enabled", true);
                if (zj.contains("stroke"))
                    for (const auto& p : zj["stroke"]) z.stroke.push_back(vec3FromJson(p));
                m.refineZones.push_back(std::move(z));
                m.nextZoneId = std::max(m.nextZoneId, m.refineZones.back().id + 1);
            }
        if (j.contains("blocks"))
            for (const auto& bj : j["blocks"]) {
                Block b;
                b.id = bj.at("id").get<int>();
                b.name = bj.value("name", "B" + std::to_string(b.id));
                b.enabled = bj.value("enabled", true);
                auto vs = bj.at("vertices").get<std::vector<int>>();
                auto cs = bj.at("curves").get<std::vector<int>>();
                if (vs.size() != 8 || cs.size() != 12)
                    throw std::runtime_error("块顶点/曲线数错误");
                for (int i = 0; i < 8; ++i) b.verts[i] = vs[i];
                for (int i = 0; i < 12; ++i) b.curves[i] = cs[i];
                if (bj.contains("patches")) {
                    auto ps = bj["patches"].get<std::vector<std::string>>();
                    for (int i = 0; i < 6 && i < (int)ps.size(); ++i) b.patches[i] = ps[i];
                }
                m.blocks.push_back(std::move(b));
                m.nextBlockId = std::max(m.nextBlockId, m.blocks.back().id + 1);
            }
        *this = std::move(m);
        return true;
    } catch (const std::exception& e) {
        if (err) *err = e.what();
        return false;
    }
}

bool Model::saveJsonFile(const std::string& path, std::string* err) const {
    std::ofstream f(path);
    if (!f) {
        if (err) *err = "无法写入文件: " + path;
        return false;
    }
    f << toJsonString();
    return true;
}

bool Model::loadJsonFile(const std::string& path, std::string* err) {
    std::ifstream f(path);
    if (!f) {
        if (err) *err = "无法打开文件: " + path;
        return false;
    }
    std::stringstream ss;
    ss << f.rdbuf();
    return fromJsonString(ss.str(), err);
}

}  // namespace meshgen
