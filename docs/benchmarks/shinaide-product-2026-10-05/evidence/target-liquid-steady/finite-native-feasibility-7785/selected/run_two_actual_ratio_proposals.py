from pathlib import Path
import sys,json,copy,time
import numpy as np
H=Path(__file__).resolve().parent;A=H.parents[1];D=H.parent/'internal-polygon-plane-modes-7785-20261005';R=H.parent/'shared-point-ratio-7785-20261005';F=H.parent/'flat-point-quality-7785-20261005'
sys.path[:0]=[str(H),str(D),str(F)]
import compact_formula_evaluator as E
import finite_direction_proposal as S
import audit_internal_polygon_motion as C
import audit_writer_point_format as W
V=C.V

def main():
 outputs=[]
 for rank,path in [(1,D/'targeted-ratio-internal-edge-audit.json'),(3,R/'rank3-bounded-ap-only.json')]:
  old=json.loads(path.read_text());doc=old['oldDocument'];moving=old['movingPoints'];basis=old['proposal']['motionBasis'] if rank==1 else np.eye(3).tolist()
  _,oq=V.actual_metrics(doc);targets={r['face'] for r in oq['faces'] if r.get('ratio',1)<.01}
  valid=C.validate_internal_polygon if rank==1 else V.validate_structure
  ev=E.evaluator(doc,moving,structural_validator=valid,motion_basis=basis,target_faces=targets)
  proposal=S.proposal(ev,targets);results=[]
  g,t,binding=V.raw_binding(doc,moving,A.parent/'integration/.codex-tmp/cases/Shinaide_MeshAbort_CUA/Mesh',rank,A/'.codex-tmp/installed-writer-cua-7785c33f/case-file-manifest.json')
  for fraction in [1,.95,.9,.5]:
   cand,_,_=ev['actual'](proposal['parameters']*fraction)
   auditor=C.authorize_internal_polygon if rank==1 else V.authorize
   audit=auditor(doc,cand,moving);gap=V.global_gap(g,cand,moving)
   vm=W.exact_core_moments(doc)==W.exact_core_moments(cand)
   audit['globalRankPointClearance']=gap
   fmt=None;roundaudit=None
   if fraction==.95:
    points,fmt=W.write_binary_points_copy(g.poly_dir/'points',H/f'finite-rank{rank}-fraction{fraction}-points.binary-format-copy',{p:cand['points'][str(p)] for p in moving})
    read=copy.deepcopy(cand)
    for p in read['points']:read['points'][p]=points[int(p)].tolist()
    read['originalApex']=read['points'][str(read['apexWrittenPoint'])]
    roundaudit=auditor(doc,read,moving)
   results.append({'fraction':fraction,'candidateDocument':cand,'audit':audit,'coreFractionVolumeMomentExact':vm,'globalGapPass':all(r['exceedsWriter1MicrometreAnd3MicrometreSafety'] for r in gap),'binaryFormatCopy':fmt,'roundtripAudit':roundaudit,'allPassingMargins':W.margin_summary(audit['oldActualMetrics'],audit['newActualMetrics'])})
   print(json.dumps({'rank':rank,'fraction':fraction,'accepted':audit['mathematicalLocalCandidateAccepted'],'old':audit['oldCounts'],'new':audit['newCounts'],'roundtripAccepted':None if roundaudit is None else roundaudit['mathematicalLocalCandidateAccepted'],'VM':vm}),flush=True)
  out={'rank':rank,'oldDocument':doc,'movingPoints':moving,'motionBasis':basis,'proposal':{**proposal,'parameters':proposal['parameters'].tolist()},'results':results,'sourceBinding':binding,'inputSHA256':V.sha(path),'noPreviousOptimizerCoordinatesUsed':True,'nativeAcceptance':False}
  (H/f'finite-rank{rank}-ratio-result.json').write_text(json.dumps(out,indent=2)+'\n');outputs.append({'rank':rank,'proposalSeconds':proposal['elapsedSeconds'],'evaluations':proposal['evaluations'],'stop':proposal['stop']})
 (H/'two-actual-summary.json').write_text(json.dumps(outputs,indent=2)+'\n')
if __name__=='__main__':main()
