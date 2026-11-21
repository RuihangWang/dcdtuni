from pathlib import Path
import argparse,shutil,json,os
from package_paths import optional_output
P=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);ap.add_argument('--node-modules',required=True);args=ap.parse_args()
R=optional_output(args.out,P)
if any(R.iterdir()):raise ValueError('Fresh empty repository-private compiler output directory required')
dep=Path(args.node_modules).resolve();assert (dep/'vitest/vitest.mjs').is_file()
shutil.copytree(P/'code/product-source',R/'source')
(R/'inputs').mkdir();(R/'results').mkdir();(R/'tmp').mkdir();(R/'cache').mkdir()
for tag in ['2p0','1p0','0p5','0p25']:shutil.copy2(P/('evidence/gui/saved-project-'+tag+'.cfdproj'),R/('inputs/quadratic-lowflow-dt-'+tag+'.cfdproj'))
for name in ['compile.test.ts','vitest.config.ts','run_react.py']:shutil.copy2(P/'evidence/product'/name,R/name)
(R/'node_modules').symlink_to(dep,target_is_directory=True)
(R/'source/src/frontend/react/node_modules').symlink_to(dep,target_is_directory=True)
(R/'package-compiler-setup.json').write_text(json.dumps({'source':'pinned exact product code snapshot + real GUI saved projects','package':str(P),'output':str(R),'read_only_existing_node_modules':str(dep),'new_code_modified':False,'service_started':False},indent=2))
print(json.dumps({'output':str(R),'compile_command':['/usr/bin/node',str(R/'node_modules/vitest/vitest.mjs'),'run','--config',str(R/'vitest.config.ts')],'cwd':str(R)},indent=2))
