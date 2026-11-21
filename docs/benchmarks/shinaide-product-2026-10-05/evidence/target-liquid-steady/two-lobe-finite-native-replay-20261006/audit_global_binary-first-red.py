from pathlib import Path
import importlib.util,hashlib,json,numpy as np,time
here=Path(__file__).parent;start=time.monotonic()
rp=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/.codex-tmp/p12-D-eligibility-audit-20261005/polymesh_io.reader.py")
spec=importlib.util.spec_from_file_location("p12reader",rp);R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
originalPath=rp.parent/"selected/Mesh/processor0/constant/polyMesh/points"
b=originalPath.read_bytes();assert hashlib.sha256(b).hexdigest()=="e2169648116a368a0e3016655cb801d1fa6f756cc81f17922bb7c296bc083f91"
original=R.read_points(originalPath)
d=json.loads((here/"finite-seed-template.json").read_text());ids=sorted(map(int,d["points"]))
p=np.array([d["points"][str(i)]for i in ids],dtype="<f8")
out=here/"controlled-compact-points.foam"
header='FoamFile\n{\nversion 2.0;\nformat binary;\narch "LSB;label=32;scalar=64";\nclass vectorField;\nobject points;\n}\n'+str(len(p))+'\n(\n'
out.write_bytes(header.encode()+p.tobytes()+b'\n)\n')
read=R.read_points(out);assert read.shape==p.shape and read.tobytes()==p.tobytes()
entries=[]
for q in d["lobeApexWrittenPoints"]:
 xyz=p[ids.index(q)];dist=np.linalg.norm(original-xyz,axis=1);nearest=int(dist.argmin())
 assert np.isfinite(dist).all()and float(dist[nearest])>3e-6
 entries.append({"newPoint":q,"nearestOriginalWrittenPoint":nearest,"distance":float(dist[nearest]),"allOriginalPointCount":len(original)})
a,b=d["lobeApexWrittenPoints"];between=float(np.linalg.norm(p[ids.index(a)]-p[ids.index(b)]));assert between>3e-6
# The saved candidate never alters an original point, including both unused old apices.
old=json.loads((here.parent/"two-lobe-separator-star-template-20261006/complete-star-input.json").read_text())
for i,xyz in old["points"].items():assert np.array(xyz,dtype="<f8").tobytes()==p[ids.index(int(i))].tobytes()
report={"scope":"Full frozen rank0 original point-distance audit and controlled real Foam binary64 reader roundtrip; no production writer alias/canonical certificate implied.",
 "originalPoints":{"path":str(originalPath),"sha256":hashlib.sha256(originalPath.read_bytes()).hexdigest(),"count":len(original)},
 "reader":{"path":str(rp),"sha256":hashlib.sha256(rp.read_bytes()).hexdigest()},
 "compactPointCount":len(p),"arch":"LSB;label=32;scalar=64","binaryReaderCoordinatesBitExact":True,"allOriginalFixtureCoordinatesBitExact":True,
 "newPoints":entries,"newPointMutualDistance":between,"threshold":3e-6,"productionAliasAndFinalWriterNotEstablished":True,"seconds":time.monotonic()-start}
(here/"global-point-and-binary64-binding.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report))
