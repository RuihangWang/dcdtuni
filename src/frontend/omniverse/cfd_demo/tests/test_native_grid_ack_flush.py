"""Extract the actual candidate ACK flush body without importing/running Kit."""
import ast,asyncio,copy,json,types
from pathlib import Path
import pytest
from test_native_grid_geometry_receipt import fixture

@pytest.mark.parametrize('kind',['current','changed-scene','changed-owner','changed-material','changed-camera','old-view','missing-coordinator'])
def test_actual_delayed_flush_withdraws_stale_native_success_and_keeps_encoded_receipt(kind):
 from pxr import UsdShade,UsdGeom
 stage,c,current,view,obs=fixture()
 try:
  original=c.display_receipt(stage,view);assert 'nativeGeometry' in original
  payload={'requestId':'controlled','ok':True,'view':copy.deepcopy(view),'gridDisplay':original}
  emitted=[];count=[0]
  class Bus:
   def push(self,event,payload):emitted.append(json.loads(payload['renderAckJSON']) if set(payload)=={'renderAckJSON'} else copy.deepcopy(payload))
  class App:
   def get_message_bus_event_stream(self):return Bus()
   async def next_update_async(self):
    count[0]+=1
    if count[0]!=1:return
    if kind=='changed-scene':current['context']['revision']=3
    if kind=='changed-owner':
     from dataclasses import replace
     current['observation']=replace(obs,owner=('new','','/Render/New',('time',0.)))
    if kind=='changed-material':UsdShade.Shader(stage.GetPrimAtPath('/World/RenderScene/Decorations/BackgroundGrid/Looks/Grid/Surface')).GetInput('opacity').Set(.1)
    if kind=='changed-camera':UsdGeom.Camera(stage.GetPrimAtPath('/World/RenderCamera')).GetFocalLengthAttr().Set(99.)
    if kind=='old-view':payload['view']['backgroundGrid']['spacingM']=.7
  app=App()
  omni=types.SimpleNamespace(kit=types.SimpleNamespace(app=types.SimpleNamespace(get_app=lambda:app)),usd=types.SimpleNamespace(get_context=lambda:types.SimpleNamespace(get_stage=lambda:stage)))
  carb=types.SimpleNamespace(events=types.SimpleNamespace(type_from_string=lambda s:s))
  path=Path(__file__).resolve().parents[1]/'kit/extensions/dc.cfd_bridge/dc/cfd_bridge/extension.py'
  tree=ast.parse(path.read_text())
  node=next(n for cls in tree.body if isinstance(cls,ast.ClassDef) for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_flush_acks')
  namespace={'omni':omni,'carb':carb};exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
  receiver=types.SimpleNamespace(_ack_queue=[payload],_ack_flushing=True,_inbound_seq=0,ACK_QUIET_FRAMES=1,ACK_MAX_WAIT_FRAMES=3,_grid_raster=None if kind=='missing-coordinator' else c)
  asyncio.run(namespace['_flush_acks'](receiver))
  assert len(emitted)==1 and receiver._ack_flushing is False
  result=emitted[0]['gridDisplay']
  assert result['phase']=='unsupported' and result['reason']=='stream-layout-unresolved' and result['gridPixelVerified'] is False
  assert ('nativeGeometry' in result)==(kind=='current')
 finally:c.close()
