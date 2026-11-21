from pathlib import Path
import os,json,hashlib,shutil,time,concurrent.futures,stat
T=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration')
C=T/'.codex-tmp/cases/Shinaide_TypedAdmissionP15_CUA'
O=Path(__file__).parent;D=O/'case-copy';D.mkdir(exist_ok=False)
def h(p):
 d=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):d.update(b)
 return d.hexdigest()
def census():
 out=[]
 for root,ds,fs in os.walk(C,followlinks=False):
  for n in ds+fs:
   p=Path(root)/n;assert not p.is_symlink(),str(p)
  for n in fs:
   p=Path(root)/n;st=p.stat();assert stat.S_ISREG(st.st_mode)
   out.append((p.relative_to(C).as_posix(),st.st_size,st.st_dev,st.st_ino,st.st_mtime_ns))
 return sorted(out)
start=time.monotonic();before=census();assert len(before)==373
def one(row):
 n,size,dev,ino,mtime=row;src=C/n;dst=D/n;dst.parent.mkdir(parents=True,exist_ok=True)
 pre=h(src);shutil.copyfile(src,dst);copy=h(dst);post=h(src);st=src.stat()
 assert pre==copy==post
 assert (st.st_size,st.st_dev,st.st_ino,st.st_mtime_ns)==(size,dev,ino,mtime)
 assert dst.stat().st_size==size
 return {'path':n,'bytes':size,'sha256':pre,'sourceBeforeSHA':pre,'sourceAfterSHA':post,'copySHA':copy,'statStable':True}
rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for i,r in enumerate(pool.map(one,before),1):
  rows.append(r)
  if i%50==0:print(json.dumps({'copiedAndVerified':i,'of':len(before)}),flush=True)
after=census();assert after==before
m={'schema':'P15-complete-case-independent-copy/v1','sourceCase':str(C),'copyRoot':str(D),'files':rows,'payloadCount':len(rows),'payloadBytes':sum(r['bytes'] for r in rows),'noSymlinks':True,'namespaceAndStatBeforeAfterExact':True,'copyElapsedSeconds':time.monotonic()-start,'copyElapsedScope':'independent archive work, not native meshing or SSE wall'}
(O/'complete-case-copy-manifest.json').write_text(json.dumps(m,indent=2)+'\n')
P=T/'.codex-tmp/p15-normal-ui-native-observer-20261006'
obs=[]
( O/'observer-originals').mkdir(exist_ok=False)
for src in sorted(P.iterdir()):
 if not src.is_file():continue
 b=src.read_bytes();dst=O/'observer-originals'/src.name;dst.write_bytes(b);assert b==src.read_bytes()==dst.read_bytes()
 obs.append({'source':str(src),'copy':str(dst.relative_to(O)),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
(O/'observer-original-copy-identity.json').write_text(json.dumps(obs,indent=2)+'\n')
print(json.dumps({'completeCaseCount':len(rows),'completeCaseBytes':m['payloadBytes'],'manifestSHA':hashlib.sha256((O/'complete-case-copy-manifest.json').read_bytes()).hexdigest(),'copyElapsedSeconds':m['copyElapsedSeconds'],'observerFiles':len(obs)}),flush=True)
