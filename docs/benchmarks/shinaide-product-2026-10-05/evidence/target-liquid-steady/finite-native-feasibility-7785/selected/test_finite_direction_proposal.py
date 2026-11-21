from pathlib import Path
import sys,json,ast
import numpy as np
H=Path(__file__).resolve().parent;sys.path.insert(0,str(H))
import finite_direction_proposal as S

def test_projection_halfspace():
 d,ok,n=S.project(np.array([-2.,3.]),np.array([[1.,0.],[0.,-1.]]),np.array([0.,1.]))
 assert ok and np.allclose(d,[0.,1.]) and n<=384

def test_zero_gradient_infeasible_row_refused():
 d,ok,n=S.project(np.ones(2),np.zeros((1,2)),np.array([-1.]))
 assert not ok and n==0

def test_insufficient_projection_cap_refused():
 d,ok,n=S.project(np.array([-2.,-2.]),np.eye(2),np.zeros(2),updates=1)
 assert not ok and n<=1

def test_projection_deterministic():
 aa=np.array([[1.,0.],[0.,1.],[1.,1.]]);bb=np.array([0.,0.,-1.]);a=S.project(np.array([-.1,-.2]),aa,bb);b=S.project(np.array([-.1,-.2]),aa,bb)
 assert np.array_equal(a[0].view(np.uint64),b[0].view(np.uint64)) and a[1:]==b[1:]

def toy():
 return {'dimension':1,'q0':np.zeros(2),'faceIDs':[1],
         'fast':lambda q:(None,None,np.array([.001+.0001*q[0]])),
         'constraints':lambda q:np.array([1+q[0],1-q[0]])}

def test_every_evaluation_obeys_cap():
 out=S.proposal(toy(),{1},max_evaluations=4)
 assert out['evaluations']==4 and out['stop']=='evaluation cap'

def test_empty_target_refused():
 import pytest
 with pytest.raises(ValueError):S.proposal(toy(),set())

def test_actual_two_ratios_from_original_not_optimizer_coordinates():
 for rank in [1,3]:
  row=json.loads((H/f'finite-rank{rank}-ratio-result.json').read_text())
  assert row['noPreviousOptimizerCoordinatesUsed'] and not row['nativeAcceptance']
  assert row['proposal']['evaluations']<=256
  r=next(x for x in row['results'] if x['fraction']==.95)
  assert r['audit']['mathematicalLocalCandidateAccepted'] and r['roundtripAudit']['mathematicalLocalCandidateAccepted']
  assert r['audit']['oldCounts']['ratio']==1 and r['audit']['newCounts']['ratio']==0
  assert r['audit']['oldCounts']['D']==r['audit']['newCounts']['D']
  assert r['audit']['oldCounts']['weight']==r['audit']['newCounts']['weight']
  assert r['coreFractionVolumeMomentExact'] and r['globalGapPass']

def test_original_long_run_endpoint_rejected():
 row=json.loads((H/'finite-projection-result.json').read_text());r=next(x for x in row['results'] if x['fraction']==1)
 assert not r['audit']['mathematicalLocalCandidateAccepted'] and r['audit']['newCounts']['D']>r['audit']['oldCounts']['D']

def test_no_optimizer_import_or_call():
 for name in ['finite_direction_proposal.py','compact_formula_evaluator.py']:
  tree=ast.parse((H/name).read_text())
  imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
  assert not any(x and 'scipy.optimize' in x for x in imports)
  assert not any(isinstance(n,ast.Name) and n.id in {'minimize','linprog','SLSQP'} for n in ast.walk(tree))


def test_actual_small_modes_derived_without_case_labels():
 import select_small_mode as M
 for rank,target in [(1,1468973),(3,1512587)]:
  raw=json.loads((H/f'finite-rank{rank}-ratio-result.json').read_text());choice=M.select(raw['oldDocument'],target)
  assert choice['movingPoints']==raw['movingPoints']
  assert choice['motionBasis']==raw['motionBasis']
  assert choice['allInputsFromCurrentTopology']

def test_pair_rejects_unequal_plane_and_missing_zone_identity():
 import select_small_mode as M
 import copy
 raw=json.loads((H/'finite-rank1-ratio-result.json').read_text());doc=raw['oldDocument'];pair=raw['movingPoints'][1:]
 bad=copy.deepcopy(doc);bad['points'][str(pair[0])][1]=float(np.nextafter(bad['points'][str(pair[0])][1],1))
 assert not M.qualifies_pair_axis(bad,pair,1)[0]
 bad=copy.deepcopy(doc);f=next(f for f in bad['faces'].values() if pair[0] in f['pointIDs']);f.pop('sourceZone')
 assert not M.qualifies_pair_axis(bad,pair,1)[0]


def test_joint_four_point_recomputed_not_counts_added():
 row=json.loads((H/'combined-four-point-two-ratio-audit.json').read_text());a=row['jointAudit']
 assert a['mathematicalLocalCandidateAccepted'] and row['exactFractionCoreVolumeMoment']
 assert a['oldCounts']['ratio']==2 and a['newCounts']['ratio']==0
 assert a['oldCounts']['D']==a['newCounts']['D'] and a['oldCounts']['weight']==a['newCounts']['weight']
 assert a['maximumWarningDegrees']<=88.2022 and not row['nativeAcceptance']
