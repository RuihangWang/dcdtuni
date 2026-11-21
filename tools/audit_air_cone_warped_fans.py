#!/usr/bin/env python3
"""Enumerate original-vertex fan area/moment changes for saved warped internal bases."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
from audit_air_mesh_geometry import sha
from audit_air_conforming_cones import canonical_cycle

def audit(face):
    x=np.asarray(face["points"]);area=np.asarray(face["originalArea"])
    centre=np.asarray(face["originalCentre"]);cycle=canonical_cycle(list(range(len(x))),x)
    span=float(np.linalg.norm(x-centre,axis=1).max());rows=[]
    for start in range(len(x)):
        sequence=cycle[start:]+cycle[:start]
        total=np.zeros(3);moment=np.zeros(3);amount=0;pieces=[];own_v=0;nei_v=0
        for j in range(1,len(x)-1):
            ids=[sequence[0],sequence[j],sequence[j+1]];q=x[ids]
            sf=.5*np.cross(q[1]-q[0],q[2]-q[0])
            if sf@area<0:sf=-sf
            magnitude=float(np.linalg.norm(sf));cc=q.mean(axis=0)
            total+=sf;moment+=magnitude*cc;amount+=magnitude
            own_v+=sf@(cc-face["ownerApex"])/3
            nei_v+=(-sf)@(cc-face["neighbourApex"])/3
            pieces.append({"originalVertexIds":ids,"areaM2":magnitude,
                           "signedProjectedAreaM2":float(sf@area/np.linalg.norm(area))})
        fc=moment/amount
        arerr=float(np.linalg.norm(total-area)/np.linalg.norm(area))
        unsigned=abs(amount-float(np.linalg.norm(area)))/float(np.linalg.norm(area))
        fcrel=float(np.linalg.norm(fc-centre)/span)
        rows.append({"canonicalStart":start,"anchorOriginalVertex":sequence[0],"pieces":pieces,
          "hasZeroAreaTriangle":any(p["areaM2"]==0 for p in pieces),
          "relativeAreaVectorError":arerr,"relativeUnsignedAreaError":unsigned,
          "areaWeightedTriangleCentre":fc.tolist(),"centreErrorM":float(np.linalg.norm(fc-centre)),
          "relativeCentreError":fcrel,"passesRecordedAreaMomentGuard1e_10":arerr<=1e-10 and unsigned<=1e-10 and fcrel<=1e-10,
          "summedOwnerBasePyramidVolumeM3":float(own_v),"originalOwnerBasePyramidVolumeM3":float(area@(centre-face["ownerApex"])/3),
          "summedNeighbourBasePyramidVolumeM3":float(nei_v),"originalNeighbourBasePyramidVolumeM3":float((-area)@(centre-face["neighbourApex"])/3)})
    eligible=[v for v in rows if not v["hasZeroAreaTriangle"] and v["relativeUnsignedAreaError"]<=1e-10]
    return {"nativeFace":face["face"],"reportedPlaneDeviationM":face["maxOffPlane"],"spanM":span,
      "canonicalCycleOriginalVertexIds":cycle,"fans":rows,
      "bestNonzeroAreaAndUnsignedAreaPreservingFanCentreRelativeError":min(v["relativeCentreError"] for v in eligible),
      "anyNonzeroFanPassingAreaMomentGuard":any(not v["hasZeroAreaTriangle"] and v["passesRecordedAreaMomentGuard1e_10"] for v in rows)}

def main():
    p=argparse.ArgumentParser();p.add_argument("--input-dir",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();paths=sorted(a.input_dir.glob("conforming-cone-preflight-rank*.json"))
    rows=[audit(f) for path in paths for f in json.loads(path.read_text())["badBaseFaces"]]
    out={"format":"warped-internal-original-vertex-fan-audit/v1",
      "scope":"Read-only mathematical guard diagnosis, no native execution or mesh mutation.",
      "sourceSha256":sha(Path(__file__)),"canonicalHelperSha256":sha(Path(__file__).with_name("audit_air_conforming_cones.py")),
      "inputFilesSha256":{path.name:sha(path) for path in paths},"faces":rows,
      "limits":["Only original-vertex anchors are enumerated; other possible subdivisions are not ruled out.",
                "Zero-area triangles are not accepted and preservation of area alone is insufficient.",
                "No new split geometry, source coordinate projection, matrix threshold or solver acceptance is authorized.",
                "Existing seam plane quantization is being corrected separately at its source; this diagnostic does not implement a warped-fan bypass."]}
    a.output.write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps([{"face":v["nativeFace"],"bestFCRelativeError":v["bestNonzeroAreaAndUnsignedAreaPreservingFanCentreRelativeError"],
          "anyFanPassing":v["anyNonzeroFanPassingAreaMomentGuard"]} for v in rows]))
if __name__=="__main__":main()
