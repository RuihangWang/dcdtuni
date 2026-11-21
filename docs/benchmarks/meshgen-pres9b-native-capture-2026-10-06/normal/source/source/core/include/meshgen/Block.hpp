#pragma once
#include <array>
#include <string>

#include "meshgen/Vec.hpp"

namespace meshgen {

// 六面体标准拓扑（与 OpenFOAM blockMesh 顶点顺序一致）：
//   0:(0,0,0) 1:(1,0,0) 2:(1,1,0) 3:(0,1,0)
//   4:(0,0,1) 5:(1,0,1) 6:(1,1,1) 7:(0,1,1)
namespace hex {

constexpr int kVerts[8][3] = {{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0},
                              {0, 0, 1}, {1, 0, 1}, {1, 1, 1}, {0, 1, 1}};

// 12 条边：{顶点a, 顶点b}（正向 a→b）
constexpr int kEdges[12][2] = {{0, 1}, {3, 2}, {7, 6}, {4, 5},   // i 向（轴0）
                               {0, 3}, {1, 2}, {5, 6}, {4, 7},   // j 向（轴1）
                               {0, 4}, {1, 5}, {2, 6}, {3, 7}};  // k 向（轴2）

constexpr int kEdgeAxis[12] = {0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2};

// 6 个面（外法向，右手法则），角点顺序 (u0v0, u1v0, u1v1, u0v1)
constexpr int kFaces[6][4] = {{0, 3, 2, 1},  // f0 底面 -z（k=0）
                              {4, 5, 6, 7},  // f1 顶面 +z（k=1）
                              {0, 1, 5, 4},  // f2 前面 -y（j=0）
                              {2, 3, 7, 6},  // f3 后面 +y（j=1）
                              {3, 0, 4, 7},  // f4 左面 -x（i=0）
                              {1, 2, 6, 5}}; // f5 右面 +x（i=1）

constexpr int kFaceNormalAxis[6] = {2, 2, 1, 1, 0, 0};
constexpr int kFaceLow[6] = {1, 0, 1, 0, 1, 0};  // 1: 位于低侧（坐标0）

inline const char* faceName(int f) {
    static const char* names[6] = {"-z", "+z", "-y", "+y", "-x", "+x"};
    return names[f];
}

// 面局部坐标系：角点0→角点1 为 u 方向，角点1→角点2 为 v 方向
struct FaceInfo {
    int corners[4];
    int axisU = 0, axisV = 1;
    int signU = 1, signV = 1;  // 相对块轴正方向
    int u0 = 0, v0 = 0;        // 角点0在 (axisU, axisV) 上的坐标层级
    int wAxis = 2, wLvl = 0;   // 法向轴及其层级
};

const FaceInfo& faceInfo(int f);

// 在边上定位：返回沿轴 axis、另外两轴层级为 lvlB/lvlC 的边编号；无则 -1。
int edgeByLevels(int axis, int lvlB, int lvlC);

// 通用定位：沿 axis，且轴 axB 在层级 lvlB、轴 axC 在层级 lvlC 的边；无则 -1。
inline int edgeWithFixedLevels(int axis, int axB, int lvlB, int axC, int lvlC) {
    for (int e = 0; e < 12; ++e) {
        if (kEdgeAxis[e] != axis) continue;
        const auto& v = kVerts[kEdges[e][0]];
        if (v[axB] == lvlB && v[axC] == lvlC) return e;
    }
    return -1;
}

// 角点质量评估用的邻接顶点（右手序），使 (pa-p)·((pb-p)×(pc-p)) > 0
constexpr int kCornerNb[8][3] = {{4, 1, 3}, {5, 2, 0}, {6, 3, 1}, {7, 0, 2},
                                 {7, 5, 0}, {6, 1, 4}, {7, 2, 5}, {6, 4, 3}};

}  // namespace hex

struct Block {
    int id = 0;
    std::string name;
    bool enabled = true;
    std::array<int, 8> verts{};    // 点 id（hex 顺序）
    std::array<int, 12> curves{};  // 带符号曲线 id（负号 = 与 hex 边正向相反）
    std::array<std::string, 6> patches{"wall", "wall", "wall", "wall", "wall", "wall"};

    int absCurve(int e) const { return curves[e] < 0 ? -curves[e] : curves[e]; }
    bool curveReversed(int e) const { return curves[e] < 0; }
};

}  // namespace meshgen
