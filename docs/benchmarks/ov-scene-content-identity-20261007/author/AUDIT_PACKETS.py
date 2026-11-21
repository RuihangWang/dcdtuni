#!/usr/bin/env python3
"""Readonly replay of four original packet hashes. Never writes raw chunk data."""
from pathlib import Path
import argparse,hashlib,json,ast
F=Path(__file__).resolve().parent;B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();out=a.output.resolve();assert out.is_relative_to(B/'.codex-tmp') and not out.exists()
ref=json.loads((F/'evidence/RUNTIME-WHITELIST.json').read_text());p=Path(ref['path']);count=ref['capturedBytes']
with p.open('rb') as f:raw=f.read(count)
assert len(raw)==count and hashlib.sha256(raw).hexdigest()==ref['capturedPrefixSha256'],'original log prefix unavailable or changed'
def canonical(v):return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()
pending={};rows=[];packets=[]
for number,line in enumerate(raw.decode().splitlines(),1):
 if 'Processing custom kit message:' not in line or '{' not in line:continue
 try:v=json.loads(line[line.index('{'):])
 except ValueError:
  try:v=ast.literal_eval(line[line.index('{'):])
  except (ValueError,SyntaxError):continue
 v=v.get('payload',v)
 if isinstance(v,str):v=json.loads(v)
 command=v.get('command_name');m=v.get('message')
 if isinstance(m,str):
  try:m=json.loads(m)
  except ValueError:m=json.loads(json.loads('"'+m+'"'))
 if not isinstance(m,dict):continue
 rid=m.get('requestId')
 if command=='beginRenderScene':pending[rid]={'chunks':m['chunks'],'data':{},'beginLine':number}
 elif command=='renderSceneChunk' and rid in pending:
  slot=pending[rid];index=m['index'];assert type(index) is int and 0<=index<slot['chunks'] and index not in slot['data'];slot['data'][index]=m['data']
 elif command=='loadRenderScene' and rid in pending:
  slot=pending.pop(rid);assert len(slot['data'])==slot['chunks'];text=''.join(slot['data'][i] for i in range(slot['chunks']));packet=json.loads(text);packets.append(packet)
  row={'requestId':rid,'beginLine':slot['beginLine'],'commitLine':number,'chunks':slot['chunks'],'wireBytes':len(text.encode()),'wireSha256':hashlib.sha256(text.encode()).hexdigest(),'project':packet['project'],'projectHash':m['projectHash'],'primitiveCount':len(packet['primitives']),'textureCount':len(packet.get('textures',{})),'selectedUids':packet['view'].get('selectedUids'),'timeSeconds':packet['view'].get('timeSeconds'),'withoutViewCanonicalSha256':digest({k:x for k,x in packet.items() if k!='view'}),'primitiveCanonicalSha256':digest(packet['primitives']),'textureCanonicalSha256':digest(packet.get('textures',{}))};rows.append(row)
assert len(rows)==4 and not pending
for actual,expected in zip(rows,ref['completeScenePackets'],strict=True):
 for key in ['requestId','chunks','wireBytes','wireSha256','project','projectHash','primitiveCount','textureCount','selectedUids','timeSeconds','withoutViewCanonicalSha256','primitiveCanonicalSha256','textureCanonicalSha256']:assert actual[key]==expected[key],key
comparisons=[]
for i in range(1,4):
 before={v['id']:v for v in packets[i-1]['primitives']};after={v['id']:v for v in packets[i]['primitives']};fields={};models=set();changed=0
 assert set(before)==set(after)
 for pid in before:
  if before[pid]==after[pid]:continue
  changed+=1;models.add(after[pid]['modelUid'])
  for k in set(before[pid])|set(after[pid]):
   if before[pid].get(k)!=after[pid].get(k):fields[k]=fields.get(k,0)+1
 comparisons.append({'from':i,'to':i+1,'changedPrimitives':changed,'changedFieldCounts':fields,'changedModelUids':sorted(models),'allNonViewEqual':rows[i-1]['withoutViewCanonicalSha256']==rows[i]['withoutViewCanonicalSha256']})
assert comparisons[0]['changedFieldCounts']=={'material':34} and comparisons[0]['changedModelUids']==['m_30'];assert comparisons[1]['allNonViewEqual'] and comparisons[2]['allNonViewEqual']
result={'scope':'Original owned log prefix reconstructed only in RAM; bounded hashes/counts emitted. No raw scene, texture/chunk data or media protocol output. No live operations.','prefixBytes':count,'prefixSha256':ref['capturedPrefixSha256'],'packets':rows,'comparisons':comparisons,'limits':'Metadata equality does not identify which native input was delivered or prove actual current browser selection/pixels.'};out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'packetCount':len(rows),'comparisons':comparisons,'output':str(out)},indent=2))
