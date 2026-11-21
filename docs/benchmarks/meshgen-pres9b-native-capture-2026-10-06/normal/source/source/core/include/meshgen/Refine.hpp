#pragma once
#include <string>
#include <vector>

#include "meshgen/Model.hpp"

namespace meshgen {

struct RefineResult {
    int curvesModified = 0;
    int rounds = 0;
    std::vector<std::string> log;
};

// 魔法笔：根据 refineZones 对所有曲线做自适应布点（间距推进法），
// 并在耦合边之间传播点数与序列，保证界面一致。
RefineResult applyRefinement(Model& m);

// 清空所有曲线的自适应序列（恢复纯解析布点）
int clearRefinement(Model& m);

}  // namespace meshgen
