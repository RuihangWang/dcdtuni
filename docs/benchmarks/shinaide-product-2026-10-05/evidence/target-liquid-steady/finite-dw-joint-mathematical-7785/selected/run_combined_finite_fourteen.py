from pathlib import Path
import sys,json,copy,time
H=Path(__file__).resolve().parent;A=H.parents[1];D=H.parent/'internal-polygon-plane-modes-7785-20261005';R=H.parent/'shared-point-ratio-7785-20261005';N=H.parent/'shared-point-quality-7785-20261005';F=H.parent/'flat-point-quality-7785-20261005';P=H.parent/'shared-point-native-feasibility-20261005'
sys.path[:0]=[str(D),str(F)]
import combine_rank_tagged_motion as C
import audit_internal_polygon_motion as Poly
import audit_writer_point_format as W
V=C.V

def main():
 start=time.monotonic();chosen=[]
 for name,f in [('interior9',.95),('mirror9',.95),('flat11',1)]:
  x=json.loads((H/f'{name}-result.json').read_text());r=next(r for r in x['results'] if r['fraction']==f)
  chosen.append({'input':x,'result':r,'fraction':f})
 left=json.loads((P/'finite-rank1-ratio-result.json').read_text());lr=next(r for r in left['results'] if r['fraction']==.95)
 ap=json.loads((P/'finite-rank3-ratio-result.json').read_text());ar=next(r for r in ap['results'] if r['fraction']==.95)
 right=json.loads((R/'combined-eleven-point-audit.json').read_text());old3=right['oldDocument'];new3=copy.deepcopy(old3)
 for entry in chosen:
  for p in entry['input']['movingPoints']:new3['points'][str(p)]=entry['result']['candidateDocument']['points'][str(p)]
 for p in ap['movingPoints']:new3['points'][str(p)]=ar['candidateDocument']['points'][str(p)]
 new3['originalApex']=new3['points'][str(new3['apexWrittenPoint'])]
 old1=left['oldDocument'];new1=copy.deepcopy(lr['candidateDocument']);bindings=[];formats=[];components=[]
 for rank,old,new,moving in [(1,old1,new1,left['movingPoints']),(3,old3,new3,right['movingPoints'])]:
  M=A.parent/'integration/.codex-tmp/cases/Shinaide_MeshAbort_CUA/Mesh';g,t,b=V.raw_binding(old,moving,M,rank,A/'.codex-tmp/installed-writer-cua-7785c33f/case-file-manifest.json');bindings.append(b)
  pts,form=W.write_binary_points_copy(g.poly_dir/'points',H/f'combined-rank{rank}-finite-points.binary-format-copy',{p:new['points'][str(p)] for p in moving});formats.append(form)
  for p in new['points']:new['points'][p]=pts[int(p)].tolist()
  new['originalApex']=new['points'][str(new['apexWrittenPoint'])]
  audit=Poly.authorize_internal_polygon(old,new,moving) if rank==1 else C.Flat.authorize_flat(old,new,moving,physical_points=right['physicalPoints'])
  gap=V.global_gap(g,new,moving)
  components.append({'rank':rank,'audit':audit,'gap':gap,'gapPass':all(r['exceedsWriter1MicrometreAnd3MicrometreSafety'] for r in gap),'exactFractionCoreVM':W.exact_core_moments(old)==W.exact_core_moments(new)})
 old=C.combine(C.relabel(old1,1),C.relabel(old3,3));new=C.combine(C.relabel(new1,1),C.relabel(new3,3))
 moving=[p+C.STRIDE for p in left['movingPoints']]+[p+3*C.STRIDE for p in right['movingPoints']]
 physical=[p+3*C.STRIDE for p in right['physicalPoints']];poly=[p+C.STRIDE for p in left['movingPoints'][1:]]
 audit=C.authorize(old,new,moving,physical,poly)
 out={'format':'actual7785-finite-direction-full-fourteen-point-audit/v1','oldDocument':old,'candidateDocument':new,'movingPoints':moving,'physicalPoints':physical,'internalPolygonPoints':poly,'jointAudit':audit,'fullRankComponentGuards':components,'sourceBindings':bindings,'binaryFormatCopies':formats,'exactFractionCoreVM':W.exact_core_moments(old)==W.exact_core_moments(new),'allPassingMargins':C.all_passing_margin(audit['newActualMetrics']),'selectedFractions':{'interior9':.95,'mirror9':.95,'flat11':1,'leftR':.95,'rightR':.95},'noPreviousOptimizerCoordinatesUsed':True,'nativeAcceptance':False,'elapsedSeconds':time.monotonic()-start,'inputSHA256':{str(p):V.sha(p) for p in [H/'interior9-result.json',H/'mirror9-result.json',H/'flat11-result.json',P/'finite-rank1-ratio-result.json',P/'finite-rank3-ratio-result.json']}}
 out['jointMathAccepted']=audit['mathematicalLocalCandidateAccepted'] and out['exactFractionCoreVM'] and all(r['audit']['mathematicalLocalCandidateAccepted'] and r['gapPass'] and r['exactFractionCoreVM'] for r in components)
 (H/'combined-finite-fourteen-audit.json').write_text(json.dumps(out,indent=2)+'\n')
 print(json.dumps({'accepted':out['jointMathAccepted'],'old':audit['oldCounts'],'new':audit['newCounts'],'core':len(old['children']),'outside':len(old['outsideCells']),'Nmax':audit['maximumWarningDegrees'],'VM':out['exactFractionCoreVM'],'elapsed':out['elapsedSeconds'],'margins':out['allPassingMargins']}),flush=True)
if __name__=='__main__':main()
