"""Tests for mesh pipeline dispatch logic in STLGenRunner.run_mesh_only()."""

import os
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from backend.aircooling.runner import stlgen_runner as sr
from backend.aircooling.runner.stlgen_runner import MeshMemoryError, STLGenRunner


def _make_runner(
    parallel_mesh: bool, mesh_process: int, mesh_engine: str = None
) -> STLGenRunner:
    """Create a minimal STLGenRunner with injected _data (skip __init__)."""
    runner = STLGenRunner.__new__(STLGenRunner)
    runner._data = {
        "meshConfig": {
            "parallelMesh": parallel_mesh,
            "meshProcess": mesh_process,
        },
    }
    # Omitted means the dcCart product default; these dispatch tests default
    # to snappy, whose serial/parallel split is what most of them check.
    runner._data["meshConfig"]["meshEngine"] = mesh_engine or "snappy"
    # run_mesh_only refuses an OpenFOAM-invalid case path before anything else
    # (_preflight_case_path), so the case dir must be set and must be valid.
    runner.case_dir = Path("/tmp/fake_case")
    # Provide minimal attributes that run_mesh_only touches after dispatch
    runner.mesh_dir = Path("/tmp/fake_mesh")
    # Dispatch tests do not have an exported manifest or a real mesh; source
    # preflight/receipts are verified in their own runner tests.
    runner._verify_hybrid_source_stls = MagicMock()
    runner._record_hybrid_mesh_source = MagicMock()
    runner._record_source_surface_mesh_source = MagicMock()
    runner._record_source_region_mesh_source = MagicMock()
    runner._record_air_mesh_quality = MagicMock()
    return runner


_PATCH_PREFIX = "backend.aircooling.runner.stlgen_runner.STLGenRunner"
# run_mesh_only preflights a dcCart run against the host's live MemAvailable /
# cgroup remainder. Dispatch tests pin that budget so they never depend on what
# else the host runs (40901, 2026-09-30: another user's jobs left 29 GB and the
# 4-rank dcCart subtest was refused). The guard itself: test_dccart_memory_guard.py.
_BUDGET = "backend.aircooling.runner.stlgen_runner._dcCart_mem_budget_bytes"
_AMPLE_MEM_BUDGET = 1 << 50


