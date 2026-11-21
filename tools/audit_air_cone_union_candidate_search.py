#!/usr/bin/env python3
"""Deterministic pure-math candidate search on a trace-bound compact stencil."""
from __future__ import annotations
import argparse,hashlib,json,sys,time
from pathlib import Path
import numpy as np
from scipy.optimize import differential_evolution
from audit_air_cone_candidate_independent import Stencil,evaluate

def score(doc,apex,local):
    m=Stencil(doc,apex)
    children=(doc["repartition"]["replacementCells"] if "repartition" in doc else doc["auditReplacementCells"]) if local else doc["children"]
    faces=sorted({f for c in children for f in m.cells[c]["faces"]})
    ds=[];ws=[];rs=[];ss=[];ns=[]
    for c in children:
        areas,fc,cc,V=m.cell(c)
        a=np.array([sf for f,sf in zip(m.cells[c]["faces"],areas)
                    if m.faces[f]["patch"] is None or m.faces[f]["patch"]["type"] in {"cyclic","processor","processorCyclic"}])
        avg=np.linalg.norm(a,axis=1).mean()
        ds.append(abs(np.linalg.det((a/avg).T@(a/avg)))/8)
    for f in faces:
        row=m.faces[f];sf,fc=m.face(f);oc=m.cell(row["owner"])[2];ov=m.cell(row["owner"])[3]
        nc=m.cell(row["neighbour"]) if row["neighbour"] is not None else None
        n=sf/np.linalg.norm(sf)
        if nc is not None:
            do=abs(np.dot(sf,fc-oc));dn=abs(np.dot(sf,nc[2]-fc))
            ws.append(min(do,dn)/(do+dn));rs.append(min(ov,nc[3])/max(ov,nc[3]))
            d=nc[2]-oc;factor=.2
            ns.append(np.degrees(np.arccos(np.clip(np.dot(n,d)/np.linalg.norm(d),-1,1))))
        else:d=n*np.dot(n,fc-oc);factor=.4
        cpf=fc-oc;sv=cpf-(np.dot(sf,cpf)/(np.dot(sf,d)+1e-150))*d;vhat=sv/(np.linalg.norm(sv)+1e-150)
        fd=max(factor*np.linalg.norm(d)+1e-150,float(abs((m.polygon(f)-fc)@vhat).max()))
        ss.append(np.linalg.norm(sv)/fd)
    D,W,R,S,N=min(ds),min(ws),min(rs),max(ss),max(ns)
    return {"minimumD":float(D),"minimumWeight":float(W),"minimumRatio":float(R),"maximumSkew":float(S),
            "maximumNonOrthDegrees":float(N),"score":float(min(D/.001,W/.05,R/.01,4/S,70/N))}

def search(doc,local,iterations=45):
    original=doc["parentOriginal"]
    normals=np.array([f["outwardArea"] for f in original["originalFaces"]])
    normals/=np.linalg.norm(normals,axis=1)[:,None]
    fc=np.array([f["OFcentre"] for f in original["originalFaces"]]);b=(normals*fc).sum(axis=1)
    lo=np.array(original["bbox"][0])+3.1e-6;hi=np.array(original["bbox"][1])-3.1e-6
    calls=[0];start=time.monotonic()
    def loss(x):
        calls[0]+=1
        if min(b-normals@x)<=3e-6:return 1e6
        return -np.log(max(score(doc,x,local)["score"],1e-300))
    result=differential_evolution(loss,list(zip(lo,hi)),seed=20261005,popsize=7,
                                 maxiter=iterations,tol=.001,polish=False,workers=1)
    return {"optimizedScope":"replacementCellsAndTheirFaces" if local else "allParentChildrenAndTheirFaces",
            "candidate":result.x.tolist(),"local":score(doc,result.x,True),"fullParent":score(doc,result.x,False),
            "evaluations":calls[0],"elapsedSeconds":time.monotonic()-start,
            "optimizerSuccess":bool(result.success),"optimizerMessage":str(result.message),
            "minimumKernelSlackM":float(min(b-normals@result.x)),
            "fullEvaluation":evaluate(doc,result.x)}

def main():
    p=argparse.ArgumentParser();p.add_argument("--input",type=Path,action="append",required=True)
    p.add_argument("--output",type=Path,required=True);p.add_argument("--iterations",type=int,default=45);args=p.parse_args()
    rows=[]
    for path in args.input:
        doc=json.loads(path.read_text())
        row=search(doc,True,args.iterations);row["parent"]=doc["parentOriginal"]["inputParent"]
        row["input"]=path.name;row["inputSHA256"]=hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(row)
        print(json.dumps({k:v for k,v in row.items() if k!="fullEvaluation"}),flush=True)
    args.output.write_text(json.dumps({"toolSHA256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          "scope":"Pure mathematical diagnostic; no native mesh or API/browser operation. Five quality thresholds and strict kernel preserved. Entire parent may retain unrelated failures.",
          "optimizer":{"seed":20261005,"populationPerDimension":7,"iterations":args.iterations,"workers":1,
                       "polish":False,"thresholds":{"D":.001,"weight":.05,"ratio":.01,"skew":4,"nonOrthDegrees":70}},
          "candidates":rows},indent=2)+"\n")
if __name__=="__main__":main()
