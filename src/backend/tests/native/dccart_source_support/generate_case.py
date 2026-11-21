"""Generate a tiny two-sheet dcCart source-support regression case."""
from __future__ import annotations

import argparse
from pathlib import Path


def rectangle(path: Path, name: str, corners: tuple[tuple[float, float, float], ...]) -> None:
    triangles = ((0, 1, 2), (0, 2, 3))
    lines = [f"solid {name}"]
    for a, b, c in triangles:
        lines.extend(("facet normal 0 0 0", "outer loop"))
        for i in (a, b, c):
            lines.append("vertex " + " ".join(f"{x:.9f}" for x in corners[i]))
        lines.extend(("endloop", "endfacet"))
    lines.append(f"endsolid {name}")
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", type=Path)
    parser.add_argument("--control", action="store_true",
                        help="Keep the same sheets away from grid planes")
    args = parser.parse_args()
    case = args.case
    system = case / "system"
    surfaces = case / "constant" / "triSurface"
    system.mkdir(parents=True, exist_ok=True)
    surfaces.mkdir(parents=True, exist_ok=True)
    rectangle(surfaces / "wall.stl", "wall", (
        (0.100000, 0.100000, 0.100000),
        (0.100000, 0.180000, 0.100000),
        (0.100000, 0.180000, 0.180000),
        (0.100000, 0.100000, 0.180000),
    ))
    rectangle(surfaces / "floor.stl", "floor", (
        (0.100001, 0.100000, 0.101000),
        (0.180000, 0.100000, 0.101000),
        (0.180000, 0.100000, 0.180000),
        (0.100001, 0.100000, 0.180000),
    ))
    (system / "controlDict").write_text("""FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application dcCartMesh; startFrom startTime; startTime 0; stopAt endTime; endTime 0;
deltaT 1; writeControl timeStep; writeInterval 1; writeFormat binary;
""")
    background = ("x (0 0.085 0.125 0.2); y (0 0.085 0.125 0.2); "
                  "z (0 0.085 0.125 0.2);") if args.control else (
                  "x (0 0.102330 0.2); y (0 0.080357 0.2); "
                  "z (0 0.097610 0.2);")
    (system / "dcCartMeshDict").write_text("""FoamFile { version 2.0; format ascii; class dictionary; object dcCartMeshDict; }
maxLevel 3; maxGlobalCells 100000; timeLimitSeconds 60;
background { """ + background + """ }
cutCells true; cutBaffles true; splitBaffles true;
wallPairZones (wall floor);
weldTolerance 0.0012; firstLayerMinFraction 0.3; firstLayerMax 0.005;
""")
    (system / "snappyHexMeshDict").write_text("""FoamFile { version 2.0; format ascii; class dictionary; object snappyHexMeshDict; }
geometry
{
    wall.stl { type triSurfaceMesh; name wall; }
    floor.stl { type triSurfaceMesh; name floor; }
}
castellatedMeshControls
{
    refinementSurfaces
    {
        wall { level (2 2); faceType internal; faceZone wall; }
        floor { level (2 2); faceType internal; faceZone floor; }
    }
    refinementRegions {}
    locationInMesh (0.04 0.04 0.04);
}
""")


if __name__ == "__main__":
    main()
