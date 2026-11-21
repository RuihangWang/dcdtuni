"""A pipe carrier region is refined until its cells resolve the tube diameter.

At the box level a 100 mm pipe sat inside a single 125 mm cell: the cut made
cells with a tunnel through them (Shinaide_liquid: 41 wrongly oriented faces,
6 open cells, all on the ring pipes). Three cells across the diameter clear it.
"""
import math
from pathlib import Path

from backend.aircooling.runner.stlgen_runner import _tube_refinement_levels


def _tube(name: str, radius: float, length: float, sides: int = 16) -> str:
    ring = [(radius * math.cos(2 * math.pi * j / sides), radius * math.sin(2 * math.pi * j / sides))
            for j in range(sides)]
    facets = []

    def tri(a, b, c):
        facets.append("  facet normal 0 0 0\n    outer loop\n"
                      + "".join(f"      vertex {p[0]:.9f} {p[1]:.9f} {p[2]:.9f}\n" for p in (a, b, c))
                      + "    endloop\n  endfacet\n")

    for j in range(sides):
        (y0, z0), (y1, z1) = ring[j], ring[(j + 1) % sides]
        a, b, c, d = (0, y0, z0), (0, y1, z1), (length, y0, z0), (length, y1, z1)
        tri(a, c, b); tri(b, c, d)
        tri((0, 0, 0), a, b); tri((length, 0, 0), d, c)
    return f"solid {name}\n" + "".join(facets) + f"endsolid {name}\n"


def test_level_resolves_three_cells_across_each_tube(tmp_path: Path):
    stl = tmp_path / "merged_liquid_transport.stl"
    stl.write_text(_tube("ring", 0.05, 8.0) + _tube("hose", 0.0125, 1.0))
    levels = _tube_refinement_levels(stl, base_size=0.25)
    # 0.25 / 2**3 = 31 mm <= 100 mm / 3 ; 0.25 / 2**5 = 7.8 mm <= 25 mm / 3
    assert levels == {"ring": 3, "hose": 5}


def test_unreadable_or_empty_carrier_gives_no_levels(tmp_path: Path):
    assert _tube_refinement_levels(tmp_path / "missing.stl", base_size=0.25) == {}
