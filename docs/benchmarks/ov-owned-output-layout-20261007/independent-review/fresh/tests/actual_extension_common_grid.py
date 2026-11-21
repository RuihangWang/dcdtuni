"""Actual IExt and CPUUSD shared grid state; no live display/pixel proof."""
from pathlib import Path
import asyncio,copy,importlib.util,json,sys,types,weakref
W=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('actual_runtime',W/'tests/controlled_actual_runtime.py');t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
from controlled_bridge import output_layout as o
view=json.loads((W/'harness/common-view.json').read_text())['view']
async def route():
 s,a,state,setting,calls,run=t.fixture();s.GetPrimAtPath(t.r.ROOT).SetCustomDataByKey('dcdtuni:projectUid','CONTROLLED_PROJECT');selection=[]
 ctx=types.SimpleNamespace(get_stage=lambda:s,get_selection=lambda:types.SimpleNamespace(get_selected_prim_paths=lambda:list(selection),set_selected_prim_paths=lambda paths,_:selection.__setitem__(slice(None),paths)))
 t.contexts[None]=ctx;sys.modules['carb.settings'].get_settings=lambda:setting;sys.modules['omni.kit.viewport.utility'].get_active_viewport=lambda:weakref.proxy(a);sys.modules['omni.kit.viewport.utility'].get_active_viewport_and_window=lambda:(weakref.proxy(a),t.Outer());sys.modules['omni.appwindow'].get_default_app_window=lambda:t.App()
 c=t.ext.CfdBridgeExtension();c._render_uid_paths=state['uidPaths'];c._scene_revision=1;c._render_project_hash=t.H;c._last_scene_request_id='scene';c._display_generation=7;c._bounded_display_readback=t.p.ReadbackController(True);c._grid_raster=None;c._grid_frame_revision=0;c._grid_viewport_api=None;c._grid_subscriptions=[];factory_calls=[]
 c._owned_output_layout=o.OwnedOutputLayout(False,lambda:factory_calls.append(True));acks=[];c._render_ack=lambda rid,**kw:acks.append(dict(requestId=rid,**kw))
 try:
  for index,visible,spacing in [(1,True,.6),(2,False,.6),(3,True,.9)]:
   current=copy.deepcopy(view);current['backgroundGrid'].update(visible=visible,spacingM=spacing,divisions=20,extentM=spacing*20);current['backgroundGridRaster']['binding']['viewRevision']=index
   c._route_render_command('updateViewState',json.dumps({'requestId':str(index),'view':current}));assert acks[-1]['ok'],acks[-1]
   assert t.r.read_view(s,[])['backgroundGrid']==current['backgroundGrid']
   lines=t.UsdGeom.BasisCurves(s.GetPrimAtPath(t.r.BACKGROUND_GRID_ROOT+'/Lines'));assert bool(lines)is visible
   if visible:
    points=lines.GetPointsAttr().Get();assert len(points)==4*(current['backgroundGrid']['divisions']+1);assert abs(float(points[4][2]-points[0][2])-spacing)<1e-3
    a._sync_viewport_api(t.UsdGeom.Camera(s.GetPrimAtPath(t.r.CAMERA)),(1600,638),force_update=True);c._grid_frame_revision+=1;c._grid_raster.notify();await c._grid_raster._task
    assert c._grid_raster.receipt['phase']=='unsupported' and c._grid_raster.receipt['reason']=='stream-layout-unresolved'
   else:assert c._grid_raster.receipt['phase']=='hidden'
   assert c._grid_raster.carrier_contract(s)is None
   c._route_render_command('getViewState',json.dumps({'requestId':'plain'}));assert acks[-1]['ok'] and acks[-1]['view']['backgroundGrid']==current['backgroundGrid']
  assert not factory_calls
  assert c._grid_raster.layout_lease() is None
  lease=o.LayoutLease(object(),1,'controlled adapter lease');c._owned_output_layout.current_lease=lambda:lease
  assert c._grid_raster.layout_lease() is lease
  c._owned_output_layout.current_lease=lambda:None
  c._route_render_command('getViewState',t.payload());assert acks[-1]['ok'] and acks[-1]['diagnostics']['pixelVerified']is False and acks[-1]['diagnostics']['viewport']['layoutProof']=='unresolved'
  print(json.dumps({'case':'common IExt grid show/hide/spacing .6→.9','sourceCommonStateMatches':True,'canonicalUSDRoute':True,'ownedOffSDKWork':0,'rasterLayout':'unresolved','pixelVerified':False}))
 finally:
  if c._grid_raster:c._grid_raster.close()
  for sub in c._grid_subscriptions:sub.destroy()
asyncio.run(route())
# Actual startup executes with controlled event/subscription carriers only.
for name in ['omni.kit.livestream','omni.kit.livestream.messaging']:
 m=types.ModuleType(name);m.__path__=[];sys.modules[name]=m;setattr(sys.modules[name.rsplit('.',1)[0]],name.rsplit('.',1)[1],m)
sys.modules['omni.kit.livestream.messaging'].register_event_type_to_send=lambda *_:None;sys.modules['carb.events'].type_from_string=lambda _:1;sys.modules['omni.kit.app'].register_event_alias=lambda *_:True;sys.modules['carb.eventdispatcher'].get_eventdispatcher=lambda:types.SimpleNamespace(observe_event=lambda **_:object())
original=asyncio.ensure_future;asyncio.ensure_future=lambda coroutine:coroutine.close()
try:
 for value in [None,False,True,0,1,'true']:
  s,a,state,setting,calls,run=t.fixture();setting.values['/exts/dc.cfd_bridge/ownedOutputLayout/enabled']=value;sys.modules['carb.settings'].get_settings=lambda:setting;t.contexts[None]=types.SimpleNamespace(get_stage=lambda:s,get_stage_event_stream=lambda:types.SimpleNamespace(create_subscription_to_pop=lambda *a,**kw:object()))
  c=t.ext.CfdBridgeExtension();c.on_startup('controlled-real-method');assert c._owned_output_layout.enabled is(value is True);setting.values['/exts/dc.cfd_bridge/ownedOutputLayout/enabled']=not(value is True);assert c._owned_output_layout.enabled is(value is True);c._owned_output_layout.close()
 print(json.dumps({'case':'actual IExt startup strict boolean captured once','values':6,'scope':'controlled lifecycle/event carriers + literal API + CPUUSD, no live Kit/UI/GPU/stream'}))
finally:asyncio.ensure_future=original
