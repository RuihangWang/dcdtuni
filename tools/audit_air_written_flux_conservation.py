#!/usr/bin/env python3
"""Read-only volume-flux audit: distinguish cell balance from global conservation.

A pressure solve can make every local cell divergence zero while two processor
copies of the same physical face carry different fluxes. This tool checks the
actual reciprocal coincident coupled geometry before comparing their oriented
surface phi. No field is changed; no solver is invoked; no convergence or product
acceptance is inferred. Transformed/AMI/mapped coupling is unsupported.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np

import audit_air_mesh_geometry as geometry
import audit_air_written_coupled_pairs as pairs
import audit_air_written_fields as fields


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def balance_summary(physical, coupled, cell_sums):
    """All ranks' face accounting identity, with each coupled side included."""
    net_physical=math.fsum(float(x) for x in physical)
    net_coupled=math.fsum(float(x) for x in coupled)
    net_cells=math.fsum(float(x) for x in cell_sums)
    return {
        "physicalBoundaryNetM3s":net_physical,
        "allCoupledCopiesNetM3s":net_coupled,
        "allOwnedCellsNetM3s":net_cells,
        "globalAccountingIdentityErrorM3s":net_cells-net_physical-net_coupled,
        "warning":"Small cell divergence does not establish physical-boundary conservation when coupled copies do not cancel."
    }


def audit_frame(run,time):
    procdirs={int(p.name.removeprefix("processor")):p for p in run.glob("processor[0-9]*")
              if p.is_dir() and p.name.removeprefix("processor").isdigit()}
    if len(procdirs)<2 or sorted(procdirs)!=list(range(len(procdirs))):
        raise ValueError("requires >=2 contiguous actual MPI processor directories")
    meshes={rank:pairs.Mesh(p/"constant/polyMesh") for rank,p in procdirs.items()}
    gs={rank:geometry.Geometry(p/"constant/polyMesh") for rank,p in procdirs.items()}
    phis={};patch_phi={};hashes={};cell_rows=[];physical_rows=[];physical_terms=[];coupled_terms=[];cell_terms=[]
    for rank,proc in sorted(procdirs.items()):
        path=proc/time/"phi";text=path.read_text()
        if not re.search(r"class\s+surfaceScalarField;",text):
            raise ValueError("phi is not surfaceScalarField")
        if not re.search(r"dimensions\s+\[\s*0\s+3\s+-1\s+0\s+0\s+0\s+0\s*\]",text):
            raise ValueError("requires volume phi in m3/s")
        g=gs[rank]
        internal,bc=fields.field(path,len(g.neighbour))
        phi=internal[:,0]
        phis[rank]=phi
        imbalance=np.zeros(g.n_cells)
        np.add.at(imbalance,g.owner[:len(phi)],phi)
        np.add.at(imbalance,g.neighbour,-phi)
        for name,meta in meshes[rank].metadata.items():
            if name not in bc:raise ValueError("missing actual phi patch")
            values=fields.values(bc[name],"value",meta["count"])[:,0]
            patch_phi[rank,name]=values
            np.add.at(imbalance,g.owner[meta["start"]:meta["start"]+meta["count"]],values)
            if meta["type"] in ("processor","processorCyclic","cyclic"):
                coupled_terms.extend(values.tolist())
            elif meta["type"].startswith(("processor","cyclic","mapped")) or "AMI" in meta["type"]:
                raise ValueError("unsupported coupling: "+meta["type"])
            else:
                physical_terms.extend(values.tolist())
                physical_rows.append({"rank":rank,"patch":name,"type":meta["type"],
                    "faces":meta["count"],"netM3s":math.fsum(values),
                    "sumAbsM3s":math.fsum(np.abs(values))})
        cell_terms.extend(imbalance.tolist())
        cell_rows.append({"rank":rank,"cells":g.n_cells,"maxAbsM3s":float(np.max(np.abs(imbalance))),
                          "sumAbsM3s":math.fsum(np.abs(imbalance)),
                          "sumM3s":math.fsum(imbalance)})
        for name in ("points","faces","owner","neighbour","boundary"):
            path=proc/"constant/polyMesh"/name
            hashes[str(path.relative_to(run))]=digest(path)
        hashes[str((proc/time/"phi").relative_to(run))]=digest(proc/time/"phi")
    seen=set();coupled_rows=[]
    for rank,mesh in sorted(meshes.items()):
        for name,meta in mesh.metadata.items():
            if meta["type"] not in ("processor","processorCyclic","cyclic"):continue
            remote,other=pairs.counterpart(rank,name,meshes)
            identity=tuple(sorted(((rank,name),(remote,other))))
            if identity in seen:continue
            seen.add(identity)
            if pairs.counterpart(remote,other,meshes)!=(rank,name):
                raise ValueError("coupled counterpart not reciprocal")
            target=meshes[remote].metadata[other]
            if target["count"]!=meta["count"]:raise ValueError("coupled size differs")
            for i in range(meta["count"]):
                check=pairs.pair_check(mesh.face(meta["start"]+i),meshes[remote].face(target["start"]+i))
                if not all(check[k] for k in ("sameArity","samePolygon","samePoint0","sameStartReverse")):
                    raise ValueError("actual relative-face/point geometry mismatch")
            a=patch_phi[rank,name];b=patch_phi[remote,other];delta=a+b
            samples=[{"relativeFace":int(i),"phiLocalM3s":float(a[i]),
                      "phiRemoteM3s":float(b[i]),"sumM3s":float(delta[i])}
                     for i in np.flatnonzero(delta)]
            coupled_rows.append({"rank":rank,"patch":name,"remoteRank":remote,
                "remotePatch":other,"type":meta["type"],"faces":meta["count"],
                "exactRelativeFaceAndPointMapping":True,
                "nonAntisymmetricFaces":len(samples),"maxAbsPairSumM3s":float(np.max(np.abs(delta))) if len(delta) else 0.,
                "sumAbsPairSumM3s":math.fsum(np.abs(delta)),
                "netPairSumM3s":math.fsum(delta),"mismatchFaces":samples})
    if not coupled_rows:raise ValueError("no supported actual coupled pairs")
    return {"format":"written-mpi-volume-flux-conservation/v1","run":str(run),"time":time,
        "scope":"Read-only frozen phi volume flux, all actual cells/physical patches and geometry-validated coincident coupled copies. No solver/convergence/product acceptance.",
        "sourceSha256":{p.name:digest(p) for p in [Path(__file__),Path(fields.__file__),Path(pairs.__file__),Path(geometry.__file__)]},
        "inputSha256":hashes,"cellBalance":cell_rows,"physicalPatches":physical_rows,"coupledPairs":coupled_rows,
        "summary":{**balance_summary(physical_terms,coupled_terms,cell_terms),
            "cells":sum(g.n_cells for g in gs.values()),
            "coupledPairFaces":sum(r["faces"] for r in coupled_rows),
            "nonAntisymmetricCoupledFaces":sum(r["nonAntisymmetricFaces"] for r in coupled_rows),
            "maxAbsCoupledPairSumM3s":max(r["maxAbsPairSumM3s"] for r in coupled_rows),
            "maxAbsCellNetM3s":max(r["maxAbsM3s"] for r in cell_rows)}}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--time",action="append",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    data={"frames":[audit_frame(args.run.resolve(),time) for time in args.time]}
    args.output.write_text(json.dumps(data,indent=2)+"\n")
    for row in data["frames"]:print(row["time"],json.dumps(row["summary"],indent=2))


if __name__=="__main__":main()
