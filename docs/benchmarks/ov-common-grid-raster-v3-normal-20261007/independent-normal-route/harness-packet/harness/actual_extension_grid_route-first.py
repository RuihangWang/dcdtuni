from pathlib import Path
import asyncio,copy,importlib.util,json,sys,types,weakref
T=Path(__file__).resolve().parents[1];reference='--reference'in sys.argv
Q=T/'diagnostic-harness';q=Q/'tests/native_runtime.py';env={'__file__':str(q),'__name__':'actual_grid_route_fixture'};exec(q.read_text().split('def positive():',1)[0],env)
r,ext,UsdGeom=env['r'],env['ext'],env['UsdGeom']
if reference:
 q=T/'before-correction/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/extension.py';spec=importlib.util.spec_from_file_location('controlled_bridge.extension_reference',q);ext=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ext;spec.loader.exec_module(ext)
view=json.loads((T/'logs/joint-actual-native-reply.json').read_text())['view'];rows=[]
async def actual_route():
 s,a,state,setting,calls,run=env['fixture']();s.GetPrimAtPath(r.ROOT).SetCustomDataByKey('dcdtuni:projectUid','CONTROLLED_PROJECT');selection=[]
 ctx=types.SimpleNamespace(get_stage=lambda:s,get_selection=lambda:types.SimpleNamespace(get_selected_prim_paths=lambda:list(selection),set_selected_prim_paths=lambda paths,_:selection.__setitem__(slice(None),paths)))
 env['contexts'][None]=ctx;sys.modules['carb.settings'].get_settings=lambda:setting;sys.modules['omni.kit.viewport.utility'].get_active_viewport=lambda:weakref.proxy(a);sys.modules['omni.kit.viewport.utility'].get_active_viewport_and_window=lambda:(weakref.proxy(a),env['Outer']());sys.modules['omni.appwindow'].get_default_app_window=lambda:env['App']()
 # Controlled native lifecycle carrier fields exactly mirror on_startup; real
 # IExt route, actual USD and complete installed ViewportAPI are executed.
 c=ext.CfdBridgeExtension();c._render_uid_paths=state['uidPaths'];c._scene_revision=1;c._render_project_hash=env['H'];c._last_scene_request_id='scene';c._display_generation=7;c._bounded_display_readback=env['p'].ReadbackController(True);c._grid_raster=None;c._grid_frame_revision=0;c._grid_viewport_api=None;c._grid_subscriptions=[];acks=[];c._render_ack=lambda rid,**kw:acks.append(dict(requestId=rid,**kw))
 try:
  c._route_render_command('updateViewState',json.dumps({'requestId':'update','view':view}))
  assert acks[-1]['ok']is(not reference)
  if reference:
   assert '_current_scene_context'in acks[-1]['error'];rows.append({'case':'actual-update-route','ok':False,'error':acks[-1]['error'],'missingContextMethod':True});return
  assert acks[-1]['view']['backgroundGridRaster']==view['backgroundGridRaster'];await asyncio.sleep(.001)
  # Publish a controlled native frame through the actual public API callback;
  # absent owned encoded layout must remain explicitly unsupported.
  c._grid_frame_revision+=1;c._grid_raster.notify();await c._grid_raster._task
  assert c._grid_raster.receipt['phase']=='unsupported'and c._grid_raster.receipt['reason']=='stream-layout-unresolved';assert c._grid_raster.carrier_contract(s)is None
  c._route_render_command('getViewState',json.dumps({'requestId':'plain'}));plain=copy.deepcopy(acks[-1]);assert plain['ok']and plain['view']['backgroundGridRaster']==view['backgroundGridRaster']and plain['gridDisplay']['phase']=='unsupported'
  c._route_render_command('getViewState',env['payload']());diagnostic=acks[-1];assert diagnostic['ok']and diagnostic['view']==plain['view']and diagnostic['diagnostics']['pixelVerified']is False and diagnostic['diagnostics']['viewport']['layoutProof']=='unresolved'
  (T/'logs/joint-actual-extension-reply.json').write_text(json.dumps(diagnostic,indent=2)+'\n')
  identity=c._current_scene_context(s);assert identity=={'projectUid':'CONTROLLED_PROJECT','projectContentSha256':env['H'],'revision':1,'displayGeneration':7}
  c._display_generation=8;assert c._current_scene_context(s)['displayGeneration']==8
  other=env['Usd'].Stage.CreateInMemory()
  try:c._current_scene_context(other)
  except ValueError:pass
  else:raise AssertionError('foreign active stage accepted as current context')
  rows.append({'case':'actual-update-to-ordinary-to-bounded-route','viewEqual':True,'gridDisplay':'unsupported','reason':'stream-layout-unresolved','pixelVerified':False,'generationBound':True,'foreignStageRefused':True})
 finally:
  if c._grid_raster:c._grid_raster.close()
  for sub in c._grid_subscriptions:sub.destroy()
asyncio.run(actual_route());print(json.dumps({'reference':reference,'rows':rows,'scope':'actual IExt methods + complete installed public API + CPU USD; controlled lifecycle fields, no live media/layout/pixel proof'},indent=2));(T/'logs'/('extension-reference-results.json'if reference else'extension-corrected-results.json')).write_text(json.dumps(rows,indent=2)+'\n')
