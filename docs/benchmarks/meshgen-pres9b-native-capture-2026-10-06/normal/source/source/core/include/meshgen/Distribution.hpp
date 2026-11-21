#pragma once
#include <string>
#include <vector>

namespace meshgen {

// 布点律（节点分布）类型。
// p1 含义：
//   Geometric : 相邻网格单元尺寸之比 r（拉伸率）。r>1 起点加密，r<1 终点加密，r=1 均匀。
//   Tanh      : 单端双曲正切聚拢强度 a（>=0，越大起点越密）。
//   TanhSym   : 两端对称双曲正切聚拢强度 a。
//   ExpSym    : 两端对称指数聚拢强度 a。
enum class DistType { Uniform, Geometric, Tanh, TanhSym, ExpSym };

struct Distribution {
    DistType type = DistType::Uniform;
    double p1 = 1.0;
    double p2 = 0.0;  // 预留

    static const char* name(DistType t);
    static DistType fromName(const std::string& s, bool* ok = nullptr);
    std::string describe() const;
    bool isSameAs(const Distribution& o, double tol = 1e-9) const;
};

// 归一化 [0,1] 节点序列（含两端），count 为总点数（>=2）。
std::vector<double> distributionSequence(const Distribution& d, int count);

// 由序列计算总体拉伸率（末段长度 / 首段长度），用于 blockMeshDict simpleGrading 近似。
double sequenceExpansionRatio(const std::vector<double>& s);

}  // namespace meshgen
