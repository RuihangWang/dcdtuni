#pragma once
#include <string>
#include <vector>

#include "meshgen/Model.hpp"

namespace meshgen {

// 面耦合：两个块的某个面共享同一组 4 个角点
struct FaceCoupling {
    int blockA = 0, faceA = 0, blockB = 0, faceB = 0;
    int nA[2] = {0, 0};    // A 面沿其局部 u、v 方向的点数
    int nB[2] = {0, 0};    // B 面沿其局部 u、v 方向的点数
    int mapDir[2] = {0, 1}; // A 的局部方向 d 对应 B 的局部方向 mapDir[d]
    int mapSign[2] = {1, 1};
    bool countMatch = false;  // 同点数
    bool distMatch = false;   // 布点律也一致
};

// 边耦合：端点重合的不同曲线（几何耦合，非拓扑共享）
struct EdgeCoupling {
    int curveA = 0, curveB = 0;
    bool countMatch = false;
    bool distMatch = false;
};

struct CouplingReport {
    std::vector<FaceCoupling> faces;
    std::vector<EdgeCoupling> edges;      // 几何耦合边对（可能失配）
    std::vector<int> sharedCurves;        // 被多个块边共享的曲线（天然同点数）
    std::vector<int> matchedCurves;       // 处于“已匹配耦合”的曲线（绿色高亮）
    std::vector<int> mismatchedCurves;    // 失配曲线（红色高亮）
    int nMismatch = 0;
};

CouplingReport analyzeCouplings(const Model& m);

// 自动识别并修复失配：传播点数/布点律（含块内各轴一致性），返回操作日志
std::vector<std::string> autoFixCouplings(Model& m);

}  // namespace meshgen
