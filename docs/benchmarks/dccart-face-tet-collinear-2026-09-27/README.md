# dcCart face-tet 共线节点 RED

2026-09-27 在 40901 的指定自有仓库内，用 OpenFOAM v2412 和 CPU0、1 线程、nice 10、GPU 不可见，重新运行 [原生生成器和断言脚本](../../../src/backend/tests/native/dccart_face_tet_collinear/README.md)。所有生成案例和中间文件写在仓库内部 `.codex-tmp/residual-mesh-defects-2026-09-27/face-tet-run-2/`。

[8 点共线共享面的终检](collinear.checkMesh.txt)报告 7 个坏 face-tet，包含内部共享面 0；[四角面对照终检](plain.checkMesh.txt)报告 `Face tets OK`。两例的单元体积相同，各有 2 个低 determinant 单元，属于仅两单元、单一内部邻居的共同背景。`Allrun` 在全新仓库内 scratch 中通过了这些文本、坏面集合和体积断言。此测试固定缺陷机制，未修改 mesher，也不表示 Shinaide 风冷网格通过。
