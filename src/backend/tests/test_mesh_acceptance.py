"""The patch/solid acceptance check must not cry wolf, and must not miss a loss.

Both directions matter. A false positive makes the gate useless (every case
"fails" and nobody looks). A false negative lets a boundary condition disappear
silently — the geometry stays, but nothing can be prescribed on the region and
the solve runs a quietly different problem.

The exceptions are read out of the runner's own generated dictionaries, so the
fixtures here are miniature versions of those dictionaries rather than mocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aircooling.runner.mesh_acceptance import (
    check_patch_solid_correspondence,
)


def _stl(path: Path, *solids: str) -> None:
    """Each solid gets DISTINCT geometry.

    Identical geometry is meaningful to the checker — coincident solids are
    treated as one surface — so a fixture that gave every solid the same
    triangle would silently exercise that path instead of the one under test.
    """
    body = ""
    for i, s in enumerate(solids):
        # offset well away from _stl_coincident's plane so the two helpers can
        # be used in the same fixture without accidentally coinciding
        z = 10.0 + float(i)
        body += (
            f"solid {s}\n"
            "  facet normal 0 0 1\n    outer loop\n"
            f"      vertex 0 0 {z}\n      vertex 1 0 {z}\n      vertex 0 1 {z}\n"
            "    endloop\n  endfacet\n"
            f"endsolid {s}\n"
        )
    path.write_text(body, encoding="utf-8")


def _stl_coincident(path: Path, *solids: str) -> None:
    """Every solid occupies the SAME space — the U-slot top/bottom situation."""
    body = "".join(
        f"solid {s}\n"
        "  facet normal 0 0 1\n    outer loop\n"
        "      vertex 0 0 0\n      vertex 1 0 0\n      vertex 0 1 0\n"
        "    endloop\n  endfacet\n"
        f"endsolid {s}\n"
        for s in solids
    )
    path.write_text(body, encoding="utf-8")


def _boundary(path: Path, *patches: str) -> None:
    entries = "".join(
        f"    {p}\n    {{\n        type            wall;\n"
        f"        nFaces          10;\n        startFace       0;\n    }}\n"
        for p in patches
    )
    path.write_text(
        "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
        "    class polyBoundaryMesh;\n    object boundary;\n}\n"
        f"\n{len(patches)}\n(\n{entries})\n",
        encoding="utf-8",
    )


def _snappy(path: Path, surfaces: dict) -> None:
    """surfaces: {surfaceName: [region, ...] or None for no regions block}"""
    blocks = []
    for surface, regions in surfaces.items():
        if regions is None:
            blocks.append(f"    {surface}\n    {{\n        level (1 1);\n    }}\n")
        else:
            inner = "".join(
                f"            {r}\n            {{\n"
                f"                level (2 2);\n"
                f"                patchInfo {{ type wall; }}\n            }}\n"
                for r in regions
            )
            blocks.append(
                f"    {surface}\n    {{\n        level (1 1);\n"
                f"        regions\n        {{\n{inner}        }}\n    }}\n"
            )
    path.write_text(
        "FoamFile\n{\n    version 2.0;\n    object snappyHexMeshDict;\n}\n"
        "castellatedMesh on;\nsnap off;\naddLayers off;\n\n"
        "castellatedMeshControls\n{\n"
        "    refinementSurfaces\n    {\n" + "".join(blocks) + "    }\n\n"
        "    refinementRegions\n    {\n    }\n}\n",
        encoding="utf-8",
    )


def _baffles(path: Path, *zones: str) -> None:
    blocks = "".join(
        f"    {z}\n    {{\n        type faceZone;\n        zoneName {z};\n"
        "        patches\n        {\n"
        f"            master {{ name {z}_master; type cyclic;"
        f" neighbourPatch {z}_slave; }}\n"
        f"            slave  {{ name {z}_slave;  type cyclic;"
        f" neighbourPatch {z}_master; }}\n"
        "        }\n    }\n"
        for z in zones
    )
    path.write_text(
        "FoamFile\n{\n    version 2.0;\n    object createBafflesDict;\n}\n"
        f"internalFacesOnly true;\nbaffles\n{{\n{blocks}}}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def mesh_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Mesh"
    (d / "constant" / "triSurface").mkdir(parents=True)
    (d / "constant" / "polyMesh").mkdir(parents=True)
    (d / "system").mkdir(parents=True)
    return d


def test_clean_one_to_one_case_passes(mesh_dir: Path):
    _stl(mesh_dir / "constant/triSurface/merged_acu.stl", "ACU_Front", "ACU_Back")
    _snappy(mesh_dir / "system/snappyHexMeshDict", {"merged_acu": ["ACU_Front", "ACU_Back"]})
    _boundary(mesh_dir / "constant/polyMesh/boundary", "ACU_Front", "ACU_Back")

    r = check_patch_solid_correspondence(mesh_dir, "clean")
    assert r.ok, r.summary()
    assert r.n_solids == 2
    assert r.n_patches == 2


def test_dropped_patch_is_caught(mesh_dir: Path):
    """The real failure mode: an ACU bottom flush with the floor gets no faces,
    snappy deletes the zero-sized patch, and its BC is silently lost."""
    _stl(mesh_dir / "constant/triSurface/merged_acu.stl", "ACU_Front", "ACU_Bottom")
    _snappy(mesh_dir / "system/snappyHexMeshDict", {"merged_acu": ["ACU_Front", "ACU_Bottom"]})
    _boundary(mesh_dir / "constant/polyMesh/boundary", "ACU_Front")

    r = check_patch_solid_correspondence(mesh_dir, "dropped")
    assert not r.ok
    assert r.missing_patches == ["ACU_Bottom"]


def test_baffle_solids_count_as_their_master_slave_pair(mesh_dir: Path):
    """Three Baffle_* solids merged into one faceZone yield exactly two patches."""
    _stl(
        mesh_dir / "constant/triSurface/merged_wall_baffle.stl",
        "Baffle_1", "Baffle_2", "Baffle_3",
    )
    _snappy(
        mesh_dir / "system/snappyHexMeshDict",
        {"merged_wall_baffle": ["Baffle_1", "Baffle_2", "Baffle_3"]},
    )
    _baffles(mesh_dir / "system/createBafflesDict", "merged_wall_baffle")
    _boundary(
        mesh_dir / "constant/polyMesh/boundary",
        "merged_wall_baffle_master", "merged_wall_baffle_slave",
    )

    r = check_patch_solid_correspondence(mesh_dir, "baffle")
    assert r.ok, r.summary()
    assert set(r.baffle_solids) >= {"Baffle_1", "Baffle_2", "Baffle_3"}


def test_zone_named_after_the_region_not_the_file(mesh_dir: Path):
    """perforated_floor_<zone> is declared, but the zone is <zone>: both the
    zone-prefixes-region and region-prefixes-zone spellings must resolve."""
    _stl(
        mesh_dir / "constant/triSurface/perforated_floor_Raised_Floor.stl",
        "perforated_floor_Raised_Floor",
    )
    _snappy(
        mesh_dir / "system/snappyHexMeshDict",
        {"perforated_floor_Raised_Floor": ["perforated_floor_Raised_Floor"]},
    )
    _baffles(mesh_dir / "system/createBafflesDict", "Raised_Floor")
    _boundary(
        mesh_dir / "constant/polyMesh/boundary",
        "Raised_Floor_master", "Raised_Floor_slave",
    )

    r = check_patch_solid_correspondence(mesh_dir, "prefixed-zone")
    assert r.ok, r.summary()


def test_surface_without_a_regions_block_uses_its_stl_solid_names(mesh_dir: Path):
    """merged_wallBlankingPanel_10U declares no regions, but its STL names the
    solid Default_Rack_1_inlet_u1_u10 — and that is the patch snappy creates."""
    _stl(
        mesh_dir / "constant/triSurface/merged_wallBlankingPanel_10U.stl",
        "Default_Rack_1_inlet_u1_u10",
    )
    _snappy(mesh_dir / "system/snappyHexMeshDict", {"merged_wallBlankingPanel_10U": None})
    _boundary(mesh_dir / "constant/polyMesh/boundary", "Default_Rack_1_inlet_u1_u10")

    r = check_patch_solid_correspondence(mesh_dir, "blanking")
    assert r.ok, r.summary()
    assert r.unexpected_patches == []


def test_refinement_only_solids_are_reported_not_failed(mesh_dir: Path):
    """A through-zone is a refinement volume declared only in geometry."""
    _stl(mesh_dir / "constant/triSurface/merged_acu.stl", "ACU_Front")
    _stl(
        mesh_dir / "constant/triSurface/merged_through_zone_15U__Rack_1.stl",
        "Rack_1_u1_u15_through_zone",
    )
    _snappy(mesh_dir / "system/snappyHexMeshDict", {"merged_acu": ["ACU_Front"]})
    _boundary(mesh_dir / "constant/polyMesh/boundary", "ACU_Front")

    r = check_patch_solid_correspondence(mesh_dir, "throughzone")
    assert r.ok, r.summary()
    assert r.not_requested_solids == ["Rack_1_u1_u15_through_zone"]


def test_unexpected_patch_is_caught(mesh_dir: Path):
    _stl(mesh_dir / "constant/triSurface/merged_acu.stl", "ACU_Front")
    _snappy(mesh_dir / "system/snappyHexMeshDict", {"merged_acu": ["ACU_Front"]})
    _boundary(mesh_dir / "constant/polyMesh/boundary", "ACU_Front", "mysteryPatch")

    r = check_patch_solid_correspondence(mesh_dir, "extra")
    assert not r.ok
    assert r.unexpected_patches == ["mysteryPatch"]


def test_synthetic_patches_are_allowed(mesh_dir: Path):
    _stl(mesh_dir / "constant/triSurface/merged_acu.stl", "ACU_Front")
    _snappy(mesh_dir / "system/snappyHexMeshDict", {"merged_acu": ["ACU_Front"]})
    _boundary(mesh_dir / "constant/polyMesh/boundary", "ACU_Front", "backgroundBoundary")

    r = check_patch_solid_correspondence(mesh_dir, "synthetic")
    assert r.ok, r.summary()


def test_missing_boundary_file_is_an_error_not_a_pass(mesh_dir: Path):
    _stl(mesh_dir / "constant/triSurface/merged_acu.stl", "ACU_Front")
    _snappy(mesh_dir / "system/snappyHexMeshDict", {"merged_acu": ["ACU_Front"]})

    r = check_patch_solid_correspondence(mesh_dir, "nomesh")
    assert not r.ok
    assert "boundary" in r.error


def test_missing_snappy_dict_is_an_error_not_a_pass(mesh_dir: Path):
    _stl(mesh_dir / "constant/triSurface/merged_acu.stl", "ACU_Front")
    _boundary(mesh_dir / "constant/polyMesh/boundary", "ACU_Front")

    r = check_patch_solid_correspondence(mesh_dir, "nodict")
    assert not r.ok
    assert "refinementSurfaces" in r.error


def test_coincident_solids_share_one_patch(mesh_dir: Path):
    """The U-slot system names one physical plane twice — the top face of the
    server below and the bottom face of the server above. Only one of them can
    own the mesh faces, so the pair is satisfied when either got a patch."""
    _stl_coincident(
        mesh_dir / "constant/triSurface/merged_server_4U.stl",
        "Slot19_top", "Slot23_bottom",
    )
    _snappy(
        mesh_dir / "system/snappyHexMeshDict",
        {"merged_server_4U": ["Slot19_top", "Slot23_bottom"]},
    )
    _boundary(mesh_dir / "constant/polyMesh/boundary", "Slot19_top")

    r = check_patch_solid_correspondence(mesh_dir, "coincident")
    assert r.ok, r.summary()
    assert r.duplicate_geometry == {"Slot23_bottom": "Slot19_top"}


def test_coincident_pair_with_neither_meshed_is_classified_buried(mesh_dir: Path):
    """A shared plane where NEITHER name got a patch is reported as buried, not
    as a failure.

    Evidence: all 451 coincident groups on Alibaba-sige are like this, and each
    one is the interface between two stacked solid server blocks — solid on both
    sides, so no fluid face can exist and a boundary condition there would have
    nothing to act on.

    KNOWN LIMITATION: from the STL alone this is indistinguishable from a
    coincident pair that genuinely faces fluid and was genuinely dropped.
    Telling them apart needs the MESH (whether any cell survived next to the
    plane). The gate errs toward not crying wolf, because a check that fails
    every case is a check nobody reads."""
    _stl_coincident(
        mesh_dir / "constant/triSurface/merged_server_4U.stl",
        "Slot19_top", "Slot23_bottom",
    )
    _stl(mesh_dir / "constant/triSurface/merged_acu.stl", "ACU_Front")
    _snappy(
        mesh_dir / "system/snappyHexMeshDict",
        {
            "merged_server_4U": ["Slot19_top", "Slot23_bottom"],
            "merged_acu": ["ACU_Front"],
        },
    )
    _boundary(mesh_dir / "constant/polyMesh/boundary", "ACU_Front")

    r = check_patch_solid_correspondence(mesh_dir, "both-lost")
    assert r.ok, r.summary()
    assert set(r.buried_interfaces) == {"Slot19_top", "Slot23_bottom"}
    assert r.missing_patches == []


def test_editor_only_interface_bucket_is_not_acceptance_evidence(mesh_dir: Path):
    """WP-W7: merged_server_interface_walls.stl is an editor-export artifact —
    the server left/right faces where a server touches a rack-frame cavity
    wall, with no fluid on either side. The mesh path never sees the file, so
    its solids must not be counted as mesh-input solids (n_solids) nor show up
    as 'not requested' — the runner did not decline to declare them, the file
    is not a mesh input at all."""
    _stl(mesh_dir / "constant/triSurface/merged_acu.stl", "ACU_Front")
    _stl(
        mesh_dir / "constant/triSurface/merged_server_interface_walls.stl",
        "Server_1_left", "Server_1_right",
    )
    _stl(
        mesh_dir / "constant/triSurface/merged_server_interface_walls_left.stl",
        "Server_2_left",
    )
    _snappy(mesh_dir / "system/snappyHexMeshDict", {"merged_acu": ["ACU_Front"]})
    _boundary(mesh_dir / "constant/polyMesh/boundary", "ACU_Front")

    r = check_patch_solid_correspondence(mesh_dir, "editor-only")
    assert r.ok, r.summary()
    assert r.n_solids == 1
    assert r.not_requested_solids == []


def test_editor_only_twin_cannot_excuse_a_missing_patch(mesh_dir: Path):
    """The buried-interface excuse needs a coincident MESH INPUT. A geometry
    twin that exists only in the editor-only interface bucket proves nothing
    about the meshed world — a declared region whose only 'twin' lives there
    must stay MISSING, not get laundered into buried_interfaces."""
    _stl_coincident(
        mesh_dir / "constant/triSurface/merged_server_4U.stl", "Slot19_top"
    )
    _stl_coincident(
        mesh_dir / "constant/triSurface/merged_server_interface_walls.stl",
        "Slot19_top_interface",
    )
    _stl(mesh_dir / "constant/triSurface/merged_acu.stl", "ACU_Front")
    _snappy(
        mesh_dir / "system/snappyHexMeshDict",
        {"merged_server_4U": ["Slot19_top"], "merged_acu": ["ACU_Front"]},
    )
    _boundary(mesh_dir / "constant/polyMesh/boundary", "ACU_Front")

    r = check_patch_solid_correspondence(mesh_dir, "launder")
    assert not r.ok
    assert r.missing_patches == ["Slot19_top"]
    assert r.buried_interfaces == []
