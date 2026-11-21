"""Stdlib preflight tests only. They do not import a numerical solver."""
import copy,importlib.util,unittest
from pathlib import Path
S=importlib.util.spec_from_file_location("probe",Path(__file__).with_name("trajectory_ad_probe.py"));P=importlib.util.module_from_spec(S);S.loader.exec_module(P)
def bundle():
    raw={"schemaVersion":4,"time":{"dt_s":.5,"N_t":50},"analysis":{"mode":"transient","initialization":"steady"},"execution":{"device":"cpu","dtype":"float64","max_threads":2},"controllers":[],"events":[]}
    cp={"format":"liquid-dae-replay-checkpoint","version":3,"seq":49,"controllers":[],"run_id":"own","applied_inputs":[{"seq":19,"time_s":9.5,"heat_multiplier":1.1}]}
    ledger={"format":"liquid-accepted-inputs/v1","run_id":"own","accepted_sample_count":50,"original_sample_count":50,"complete_original_horizon":True,"accepted_time_s":24.5}
    return {"original":raw,"executed":copy.deepcopy(raw),"checkpoint":cp,"ledger":ledger}
class PreflightTests(unittest.TestCase):
    def test_complete_grid_not_mutated(self):
        x=bundle();before=P.canonical(x);self.assertEqual(P.preflight(x)["N_t"],50);self.assertEqual(P.canonical(x),before)
    def test_prefix_rejected(self):
        x=bundle();x["executed"]["time"]["N_t"]=49
        with self.assertRaisesRegex(ValueError,"grid differs"):P.preflight(x)
    def test_duplicate_event_rejected(self):
        x=bundle();x["checkpoint"]["applied_inputs"]*=2
        with self.assertRaisesRegex(ValueError,"duplicate"):P.preflight(x)
    def test_offgrid_event_rejected(self):
        x=bundle();x["checkpoint"]["applied_inputs"][0]["time_s"]=9.25
        with self.assertRaisesRegex(ValueError,"sample boundary"):P.preflight(x)
    def test_unknown_event_rejected(self):
        x=bundle();x["checkpoint"]["applied_inputs"][0]["futureControl"]=1
        with self.assertRaisesRegex(ValueError,"unknown"):P.preflight(x)
    def test_gpu_not_silently_rewritten(self):
        x=bundle();x["original"]["execution"]["device"]="cuda"
        with self.assertRaisesRegex(ValueError,"already request CPU"):P.preflight(x)
        self.assertEqual(x["original"]["execution"]["device"],"cuda")
    def test_bool_clock_rejected(self):
        x=bundle();x["original"]["time"]["dt_s"]=True
        with self.assertRaisesRegex(ValueError,"finite positive"):P.preflight(x)
    def test_run_identity_rejected(self):
        x=bundle();x["ledger"]["run_id"]="other"
        with self.assertRaisesRegex(ValueError,"identity"):P.preflight(x)
if __name__=="__main__":unittest.main()
