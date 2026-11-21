from pathlib import Path
import json,hashlib,signal,time
R=Path(__file__).resolve().parent;parent=R.parent/'source-plane-independent-review-20261007';ns={};exec((parent/'REPLAY.py').read_text().split('# Independent Foam quoted-string decoder:')[0],ns);point,plane,moments,sum_moments=tuple(ns[n] for n in ['point','plane','moments','sum_moments']);start=time.monotonic()
cpp=[json.loads(line) for line in (R/'cpp/run.stdout').read_text().splitlines() if line.startswith('{')];actual={r['source']:r for r in cpp if r['kind']=='authentic-helper'};assert set(actual)=={3579,3526} and all(x['pass'] for x in actual.values())
records=json.loads((ns['F']/'logs/ON.geometry.json').read_text());g=next(e for r in records for e in r['events'] if e['kind']=='group' and e['sourceFacet']==3579);source=[point(x) for x in g['outlineBits']];normal,offset,axis=plane(source);targetA,targetM=moments(source)
old=actual[3579]['fcBits'];new=actual[3579]['fanBits'];assert all(old[k]==new[k] for k in range(3) if k!=axis);assert new[axis]==g['outlineBits'][0][axis]
oldTiles=[];newTiles=[];count=0
for f in g['facets']:
 oldTiles.append([point(v) for v in f['worldBits']]);tile=[]
 for v in f['worldBits']:
  if v==old:v=new;count+=1
  tile.append(point(v))
 newTiles.append(tile)
assert count==6
oldA,oldM=sum_moments(oldTiles);newA,newM=sum_moments(newTiles);assert newA==targetA and newM==targetM and oldA==targetA and oldM!=targetM
assert all(ns['dot'](normal,p)==offset for tile in newTiles for p in tile)
assert all(tuple(a[k] for k in range(3) if k!=axis)==tuple(b[k] for k in range(3) if k!=axis) for aa,bb in zip(oldTiles,newTiles) for a,b in zip(aa,bb))
prior=json.loads((parent/'INDEPENDENT-two-group-coverage-volume.json').read_text());proof=next(x for x in prior['groups'] if x['sourceFacet']==3579);assert proof['allProjectedTriangleVerticesInTarget'] and proof['all15PairsProjectedInteriorDisjoint'] and proof['sumProjectedAreasExact']
a={'scope':'Mathematical copy of six actual observed triangles using independently executed actual C++ fanCentre; not an applied current mesh or source grant','actualCppResultSHA256':hashlib.sha256((R/'cpp/run.stdout').read_bytes()).hexdigest(),'replacedCentroidOccurrences':count,'originalBits':old,'candidateBits':new,'tangentialBitsAndEveryProjectedTileUnchanged':True,'projectedInside15PairDisjointAreaProofRetainedFromIndependentParent':True,'allVerticesOnOriginal3DPlane':True,'allThreeAreaComponentsExactEqual':newA==targetA,'allNineM1ComponentsExactEqual':newM==targetM,'originalAllNineM1ComponentsEqual':oldM==targetM,'deltaNewArea':[newA[k]-targetA[k] for k in range(3)],'deltaNewM1':[[newM[k][j]-targetM[k][j] for j in range(3)] for k in range(3)],'source3526Scope':'Two observed incident tiles only; no complete source3526 footprint/M1 claim','seconds':time.monotonic()-start};(R/'INDEPENDENT-cpp-bound-source3579-footprint.json').write_text(json.dumps(ns['enc'](a),indent=2)+'\n');signal.alarm(0);print(json.dumps(ns['enc'](a),indent=2))
