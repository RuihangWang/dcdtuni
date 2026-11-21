#pragma once
#include <string>

#include "meshgen/Mesher.hpp"
#include "meshgen/Model.hpp"

namespace meshgen {

// 写 OpenFOAM constant/polyMesh：points / faces / owner / neighbour / boundary
bool writeOpenFoamPolyMesh(const MeshResult& mesh, const std::string& dir, std::string& err);

// 写 blockMeshDict（simpleGrading 近似；arc 边保留，折线边降级为直线并给出警告）
bool writeBlockMeshDict(const Model& m, const std::string& path, std::string& err);

// 由补丁名推断 OpenFOAM 边界类型
std::string foamPatchType(const std::string& name);

}  // namespace meshgen
