"""Materialise a B-contained review tree using by-value targets and hash-guarded normal dependencies."""
from pathlib import Path
import argparse,hashlib,json,shutil
F=Path(__file__).resolve().parent;B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni').resolve()
ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();repo=a.repo.resolve();out=a.out.resolve()
if not repo.is_relative_to(B) or not out.is_relative_to(B) or out==B or out.exists():raise RuntimeError('new B-contained output and normal repository required')
for rel,expected in json.loads((F/'NORMAL-SOURCE-GUARDS.json').read_text()).items():
 if hashlib.sha256((repo/rel).read_bytes()).hexdigest()!=expected:raise RuntimeError('normal dependency changed: '+rel)
owned=['src/frontend/shared/js/geometry_scene.js','src/frontend/react/src/legacy/cfdrunBuild.ts','src/frontend/react/test/floorSourceSurfaceEmission.test.ts','src/frontend/react/test/fixtures/floorSourceSurfaceEmission.json']
for label in ['baseline','candidate']:
 tree=out/label;tree.mkdir(parents=True)
 for rel in owned:
  src=F/('before' if label=='baseline' and rel in owned[:2] else 'candidate')/rel
  dst=tree/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
 def overlay(src,dst):
  dst.mkdir(parents=True,exist_ok=True)
  for q in src.iterdir():
   if q.name in ['node_modules','dist','.vite']:continue
   target=dst/q.name
   if target.exists() or target.is_symlink():
    if q.is_dir() and target.is_dir() and not target.is_symlink():overlay(q,target)
    continue
   target.symlink_to(q,target_is_directory=q.is_dir())
 for rel in ['src/frontend/shared','src/frontend/react']:overlay(repo/rel,tree/rel)
 for dst,src in [('node_modules','src/frontend/react/node_modules'),('src/frontend/react/node_modules','src/frontend/react/node_modules'),('src/backend','src/backend'),('case','case'),('tools','tools')]:
  target=tree/dst
  if not target.exists():target.symlink_to(repo/src,target_is_directory=True)
(out/'tmp').mkdir();(out/'vite.config.mjs').write_text('export default {cacheDir:'+repr(str(out/'tmp/vite'))+',resolve:{preserveSymlinks:true},test:{environment:"jsdom",pool:"forks",poolOptions:{forks:{singleFork:true}},testTimeout:15000}}\n')
print(out)
