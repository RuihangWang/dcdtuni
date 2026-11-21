#pragma once
#include "meshgen/Mesher.hpp"

namespace meshgen {

struct QualityStats {
    long nCells = 0;
    double minSJ = 1.0, avgSJ = 1.0;   // 缩放雅可比（>0 有效，1 最优）
    long nBadSJ = 0;                    // minSJ < 阈值
    double sjThreshold = 0.25;
    double maxNonOrtho = 0.0, avgNonOrtho = 0.0;  // 非正交角（度）
};

// 计算单元质量（并行）：填充 mesh.cellQuality / mesh.cellCenters，返回统计
QualityStats computeQuality(MeshResult& mesh);

}  // namespace meshgen
