"""L0 passages from actual production-exported port polygons and rigid positions.

Effective capture/bypass area fractions below are unfitted declared LF model
parameters, not measured apertures or HF validation. Layout derivatives use
rigid translation of the same actual exported ports. No synthetic STL exists.
"""
from __future__ import annotations
import json,re,hashlib
from pathlib import Path
from dataclasses import dataclass
import numpy as np
import torch
from .l0_network import AirNetwork

def stl_ports(folder,manifest):
    solids={}
    for entry in manifest["geometryExport"]["stlFiles"].values():
        path=folder/entry["path"]
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry["contentHash"]:raise ValueError("production STL SHA differs")
        text=path.read_text()
        for match in re.finditer(r"^solid ([^\n]+)\n(.*?)^endsolid",text,re.M|re.S):
            vertices=[list(map(float,v.split())) for v in re.findall(r"^\s*vertex (.+)$",match.group(2),re.M)]
            triangles=np.asarray(vertices).reshape(-1,3,3)
            cross=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
            areas=np.linalg.norm(cross,axis=1)/2;area=areas.sum()
            if area<=0:raise ValueError("zero area actual port")
            center=np.sum(triangles.mean(axis=1)*areas[:,None],axis=0)/area
            normal=cross.sum(axis=0);normal=normal/np.linalg.norm(normal)
            solids[match.group(1)]={"area_m2":float(area),"center":center,"normal":normal}
    return solids

@dataclass
class GeometryNetwork:
    network:AirNetwork
    positions:torch.Tensor
    normals:torch.Tensor
    edge_modes:tuple[str,...]
    edge_bases:torch.Tensor
    area_groups:tuple[tuple[int,...],...]
    parent_names:tuple[str,...]
    node_parent:tuple[int,...]
    manifest_sha256:str
    project_sha256:str
    def layout_positions(self,translations):
        if translations.shape!=(len(self.parent_names),2):raise ValueError("one x/z rigid translation per rack/ACU")
        padding=translations.new_zeros((len(self.parent_names),3))
        padding[:,0]=translations[:,0];padding[:,2]=translations[:,1]
        parents=torch.tensor(self.node_parent,dtype=torch.long)
        valid=parents>=0;shift=padding[parents.clamp_min(0)]*valid[:,None]
        return self.positions+shift
    def areas(self,positions=None):
        positions=self.positions if positions is None else positions
        pairs=self.network.passive_pairs
        a=torch.tensor([p[0] for p in pairs],dtype=torch.long);b=torch.tensor([p[1] for p in pairs],dtype=torch.long)
        delta=positions[b]-positions[a];distance2=(delta*delta).sum(-1)
        direction=delta/torch.sqrt(distance2+1e-6)[:,None]
        outgoing=(self.normals[a]*direction).sum(-1)
        incoming=-(self.normals[b]*direction).sum(-1)
        positive=lambda z:.5*(z+torch.sqrt(z*z+1e-4))
        sight=(.05+positive(outgoing)*positive(incoming))/(.25+distance2)
        vertical=torch.exp(-(delta[:,1]/.65)**2)+.03
        weights=sight.clone()
        for i,mode in enumerate(self.edge_modes):
            if mode=="vertical":weights[i]=vertical[i]
            elif mode=="room":weights[i]=1.
        # Each group distributes a declared fraction of a physical port area.
        # The distribution changes smoothly with true hard-layout translations.
        output=weights.clone()
        for group in self.area_groups:
            ids=torch.tensor(group,dtype=torch.long)
            output[ids]=self.edge_bases[ids]*weights[ids]/weights[ids].sum()
        return output
    def identity(self):
        return {"scope":"L0 rigid-translation pressure/enthalpy passages from true exported ports","manifest_sha256":self.manifest_sha256,"project_sha256":self.project_sha256,
                "nodes":len(self.network.node_names),"servers":len(self.network.server_pairs),"units":len(self.network.unit_pairs),"passages":len(self.network.passive_pairs),
                "calibration":"unfitted LF capture/bypass fractions; no CFD/field/I2 claim","parents":self.parent_names}

