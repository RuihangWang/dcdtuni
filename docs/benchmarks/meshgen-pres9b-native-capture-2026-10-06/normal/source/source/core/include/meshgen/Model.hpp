#pragma once
#include <array>
#include <string>
#include <vector>

#include "meshgen/Block.hpp"
#include "meshgen/Distribution.hpp"
#include "meshgen/Vec.hpp"

namespace meshgen {

struct GeoPoint {
    int id = 0;
    std::string name;
    Vec3 pos;
};

enum class CurveType { Line, Arc, Polyline };

struct Curve {
    int id = 0;
    std::string name;
    CurveType type = CurveType::Line;
    int p0 = -1, p1 = -1;  // Line/Arc 端点 id；Polyline 可为 -1
    Vec3 center;           // Arc 圆心
    Vec3 normalHint;       // Arc：180° 弧的法向提示（零向量=自动）
    std::vector<Vec3> pts;  // Polyline 顶点（含端点，>=2）
    int nPoints = 11;       // 网格总点数（含端点）
    Distribution dist;
    // 魔法笔自适应序列：非空且长度==nPoints 时优先于 dist（顶点索引参数化）
    std::vector<double> customSeq;

    Vec3 endpointPos(const class Model& m, bool end) const;
    Vec3 at(const Model& m, double t) const;       // t∈[0,1]，p0→p1 方向
    Vec3 tangent(const Model& m, double t) const;
    double length(const Model& m) const;
};

// 魔法笔加密区：沿手绘折线、半径 radius 内将网格间距压到 targetSize
struct RefineZone {
    int id = 0;
    std::string name;
    std::vector<Vec3> stroke;  // 手绘折线（构造平面内）
    double radius = 0.1;       // 影响半径
    double targetSize = 0.01;  // 目标最小网格尺寸
    bool enabled = true;
};

class Model {
public:
    std::vector<GeoPoint> points;
    std::vector<Curve> curves;
    std::vector<Block> blocks;
    std::vector<RefineZone> refineZones;

    int nextPointId = 1, nextCurveId = 1, nextBlockId = 1, nextZoneId = 1;

    // ---------- 编辑 ----------
    int addPoint(const Vec3& p, const std::string& name = "");
    int addLine(int p0, int p1, int nPoints = 11, const Distribution& dist = {});
    int addArc(int p0, int p1, const Vec3& center, int nPoints = 11,
               const Distribution& dist = {}, const Vec3& normalHint = {});
    // 独立折线曲线（不依附点），用于切分面等派生几何
    int addPolyline(std::vector<Vec3> pts, int nPoints, const Distribution& dist = {});

    // 查找连接两点（任意方向）的已有曲线；无则返回 0
    int findCurve(int pa, int pb) const;
    // 确保两点间存在曲线（缺省补直线），返回带符号 id（正 = pa→pb 方向）
    int ensureCurve(int pa, int pb, int nPoints = 11, const Distribution& dist = {});

    // 用 8 个顶点添加块；缺失的边曲线自动以直线补齐
    int addBlock(const std::array<int, 8>& verts,
                 const std::array<std::string, 6>& patches = {}, int defaultN = 11);

    // 沿轴 dir(0=i,1=j,2=k) 把块切成 n 段；ratios 为切点逻辑坐标（可空=均匀）。
    // 原块保留为第一段，返回全部 n 个块的 id。
    std::vector<int> splitBlock(int blockId, int dir, int n,
                                const std::vector<double>& ratios = {});

    // 复制块并整体平移 vec（快速堆叠多块），返回新块 id
    int extrudeBlock(int blockId, const Vec3& vec);

    bool deletePoint(int id);
    bool deleteCurve(int id);
    bool deleteBlock(int id);

    // ---------- 查询 ----------
    GeoPoint* point(int id);
    const GeoPoint* point(int id) const;
    Curve* curve(int id);
    const Curve* curve(int id) const;
    Block* block(int id);
    const Block* block(int id) const;
    int pointCount() const { return (int)points.size(); }
    int curveCount() const { return (int)curves.size(); }
    int blockCount() const { return (int)blocks.size(); }

    // 块顶点坐标（按 hex 顺序）
    bool blockCorners(const Block& b, Vec3 out[8]) const;

    // 包围盒对角线长度
    double diag() const;

    // 校验：返回错误列表（空 = 通过）
    std::vector<std::string> validate() const;

    // ---------- JSON ----------
    std::string toJsonString(int indent = 2) const;
    bool fromJsonString(const std::string& s, std::string* err);
    bool saveJsonFile(const std::string& path, std::string* err) const;
    bool loadJsonFile(const std::string& path, std::string* err);

    void clear();
};

}  // namespace meshgen