class TestMeshPipelineDispatch(unittest.TestCase):
    """Verify run_mesh_only() dispatches to the correct pipeline."""

    def setUp(self):
        budget = patch(_BUDGET, return_value=_AMPLE_MEM_BUDGET)
        budget.start()
        self.addCleanup(budget.stop)

    @patch(f"{_PATCH_PREFIX}._setup_complete_case_in_mesh")
    @patch(f"{_PATCH_PREFIX}._run_parallel_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._run_serial_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._generate_mesh_dicts")
    @patch(f"{_PATCH_PREFIX}._prepare_runtime_metadata")
    @patch(f"{_PATCH_PREFIX}._ensure_watertight_room_enclosure")
    @patch(f"{_PATCH_PREFIX}._load")
    def test_dispatches_to_parallel_when_config_enabled(
        self, m_load, m_enclosure, m_meta, m_dicts, m_serial, m_parallel, m_setup
    ):
        runner = _make_runner(parallel_mesh=True, mesh_process=8)
        # Prevent touch() on non-existent path
        runner.mesh_dir = MagicMock()
        runner.mesh_dir.__truediv__ = MagicMock(side_effect=lambda x: MagicMock())

        runner.run_mesh_only()

        m_parallel.assert_called_once_with(8)
        m_serial.assert_not_called()

    @patch(f"{_PATCH_PREFIX}._setup_complete_case_in_mesh")
    @patch(f"{_PATCH_PREFIX}._run_parallel_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._run_serial_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._generate_mesh_dicts")
    @patch(f"{_PATCH_PREFIX}._prepare_runtime_metadata")
    @patch(f"{_PATCH_PREFIX}._ensure_watertight_room_enclosure")
    @patch(f"{_PATCH_PREFIX}._load")
    def test_dispatches_to_serial_when_config_disabled(
        self, m_load, m_enclosure, m_meta, m_dicts, m_serial, m_parallel, m_setup
    ):
        runner = _make_runner(parallel_mesh=False, mesh_process=4)
        runner.mesh_dir = MagicMock()
        runner.mesh_dir.__truediv__ = MagicMock(side_effect=lambda x: MagicMock())

        runner.run_mesh_only()

        m_serial.assert_called_once()
        m_parallel.assert_not_called()

    @patch(f"{_PATCH_PREFIX}._setup_complete_case_in_mesh")
    @patch(f"{_PATCH_PREFIX}._run_parallel_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._run_serial_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._generate_mesh_dicts")
    @patch(f"{_PATCH_PREFIX}._prepare_runtime_metadata")
    @patch(f"{_PATCH_PREFIX}._ensure_watertight_room_enclosure")
    @patch(f"{_PATCH_PREFIX}._load")
    def test_falls_back_to_serial_when_meshProcess_is_1(
        self, m_load, m_enclosure, m_meta, m_dicts, m_serial, m_parallel, m_setup
    ):
        """snappy: one process is chosen as the serial pipeline up front.

        This is a dispatch decision, not a rerun after a failed MPI job — the
        dcCart pipeline has no such rerun (a failed MPI run is an error).
        """
        runner = _make_runner(parallel_mesh=True, mesh_process=1)
        runner.mesh_dir = MagicMock()
        runner.mesh_dir.__truediv__ = MagicMock(side_effect=lambda x: MagicMock())

        runner.run_mesh_only()

        m_serial.assert_called_once()
        m_parallel.assert_not_called()

    @patch(f"{_PATCH_PREFIX}._setup_complete_case_in_mesh")
    @patch(f"{_PATCH_PREFIX}._run_dcCart_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._run_parallel_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._run_serial_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._generate_mesh_dicts")
    @patch(f"{_PATCH_PREFIX}._prepare_runtime_metadata")
    @patch(f"{_PATCH_PREFIX}._ensure_watertight_room_enclosure")
    @patch(f"{_PATCH_PREFIX}._load")
    def test_dcCart_runs_its_own_pipeline_on_meshProcess_ranks_only_when_parallel(
        self, m_load, m_enclosure, m_meta, m_dicts, m_serial, m_parallel, m_dccart, m_setup
    ):
        """dcCart never takes the snappy serial/parallel pipelines; parallelMesh
        only decides the rank count it is given."""
        for parallel_mesh, mesh_process, ranks in ((True, 4, 4), (True, 1, 1), (False, 4, 1)):
            with self.subTest(parallelMesh=parallel_mesh, meshProcess=mesh_process):
                m_dccart.reset_mock()
                runner = _make_runner(parallel_mesh, mesh_process, mesh_engine="dcCart")
                runner.mesh_dir = MagicMock()
                runner.mesh_dir.__truediv__ = MagicMock(side_effect=lambda x: MagicMock())

                runner.run_mesh_only()

                m_dccart.assert_called_once_with(ranks)
        m_serial.assert_not_called()
        m_parallel.assert_not_called()

    @patch(f"{_PATCH_PREFIX}._setup_complete_case_in_mesh")
    @patch(f"{_PATCH_PREFIX}._run_dcCart_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._run_parallel_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._run_serial_mesh_pipeline")
    @patch(f"{_PATCH_PREFIX}._generate_mesh_dicts")
    @patch(f"{_PATCH_PREFIX}._prepare_runtime_metadata")
    @patch(f"{_PATCH_PREFIX}._ensure_watertight_room_enclosure")
    @patch(f"{_PATCH_PREFIX}._load")
    def test_dcCart_memory_guard_sees_the_rank_count_dcCart_is_given(
        self, m_load, m_enclosure, m_meta, m_dicts, m_serial, m_parallel, m_dccart, m_setup
    ):
        """A budget between the 1-rank and 4-rank lower bounds (where 40901's
        29 GB fell) refuses meshProcess=4 with parallelMesh on, before the
        mesher starts, and passes the same meshProcess with parallelMesh off,
        which dispatches on one rank."""
        cap = sr.DCCART_MAX_GLOBAL_CELLS_FALLBACK
        one = sr._dcCart_mem_lower_bound_bytes(cap, 1)
        four = sr._dcCart_mem_lower_bound_bytes(cap, 4)
        with patch.dict(os.environ), patch(_BUDGET, return_value=(one + four) // 2):
            os.environ.pop(sr.DCCART_MEM_GUARD_ENV, None)
            for parallel_mesh in (True, False):
                with self.subTest(parallelMesh=parallel_mesh):
                    m_dccart.reset_mock()
                    runner = _make_runner(parallel_mesh, 4, mesh_engine="dcCart")
                    runner.mesh_dir = MagicMock()
                    runner.mesh_dir.__truediv__ = MagicMock(side_effect=lambda x: MagicMock())

                    if parallel_mesh:
                        with self.assertRaisesRegex(MeshMemoryError, r"on 4 rank\(s\)"):
                            runner.run_mesh_only()
                        m_dccart.assert_not_called()
                    else:
                        runner.run_mesh_only()
                        m_dccart.assert_called_once_with(1)
        m_serial.assert_not_called()
        m_parallel.assert_not_called()


class TestParallelMeshPipelineOrder(unittest.TestCase):
    """Verify _run_parallel_mesh_pipeline issues commands in the correct order."""

    def _make_runner(self) -> STLGenRunner:
        runner = STLGenRunner.__new__(STLGenRunner)
        runner._data = {
            "meshConfig": {
                "parallelMesh": True,
                "meshProcess": 4,
            },
        }
        runner.mesh_dir = Path("/tmp/fake_mesh")
        # Command-order tests stop at the quality command, which needs a real
        # OpenFOAM case; give that independent stage a successful response.
        runner._run_cmd_capture = MagicMock(return_value="Mesh OK.")
        # Orphan-region clean-up reads the real boundary; it has its own tests.
        runner._remove_disconnected_cells = MagicMock()
        return runner

    @patch(f"{_PATCH_PREFIX}._diagnose_background_patch")
    @patch(f"{_PATCH_PREFIX}._ensure_mesh_system_files")
    @patch(f"{_PATCH_PREFIX}._run_cmd")
    def test_correct_command_order_for_parallel_mesh(
        self, m_run_cmd, m_ensure, m_diag
    ):
        """blockMesh → decomposePar -force → mpirun snappyHexMesh -parallel.

        reconstructParMesh is skipped when snappyHexMesh -overwrite -parallel has
        already reconstructed the mesh in place (no processor* dirs), so it never
        reaches _run_cmd here — only the first three commands do.
        """
        runner = self._make_runner()

        # No createBafflesDict, no surfaceFeatureExtractDict
        with patch("pathlib.Path.exists", return_value=False), \
             patch(f"{_PATCH_PREFIX}._clean_processor_dirs"), \
             patch("backend.aircooling.runner.stlgen_runner.generate_decompose_par_dict"):
            runner._run_parallel_mesh_pipeline(4)

        cmds = [call.args[0] for call in m_run_cmd.call_args_list]
        self.assertEqual(cmds[0], "blockMesh")
        self.assertEqual(cmds[1], "decomposePar -force -no-fields")
        self.assertIn("mpirun", cmds[2])
        self.assertIn("snappyHexMesh", cmds[2])
        self.assertIn("-parallel", cmds[2])
        self.assertIn("-np 4", cmds[2])
        # reconstructParMesh is skipped (snappy -parallel reconstructs in place);
        # only blockMesh, decomposePar and snappyHexMesh route through _run_cmd.
        self.assertEqual(len(cmds), 3)

    @patch(f"{_PATCH_PREFIX}._diagnose_background_patch")
    @patch(f"{_PATCH_PREFIX}._ensure_mesh_system_files")
    @patch(f"{_PATCH_PREFIX}._run_cmd")
    def test_decompose_par_dict_generated_before_decompose(
        self, m_run_cmd, m_ensure, m_diag
    ):
        """generate_decompose_par_dict must be called, with hierarchical.

        Pre-snappy the mesh is still the uniform background block, so a scotch
        graph partition is wasted work (measured: 1.9 s vs 0.3 s on a 29k-cell
        block). The method is part of the contract, not an incidental kwarg.
        """
        runner = self._make_runner()

        with patch("pathlib.Path.exists", return_value=False), \
             patch(f"{_PATCH_PREFIX}._clean_processor_dirs"), \
             patch("backend.aircooling.runner.stlgen_runner.generate_decompose_par_dict") as m_gen:
            runner._run_parallel_mesh_pipeline(4)

        m_gen.assert_called_once_with(runner.mesh_dir, 4, method="hierarchical")

    @patch(f"{_PATCH_PREFIX}._diagnose_background_patch")
    @patch(f"{_PATCH_PREFIX}._ensure_mesh_system_files")
    @patch(f"{_PATCH_PREFIX}._run_cmd")
    def test_processor_dirs_cleaned_after_reconstruct(
        self, m_run_cmd, m_ensure, m_diag
    ):
        """Processor directories must be cleaned up after reconstructParMesh."""
        runner = self._make_runner()

        with patch("pathlib.Path.exists", return_value=False), \
             patch(f"{_PATCH_PREFIX}._clean_processor_dirs") as m_clean, \
             patch("backend.aircooling.runner.stlgen_runner.generate_decompose_par_dict"):
            runner._run_parallel_mesh_pipeline(4)

        # _clean_processor_dirs should be called at least once after reconstruct
        self.assertTrue(m_clean.call_count >= 1)

    @patch(f"{_PATCH_PREFIX}._convert_wall_baffle_patches_to_wall")
    @patch(f"{_PATCH_PREFIX}._regenerate_fields_for_baffles")
    @patch(f"{_PATCH_PREFIX}._diagnose_background_patch")
    @patch(f"{_PATCH_PREFIX}._ensure_mesh_system_files")
    @patch(f"{_PATCH_PREFIX}._run_cmd")
    def test_parallel_pipeline_includes_create_baffles_when_dict_exists(
        self, m_run_cmd, m_ensure, m_diag, m_regen, m_convert
    ):
        """When createBafflesDict exists, createBaffles runs AFTER snappyHexMesh."""
        runner = self._make_runner()

        _orig_exists = Path.exists

        def fake_exists(path_self):
            # createBafflesDict exists, surfaceFeatureExtractDict does not
            if "createBafflesDict" in str(path_self):
                return True
            if "surfaceFeatureExtractDict" in str(path_self):
                return False
            return _orig_exists(path_self)

        with patch("pathlib.Path.exists", autospec=True, side_effect=fake_exists), \
             patch(f"{_PATCH_PREFIX}._clean_processor_dirs"), \
             patch("backend.aircooling.runner.stlgen_runner.generate_decompose_par_dict"):
            runner._run_parallel_mesh_pipeline(4)

        cmds = [call.args[0] for call in m_run_cmd.call_args_list]
        # createBaffles must be the last command
        self.assertEqual(cmds[-1], "createBaffles -overwrite")
        # And it must come after snappyHexMesh (reconstructParMesh is skipped when
        # snappy -overwrite -parallel already reconstructed the mesh in place).
        snappy_idx = next(i for i, c in enumerate(cmds) if "snappyHexMesh" in c)
        baffles_idx = next(i for i, c in enumerate(cmds) if "createBaffles" in c)
        self.assertGreater(baffles_idx, snappy_idx)
        m_regen.assert_called_once()
        m_convert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
