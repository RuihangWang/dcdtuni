"""ASCII faces: OpenFOAM writes a face of more than ten points over several lines."""
import numpy as np

from backend.aircooling.runner.polymesh_io import read_all_faces

HEADER = """FoamFile
{
    version     2.0;
    format      ascii;
    class       faceList;
    location    "constant/polyMesh";
    object      faces;
}
"""


def test_multiline_face_is_one_face(tmp_path):
    body = "3\n(\n4(0 1 2 3)\n12\n(\n0\n1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n)\n3(4 5 6)\n)\n"
    (tmp_path / "faces").write_text(HEADER + body)
    offsets, verts = read_all_faces(tmp_path / "faces")
    assert offsets.tolist() == [0, 4, 16, 19]
    assert verts[4:16].tolist() == list(range(12))
