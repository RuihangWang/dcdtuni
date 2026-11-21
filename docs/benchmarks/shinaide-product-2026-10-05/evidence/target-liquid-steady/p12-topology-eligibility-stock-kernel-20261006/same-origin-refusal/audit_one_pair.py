from pathlib import Path
import json,hashlib,sys,copy,itertools
from fractions import Fraction as F
import numpy as np
B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni')
A=B/'.codex-tmp/goal-20261005/air'; I=B/'.codex-tmp/goal-20261005/integration'
P=I/'.codex-tmp/p12-D-eligibility-audit-20261005'
here=Path(__file__).parent
m=json.loads((I/'.codex-tmp/finite-best-hard-checkpoint-native-20261005/full-case-raw-manifest.json').read_text())
case=Path(m['case']);bindings={}
def checked(rel,src=None):
    p=(case/rel if src is None else src); b=p.read_bytes(); h=hashlib.sha256(b).hexdigest()
    assert h==m['files'][rel]['sha256'],(rel,h)
    bindings[rel]={'path':str(p),'bytes':len(b),'sha256':h,'P12ManifestExact':True}
    return b
poly=here/'poly';poly.mkdir()
for name in ['faces','owner','neighbour','boundary']:
    rel='Mesh/processor0/constant/polyMesh/'+name
    src=P/'selected'/rel
    checked(rel,src);(poly/name).symlink_to(src)
(poly/'points').write_bytes(checked('Mesh/processor0/constant/polyMesh/points'))
sys.path.insert(0,str(I/'tools'))
import audit_air_mesh_geometry as g
import audit_air_cone_small_union_independent as u
geo=g.Geometry(poly);a,b=19189,538794;group={a,b}
parents=np.frombuffer(checked('Mesh/conforming-written-rank0-cell-parent.i32',P/'selected/Mesh/conforming-written-rank0-cell-parent.i32'),dtype='<i4')
native=np.frombuffer(checked('Mesh/conforming-written-rank0-cell-native.i32',P/'selected/Mesh/conforming-written-rank0-cell-native.i32'),dtype='<i4')
bases=np.frombuffer(checked('Mesh/conforming-written-rank0-face-base.i32'),dtype='<i4')
zones=np.frombuffer(checked('Mesh/conforming-source-rank0-zone.i32'),dtype='<i4')
def neighbors(c):
    return {int(geo.owner[f] if geo.owner[f]!=c else geo.neighbour[f]) for f in geo.cell(c)[0] if f<len(geo.neighbour)}
first=group|neighbors(a)|neighbors(b)
second=first|set().union(*(neighbors(c) for c in first))
allfaces=sorted(set().union(*(set(map(int,geo.cell(c)[0])) for c in second)))
pointids=sorted(set().union(*(set(map(int,geo.ids(f))) for f in allfaces)))
faces={str(f):{'pointIDs':list(map(int,geo.ids(f))),'owner':int(geo.owner[f]),
              'neighbour':int(geo.neighbour[f]) if f<len(geo.neighbour) else None,
              'patch':geo.patch(f),'sourceBase':int(bases[f]),
              'sourceZone':int(zones[bases[f]]) if bases[f]>=0 else -1} for f in allfaces}
cells={str(c):{'faces':list(map(int,geo.cell(c)[0])),'sourceParent':int(parents[c]),
              'writtenCentre':geo.cell(c)[3].tolist(),'writtenVolume':geo.cell(c)[4],
              'nativeCell':int(native[c])} for c in sorted(second)}
shared=sorted(set(cells[str(a)]['faces'])&set(cells[str(b)]['faces']))
assert len(shared)==1
apex=faces[str(shared[0])]['pointIDs'][0]
doc={'job':'52d0e89c','rank':0,'children':[a,b],'outsideCells':sorted(second-group),
     'points':{str(p):geo.points[p].tolist() for p in pointids},'faces':faces,'cells':cells,
     'apexWrittenPoint':apex,'originalApex':geo.points[apex].tolist(),'core':[a,b],
     'firstHalo':sorted(first-group),'secondHalo':sorted(second-first),'sharedFace':shared[0],
     'role':'fixed actual remaining same-parent pair; source trace for witness only, never native authorization'}
