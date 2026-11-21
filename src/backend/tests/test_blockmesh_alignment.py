"""Tests for blockMesh uniform grid generation.

The algorithm creates a uniform grid where:
1. Room boundaries are exact breakpoints
2. The main segment gets round(length/baseSize) cells
3. 1 padding cell on each side
"""
import math

import pytest


def _import_multiblock():
    from backend.aircooling.runner.stlgen_runner import compute_multiblock_grid
    return compute_multiblock_grid


def _get_breakpoints(segments):
    """Extract sorted breakpoints from segment list."""
    pts = set()
    for seg_min, seg_max, _ in segments:
        pts.add(round(seg_min, 9))
        pts.add(round(seg_max, 9))
    return sorted(pts)


class TestUniformGrid:
    """Room boundaries must be exact breakpoints with uniform cells."""

    ROOM = {"x": (0.0, 10.0), "y": (0.0, 3.4), "z": (-2.0, 5.0)}

    def test_room_boundaries_are_breakpoints(self):
        mb = _import_multiblock()
        result = mb(room_bounds=self.ROOM, base_size=0.34)
        for axis in ("x", "y", "z"):
            breaks = _get_breakpoints(result[axis])
            r_min, r_max = self.ROOM[axis]
            assert r_min in breaks, f"{axis}: room_min {r_min} not a breakpoint"
            assert r_max in breaks, f"{axis}: room_max {r_max} not a breakpoint"

    def test_padding_is_one_cell(self):
        mb = _import_multiblock()
        result = mb(room_bounds=self.ROOM, base_size=0.34)
        for axis in ("x", "y", "z"):
            segs = result[axis]
            assert segs[0][2] == 1, f"{axis}: low padding should be 1 cell"
            assert segs[-1][2] == 1, f"{axis}: high padding should be 1 cell"

    def test_three_segments_per_axis(self):
        """Uniform grid: exactly 3 segments (low pad + main + high pad)."""
        mb = _import_multiblock()
        result = mb(room_bounds=self.ROOM, base_size=0.34)
        for axis in ("x", "y", "z"):
            assert len(result[axis]) == 3, \
                f"{axis}: expected 3 segments (pad+main+pad), got {len(result[axis])}"

    def test_cell_size_near_base_size(self):
        mb = _import_multiblock()
        result = mb(room_bounds=self.ROOM, base_size=0.34)
        for axis in ("x", "y", "z"):
            for seg_min, seg_max, n in result[axis]:
                if n <= 1:
                    continue
                cs = (seg_max - seg_min) / n
                assert 0.15 < cs < 0.55, \
                    f"{axis}: segment [{seg_min:.2f},{seg_max:.2f}] cs={cs:.4f} too far from 0.34"

    def test_rack_configs_ignored(self):
        """rack_configs parameter is accepted but ignored."""
        mb = _import_multiblock()
        rack = {
            "anchor": {"x": 2.07, "y": 0.4, "z": 1.25},
            "size": {"W": 0.6, "D": 1.2, "H": 2.0},
            "orientation": 0,
        }
        result_no_rack = mb(room_bounds=self.ROOM, base_size=0.34)
        result_with_rack = mb(room_bounds=self.ROOM, base_size=0.34, rack_configs=[rack])

        for axis in ("x", "y", "z"):
            segs_nr = result_no_rack[axis]
            segs_wr = result_with_rack[axis]
            assert len(segs_nr) == len(segs_wr), \
                f"{axis}: segment count differs with/without rack_configs"
            for s1, s2 in zip(segs_nr, segs_wr):
                assert s1 == s2, f"{axis}: segments differ: {s1} vs {s2}"

    def test_main_segment_cell_count(self):
        """Main segment cell count = round(length / base_size)."""
        mb = _import_multiblock()
        base = 0.34
        result = mb(room_bounds=self.ROOM, base_size=base)
        for axis in ("x", "y", "z"):
            r_min, r_max = self.ROOM[axis]
            length = r_max - r_min
            expected_n = max(1, round(length / base))
            main_seg = result[axis][1]  # middle segment
            assert main_seg[2] == expected_n, \
                f"{axis}: expected {expected_n} cells, got {main_seg[2]}"

    def test_padding_size_matches_cell_size(self):
        """Padding cell size equals the main segment cell size."""
        mb = _import_multiblock()
        result = mb(room_bounds=self.ROOM, base_size=0.34)
        for axis in ("x", "y", "z"):
            segs = result[axis]
            main_cs = (segs[1][1] - segs[1][0]) / segs[1][2]
            lo_pad = segs[0][1] - segs[0][0]
            hi_pad = segs[2][1] - segs[2][0]
            assert abs(lo_pad - main_cs) < 1e-9, \
                f"{axis}: low pad {lo_pad} != main cs {main_cs}"
            assert abs(hi_pad - main_cs) < 1e-9, \
                f"{axis}: high pad {hi_pad} != main cs {main_cs}"
