from pathlib import Path
import json,ast
H=Path(__file__).resolve().parent

def test_three_finite_component_positives():
 for name,f in [('interior9',.95),('mirror9',.95),('flat11',1)]:
  d=json.loads((H/f'{name}-result.json').read_text());r=next(r for r in d['results'] if r['fraction']==f)
  assert d['proposal']['evaluations']==256 and d['proposal']['stop']=='evaluation cap'
  assert d['noPreviousOptimizerCoordinatesUsed'] and not d['nativeAcceptance']
  assert r['audit']['mathematicalLocalCandidateAccepted'] and r['coreFractionVolumeMomentExact']
  assert r['audit']['newCounts']['D']<r['audit']['oldCounts']['D']

def test_proposal_endpoint_or_backtrack_not_implicitly_authorized():
 for name in ['interior9','mirror9']:
  d=json.loads((H/f'{name}-result.json').read_text());r=next(r for r in d['results'] if r['fraction']==1)
  assert not r['audit']['mathematicalLocalCandidateAccepted'] and not r['audit']['hardOriginalPassingRoleGuard']
 d=json.loads((H/'flat11-result.json').read_text());r=next(r for r in d['results'] if r['fraction']==.95)
 assert not r['audit']['mathematicalLocalCandidateAccepted'] and not r['audit']['lineage']['weight']['worstFailedSeverityDoesNotIncrease']

def test_all_fourteen_recomputed_geometry_and_overlap():
 d=json.loads((H/'combined-finite-fourteen-audit.json').read_text());a=d['jointAudit']
 assert d['jointMathAccepted'] and d['exactFractionCoreVM'] and d['noPreviousOptimizerCoordinatesUsed']
 assert len(d['oldDocument']['children'])==373 and len(d['oldDocument']['outsideCells'])==271
 assert a['oldCounts']['D']==47 and a['newCounts']['D']==38
 assert a['oldCounts']['weight']==32 and a['newCounts']['weight']==23
 assert a['oldCounts']['ratio']==2 and a['newCounts']['ratio']==0
 assert a['hardOriginalPassingRoleGuard'] and a['cellGeometryGuard'] and a['faceGeometryGuard']
 assert all(r['audit']['mathematicalLocalCandidateAccepted'] and r['gapPass'] and r['exactFractionCoreVM'] for r in d['fullRankComponentGuards'])
 assert not d['nativeAcceptance']

def test_physical_flat_is_separate_footprint_contract():
 d=json.loads((H/'combined-finite-fourteen-audit.json').read_text());a=d['jointAudit']
 assert not a['unchangedShellOrderedWorldCoordinatesAndRoles']
 assert len(d['physicalPoints'])==1 and len(d['internalPolygonPoints'])==2
 assert a['faceGeometryGuard'] and a['cellGeometryGuard']

def test_binary64_margins_and_warnings():
 d=json.loads((H/'combined-finite-fourteen-audit.json').read_text())
 assert len(d['binaryFormatCopies'])==2
 assert all(r['absoluteMargin']>0 for r in d['allPassingMargins'].values())
 assert d['jointAudit']['warningLabelExchangeAllowed'] and d['jointAudit']['maximumWarningDegrees']<=88.2022

def test_no_optimizer_import_or_call():
 for name in ['finite_direction_proposal.py','compact_formula_evaluator.py']:
  tree=ast.parse((H/name).read_text())
  assert not any(isinstance(n,ast.ImportFrom) and n.module and 'scipy.optimize' in n.module for n in ast.walk(tree))
  assert not any(isinstance(n,ast.Name) and n.id in {'minimize','linprog','SLSQP'} for n in ast.walk(tree))