candidate,removed,outer=u.union_document(doc,group)
st=u.WrittenStencil(candidate,candidate['originalApex'])
metric=st.cell_detail(min(group))
def determinant(p,q,r,s):
    x=[[F(float(v)) for v in vec] for vec in [p,q,r,s]]
    a=[x[1][i]-x[0][i] for i in range(3)]
    b=[x[2][i]-x[0][i] for i in range(3)]
    c=[x[3][i]-x[0][i] for i in range(3)]
    return a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
pairs=[]
for f,k in itertools.permutations(outer,2):
    sf,fc=st.face(f); sf=sf if st.faces[f]['owner']==min(group) else -sf
    kc=st.face(k)[1]; delta=kc-fc; cosine=float(np.dot(sf,delta)/(np.linalg.norm(sf)*np.linalg.norm(delta)))
    if cosine>-1e-6:
        ids=list(dict.fromkeys(st.faces[f]['pointIDs']+st.faces[k]['pointIDs']))
        pts=[st.points[p] for p in ids]
        exact=all(determinant(*[pts[i] for i in comb])==0 for comb in itertools.combinations(range(len(pts)),4))
        basef=st.faces[f]['sourceBase'];basek=st.faces[k]['sourceBase']
        outside=lambda z: st.faces[z]['neighbour'] if st.faces[z]['owner']==min(group) else st.faces[z]['owner']
        pairs.append({'face':f,'otherFace':k,'unitDot':cosine,'sourceBase':[basef,basek],
                      'sameNonnegativeSourceBase':basef>=0 and basef==basek,'exactBinaryCoplanar':exact,
                      'outsideCells':[outside(f),outside(k)],'differentOutsideCells':outside(f)!=outside(k)})
# Extract immutable source outlines for each nonnegative exterior base.
source={}
for suffix in ['points.f64','face-offsets.u64','face-points.i32','patch.i32','owner.i32','neighbour.i32']:
    source[suffix]=checked('Mesh/conforming-source-rank0-'+suffix)
sp=np.frombuffer(source['points.f64'],dtype='<f8').reshape(-1,3)
so=np.frombuffer(source['face-offsets.u64'],dtype='<u8');sv=np.frombuffer(source['face-points.i32'],dtype='<i4')
ss={}
for base in sorted(set(faces[str(f)]['sourceBase'] for f in outer)-{-1}):
    ids=sv[int(so[base]):int(so[base+1])];ss[str(base)]={'pointIDs':ids.tolist(),'coordinates':sp[ids].tolist(),
    'sourcePatch':int(np.frombuffer(source['patch.i32'],dtype='<i4')[base]),'sourceZone':int(zones[base]),
    'owner':int(np.frombuffer(source['owner.i32'],dtype='<i4')[base]),'neighbour':int(np.frombuffer(source['neighbour.i32'],dtype='<i4')[base])}
report={'scope':'One actual P12 pair. Python geometry reconstruction, not native/API or final installed acceptance.',
        'pair':[a,b],'nativePair':[int(native[a]),int(native[b])],'normalParentTraceWitness':[int(parents[a]),int(parents[b])],
        'sharedFace':shared[0],'outer':outer,'coreCells':2,'completeStoredCells':len(second),
        'completeStoredFaces':len(allfaces),'completeStoredPoints':len(pointids),
        'oldCells':[u.WrittenStencil(doc,doc['originalApex']).cell_detail(c) for c in [a,b]],
        'candidateCell':metric,'faceCentreTriggerWitness':pairs,
        'sourceOriginalFacets':ss,'mathematicalPlaneTiesAllowingConvexityDoesNotImplyStockPass':True,
        'nativeSourceAuthorizationNotInferredFromTrace':True,'inputBindings':bindings,
        'readerSHA256':g.sha(Path(g.__file__)),'unionOracleSHA256':g.sha(Path(u.__file__))}
for name,obj in [('pair-input.json',doc),('pair-geometry-report.json',report)]:
    (here/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
print(json.dumps({'pair':report['pair'],'nativePair':report['nativePair'],'oldD':[v['D'] for v in report['oldCells']],
  'newD':metric['D'],'triggers':pairs,'newGeom':metric,'out':str(here)},indent=2))
