"""Readiness restoration must require current accepted mesh/input bytes.

Checker output below is synthetic unit data, never native acceptance evidence.
"""
import hashlib,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

IT=Path(__file__).resolve().parents[3]
OUT=IT/".codex-tmp/test-server-mesh-readiness"
(OUT/"tmp").mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(IT/"src"))
from backend.aircooling.runner.case_execution_lock import case_execution_lock,CaseExecutionBusyError
from backend.aircooling.runner.air_mesh_quality_receipt import record_prechecked_air_mesh_quality
from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256

# Import the complete installed server module, not an AST-extracted helper.
# __main__ never runs; assert import cannot launch processes, threads or listeners.
sys.path.insert(0,str(IT/"src/server"))
spec=importlib.util.spec_from_file_location("test_mesh_readiness_server",IT/"src/server/server.py")
server=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=server
with patch("subprocess.run",side_effect=AssertionError("native invocation forbidden")), \
     patch("subprocess.Popen",side_effect=AssertionError("process launch forbidden")), \
     patch("threading.Thread.start",side_effect=AssertionError("thread launch forbidden")), \
     patch("http.server.ThreadingHTTPServer.__init__",side_effect=AssertionError("listener forbidden")):
 spec.loader.exec_module(server)
scan=server._scan_case_mesh_status

