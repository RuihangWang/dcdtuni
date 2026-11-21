from pathlib import Path
import struct
MAX_CELLS,MAX_POINTS,MAX_FACES,MAX_REFS,MAX_BYTES=16384,65536,262144,1048576,64*1024*1024
def decode(data):
 if len(data)>MAX_BYTES or data[:8]!=b"MGPCAP1\0" or data[-8:]!=b"COMPLETE":raise ValueError("missing complete envelope")
 pos=8
 def take(n):
  nonlocal pos
  if pos+n>len(data)-8:raise ValueError("truncated")
  x=data[pos:pos+n];pos+=n;return x
 def u():return struct.unpack("<Q",take(8))[0]
 def i():return struct.unpack("<q",take(8))[0]
 def f():return struct.unpack("<d",take(8))[0]
 def v():return [f(),f(),f()]
 if u()!=1 or u()!=0x0102030405060708:raise ValueError("unsupported schema")
 out={"sourceBaseSHA":take(64).decode()}
 for name in ["rank","size"]:out[name]=i()
 out["distributed"]=bool(u());out["stage"]=i();out["onlyCi"]=i();out["allGreenOnly"]=bool(u())
 out["pointGridBuilt"]=bool(u());out["pointGridSize"]=u()
 out["H"]=f();out["clipTol"]=f();out["mergeVolFrac"]=f();out["origin"]=v()
 for name in ["baseLevel","maxLevel","mergeMaxPasses","maxGlobalCells"]:out[name]=i()
 for name in ["qualityGateWarn","dcMode","mode2d"]:out[name]=bool(u())
 np,nc,nf,nr=u(),u(),u(),u()
 if np>MAX_POINTS or nc>MAX_CELLS or nf>MAX_FACES or nr>MAX_REFS:raise ValueError("schema caps")
 out["points"]=[v()for _ in range(np)];out["cells"]=[];actualF=actualR=0
 for expected in range(nc):
  c={"index":u(),"level":i(),"leaf":i(),"h":f(),"vol":f(),"cen":v(),"flags":u(),"nAbsorb":i(),"owner":i(),"filled":i()}
  if c["index"]!=expected:raise ValueError("cell order")
  c["leafBinding"]={"valid":bool(u()),"key":u(),"level":i(),"box":[f()for _ in range(6)]}
  count=u();actualF+=count
  if actualF>nf:raise ValueError("face count")
  c["faces"]=[]
  for _ in range(count):
   face={"patch":i(),"nbr":i(),"home":v()};n=u();actualR+=n
   if actualR>nr:raise ValueError("reference count")
   face["pts"]=[i()for _ in range(n)]
   if any(pid<0 or pid>=np for pid in face["pts"]):raise ValueError("point id")
   c["faces"].append(face)
  out["cells"].append(c)
 if actualF!=nf or actualR!=nr or pos!=len(data)-8:raise ValueError("totals or trailing bytes")
 out["completeCapture"]=True
 out["nativeQualityPassed"]=False
 return out
