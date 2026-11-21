import importlib.util, json, sys, hashlib
from pathlib import Path
import numpy as np
sys.path.insert(0,'src')
from backend.aircooling.runner.polymesh_io import read_label_list
path=Path('docs/benchmarks/air-product-presets-2026-10-05/audit_written_mesh.py')
spec=importlib.util.spec_from_file_location('audit',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
case=Path('.codex-tmp/strict-stage-baseline/Tri_angle_6b82d2c4/Mesh')
for rank in range(8):
    root=case/f'processor{rank}/constant/polyMesh'
    mesh=mod.Mesh(root)
    raw=(root/'sets/concaveCells').read_text();import re
    hit=re.search(r'\n(\d+)\n\(\s*([0-9\s]+)\)',raw);ids=np.array([int(x) for x in hit.group(2).split()]);assert len(ids)==int(hit.group(1))
    for c in ids[:300]:
        c=int(c);fs,centre,volume,lo,hi=mesh.cell(c)
        vertices=np.unique(np.concatenate([mesh.poly(f) for f in fs]));pts=mesh.points[vertices]
        violations=[];max_vertex_outside=-1e30
        for f in fs:
            area,fc=mesh.geo(f);n=area/np.linalg.norm(area)
            if int(mesh.owners[f])!=c:n=-n
            max_vertex_outside=max(max_vertex_outside,float(np.max((pts-fc)@n)))
            for g in fs:
                if g==f:continue
                vector=mesh.geo(g)[1]-fc;dot=float(vector@n/max(np.linalg.norm(vector),1e-300))
                if dot>-1e-6:violations.append({'face':int(f),'otherFace':int(g),'unitDot':dot,'normal':n.tolist(),'faceCentre':fc.tolist(),'otherCentre':mesh.geo(g)[1].tolist()})
        if violations and max_vertex_outside<1e-10 and volume>1e-6:
            obj={'format':'planar-convex-checkmesh-evidence/v1','checkerSource':'primitiveMeshCheck.C lines 43, 810-900','planarCosAngle':1e-6,'sourceMesh':str(case),'rank':rank,'cell':c,'cellVolume':volume,'maximumVertexOutsideFacePlaneMetres':max_vertex_outside,'mathematicallyConvexFromAllFaceHalfspaces':True,'checkerConcavityTriggers':violations,'cell':mesh.summary(c),'vertexCoordinates':pts.tolist(),'inputSha256':{str(q.relative_to(case)):hashlib.sha256(q.read_bytes()).hexdigest() for q in root.glob('*') if q.is_file()}}
            Path('docs/benchmarks/air-product-presets-2026-10-05/strict-planar-convex-example.json').write_text(json.dumps(obj,indent=2)+'\n')
            print(json.dumps({'rank':rank,'cell':c,'volume':volume,'maxOutside':max_vertex_outside,'triggers':len(violations)}));sys.exit(0)
raise RuntimeError('no complete convex example in scanned subset')
