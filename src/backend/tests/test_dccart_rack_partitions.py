"""Zero-thickness rack shelves retain a fluid boundary on both sides."""
from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner


def shelf(name: str, height: float) -> str:
    return f"""solid {name}
facet normal 0 -1 0
outer loop
vertex 0 {height} 0
vertex 1 {height} 0
vertex 0 {height} 1
endloop
endfacet
endsolid {name}
"""


def setup_runner(tmp_path: Path, engine: str = "dcCart"):
    runner = STLGenRunner(tmp_path, tmp_path / "model/p.cfdrun")
    runner.tri_surface_dir.mkdir(parents=True)
    (runner.mesh_dir / "system").mkdir()
    runner._data = {"meshConfig": {"meshEngine": engine}}
    names = ["Rack_u17_partition", "Rack_u23_partition"]
    source = runner.tri_surface_dir / "merged_partition_1U.stl"
    source.write_text(shelf(names[0], .8) + shelf(names[1], 1.06))
    runner._stl_solids = {source.name: names}
    return runner, source, names


def test_dcCart_shelves_become_independent_wall_baffles_without_seed_offsets(tmp_path):
    runner, source, names = setup_runner(tmp_path)
    original = source.read_bytes()
    runner._prepare_dcCart_rack_partitions()
    assert source.read_bytes() == original
    assert source.name not in runner._stl_solids
    for name in names:
        filename = f"baffle_{name}.stl"
        assert runner._stl_solids[filename] == [name]
        assert f"solid {name}\n" in (runner.tri_surface_dir / filename).read_text()
    assert runner._collect_create_baffle_seed_specs() == []
    runner._generate_create_baffles_dict()
    assert not (runner.mesh_dir / "system/createBafflesDict").exists()
    for name in names:
        assert runner._baffle_physics[name]["directWall"] is True


def test_shelf_preparation_can_repeat_from_retained_export(tmp_path):
    runner, source, names = setup_runner(tmp_path)
    runner._prepare_dcCart_rack_partitions()
    expected = {name: (runner.tri_surface_dir / name).read_bytes() for name in runner._stl_solids}
    runner._stl_solids[source.name] = names
    runner._prepare_dcCart_rack_partitions()
    assert {name: (runner.tri_surface_dir / name).read_bytes() for name in runner._stl_solids} == expected


@pytest.mark.parametrize("engine", ["snappy"])
def test_other_engines_keep_their_existing_partition_path(tmp_path, engine):
    runner, source, names = setup_runner(tmp_path, engine)
    runner._prepare_dcCart_rack_partitions()
    assert runner._stl_solids == {source.name: names}
    assert not list(runner.tri_surface_dir.glob("baffle_*.stl"))
