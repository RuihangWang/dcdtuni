from pathlib import Path
import json,copy,struct
import pytest
here=Path(__file__).parent
ns={'__file__':str(here/'audit_joint.py'),'__name__':'test_certify'}
exec(compile((here/'audit_joint.py').read_text().split('shape=certify(old,new)')[0],str(here/'audit_joint.py'),'exec'),ns)
certify,old,new=[ns[k]for k in ['certify','old','new']]
def mutation(change,match):
 d=copy.deepcopy(new);change(d)
 with pytest.raises(AssertionError,match=match):certify(old,d)
def test_actual_whole_star_and_Fraction_contract():
 r=certify(old,new);assert len(r['literalOuterFaces'])==38 and len(r['allOutsideFractionShapeExact'])==69
 assert r['movedOriginalPoints']==[28144] and r['FractionCavityVAndFirstMomentExact']
def test_typed_authoritative_refusal_is_not_hidden_by_lower_count():
 a=json.loads((here/'installed-joint-audit.json').read_text())
 assert not a['qualifiedCandidate'];assert [len(a['metrics']['D'][k])for k in ['oldBad','newBad']]==[5,4]
 assert len(a['newCellsInheritReplacedPassingDRefusals'])==2
 assert all(r['D']<.001 and r['inheritsPassingDOrigins']for r in a['newCellsInheritReplacedPassingDRefusals'])
def test_warning_and_hard_metric_contract_distinct():
 a=json.loads((here/'installed-joint-audit.json').read_text());m=a['metrics']
 assert [len(m['N'][k])for k in ['oldBad','newBad']]==[4,6]
 assert m['N']['newMax']<m['N']['oldMax']
 for k in ['D','weight','ratio','skew']:assert not m[k]['retainedPassingRoleRegressions']
def test_real_installed_geometry_is_valid_even_while_D_refused():
 a=json.loads((here/'installed-joint-audit.json').read_text())
 assert a['actualInstalledStockConcavityAnyCompleteCell']==[False,False]
 assert a['minimumNewCellPyramidVolume']>0
 assert a['minimumNewSharedAnchorTetQuality']>a['officialTetQualityFloor']
def test_physical_role_change_refused():
 mutation(lambda d:d['faces']['2217059'].update(sourceZone=-1),'outer-role')
def test_physical_patch_change_refused():
 mutation(lambda d:d['faces']['2217059']['patch'].update(name='other'),'outer-role')
def test_physical_coordinate_move_refused():
 mutation(lambda d:d['points']['61114'].__setitem__(0,d['points']['61114'][0]+1e-6),'non-approved-point')
def test_point0_rotation_refused():
 mutation(lambda d:d['faces']['2217059'].__setitem__('pointIDs',d['faces']['2217059']['pointIDs'][1:]+d['faces']['2217059']['pointIDs'][:1]),'outer-ring')
def test_extra_original_internal_point_move_refused():
 mutation(lambda d:d['points']['28142'].__setitem__(1,d['points']['28142'][1]+1e-6),'non-approved-point')
def test_incomplete_cell_incidence_refused():
 mutation(lambda d:d['cells'][str(d['children'][0])]['faces'].pop(),'incomplete-cell-incidence')
def test_wrong_outside_endpoint_refused():
 def change(d):
  f=next(f for f in ns['top']['vertexStarExteriorFaces']if old['faces'][str(f)]['neighbour']is not None)
  r=d['faces'][str(f)];c=r['owner']if r['owner']not in d['children']else r['neighbour'];replacement=next(c0 for c0 in d['outsideCells']if c0!=c)
  for k in ['owner','neighbour']:
   if r[k]==c:r[k]=replacement
 mutation(change,'incomplete-cell-incidence|outside-endpoint')
def test_new_point_identity_refused():
 def change(d):d['points']['591825']=d['points'].pop('591824')
 mutation(change,'new-point-identity')
def test_fixed_outer_necessary_bound_is_not_feasibility_claim():
 a=json.loads((here/'fixed-outer-triangle-one-cone-necessary-bounds.json').read_text())
 assert a['minimum']>.001 and not a['below001Faces']
 assert 'Not a bound for polycells' in a['scope']
def test_initial_passing_regression_run_preserved_as_refusal():
 a=json.loads((here/'joint-six-dimensional-v1/search-report.json').read_text());b=json.loads((here/'joint-six-dimensional-v2/search-report.json').read_text())
 assert a['best']['counts']['D']>a['original']['counts']['D']
 assert b['best']['originalRetainedPassingHardRolesPreserved']
 assert b['requests']<=256
def test_history_replay_has_no_new_qualified_proposal():
 a=json.loads((here/'history-lineage-replay/replay.json').read_text());assert not a['allNewPassCandidateRequests']
def test_typed_points_differ_for_baseline_and_candidate():
 t=(here/'typed-joint-v2/actual_fixture.H').read_text();s=(here/'typed-joint-v2/installed_kernel.cpp').read_text()
 assert 'actualPoints' in t and 'candidatePoints' in t
 assert 'merged?candidatePoints:actualPoints' in s
