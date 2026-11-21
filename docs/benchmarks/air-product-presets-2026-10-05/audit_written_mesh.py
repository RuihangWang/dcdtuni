#!/usr/bin/env python3
"""Read-only OF2412 binary mesh audit: face/cell geometry and centroid ULP parity.
Usage: python audit_written_mesh.py PATH/TO/Mesh 1:1340400 3:1485680 6:1358631 6:1359210
Only reads input files; JSON on stdout may be redirected to a repository receipt.
"""
from pathlib import Path
import sys, re, json
import numpy as np

def read_array(path, dtype, width):
    blob = path.read_bytes()
    match = re.search(rb"\n(\d+)\n\(", blob)
    if not match:
        raise ValueError("Expected OF binary list: " + str(path))
    count = int(match.group(1))
    return blob, np.frombuffer(blob, dtype=dtype, count=count, offset=match.end()), match.end() + count * width

class Mesh:
    def __init__(self, path):
        blob, self.offsets, end = read_array(path / "faces", "<i4", 4)
        match = re.search(rb"\n(\d+)\n\(", blob[end:])
        self.vertices = np.frombuffer(blob, dtype="<i4", count=int(match.group(1)), offset=end + match.end())
        _, self.points, _ = read_array(path / "points", np.dtype(("<f8", 3)), 24)
        _, self.owners, _ = read_array(path / "owner", "<i4", 4)
        _, self.neighbours, _ = read_array(path / "neighbour", "<i4", 4)
        self.patches = []
        for name, body in re.findall(r"(?m)^\s*([^\s{}()]+)\s*\n\s*\{([^{}]+)\}", (path / "boundary").read_text()):
            start = re.search(r"startFace\s+(\d+)", body)
            count = re.search(r"nFaces\s+(\d+)", body)
            kind = re.search(r"type\s+(\w+)", body)
            if start and count and kind:
                self.patches.append((int(start.group(1)), int(count.group(1)), name, kind.group(1)))
        self.geometry = {}
        self.cells = {}

    def patch(self, face):
        for start, count, name, kind in self.patches:
            if start <= face < start + count:
                return {"name": name, "type": kind}
        return None

    def poly(self, face):
        return self.vertices[self.offsets[face]:self.offsets[face + 1]]

    def geo(self, face, of=True):
        key = (face, of)
        if key not in self.geometry:
            points = self.points[self.poly(face)]
            mean = np.zeros(3)
            for point in points:
                mean += point
            mean /= len(points)
            if len(points) == 3:
                area = np.cross(points[1] - points[0], points[2] - points[0]) * .5
                centre = mean
            else:
                normal = np.zeros(3); accum = np.zeros(3); total = 0.
                for i, point in enumerate(points):
                    next_point = points[(i + 1) % len(points)]
                    triangle = np.cross(next_point - point, mean - point)
                    weight = np.linalg.norm(triangle)
                    normal += triangle; total += weight
                    accum += weight * (point + next_point + mean)
                area = normal * .5
                # Different algebraically equal operation sequences expose
                # the exact threshold crossing on the Raised_Floor rectangle.
                centre = ((1. / 3.) * accum / total if of else accum / (3. * total)) if total else mean
            self.geometry[key] = area, centre
        return self.geometry[key]

    def cell(self, cell, of=True):
        key = (cell, of)
        if key not in self.cells:
            owner_faces = np.flatnonzero(self.owners == cell).tolist()
            neighbour_faces = np.flatnonzero(self.neighbours == cell).tolist()
            faces = owner_faces + neighbour_faces
            estimate = np.zeros(3)
            for face in faces:
                estimate += self.geo(face, of)[1]
            estimate /= len(faces)
            centre = np.zeros(3); vol3 = 0.
            for face in faces:
                area, face_centre = self.geo(face, of)
                pyramid3 = np.dot(area, face_centre - estimate) if face in owner_faces else np.dot(area, estimate - face_centre)
                centre += pyramid3 * (.75 * face_centre + .25 * estimate)
                vol3 += pyramid3
            centre = centre / vol3 if vol3 else estimate
            vertices = np.unique(np.concatenate([self.poly(face) for face in faces]))
            points = self.points[vertices]
            self.cells[key] = faces, centre, vol3 / 3., points.min(axis=0), points.max(axis=0)
        return self.cells[key]

    def skew(self, face, of=True):
        area, centre = self.geo(face, of)
        owner = int(self.owners[face])
        cpf = centre - self.cell(owner, of)[1]
        if face < len(self.neighbours):
            direction = self.cell(int(self.neighbours[face]), of)[1] - self.cell(owner, of)[1]
            factor = .2
        else:
            normal = area / np.linalg.norm(area)
            direction = normal * np.dot(normal, cpf)
            factor = .4
        offset = cpf - np.dot(area, cpf) / np.dot(area, direction) * direction
        magnitude = np.linalg.norm(offset)
        if magnitude == 0:
            return 0.
        distance = factor * np.linalg.norm(direction)
        for vertex in self.poly(face):
            distance = max(distance, abs(np.dot(offset / magnitude, self.points[vertex] - centre)))
        return float(magnitude / distance)

    def summary(self, cell):
        faces, centre, volume, lo, hi = self.cell(cell)
        rows = []
        neighbours = set()
        for face in faces:
            area, fc = self.geo(face)
            own = int(self.owners[face])
            nei = int(self.neighbours[face]) if face < len(self.neighbours) else -1
            other = nei if own == cell else own
            if other >= 0:
                neighbours.add(other)
            outward = area if own == cell else -area
            rows.append({"face": face, "patch": self.patch(face), "neighbour": other,
                "vertex_count": len(self.poly(face)), "area_m2": float(np.linalg.norm(area)),
                "centre": fc.tolist(), "pyramid_volume_m3": float(np.dot(outward, fc - centre) / 3.)})
        return {"cell": cell, "bbox_min": lo.tolist(), "bbox_max": hi.tolist(),
            "extent": (hi - lo).tolist(), "centre": centre.tolist(), "volume_m3": float(volume),
            "neighbours": sorted(neighbours), "faces": rows}

def main():
    case = Path(sys.argv[1]).resolve()
    result = []
    meshes = {}
    for spec in sys.argv[2:]:
        rank, face = map(int, spec.split(":"))
        mesh = meshes.setdefault(rank, Mesh(case / ("processor" + str(rank)) / "constant/polyMesh")) if rank not in meshes else meshes[rank]
        owner = int(mesh.owners[face])
        cell = mesh.summary(owner)
        native_area, native_fc = mesh.geo(face, False)
        of_area, of_fc = mesh.geo(face, True)
        result.append({"rank": rank, "written_face": face, "patch": mesh.patch(face),
            "vertices": mesh.poly(face).tolist(), "points": mesh.points[mesh.poly(face)].tolist(),
            "native_formula_face_centre": native_fc.tolist(), "of_formula_face_centre": of_fc.tolist(),
            "native_formula_cell_centre": mesh.cell(owner, False)[1].tolist(),
            "of_formula_cell_centre": mesh.cell(owner, True)[1].tolist(),
            "native_formula_skew": mesh.skew(face, False), "of_formula_skew": mesh.skew(face, True),
            "owner": cell, "neighbour_cells": [mesh.summary(c) for c in cell["neighbours"]]})
    print(json.dumps({"case": str(case), "audit": result}, indent=2))

if __name__ == "__main__":
    main()
