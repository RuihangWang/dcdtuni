from pathlib import Path
import json,copy
import pytest
here=Path(__file__).parent
import importlib.util
spec=importlib.util.spec_from_file_location('actual_template_audit',here/'audit_template.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
certify=module.certify
new=json.loads((here/'single-interior-point-star-v1/candidate-chebyshev.json').read_text())
def bad(change,pattern):
 d=copy.deepcopy(new);change(d)
 with pytest.raises(AssertionError,match=pattern):certify(d)
def test_exact_visible_kernel_and_coverage_contract():
 d=certify(new);assert len(d['literal38OuterOrderedRingsAndRoles'])==38
 assert d['FractionCoreVolumeAndFirstMomentExact'] and d['all69OutsideFractionShapesExact']
 assert d['apexContainedInOriginalCells']
def test_actual_installed_both_points_are_refused_by_hardD():
 a=json.loads((here/'installed-complete-star-audit.json').read_text())
 for tag,expected in [('chebyshev',6),('maxmin',10)]:
  r=a['reports'][tag];assert not r['qualifiedCandidate']
  assert len(r['inheritedPassingDRefusals'])==expected
  assert all(v['D']<.001 for v in r['inheritedPassingDRefusals'])
def test_geometry_and_warning_are_not_hardD_excuses():
 a=json.loads((here/'installed-complete-star-audit.json').read_text())
 for r in a['reports'].values():
  assert r['actualStockConcavityAnyCompleteCell']==[False,False]
  assert r['minimumNewCellPyramidVolume']>0 and r['minimumSharedAnchorTetQuality']>1e-30
  assert not any(r['metrics'][k]['retainedPassingRoleRegressions']for k in ['D','weight','ratio','skew'])
  assert len(r['metrics']['N']['newBad'])>4
def test_no_original_coordinate_motion():
 bad(lambda d:d['points']['28144'].__setitem__(0,d['points']['28144'][0]+1e-6),'original-point-coordinate')
def test_physical_point_motion_refused():
 bad(lambda d:d['points']['61114'].__setitem__(0,d['points']['61114'][0]+1e-6),'original-point-coordinate')
def test_physical_patch_role_refused():
 bad(lambda d:d['faces']['2217059']['patch'].update(name='another'),'outer-role')
def test_source_zone_change_refused():
 bad(lambda d:d['faces']['2217059'].update(sourceZone=-1),'outer-role')
def test_point0_rotation_refused():
 bad(lambda d:d['faces']['2217059'].__setitem__('pointIDs',d['faces']['2217059']['pointIDs'][1:]+d['faces']['2217059']['pointIDs'][:1]),'outer-ring-or-point0')
def test_old_internal_point_usage_refused():
 bad(lambda d:d['faces']['2251326']['pointIDs'].__setitem__(2,28142),'old-interior-points-still-used')
def test_incomplete_incidence_refused():
 bad(lambda d:d['cells'][str(d['children'][0])]['faces'].pop(),'incomplete-cell-incidence')
def test_new_apex_outside_kernel_refused():
 bad(lambda d:d['points'].__setitem__('591824',[3.,3.,3.]),'strict-visibility-kernel')
def test_new_point_identity_refused():
 bad(lambda d:d['points'].__setitem__('591825',d['points']['591824']),'new-point-identity')
def test_necessary_bound_no_feasibility_claim():
 a=json.loads((here/'fixed-outer-triangle-one-cone-necessary-bounds.json').read_text())
 assert not a['below001Faces'] and len(a['rows'])==28
 assert a['minimum']>.001
def test_one_bounded_selection_not_new_pool():
 d=json.loads((here/'single-interior-point-star-v1/finite-star-search.json').read_text())
 assert d['evaluations']==36 and d['seconds']<65
 assert not any(r['counts']['D']==0 for r in d['history'])