class ReceiptGate(unittest.TestCase):
 def setUp(self):
  self.sandbox=tempfile.TemporaryDirectory(prefix="gate-fixture-",dir=OUT/"tmp")
  self.case=Path(self.sandbox.name)/"case";self.case.mkdir()
  (self.case/"model").mkdir()
  self.raw=self.case/"model/current.cfdrun";self.raw.write_text('{"schemaVersion":4,"runId":"fixture"}')
  self.no_native=patch("subprocess.run",side_effect=AssertionError("native invocation forbidden"))
  self.no_native.start()
 def tearDown(self):
  self.no_native.stop();self.sandbox.cleanup()
 def mesh(self,ranks=1):
  directory=self.case/"Mesh"
  if ranks>1:
   (directory/"system").mkdir(parents=True)
   (directory/"system/decomposeParDict").write_text(f"numberOfSubdomains {ranks};")
  roots=[directory/"constant/polyMesh"]if ranks==1 else[directory/f"processor{r}/constant/polyMesh"for r in range(ranks)]
  for root in roots:
   root.mkdir(parents=True)
   for name in ("points","faces","owner","neighbour","boundary"):
    (root/name).write_text('FoamFile { note "nCells:1"; }\n0\n(\n)\n')
  self.meshdir=directory;self.roots=roots;return roots
 def receipt(self,accepted=True,ranks=1):
  self.mesh(ranks)
  command=["checkMesh","-allTopology","-allGeometry"]
  if ranks>1:command=["mpirun","-np",str(ranks),*command,"-parallel"]
  # Captured output is a synthetic unit fixture, never native acceptance evidence.
  return record_prechecked_air_mesh_quality(self.meshdir,self.raw,command=command,workdir=self.meshdir,
   mesh_sha256_before=mesh_identity_sha256(self.meshdir),
   cfdrun_sha256_before=hashlib.sha256(self.raw.read_bytes()).hexdigest(),
   returncode=0,stdout="Mesh OK.\n"if accepted else"Failed 2 mesh checks.\n",stderr="")
 def test_no_mesh_is_not_ready(self):
  self.assertEqual(scan(self.case),{"meshExists":False,"meshReady":False})
 def test_files_without_quality_receipt_remain_unaccepted(self):
  self.mesh();self.assertEqual(scan(self.case),{"meshExists":True,"meshReady":False})
 def test_accepted_serial_receipt_restores_reload_readiness(self):
  self.receipt();self.assertEqual(scan(self.case),{"meshExists":True,"meshReady":True})
 def test_rejected_receipt_refuses_without_full_mesh_hash(self):
  self.receipt(accepted=False)
  with patch("backend.aircooling.runner.air_mesh_quality_receipt.mesh_identity_sha256",
             side_effect=AssertionError("rejected scan must be cheap")):
   self.assertEqual(scan(self.case),{"meshExists":True,"meshReady":False})
 def test_current_raw_change_revokes_acceptance(self):
  self.receipt();self.raw.write_text('{"schemaVersion":4,"runId":"changed"}')
  self.assertFalse(scan(self.case)["meshReady"])
 def test_current_mesh_bytes_change_revokes_acceptance(self):
  self.receipt();(self.roots[0]/"points").write_text("changed point bytes")
  self.assertEqual(scan(self.case),{"meshExists":True,"meshReady":False})
 def test_missing_receipt_revokes_old_acceptance(self):
  self.receipt();(self.case/"mesh-quality-receipt.json").unlink()
  self.assertFalse(scan(self.case)["meshReady"])
 def test_malformed_receipt_cannot_restore_readiness(self):
  self.mesh();(self.case/"mesh-quality-receipt.json").write_text('{"status":"accepted"}')
  self.assertFalse(scan(self.case)["meshReady"])
 def test_invalid_receipt_json_cannot_restore_readiness(self):
  self.mesh();(self.case/"mesh-quality-receipt.json").write_text('invalid')
  self.assertFalse(scan(self.case)["meshReady"])
 def test_multiple_case_inputs_cannot_guess_acceptance(self):
  self.receipt();(self.case/"model/other.cfdrun").write_text("{}")
  self.assertFalse(scan(self.case)["meshReady"])
 def test_accepted_partitioned_receipt_uses_actual_rank_layout(self):
  self.receipt(ranks=2);self.assertTrue(scan(self.case)["meshReady"])
 def test_incomplete_partition_layout_revokes_acceptance(self):
  self.receipt(ranks=2);(self.roots[1]/"owner").unlink()
  self.assertEqual(scan(self.case),{"meshExists":False,"meshReady":False})
 def test_matching_raw_bytes_via_external_symlink_do_not_authorize(self):
  self.receipt()
  external=Path(self.sandbox.name)/"external-source.cfdrun"
  external.write_bytes(self.raw.read_bytes())
  self.raw.unlink();self.raw.symlink_to(external)
  self.assertFalse(scan(self.case)["meshReady"])
 def test_accepted_receipt_external_symlink_does_not_authorize(self):
  self.receipt()
  receipt=self.case/"mesh-quality-receipt.json"
  external=Path(self.sandbox.name)/"external-receipt.json"
  external.write_bytes(receipt.read_bytes())
  receipt.unlink();receipt.symlink_to(external)
  self.assertFalse(scan(self.case)["meshReady"])
 def test_actual_route_keeps_rejected_files_unready(self):
  self.receipt(accepted=False)
  captured=[]
  with patch.object(server,"CASE_DIR",self.case), \
       patch.object(server,"_send_json",side_effect=lambda handler,code,data:captured.append((code,data))):
   server.handle_scan_case_status(object())
  self.assertEqual(captured[0][0],200)
  self.assertTrue(captured[0][1]["meshExists"])
  self.assertFalse(captured[0][1]["meshReady"])
 def test_actual_route_restores_accepted_identity(self):
  self.receipt()
  captured=[]
  with patch.object(server,"CASE_DIR",self.case), \
       patch.object(server,"_send_json",side_effect=lambda handler,code,data:captured.append((code,data))):
   server.handle_scan_case_status(object())
  self.assertEqual(captured[0][0],200)
  self.assertTrue(captured[0][1]["meshReady"])
 def test_busy_case_cannot_restore_readiness_from_mutable_files(self):
  self.receipt()
  with case_execution_lock(self.case):
   self.assertFalse(scan(self.case)["meshReady"])

if __name__=="__main__":unittest.main(verbosity=2)
