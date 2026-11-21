#!/usr/bin/env python3
"""Offline startup-cause clue; never an end-to-end momentum reproduction.

For missing ordinary processor U caches only, compare the standard v2412 Euler
ddt correction computed with local-owner extrapolation versus actual remote U.
Coincident reciprocal face geometry is checked by the volume-flux auditor.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import audit_air_written_flux_conservation as flux


def standard_euler_correction(phi, interpolated_volume_flux, dt):
    if not np.isfinite(dt) or dt <= 0:
        raise ValueError("positive finite dt required")
    correction = phi - interpolated_volume_flux
    coefficient = 1 - min(abs(correction)/(abs(phi)+1e-15), 1)
    return coefficient*correction/dt


def audit(run, first_time, dt):
    initial = flux.audit_frame(run, "0")
    written = flux.audit_frame(run, first_time)
    if re.search(r"ddtPhiCoeff", (run/"system/fvSchemes").read_text()):
        raise ValueError("only the standard default Euler ddtPhiCoeff is supported")
    if not re.search(r"ddtSchemes\s*\{\s*default\s+Euler\s*;", (run/"system/fvSchemes").read_text()):
        raise ValueError("requires default Euler")
    if any(row["type"] != "processor" for row in initial["coupledPairs"]):
        raise ValueError("only ordinary coincident processor coupling is supported")
    ranks = [row["rank"] for row in initial["cellBalance"]]
    gs = {i:flux.geometry.Geometry(run/f"processor{i}/constant/polyMesh") for i in ranks}
    meshes = {i:flux.pairs.Mesh(run/f"processor{i}/constant/polyMesh") for i in ranks}
    velocity = {i:flux.fields.field(run/f"processor{i}/0/U", gs[i].n_cells, 3) for i in ranks}
    volume_flux = {i:flux.fields.field(run/f"processor{i}/0/phi", len(gs[i].neighbour)) for i in ranks}
    hashes = {f"processor{i}/0/U":flux.digest(run/f"processor{i}/0/U") for i in ranks}
    pairs = []
    for pair in initial["coupledPairs"]:
        a,pa,b,pb = pair["rank"],pair["patch"],pair["remoteRank"],pair["remotePatch"]
        sides = [(a,pa,b,pb),(b,pb,a,pa)]
        for rank,name,_,_ in sides:
            if re.search(r"\bvalue\b", velocity[rank][1][name]):
                raise ValueError("the local-extrapolation hypothesis requires missing processor value")
        rows = []
        for j in range(pair["faces"]):
            old = []; remote = []
            for i,p,other,po in sides:
                f = meshes[i].metadata[p]["start"]+j
                ff = meshes[other].metadata[po]["start"]+j
                sf,fc,*_ = gs[i].face(f)
                sf_remote,fc_remote,*_ = gs[other].face(ff)
                normal = sf/np.linalg.norm(sf)
                remote_normal = sf_remote/np.linalg.norm(sf_remote)
                own_cell = int(gs[i].owner[f]); neighbour_cell = int(gs[other].owner[ff])
                own_centre = gs[i].cell(own_cell)[3]
                neighbour_centre = gs[other].cell(neighbour_cell)[3]
                # Installed processorFvPatch::makeWeights, identity transform:
                # neighbour normal distance / (own + neighbour normal distance).
                d_own = float(np.dot(normal,fc-own_centre))
                d_nei = float(np.dot(remote_normal,fc_remote-neighbour_centre))
                if d_own <= 0 or d_nei <= 0:
                    raise ValueError("positive owner/neighbour face-normal distances required")
                weight = d_nei/(d_own+d_nei)
                own_u = velocity[i][0][own_cell]
                remote_u = velocity[other][0][neighbour_cell]
                phi = flux.fields.values(volume_flux[i][1][p], "value", pair["faces"])[j,0]
                old.append(standard_euler_correction(phi,float(np.dot(sf,own_u)),dt))
                remote.append(standard_euler_correction(
                    phi,float(np.dot(sf,weight*own_u+(1-weight)*remote_u)),dt))
            rows.append({"relativeFace":j,"localExtrapolationPairDdtSumM3s2":float(sum(old)),
                         "remoteCachePairDdtSumM3s2":float(sum(remote))})
        observed = next(row for row in written["coupledPairs"]
                        if (row["rank"],row["patch"]) == (a,pa))
        old_support = [row["relativeFace"] for row in rows if row["localExtrapolationPairDdtSumM3s2"] != 0]
        actual_support = [row["relativeFace"] for row in observed["mismatchFaces"]]
        pairs.append({"rank":a,"patch":pa,"remoteRank":b,"remotePatch":pb,"faces":pair["faces"],
                      "predictedNonzeroSupport":old_support,"writtenPhiMismatchSupport":actual_support,
                      "supportExactlyMatches":old_support == actual_support,
                      "remoteCachePairDdtMaxAbsM3s2":max(abs(row["remoteCachePairDdtSumM3s2"]) for row in rows),
                      "rows":rows})
    return {"format":"startup-euler-support-clue/v1","run":str(run),"firstTime":first_time,"dtS":dt,
            "scope":"Standard Euler startup correction only. Support coincidence is not independent reproduction of the full momentum operator or corrected phi amplitudes.",
            "initialFluxInputSha256":initial["inputSha256"],"initialVelocitySha256":hashes,
            "firstWrittenFluxInputSha256":written["inputSha256"],"pairs":pairs,
            "sourceSha256":{p.name:flux.digest(p) for p in (Path(__file__),Path(flux.__file__),Path(flux.geometry.__file__),Path(flux.fields.__file__),Path(flux.pairs.__file__))}}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--first-time",default="0.01")
    parser.add_argument("--dt",type=float,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    data=audit(args.run.resolve(),args.first_time,args.dt)
    args.output.write_text(json.dumps(data,indent=2)+"\n")
    for row in data["pairs"]:
        print(row["rank"],row["patch"],len(row["predictedNonzeroSupport"]),
              row["supportExactlyMatches"],row["remoteCachePairDdtMaxAbsM3s2"])


if __name__=="__main__":
    main()
