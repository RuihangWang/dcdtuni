from pathlib import Path
import sys,json,copy,time
import numpy as np
H=Path(__file__).resolve().parent;A=H.parents[1];D=H.parent/'internal-polygon-plane-modes-7785-20261005';P=H.parent/'shared-point-components-7785-20261005';F=H.parent/'flat-point-quality-7785-20261005';R=H.parent/'shared-point-ratio-7785-20261005'
sys.path[:0]=[str(H),str(D),str(F)]
import compact_formula_evaluator as E
import finite_direction_proposal as S
import flat_support as Flat
import audit_writer_point_format as W
V=Flat.V

def main():
 summary=[]
 for name,path in [('interior9',P/'cluster1-joint-audit.json'),('mirror9',P/'cluster2-joint-audit.json'),('flat11',F/'flat-count-proxy-audit.json')]:
  start=time.monotonic();input=json.loads(path.read_text());old=input['oldDocument'];moving=input['movingPoints'];physical=input.get('physicalPoints',[])
  basis=np.eye(3*len(moving))
  if physical:
   # Actual complete physical support plane fixes z; do not use optimizer coordinates.
   fixed=[]
   for j,p in enumerate(moving):
    if p in physical:
     incident=[r for r in old['faces'].values() if p in r['pointIDs'] and r['patch'] is not None]
     axes=[a for a in range(3) if all(len({V.struct.pack('<d',float(old['points'][str(q)][a])) for q in r['pointIDs']})==1 for r in incident)] if hasattr(V,'struct') else []
     if not axes:
      import struct
      axes=[a for a in range(3) if all(len({struct.pack('<d',float(old['points'][str(q)][a])) for q in r['pointIDs']})==1 for r in incident)]
     if len(axes)!=1:raise ValueError('exact common physical coordinate plane required')
     fixed.append(3*j+axes[0])
   basis=np.delete(basis,fixed,axis=1)
  valid=(lambda old,new,move:Flat.validate(old,new,move,physical)) if physical else V.validate_structure
  ev=E.evaluator(old,moving,structural_validator=valid,motion_basis=basis)
  proposal=S.proposal(ev,objective_kind='count',wall_seconds=15,max_evaluations=256)
  g,t,binding=V.raw_binding(old,moving,A.parent/'integration/.codex-tmp/cases/Shinaide_MeshAbort_CUA/Mesh',3,A/'.codex-tmp/installed-writer-cua-7785c33f/case-file-manifest.json')
  results=[]
  for f in [1,.95,.9,.5,.25]:
   cand,_,_=ev['actual'](proposal['parameters']*f)
   audit=Flat.authorize_flat(old,cand,moving,physical_points=physical) if physical else V.authorize(old,cand,moving)
   results.append({'fraction':f,'candidateDocument':cand,'audit':audit,'coreFractionVolumeMomentExact':W.exact_core_moments(old)==W.exact_core_moments(cand)})
   print(json.dumps({'name':name,'fraction':f,'accepted':audit['mathematicalLocalCandidateAccepted'],'old':audit['oldCounts'],'new':audit['newCounts'],'Nmax':audit['maximumWarningDegrees']}),flush=True)
  out={'name':name,'dimension':basis.shape[1],'movingPoints':moving,'physicalPoints':physical,'motionBasis':basis.tolist(),'oldDocument':old,'proposal':{**proposal,'parameters':proposal['parameters'].tolist()},'results':results,'actualSourceBinding':binding,'inputSHA256':V.sha(path),'noPreviousOptimizerCoordinatesUsed':True,'nativeAcceptance':False,'elapsedSeconds':time.monotonic()-start}
  (H/f'{name}-result.json').write_text(json.dumps(out,indent=2)+'\n')
  summary.append({k:out[k] for k in ['name','dimension','elapsedSeconds']});summary[-1]['requests']=proposal['evaluations'];summary[-1]['proposalSeconds']=proposal['elapsedSeconds'];summary[-1]['stop']=proposal['stop']
 (H/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
if __name__=='__main__':main()
