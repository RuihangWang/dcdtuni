"""The results view opens on the room's interior: its shell starts hidden."""
import pytest

from backend.aircooling.runner.postprocess import _patch_default_visible


@pytest.mark.parametrize("name", ["Room_Ceiling", "Room_Wall_1", "Room_Wall_12", "room_wall_3"])
def test_room_shell_starts_hidden(name):
    assert not _patch_default_visible(name)


@pytest.mark.parametrize("name", ["Room_Floor", "Room_Raising_FLoor_part1_master", "A1_frame",
                                  "AC_2_9_1_Top_patch", "Room_Wall_baffle", "partition_Room_Ceiling"])
def test_everything_else_starts_visible(name):
    assert _patch_default_visible(name)


@pytest.mark.parametrize("name", ["merged_wall_baffle_slave", "internalMesh", "internal"])
def test_slaves_and_the_internal_mesh_stay_hidden(name):
    assert not _patch_default_visible(name)
