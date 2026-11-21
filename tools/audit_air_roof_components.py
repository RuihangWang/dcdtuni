#!/usr/bin/env python3
"""Read-only compatible-roof component and frozen-boundary counterexamples."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_air_mesh_geometry import Geometry,sha
import audit_air_transition_roof as roof

def packet(lo,widths,axis=0,sign=1):
    lo=np.asarray(lo,float);widths=np.asarray(widths,float)
    u=(axis+1)%3;v=(axis+2)%3;plane=lo[axis]+(widths[axis] if sign>0 else 0)
    rows=[]
    for i in range(3):
        for j in range(3):
            fraction=(.5 if i==1 else 0)+(.5 if j==1 else 0)
            if not fraction:continue
            point=lo.copy();point[axis]=plane;point[u]+=.5*i*widths[u];point[v]+=.5*j*widths[v]
            delta=np.zeros(3);delta[axis]=sign*5e-4*widths[axis]*fraction
            rows.append({"point":point.tolist(),"delta":delta.tolist()})
    return rows

def shared(a,b):
    pa={tuple(row["point"]):np.asarray(row["delta"]) for row in a}
    pb={tuple(row["point"]):np.asarray(row["delta"]) for row in b}
    return [{"point":list(p),"firstDelta":pa[p].tolist(),"secondDelta":pb[p].tolist(),
             "exactDeltaEqual":bool(np.array_equal(pa[p],pb[p])),
             "deltaDifferenceNorm":float(np.linalg.norm(pa[p]-pb[p]))} for p in sorted(pa.keys()&pb.keys())]

def fixed_border_rank(n):
    size=(n+1)**2;rows=[]
    def index(i,j):return i*(n+1)+j
    for i in range(n):
        for j in range(n):
            row=np.zeros(size)
            for di,dj,sign in [(0,0,1),(1,1,1),(1,0,-1),(0,1,-1)]:
                row[index(i+di,j+dj)]=sign
            rows.append(row)
    for i in range(n+1):
        for j in range(n+1):
            if i in (0,n) or j in (0,n):
                row=np.zeros(size);row[index(i,j)]=1;rows.append(row)
    matrix=np.asarray(rows)
    rank=int(np.linalg.matrix_rank(matrix,tol=1e-10))
    return {"fineQuadDimension":n,"vertices":size,"constraints":len(rows),"rank":rank,"nullity":size-rank,
            "definition":"Each planar axis-aligned projected fine quad requires d00+d11-d10-d01=0; every outer-border vertex is fixed0. All matrix entries are integer0/+1/-1."}

def clone(g,points):
    result=Geometry.from_arrays(points,[g.ids(f).tolist() for f in range(g.n_faces)])
    for name in ["owner","neighbour","cell_faces","cell_offsets"]:
        setattr(result,name,getattr(g,name).copy())
    result.n_cells=g.n_cells;result.n_faces=g.n_faces
    return result

def mesh_summary(g):
    cells=[g.classify(c) for c in range(g.n_cells)]
    bad=[]
    for f in range(g.n_faces):
        area,fc,plane,*_=g.face(f)
        if plane>1e-12:
            bad.append({"face":f,"owner":int(g.owner[f]),"neighbour":int(g.neighbour[f]) if f<len(g.neighbour) else None,
              "points":g.points[g.ids(f)].tolist(),"planarityErrorM":plane,"areaVector":area.tolist(),"faceCentre":fc.tolist()})
    return {"points":len(g.points),"faces":g.n_faces,"cells":g.n_cells,
      "nonplanarFaceCountAt1e-12m":len(bad),"maximumFacePlanarityErrorM":max(g.face(f)[2] for f in range(g.n_faces)),
      "cellsFailingStrictFaceCentreCriterion":sum(sum(c["checkerTriggerCounts"].values())>0 for c in cells),
      "cellsWithNonplanarPolygon":sum(not c["allFacePolygonsPlanar"] for c in cells),
      "minimumCellPyramidVolumeM3":min(c["minimumCentroidPyramidVolumeM3"] for c in cells),
      "totalVolumeM3":sum(c["volumeM3"] for c in cells),
      "nonplanarFaceExamples":bad[:12],"cellRows":cells}

def evaluate():
    air=roof.load_air(Path(__file__).with_name("air_transition_roof_probe.py"))
    audit=Path(__file__).with_name("audit_air_mesh_geometry.py")
    flat,_=roof.evaluate_buffer(air,audit,0)
    full,_=roof.evaluate_buffer(air,audit,5e-4)
    assert len(flat.points)==len(full.points) and np.array_equal(flat.vertices,full.vertices)
    delta=full.points-flat.points
    centre=(np.abs(flat.points[:,0]-1)<1e-14)&(flat.points[:,1]>=0)&(flat.points[:,1]<=1)&(flat.points[:,2]>=0)&(flat.points[:,2]<=1)
    island=flat.points.copy();island[centre]+=delta[centre]
    outer=(flat.points[:,1]==-1)|(flat.points[:,1]==2)|(flat.points[:,2]==-1)|(flat.points[:,2]==2)
    clamp=full.points.copy();clamp[outer]=flat.points[outer]
    grid=np.zeros((7,7));clamped_grid=np.zeros((7,7))
    interface=np.flatnonzero(np.abs(flat.points[:,0]-1)<1e-14)
    for p in interface:
        i=int(round(2*(flat.points[p,1]+1)));j=int(round(2*(flat.points[p,2]+1)))
        grid[i,j]=delta[p,0];clamped_grid[i,j]=clamp[p,0]-flat.points[p,0]
    differences=np.diff(np.diff(clamped_grid,axis=0),axis=1)
    return {"format":"air-roof-component-counterexamples/v1",
      "scope":"Pure original54-cell mathematical fixture and finite-difference proof. No production eligibility or native mesh acceptance. No source/mesh/binary mutation or checker/solver execution.",
      "sourceSha256":{str(p):sha(p) for p in [Path(__file__),Path(roof.__file__),Path(__file__).with_name("air_transition_roof_probe.py")]},
      "samePlaneSharedEdgeMidpoint":shared(packet([0,0,0],[1,1,1]),packet([0,1,0],[1,1,1])),
      "orthogonalNormalConflict":shared(packet([0,1,0],[1,1,1],0),packet([1,0,0],[1,1,1],1)),
      "normalExtentConflict":shared(packet([0,0,0],[1,1,1]),packet([-.1,1,0],[1.1,1,1])),
      "allNineIdealCompatiblePackets":mesh_summary(full),
      "onlyCentralPacketEntireStar":mesh_summary(clone(flat,island)),
      "allNinePacketsWithOuterBorderFixed":mesh_summary(clone(flat,clamp)),
      "roofNormalHeightGridUnclamped":grid.tolist(),"roofNormalHeightGridOuterZero":clamped_grid.tolist(),
      "unclampedMaximumMixedDifferenceM":float(np.max(np.abs(np.diff(np.diff(grid,axis=0),axis=1)))),
      "clampedMaximumMixedDifferenceM":float(np.max(np.abs(differences))),
      "integerConstraintRanks":[fixed_border_rank(n) for n in [2,4,6]],
      "boundaryTheorem":"Every planar fine quad imposes Delta_uDelta_v d=0, hence d(i,j)=a(i)+b(j) on a complete connected rectangular grid. If every outer boundary point has displacement0, a and b are constant and d=0 throughout. A nonzero fixed-outline roof island cannot keep all original quadrilateral planes. An actual component not forming this rectangular frozen-border arrangement is not covered.",
      "limits":["Local same-plane/extent agreement is necessary, not sufficient for union-star planarity or strict quality.",
       "Protected border points, noncandidate neighboring tiles, unequal normal extent and orthogonal normal proposals must be represented, not silently ignored.",
       "The full nine-packet fixture moves border vertices; it is not eligible under production protected-point rules merely because its math is sound.",
       "No conclusion that every actual Tri/target component has a frozen rectangular border; actual exported component/star data is still required."]}
def main():
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    a.output.write_text(json.dumps(evaluate(),indent=2)+"\n")
if __name__=="__main__":main()
