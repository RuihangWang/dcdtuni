"""Verify and losslessly restore this PRIVATE packet into a new owned IT tmp directory."""
from pathlib import Path,PurePosixPath
import gzip,hashlib,json,shutil,sys
ROOT=Path(__file__).resolve().parent
ALLOWED=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/.codex-tmp').resolve()
def relative(s):
 p=PurePosixPath(s)
 if p.is_absolute() or not p.parts or '..' in p.parts or '.' in p.parts:raise ValueError('unsafe archive relative path')
 return Path(*p.parts)
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1048576),b''):h.update(c)
 return h.hexdigest()
def main():
 if len(sys.argv)!=2:raise SystemExit('Usage: python decode_archive.py NEW_ABSOLUTE_IT_CODEX_TMP_DIRECTORY')
 dest=Path(sys.argv[1])
 if not dest.is_absolute():raise ValueError('destination must be absolute')
 resolved=dest.resolve()
 if resolved==ALLOWED or not resolved.is_relative_to(ALLOWED):raise ValueError('destination outside owned integration tmp')
 if dest.exists() or dest.is_symlink():raise FileExistsError('destination must not exist')
 manifest=json.loads((ROOT/'manifest.json').read_text())
 archives=json.loads((ROOT/'archive-map.json').read_text())['entries']
 for row in manifest['payloads']:
  p=ROOT/relative(row['path'])
  if p.is_symlink() or not p.is_file() or p.stat().st_size!=row['bytes'] or digest(p)!=row['sha256']:raise ValueError('payload mismatch '+row['path'])
 # Check raw payload identity before any destination is created.
 for row in archives:
  src=ROOT/relative(row['archive_path']);h=hashlib.sha256();n=0
  with gzip.open(src,'rb') as f:
   for c in iter(lambda:f.read(1048576),b''):
    n+=len(c)
    if n>row['original_bytes']:raise ValueError('archive size overrun')
    h.update(c)
  if n!=row['original_bytes'] or h.hexdigest()!=row['original_sha256']:raise ValueError('archive raw identity mismatch')
 resolved.mkdir()
 for row in manifest['payloads']:
  rel=relative(row['path'])
  if rel.parts[0]=='archives':continue
  out=resolved/rel;out.parent.mkdir(parents=True,exist_ok=True)
  with (ROOT/rel).open('rb') as source,out.open('xb') as target:shutil.copyfileobj(source,target)
 for row in archives:
  out=resolved/relative(row['original_path']);out.parent.mkdir(parents=True,exist_ok=True)
  with gzip.open(ROOT/relative(row['archive_path']),'rb') as source,out.open('xb') as target:shutil.copyfileobj(source,target)
  if out.stat().st_size!=row['original_bytes'] or digest(out)!=row['original_sha256']:raise ValueError('restored identity mismatch')
 print(json.dumps({'restored_to':str(resolved),'raw_archives':len(archives),'all_exact':True,'normal_write':False},indent=2))
if __name__=='__main__':main()
