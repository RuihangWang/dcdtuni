import unittest,json,copy
from pathlib import Path
from trajectory_ad_probe import validate_actual_result
P=Path(__file__).resolve().parents[1]/'liquid-mixed-keyframe-actual-20261006/completed'
class ActualResultGate(unittest.TestCase):
 def setUp(self):
  self.raw=json.loads((P/'result.json').read_text());self.clock={'N_t':50,'dt_s':.5,'run_id':'b69f923f8c76499eb4ec60c64ae46cb4'}
 def test_real_complete_result_admitted(self):validate_actual_result(self.raw,self.clock)
 def test_nonfinite_reference_refused(self):
  self.raw['primary_return_K'][0][17]=float('nan')
  with self.assertRaises(ValueError):validate_actual_result(self.raw,self.clock)
 def test_shortened_reference_refused(self):
  self.raw['chip_temp_K'][0].pop()
  with self.assertRaises(ValueError):validate_actual_result(self.raw,self.clock)
 def test_wrong_run_refused(self):
  self.raw['run_id']='different'
  with self.assertRaises(ValueError):validate_actual_result(self.raw,self.clock)
 def test_shifted_grid_refused(self):
  self.raw['time_s'][17]+=.01
  with self.assertRaises(ValueError):validate_actual_result(self.raw,self.clock)
if __name__=='__main__':unittest.main()
