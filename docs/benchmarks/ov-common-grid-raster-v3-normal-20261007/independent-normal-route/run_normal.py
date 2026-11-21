from pathlib import Path
import sys,runpy,json,hashlib,traceback
R=Path(__file__).resolve().parent;P=R/'harness-packet';I=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration')
name=sys.argv[1]
if name not in('actual_extension_grid_route.py','joint_cpu_contract.py'):raise SystemExit('Exact two narrow harnesses only')
expected=json.loads((R/'expected-normal-import-bindings.json').read_text());error=None
try:runpy.run_path(str(P/'harness'/name),run_name='__main__')
except BaseException as ex:error=repr(ex);raise
finally:
 imported={}
 for key in('render_scene','extension','grid_raster','grid_viewport','display_diagnostics'):
  mod=sys.modules.get('controlled_bridge.'+key)
  if mod is None:continue
  path=Path(mod.__file__);d=path.read_bytes();row={'__file__':mod.__file__,'resolvedPath':str(path.resolve()),'bytes':len(d),'sha256':hashlib.sha256(d).hexdigest()};imported[key]=row
  assert row['__file__']==expected[key]['path'] and row['resolvedPath']==expected[key]['path'] and row['sha256']==expected[key]['sha256'],('normal import mismatch',key,row)
 schemas={}
 for key in('renderSceneSchema','displayDiagnosticSchema'):
  p=Path(expected[key]['path']);schemas[key]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size};assert schemas[key]['sha256']==expected[key]['sha256']
 mod=sys.modules.get('controlled_bridge.render_scene')
 if mod is not None:assert str(mod.SCHEMA_PATH)==expected['renderSceneSchema']['path']
 assert len(imported)==5,('incomplete normal imports',imported)
 result={'case':name,'error':error,'importedNormalModules':imported,'actualSharedSchemas':schemas,'renderSceneActualSCHEMA_PATH':str(mod.SCHEMA_PATH),'scope':'Actual normal I modules/shared schema; actual CPUUSD/complete installed API code with controlled lifecycle carriers, not live Kit/pixels'}
 (R/('imports-'+name+'.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'normalActualImportIdentities':result}),flush=True)
