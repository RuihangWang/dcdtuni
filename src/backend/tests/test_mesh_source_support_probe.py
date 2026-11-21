"""Geometry checks for the read-only dcCart source-support diagnostic."""

import math
import unittest

from backend.aircooling.runner.tools.mesh_source_support_probe import projected_support


class ProjectedSupportTests(unittest.TestCase):
    def test_detects_face_beyond_triangle_edge(self):
        triangle = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        result = projected_support((0.8, 0.002, 0.8), triangle)
        self.assertTrue(math.isclose(result.plane_offset_m, 0.002, abs_tol=1e-12))
        self.assertTrue(math.isclose(result.in_plane_gap_m, math.sqrt(0.18), abs_tol=1e-12))

    def test_accepts_point_inside_triangle(self):
        triangle = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        result = projected_support((0.2, 0.002, 0.2), triangle)
        self.assertTrue(math.isclose(result.plane_offset_m, 0.002, abs_tol=1e-12))
        self.assertEqual(result.in_plane_gap_m, 0.0)


if __name__ == "__main__":
    unittest.main()
