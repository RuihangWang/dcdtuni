#include "meshgen/Block.hpp"

namespace meshgen {
namespace hex {

const FaceInfo& faceInfo(int f) {
    static FaceInfo infos[6];
    static bool init = false;
    if (!init) {
        for (int fi = 0; fi < 6; ++fi) {
            FaceInfo& I = infos[fi];
            for (int c = 0; c < 4; ++c) I.corners[c] = kFaces[fi][c];
            const auto& c0 = kVerts[I.corners[0]];
            const auto& c1 = kVerts[I.corners[1]];
            const auto& c2 = kVerts[I.corners[2]];
            // u: 角点0→角点1
            for (int a = 0; a < 3; ++a)
                if (c1[a] != c0[a]) { I.axisU = a; I.signU = c1[a] - c0[a]; }
            // v: 角点1→角点2
            for (int a = 0; a < 3; ++a)
                if (c2[a] != c1[a]) { I.axisV = a; I.signV = c2[a] - c1[a]; }
            I.u0 = c0[I.axisU];
            I.v0 = c0[I.axisV];
            I.wAxis = kFaceNormalAxis[fi];
            I.wLvl = kFaceLow[fi] ? 0 : 1;
        }
        init = true;
    }
    return infos[f];
}

int edgeByLevels(int axis, int lvlB, int lvlC) {
    // 轴编号：axis 自身；其余两轴按 (axis+1)%3、(axis+2)%3 顺序对应 lvlB、lvlC
    const int b = (axis + 1) % 3, c = (axis + 2) % 3;
    for (int e = 0; e < 12; ++e) {
        if (kEdgeAxis[e] != axis) continue;
        const auto& va = kVerts[kEdges[e][0]];
        if (va[b] == lvlB && va[c] == lvlC) return e;
    }
    return -1;
}

}  // namespace hex
}  // namespace meshgen
