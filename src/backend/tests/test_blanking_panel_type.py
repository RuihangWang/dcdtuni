"""Tests for rack blankingPanelType: fullBlock vs typicalSmallGap.

fullBlock: blanking panels → merged_wallBlankingPanel_<N>U.stl → regular wall
(NO faceType internal, NO createBaffles). snappy treats them as normal wall
surfaces — sealed rack interior is simply not meshed.

typicalSmallGap (default): blanking panels → merged_perforatedBlankingPanel_<N>U.stl
→ faceType internal → createBaffles → porousBafflePressure.

blankingPanelType is a per-rack setting; ALL blanking panels from a rack
share the same treatment regardless of isWallBaffle geometry flag.
"""
from pathlib import Path
import pytest

from backend.aircooling.runner.bridge_bc import BCSpec, BridgeBCRegistry, PerforatedSpec
from backend.aircooling.runner.fields import FieldGenerator


SIGMA_SMALL_GAP = 0.6401


class TestBlankingPanelSnappyDict:
    """Verify snappyHexMeshDict: wallBlankingPanel has NO faceType internal,
    while perforatedBlankingPanel DOES have faceType internal."""

    def _make_runner(self, tmp_path, stl_solids, blanking_type="fullBlock", mesh_engine="snappy"):
        from backend.aircooling.runner.stlgen_runner import STLGenRunner
        runner = STLGenRunner.__new__(STLGenRunner)
        runner._data = {
            "projectName": "test",
            "room": {"height": 3.0, "plane": [{"x": 0, "y": 0, "z": 0}],
                     "locationInMesh": {"x": 1, "y": 1, "z": 1}},
            "racks": {"Rack_1": {
                "blankingPanelType": blanking_type,
                "geometry": {"size": {"W": 0.6}, "slotWidthRatio": 0.8,
                             "unitHeight": 0.04445},
                "servers": [],
                "wallBaffleSolids": [],
            }},
            "meshConfig": {"baseSize": 0.1, "meshEngine": mesh_engine},
            "solverConfig": {},
        }
        mesh_dir = tmp_path / "Mesh"
        system_dir = mesh_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)
        tri_dir = mesh_dir / "constant" / "triSurface"
        tri_dir.mkdir(parents=True, exist_ok=True)
        runner._case_dir = tmp_path
        runner.mesh_dir = mesh_dir
        runner.model_dir = tmp_path / "model"
        runner.model_dir.mkdir(parents=True, exist_ok=True)
        runner.tri_surface_dir = tri_dir
        runner._stl_solids = stl_solids
        runner._bridge_registry = None
        # No .cfdrun source inventory here; UID dictionary output has its own tests.
        runner._source_surface_dict_block = lambda _mesh_config: ""
        return runner

    def test_wallblankingpanel_no_faceType_internal(self, tmp_path):
        """wallBlankingPanel surface must NOT have faceType internal in snappy."""
        runner = self._make_runner(
            tmp_path,
            {"merged_wallBlankingPanel_10U.stl": ["Rack_1_inlet_u1_u10"]},
            blanking_type="fullBlock",
        )
        system_dir = runner.mesh_dir / "system"
        runner._generate_snappy_hex_mesh_dict(system_dir)
        snappy_text = (system_dir / "snappyHexMeshDict").read_text()
        assert "merged_wallBlankingPanel_10U" in snappy_text
        import re
        m = re.search(
            r'merged_wallBlankingPanel_10U\s*\{([^}]+)\}',
            snappy_text,
        )
        assert m is not None, "wallBlankingPanel surface entry not found in snappy dict"
        block = m.group(1)
        assert "faceType" not in block, \
            f"wallBlankingPanel must NOT have faceType internal, got: {block}"
        assert "faceZone" not in block, \
            f"wallBlankingPanel must NOT have faceZone, got: {block}"
        assert "level" in block

    def test_wallblankingpanel_is_a_wall_baffle_zone_under_dccart(self, tmp_path):
        """dcCart only makes faces from a zero-thickness sheet that is a baffle.

        Without faceType internal the open sheet carves nothing, no patch is
        written, and air crosses the "sealed" panel (CMI-Guichou: 21 m2 open).
        """
        runner = self._make_runner(
            tmp_path,
            {"merged_wallBlankingPanel_10U.stl": ["Rack_1_inlet_u1_u10"]},
            blanking_type="fullBlock",
            mesh_engine="dcCart",
        )
        system_dir = runner.mesh_dir / "system"
        runner._generate_snappy_hex_mesh_dict(system_dir)
        snappy_text = (system_dir / "snappyHexMeshDict").read_text()
        import re
        refinement = snappy_text[snappy_text.index("refinementSurfaces"):]
        m = re.search(r'merged_wallBlankingPanel_10U\s*\{([^}]+)\}', refinement)
        assert m is not None
        block = m.group(1)
        assert re.search(r'faceType\s+internal;', block), block
        assert re.search(r'faceZone\s+merged_wallBlankingPanel_10U;', block), block

    def test_perforatedblankingpanel_has_faceType_internal(self, tmp_path):
        """perforatedBlankingPanel surface MUST have faceType internal in snappy."""
        runner = self._make_runner(
            tmp_path,
            {"merged_perforatedBlankingPanel_6U.stl": [
                "Rack_1_inlet_u33_u38",
            ]},
            blanking_type="typicalSmallGap",
        )
        system_dir = runner.mesh_dir / "system"
        runner._generate_snappy_hex_mesh_dict(system_dir)
        snappy_text = (system_dir / "snappyHexMeshDict").read_text()
        assert "merged_perforatedBlankingPanel_6U" in snappy_text
        import re
        m = re.search(
            r'merged_perforatedBlankingPanel_6U\s*\{([^}]+)\}',
            snappy_text,
        )
        assert m is not None
        block = m.group(1)
        assert "faceType" in block and "internal" in block
        assert "faceZone" in block


