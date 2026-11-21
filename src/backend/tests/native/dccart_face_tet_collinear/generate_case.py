#!/usr/bin/env python3
"""Build a two-cell OpenFOAM v2412 face-tet characterization case.

Both variants have the same 0.08 x 0.08 x 0.005 m geometry.  The collinear
variant keeps x=0.03 and 0.05 m edge points in every incident face, so the
shared eight-vertex face is topologically conformal rather than a face-only
mutation.  The plain variant uses four-vertex faces as a differential control.
"""

from __future__ import annotations

import argparse
from pathlib import Path


X_PLAIN = (0.0, 0.08)
X_COLLINEAR = (0.0, 0.03, 0.05, 0.08)
Y = (0.0, 0.04, 0.08)
Z = (0.0, 0.005)
EXPECTED_CELL_VOLUME_M3 = 1.6e-5


def foam_header(cls: str, obj: str, location: str) -> str:
    return (
        "FoamFile\n{\n"
        "    version 2.0;\n"
        "    format ascii;\n"
        f"    class {cls};\n"
        f'    location "{location}";\n'
        f"    object {obj};\n"
        "}\n\n"
    )


def face_area_vector(face: list[int], points: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    result = [0.0, 0.0, 0.0]
    for a, b in zip(face, face[1:] + face[:1]):
        p, q = points[a], points[b]
        result[0] += p[1] * q[2] - p[2] * q[1]
        result[1] += p[2] * q[0] - p[0] * q[2]
        result[2] += p[0] * q[1] - p[1] * q[0]
    return tuple(v / 2.0 for v in result)


def build_mesh(xs: tuple[float, ...]):
    n = len(xs)
    points = [(x, y, z) for y in Y for z in Z for x in xs]

    def row(y_index: int, z_index: int) -> list[int]:
        return [y_index * 2 * n + z_index * n + x_index for x_index in range(n)]

    def y_face(y_index: int, positive: bool) -> list[int]:
        bottom, top = row(y_index, 0), row(y_index, 1)
        return ([bottom[0]] + top + bottom[:0:-1]) if positive else (bottom + top[::-1])

    def z_face(y_index: int, z_index: int) -> list[int]:
        first, second = row(y_index, z_index), row(y_index + 1, z_index)
        return (first + second[::-1]) if z_index else ([first[0]] + second + first[:0:-1])

    def x_face(y_index: int, x_index: int) -> list[int]:
        b0, t0 = row(y_index, 0)[x_index], row(y_index, 1)[x_index]
        b1, t1 = row(y_index + 1, 0)[x_index], row(y_index + 1, 1)[x_index]
        return [b0, t0, t1, b1] if x_index == 0 else [b0, b1, t1, t0]

    # OpenFOAM requires internal faces before boundary faces.  Face 0 is the
    # shared interface; all other face loops retain the same seam points.
    faces = [
        y_face(1, True),
        y_face(0, False),
        z_face(0, 0),
        z_face(0, 1),
        x_face(0, 0),
        x_face(0, n - 1),
        y_face(2, True),
        z_face(1, 0),
        z_face(1, 1),
        x_face(1, 0),
        x_face(1, n - 1),
    ]
    owner = [0] * 6 + [1] * 5
    neighbour = [1]
    assert len(points) == (24 if n == 4 else 12)
    assert len(faces) == 11 and len(faces[0]) == 2 * n

    # These independent closure and volume checks catch a malformed fixture
    # before checkMesh can conflate it with the face-tet property under test.
    for cell in (0, 1):
        flux = [0.0, 0.0, 0.0]
        volume = 0.0
        for fid, face in enumerate(faces):
            if owner[fid] != cell and (fid >= len(neighbour) or neighbour[fid] != cell):
                continue
            sign = 1 if owner[fid] == cell else -1
            area = face_area_vector(face, points)
            flux = [flux[k] + sign * area[k] for k in range(3)]
            volume += sign * sum(area[k] * points[face[0]][k] for k in range(3)) / 3.0
        assert max(abs(v) for v in flux) < 1e-12, (cell, flux)
        assert abs(volume - EXPECTED_CELL_VOLUME_M3) < 1e-12, (cell, volume)
    return points, faces, owner, neighbour


def write_list(path: Path, cls: str, values, render) -> None:
    body = "".join(render(value) + "\n" for value in values)
    path.write_text(
        foam_header(cls, path.name, "constant/polyMesh")
        + f"{len(values)}\n(\n{body})\n",
        encoding="ascii",
    )


def write_case(output: Path, collinear: bool) -> None:
    output.mkdir(parents=True, exist_ok=False)
    poly_mesh = output / "constant" / "polyMesh"
    system = output / "system"
    poly_mesh.mkdir(parents=True)
    system.mkdir()
    points, faces, owner, neighbour = build_mesh(X_COLLINEAR if collinear else X_PLAIN)
    write_list(poly_mesh / "points", "vectorField", points, lambda p: "(" + " ".join(f"{x:.9g}" for x in p) + ")")
    write_list(poly_mesh / "faces", "faceList", faces, lambda f: str(len(f)) + "(" + " ".join(map(str, f)) + ")")
    write_list(poly_mesh / "owner", "labelList", owner, str)
    write_list(poly_mesh / "neighbour", "labelList", neighbour, str)
    (poly_mesh / "boundary").write_text(
        foam_header("polyBoundaryMesh", "boundary", "constant/polyMesh")
        + "1\n(\n    walls\n    {\n        type wall;\n        nFaces 10;\n        startFace 1;\n    }\n)\n",
        encoding="ascii",
    )
    (system / "controlDict").write_text(
        foam_header("dictionary", "controlDict", "system")
        + "application checkMesh;\nstartFrom startTime;\nstartTime 0;\n"
        "stopAt endTime;\nendTime 1;\ndeltaT 1;\n"
        "writeControl timeStep;\nwriteInterval 1;\n",
        encoding="ascii",
    )
    (system / "fvSchemes").write_text(
        foam_header("dictionary", "fvSchemes", "system")
        + "ddtSchemes { default Euler; }\n"
        "gradSchemes { default Gauss linear; }\n"
        "divSchemes { default none; }\n"
        "laplacianSchemes { default Gauss linear corrected; }\n"
        "interpolationSchemes { default linear; }\n"
        "snGradSchemes { default corrected; }\n",
        encoding="ascii",
    )
    (system / "fvSolution").write_text(
        foam_header("dictionary", "fvSolution", "system") + "solvers {}\n",
        encoding="ascii",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new case directory")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--collinear", action="store_true")
    group.add_argument("--plain", action="store_true")
    args = parser.parse_args()
    write_case(args.output, args.collinear)


if __name__ == "__main__":
    main()
