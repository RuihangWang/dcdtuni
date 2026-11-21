#pragma once
#include <array>
#include <string>
#include <vector>

#include "meshgen/Model.hpp"

namespace meshgen {

struct MeshResult {
    std::vector<Vec3> points;
    std::vector<std::array<int, 8>> cells;  // hex8，与块顶点顺序一致
    std::vector<std::array<int, 4>> faces;  // 内部面在前、边界面在后；法向 owner→neighbour
    std::vector<int> owner;                 // 与 faces 等长
    std::vector<int> neighbour;             // 边界面为 -1
    std::vector<int> faceBlock;             // 面所属块（边界面）/ owner 侧块
    std::vector<std::string> patchNames;    // 按出现顺序
    std::vector<int> patchStart, patchNFaces;
    std::vector<int> cellBlock;             // 单元所属块 id
    std::vector<double> cellQuality;        // 单元最小缩放雅可比（填充于 Quality）
    std::vector<Vec3> cellCenters;          // 单元中心（填充于 Quality）
    std::vector<unsigned char> cellRefined; // 魔法笔加密区标记
    // 每块结构化网格点（i 最快，尺寸见 blockDims）—— 供可视化直接绘制
    std::vector<std::vector<Vec3>> blockPoints;
    std::vector<std::array<int, 3>> blockDims;
    std::vector<int> blockIds;
    int nInternalFaces = 0;
    double genMs = 0;
    std::vector<std::string> warnings;

    void clear() { *this = MeshResult{}; }
    long cellCount() const { return (long)cells.size(); }
};

// 生成多块结构网格（TFI + 全局装配）。失败返回 false 并填 err。
bool generateMesh(const Model& m, MeshResult& out, std::string& err);

// 单个块的 TFI 求值器（供网格生成与几何切分共用）
class BlockEval {
public:
    // 成功返回 true；失败（如各边点数不一致）返回 false 并填 err
    bool build(const Model& m, const Block& b, std::string* err = nullptr);

    // 各轴点数与归一化序列
    int n(int axis) const { return nPts[axis]; }
    const std::vector<double>& seq(int axis) const { return seqs[axis]; }

    // 网格节点 (i∈[0,n0-1], ...)
    Vec3 gridPoint(int i, int j, int k) const;
    // 连续参数求值 (u,v,w ∈ [0,1])
    Vec3 atParam(double u, double v, double w) const;

    // 边采样（正向，含方向修正），编号见 hex::kEdges
    const std::vector<Vec3>& edgePts(int e) const { return edgeSamples[e]; }
    // 边的布点序列（归一化参数）
    const std::vector<double>& edgeSeq(int e) const { return edgeSeqs[e]; }
    const std::array<Vec3, 8>& cornersArr() const { return corners; }

    Vec3 edgeSample(int e, double t) const;  // 边上连续参数插值（精确曲线求值）

    const Block* block = nullptr;

private:
    Block blockCopy;  // 值副本：模型向量扩容不会使其失效
    std::array<std::vector<Vec3>, 12> edgeSamples;
    std::array<std::vector<double>, 12> edgeSeqs;
    std::array<std::vector<double>, 3> seqs;
    std::array<int, 3> nPts{0, 0, 0};
    std::array<Vec3, 8> corners;
    const Model* model = nullptr;
};

// 直接对块的逻辑坐标 (u,v,w) 求值（用于切分等），要求块边点数一致
Vec3 blockPointAt(const Model& m, const Block& b, double u, double v, double w, bool* ok);

}  // namespace meshgen