class TestBlankingPanelFieldGeneration:
    """Verify field files produce the correct BC for each blanking type."""

    def _make_gen(self, tmp_path, bc_type, sigma=None):
        reg = BridgeBCRegistry()
        if bc_type == "wall_baffle":
            reg.register("blank_master", BCSpec(bc_type="wall_baffle"))
            reg.register("blank_slave", BCSpec(bc_type="wall_baffle"))
        else:
            perf = PerforatedSpec(shape="circle", holeDiameterMm=3.0,
                                 openAreaPct=(sigma or SIGMA_SMALL_GAP) * 100)
            reg.register("blank_master", BCSpec(
                bc_type="perforated_baffle", sigma=sigma or SIGMA_SMALL_GAP,
                perforated=perf))
            reg.register("blank_slave", BCSpec(
                bc_type="perforated_baffle", sigma=sigma or SIGMA_SMALL_GAP,
                perforated=perf))
        return FieldGenerator(case_dir=tmp_path, bridge_registry=reg)

    def _write_p_rgh(self, gen, tmp_path):
        path = tmp_path / "0" / "p_rgh"
        path.parent.mkdir(parents=True, exist_ok=True)
        gen._imported_bc_data = gen._collect_imported_bc_data()
        gen._write_p_rgh_field(path, [
            ("blank_master", "patch"),
            ("blank_slave", "patch"),
        ])
        return path.read_text()

    def test_full_block_p_rgh_is_wall(self, tmp_path):
        gen = self._make_gen(tmp_path, "wall_baffle")
        text = self._write_p_rgh(gen, tmp_path)
        assert "fixedFluxPressure" in text
        assert "porousBafflePressure" not in text

    def test_full_block_wall_functions(self, tmp_path):
        """A wall baffle gets the wall functions of a wall: nut the momentum one, alphat the thermal one."""
        gen = self._make_gen(tmp_path, "wall_baffle")
        gen._imported_bc_data = gen._collect_imported_bc_data()
        texts = {}
        for name, dims in (("nut", "[0 2 -1 0 0 0 0]"), ("alphat", "[0 2 -1 0 0 0 0]")):
            path = tmp_path / "0" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            gen._write_simple_scalar_field(path, [("blank_master", "patch"), ("blank_slave", "patch")],
                                           "0", "volScalarField", dims)
            texts[name] = path.read_text()
        assert texts["nut"].count("nutkWallFunction") == 2
        assert texts["alphat"].count("alphatJayatillekeWallFunction") == 2
        assert "nutkWallFunction" not in texts["alphat"]

    def test_small_gap_p_rgh_is_porous(self, tmp_path):
        gen = self._make_gen(tmp_path, "perforated_baffle", sigma=SIGMA_SMALL_GAP)
        text = self._write_p_rgh(gen, tmp_path)
        assert "porousBafflePressure" in text
        assert "fixedFluxPressure" not in text

    def test_fully_open_p_rgh_is_plain_cyclic(self, tmp_path):
        """fullyOpen exports σ = 1: the createBaffles cyclic pair carries no pressure jump (the DCTS rooms)."""
        gen = self._make_gen(tmp_path, "perforated_baffle", sigma=1.0)
        path = tmp_path / "0" / "p_rgh"
        path.parent.mkdir(parents=True, exist_ok=True)
        gen._imported_bc_data = gen._collect_imported_bc_data()
        gen._write_p_rgh_field(path, [("blank_master", "cyclic"), ("blank_slave", "cyclic")])
        text = path.read_text()
        assert "porousBafflePressure" not in text
        assert text.count("type            cyclic;") == 2


def test_per_rack_blanking_bucket_names_parse_to_their_span():
    from backend.aircooling.runner.stlgen_runner import _parse_bucket_from_filename
    assert _parse_bucket_from_filename("merged_perforatedBlankingPanel_A01_4U.stl") == ("perforatedBlankingPanel", "4")
    assert _parse_bucket_from_filename("merged_wallBlankingPanel_Rack_B_06_12U.stl") == ("wallBlankingPanel", "12")
    assert _parse_bucket_from_filename("merged_perforatedBlankingPanel_7U.stl") == ("perforatedBlankingPanel", "7")
    assert _parse_bucket_from_filename("merged_through_zone_3U__Rack_1.stl") == ("through_zone", "3")
