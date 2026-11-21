"""Tests for _solid_bounding_boxes: AABB from actual STL vertex data."""
import pytest
import textwrap
from pathlib import Path
from backend.aircooling.runner.stlgen_runner import _solid_bounding_boxes


@pytest.fixture
def tmp_stl(tmp_path):
    """Create a minimal ASCII STL with two solids at known positions."""
    stl_content = textwrap.dedent("""\
        solid Rack_A_frame
          facet normal 0 0 -1
            outer loop
              vertex 0.601 1.0 3.0
              vertex 1.601 1.0 3.0
              vertex 1.601 2.958 3.0
            endloop
          endfacet
          facet normal 0 0 1
            outer loop
              vertex 0.601 1.0 3.6
              vertex 1.601 2.958 3.6
              vertex 1.601 1.0 3.6
            endloop
          endfacet
        endsolid Rack_A_frame
        solid Rack_B_frame
          facet normal 0 0 -1
            outer loop
              vertex 2.802 1.0 2.4
              vertex 3.802 1.0 2.4
              vertex 3.802 2.958 2.4
            endloop
          endfacet
          facet normal 0 0 1
            outer loop
              vertex 2.802 1.0 3.0
              vertex 3.802 2.958 3.0
              vertex 3.802 1.0 3.0
            endloop
          endfacet
        endsolid Rack_B_frame
    """)
    stl_path = tmp_path / "merged_rack_frame.stl"
    stl_path.write_text(stl_content)
    return stl_path


class TestSolidBoundingBoxes:
    def test_parses_two_solids(self, tmp_stl):
        """Two solids → two bounding boxes."""
        bboxes = _solid_bounding_boxes(tmp_stl)
        assert len(bboxes) == 2
        assert "Rack_A_frame" in bboxes
        assert "Rack_B_frame" in bboxes

    def test_rack_a_bbox(self, tmp_stl):
        """Rack A: extends from (0.601,1.0,3.0) to (1.601,2.958,3.6)."""
        bboxes = _solid_bounding_boxes(tmp_stl)
        mn, mx = bboxes["Rack_A_frame"]
        assert mn == pytest.approx([0.601, 1.0, 3.0])
        assert mx == pytest.approx([1.601, 2.958, 3.6])

    def test_rack_b_bbox(self, tmp_stl):
        """Rack B: extends from (2.802,1.0,2.4) to (3.802,2.958,3.0)."""
        bboxes = _solid_bounding_boxes(tmp_stl)
        mn, mx = bboxes["Rack_B_frame"]
        assert mn == pytest.approx([2.802, 1.0, 2.4])
        assert mx == pytest.approx([3.802, 2.958, 3.0])

    def test_empty_solid_skipped(self, tmp_path):
        """Solid with no triangles is omitted from result."""
        stl_content = textwrap.dedent("""\
            solid EmptyFrame
            endsolid EmptyFrame
        """)
        stl_path = tmp_path / "empty.stl"
        stl_path.write_text(stl_content)
        bboxes = _solid_bounding_boxes(stl_path)
        assert "EmptyFrame" not in bboxes

    def test_nonexistent_file_raises(self, tmp_path):
        """Missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            _solid_bounding_boxes(tmp_path / "no_such.stl")