def from_export(folder,project_path,*,rho=1.19,cp=1005.):
    folder=Path(folder);payload=(folder/"air.cfdrun").read_bytes();m=json.loads(payload)
    project=json.loads(Path(project_path).read_bytes())
    if project.get("runDefaults",{}).get("liquid",{}).get("enabled"):
        raise NotImplementedError("L0 air-only network has no accepted wind/liquid heat split")
    if project.get("constructions",{}).get("baffles"):
        raise NotImplementedError("contained-aisle pressure/leakage topology needs its own verified network")
    physical=stl_ports(folder,m);bindings=list(m["boundaryBindings"].values())
    names=["room-low","room-mid","room-high"];pos=[[4.8,.4,4.2],[4.8,1.3,4.2],[4.8,2.8,4.2]];norm=[[0.,0.,0.]]*3;parents=[-1]*3;portarea={}
    ids={};parent_names=[]
    for b in bindings:
        if b["role"] not in ("fan_inlet","fan_outlet","acu_supply","acu_return"):continue
        path=b["objectPath"];uid=path.split(".")[2]
        category=path.split(".")[1];parent=category+"."+uid
        if parent not in parent_names:parent_names.append(parent)
        i=len(names);names.append(b["solidName"]);pos.append(physical[b["solidName"]]["center"].tolist());norm.append(physical[b["solidName"]]["normal"].tolist());parents.append(parent_names.index(parent));portarea[i]=physical[b["solidName"]]["area_m2"]
        ids[(path,b["role"])]=i
    server_pairs=[];flow=[];power=[]
    for path,role in ids:
        if role!="fan_inlet":continue
        server_pairs.append((ids[(path,role)],ids[(path,"fan_outlet")]))
        v=m["inputs"]["servers"][path.split(".")[-1]]
        flow.append(v["airVolumeFlowRateM3s"]);power.append(v["inputPowerW"]/1000)
    units=[];rated=[]
    for path,role in ids:
        if role!="acu_return":continue
        units.append((ids[(path,role)],ids[(path,"acu_supply")]))
        # Rated flow is fixed task/catalog information, not inferred by dividing
        # a current operating point by an undocumented fraction.
        rated.append(2.8)
    edges=[];modes=[];bases=[];groups=[];directed=[]
    def group(pairs,total,mode="sight",oneway=True):
        index=[]
        for a,b in pairs:index.append(len(edges));edges.append((a,b));modes.append(mode);bases.append(total);directed.append(oneway)
        groups.append(tuple(index))
    inlet=[a for a,b in server_pairs];exhaust=[b for a,b in server_pairs]
    returns=[a for a,b in units];supply=[b for a,b in units]
    for s in supply:
        group([(s,i) for i in inlet],.8*portarea[s])
        group([(s,r) for r in range(3)],.2*portarea[s],"vertical")
    for ex in exhaust:
        group([(ex,r) for r in returns],.8*portarea[ex])
        group([(ex,r) for r in range(3)],.2*portarea[ex],"vertical")
        group([(ex,i) for i in inlet],.1*portarea[ex])
    for i in inlet:group([(r,i) for r in range(3)],.4*portarea[i],"vertical")
    for r in returns:group([(v,r) for v in range(3)],.25*portarea[r],"vertical")
    group([(0,1),(1,2),(0,2)],3.,"room",False)
    net=AirNetwork(tuple(names),tuple(p[1] for p in pos),tuple(edges),tuple(directed),tuple(server_pairs),tuple(flow),tuple(power),tuple(units),tuple(rated),rho=rho,cp=cp)
    return GeometryNetwork(net,torch.tensor(pos,dtype=torch.float64),torch.tensor(norm,dtype=torch.float64),tuple(modes),torch.tensor(bases,dtype=torch.float64),tuple(groups),tuple(parent_names),tuple(parents),hashlib.sha256(payload).hexdigest(),hashlib.sha256(Path(project_path).read_bytes()).hexdigest())
