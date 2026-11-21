"""Read-only physical patch area comparison, including processorCyclic referPatch."""
from pathlib import Path
import json,re,sys,numpy as np
from audit_written_mesh import Mesh

def areas(case):
    paths=sorted(case.glob('processor*/constant/polyMesh')) or [case/'constant/polyMesh']
    patches={}
    for path in paths:
        mesh=Mesh(path);vertices=mesh.points[mesh.vertices]
        next_ids=np.arange(len(mesh.vertices))+1
        next_ids[mesh.offsets[1:]-1]=mesh.offsets[:-1]
        cross=np.cross(vertices,mesh.points[mesh.vertices[next_ids]])
        face_areas=np.linalg.norm(np.add.reduceat(cross,mesh.offsets[:-1],axis=0)*.5,axis=1)
        refs={}
        for name,body in re.findall(r"(?m)^\s*([^\s{}()]+)\s*\n\s*\{([^{}]+)\}",(path/'boundary').read_text()):
            ref=re.search(r'referPatch\s+([^;]+);',body)
            if ref:refs[name]=ref.group(1).strip()
        for start,count,name,kind in mesh.patches:
            if kind=='processor':continue
            if kind=='processorCyclic':name=refs[name]
            patches[name]=patches.get(name,0.)+float(np.sum(face_areas[start:start+count]))
    return patches

a,b=map(lambda x:Path(x).resolve(),sys.argv[1:3]);first=areas(a);second=areas(b)
names=set(first)|set(second);deltas={n:second.get(n,0)-first.get(n,0) for n in names}
result={'firstMesh':str(a),'secondMesh':str(b),'firstPatchAreas_m2':first,'secondPatchAreas_m2':second,'maxPhysicalPatchAreaAbsDifference_m2':max(abs(v) for v in deltas.values()),'differentPatchNames':sorted(set(first)^set(second)),'areaDifferencesOver1e-7':{n:v for n,v in deltas.items() if abs(v)>1e-7},'method':'sum written polygon vector area magnitude; processorCyclic faces attributed to their physical referPatch'}
print(json.dumps(result,indent=2))
