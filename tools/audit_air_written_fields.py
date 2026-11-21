#!/usr/bin/env python3
"""Read a frozen MPI field frame; never invoke or change an OpenFOAM solver.

Reports kinematic pressures, thermal ranges, boundary volume-flow signs and
per-cell volume-flux imbalance from phi. Boundary phi*T is only the advective
thermal moment; it omits conductive heat and cannot establish an energy balance.
Currently accepts ASCII field output, and binary/ASCII mesh labels.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from backend.aircooling.runner.polymesh_io import read_label_list, read_owner_note

def labels(path):
    try:return read_label_list(path)
    except ValueError:
        text=path.read_text()
        if not re.search(r"format\s+ascii;",text[:2048]):raise
        body=text.split("// * * *",1)[-1]
        compact=re.search(r"(?:^|\n)\s*(\d+)\s*\{\s*(-?\d+)\s*\}",body)
        if compact:return np.full(int(compact[1]),int(compact[2]),dtype=np.int64)
        empty=re.search(r"(?:^|\n)\s*0\s*\(\s*\)",body)
        if empty:return np.empty(0,dtype=np.int64)
        raise

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def blocks(text):
    result={}
    position=0
    pattern=re.compile(r"([^\s{}()]+)\s*\{")
    while match:=pattern.search(text,position):
        depth=1
        cursor=match.end()
        while depth and cursor<len(text):
            if text[cursor]=="{":depth+=1
            elif text[cursor]=="}":depth-=1
            cursor+=1
        if depth:raise ValueError("unterminated patch dictionary")
        result[match[1].strip(chr(34))]=text[match.end():cursor-1]
        position=cursor
    return result

def values(body, key, count, components=1):
    if count == 0:return np.empty((0,components))
    match=re.search(r"\b"+key+r"\s+(uniform|nonuniform)\s+",body)
    if not match:
        raise ValueError(f"missing explicit {key}")
    tail=body[match.end():]
    if count == 0:
        return np.empty((0,components))
    if match[1]=="uniform":
        data=np.fromstring(tail.split(";",1)[0].replace("("," ").replace(")"," "),sep=" ")
        if data.size!=components:raise ValueError("bad uniform field width")
        if not np.isfinite(data).all():raise ValueError("nonfinite uniform field")
        return np.broadcast_to(data,(count,components)).copy().reshape(count,-1)
    item=re.match(r"List<\w+>\s+(\d+)\s*\(",tail)
    if not item:raise ValueError("bad nonuniform declaration")
    if int(item[1])!=count:raise ValueError("field size does not match mesh")
    data=np.fromstring(tail[item.end():].split(";",1)[0].replace("("," ").replace(")"," "),sep=" ")
    if data.size!=count*components:raise ValueError("field payload size mismatch")
    if not np.isfinite(data).all():raise ValueError("nonfinite field values")
    return data.reshape(count,components)

def field(path, n_cells, components=1):
    text=path.read_text()
    if not re.search(r"format\s+ascii;",text[:2048]):raise ValueError("requires ASCII written fields")
    head, boundary=text.split("boundaryField",1)
    return values(head,"internalField",n_cells,components), blocks(boundary)

def audit(run, time):
    regions={}
    patches={}
    hashes={}
    global_fields={"cells":0,"T_K":[],"p_rgh_m2s2":[],"p_m2s2":[],"speed_ms":[]}
    cell_imbalance=[]
    processors=sorted(run.glob("processor[0-9]*"))
    if not processors:raise ValueError("no MPI processor directories in run")
    missing_labels=[str(proc/"constant/cellToRegion") for proc in processors
                    if not (proc/"constant/cellToRegion").exists()]
    labels_available=not missing_labels
    for proc in processors:
        poly=proc/"constant/polyMesh"
        region_labels=labels(proc/"constant/cellToRegion") if labels_available else None
        owner=labels(poly/"owner")
        neighbour=labels(poly/"neighbour")
        boundary=blocks((poly/"boundary").read_text().split("// * * *",1)[-1])
        n=region_labels.size if region_labels is not None else (read_owner_note(poly/"owner").get("nCells") or int(owner.max()+1))
        phi,pb=field(proc/time/"phi",neighbour.size)
        phi=phi[:,0]
        temperature,tb=field(proc/time/"T",n)
        prgh,_=field(proc/time/"p_rgh",n)
        pressure,_=field(proc/time/"p",n)
        velocity,_=field(proc/time/"U",n,3)
        speed=np.linalg.norm(velocity,axis=1)
        global_fields["cells"]+=n
        for key,data in [("T_K",temperature[:,0]),("p_rgh_m2s2",prgh[:,0]),("p_m2s2",pressure[:,0]),("speed_ms",speed)]:
            global_fields[key].append([float(data.min()),float(data.max()),float(data.sum()),n])
        divergence=np.bincount(owner[:neighbour.size],weights=phi,minlength=n)
        divergence-=np.bincount(neighbour,weights=phi,minlength=n)
        for rid in np.unique(region_labels) if region_labels is not None else []:
            mask=region_labels==rid
            item=regions.setdefault(str(int(rid)),{"cells":0,"T_K":[],"p_rgh_m2s2":[],"p_m2s2":[],"speed_ms":[],"allBoundaryVolumeFlowM3s":0.,"physicalBoundaryVolumeFlowM3s":0.})
            count=int(mask.sum());item["cells"]+=count
            for key,data in [("T_K",temperature[:,0]),("p_rgh_m2s2",prgh[:,0]),("p_m2s2",pressure[:,0]),("speed_ms",speed)]:
                v=data[mask]
                item[key].append([float(v.min()),float(v.max()),float(v.sum()),count])
        for name,body in boundary.items():
            start=re.search(r"startFace\s+(\d+);",body)
            count=re.search(r"nFaces\s+(\d+);",body)
            if not start or not count:continue
            start,count=int(start[1]),int(count[1])
            kind=re.search(r"type\s+(\w+);",body)[1]
            boundary_phi=values(pb[name],"value",count)[:,0]
            np.add.at(divergence,owner[start:start+count],boundary_phi)
            coupled=kind.startswith("processor") or kind in ("cyclic","cyclicAMI","cyclicACMI")
            patch_regions=region_labels[owner[start:start+count]] if region_labels is not None else None
            for rid in np.unique(patch_regions) if patch_regions is not None else []:
                mask=patch_regions==rid
                net=float(boundary_phi[mask].sum())
                regions[str(int(rid))]["allBoundaryVolumeFlowM3s"]+=net
                if not coupled:regions[str(int(rid))]["physicalBoundaryVolumeFlowM3s"]+=net
            if coupled:continue
            explicit_temp=bool(re.search(r"\bvalue\s+(uniform|nonuniform)\s+",tb.get(name,"")))
            temp=values(tb[name],"value",count)[:,0] if explicit_temp else None
            part=patches.setdefault(name,{"type":kind,"faces":0,"netVolumeFlowM3s":0.,"absoluteVolumeFlowM3s":0.,"advectivePhiT_KM3s":0.,"TminK":None,"TmaxK":None})
            part["faces"]+=count
            part["netVolumeFlowM3s"]+=float(boundary_phi.sum())
            part["absoluteVolumeFlowM3s"]+=float(np.abs(boundary_phi).sum())
            if temp is not None and part["advectivePhiT_KM3s"] is not None:
                part["advectivePhiT_KM3s"]+=float(np.dot(boundary_phi,temp))
            else:
                part["advectivePhiT_KM3s"]=None
                part["explicitBoundaryTemperatureAvailable"]=False
            if count and temp is not None:
                lo,hi=float(temp.min()),float(temp.max())
                part["TminK"]=lo if part["TminK"] is None else min(lo,part["TminK"])
                part["TmaxK"]=hi if part["TmaxK"] is None else max(hi,part["TmaxK"])
        cell_imbalance.append({"processor":proc.name,"maxAbsM3s":float(np.abs(divergence).max()),"sumAbsM3s":float(np.abs(divergence).sum()),"sumM3s":float(divergence.sum())})
        for name in ["T","U","p","p_rgh","phi"]:
            path=proc/time/name;hashes[str(path.relative_to(run))]=digest(path)
    for item in [global_fields,*regions.values()]:
        for key in ["T_K","p_rgh_m2s2","p_m2s2","speed_ms"]:
            rows=item[key]
            item[key]={"min":min(x[0] for x in rows),"max":max(x[1] for x in rows),"cellMean":sum(x[2] for x in rows)/sum(x[3] for x in rows)}
    return {"run":str(run),"time":time,"scope":"written snapshot, not a convergence certificate; kinematic pressure units; advective thermal moment excludes conduction",
            "regionLabelsAvailable":labels_available,"missingRegionLabelPaths":missing_labels,
            "regionLabelSource":"checkMesh cellToRegion (point-connected mesh regions; pressure coupling may join these in other cases)" if labels_available else "unavailable; no region identity inferred",
            "wholeMesh":global_fields,"regions":regions,"physicalPatches":patches,"cellVolumeFluxImbalance":cell_imbalance,"fieldSha256":hashes}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--time",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    args.output.write_text(json.dumps(audit(args.run.resolve(),args.time),indent=2)+"\n")

if __name__=="__main__":main()
