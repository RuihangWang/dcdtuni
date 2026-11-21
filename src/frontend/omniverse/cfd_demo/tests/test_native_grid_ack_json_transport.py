"""Controlled actual ACK flush extraction. No Kit/carb/native product run."""
import ast,asyncio,copy,json,types
from pathlib import Path
import pytest
def flush(payloads,fresh=None,sink_fail=False):
 emitted=[];raw=[];attempts=[0]
 class Bus:
  def push(self,event,payload):
   attempts[0]+=1
   if sink_fail and attempts[0]==1:raise RuntimeError('controlled sink failure')
   raw.append(copy.deepcopy(payload))
   # Only this measured dict None-elision behavior is simulated; strings stay exact.
   def carb_dict(v):
    if isinstance(v,dict):return {k:carb_dict(x) for k,x in v.items() if x is not None}
    if isinstance(v,list):return [carb_dict(x) for x in v]
    return v
   emitted.append(carb_dict(payload))
 class App:
  def get_message_bus_event_stream(self):return Bus()
  async def next_update_async(self):pass
 class Coordinator:
  def display_receipt(self,stage,view):return copy.deepcopy(fresh or {})
 app=App()
 omni=types.SimpleNamespace(kit=types.SimpleNamespace(app=types.SimpleNamespace(get_app=lambda:app)),usd=types.SimpleNamespace(get_context=lambda:types.SimpleNamespace(get_stage=lambda:object())))
 carb=types.SimpleNamespace(events=types.SimpleNamespace(type_from_string=lambda s:s))
 path=Path(__file__).resolve().parents[1]/'kit/extensions/dc.cfd_bridge/dc/cfd_bridge/extension.py'
 tree=ast.parse(path.read_text())
 node=next(n for cls in tree.body if isinstance(cls,ast.ClassDef) for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_flush_acks')
 namespace={'omni':omni,'carb':carb}
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
 receiver=types.SimpleNamespace(_ack_queue=copy.deepcopy(payloads),_ack_flushing=True,_inbound_seq=0,ACK_QUIET_FRAMES=1,ACK_MAX_WAIT_FRAMES=3,_grid_raster=Coordinator())
 asyncio.run(namespace['_flush_acks'](receiver))
 assert receiver._ack_flushing is False and receiver._ack_queue==[]
 return raw,emitted
def native():
 return {'schema':'controlled-transport-only','timeSeconds':None,'gridPixelVerified':False,'encodedFrameProof':'unresolved'}
def ack(n=None):
 grid={'phase':'unsupported','reason':'stream-layout-unresolved','gridPixelVerified':False}
 if n is not None:grid['nativeGeometry']=n
 return {'requestId':'controlled','ok':True,'view':{'timeSeconds':None,'selectedUids':[]},'gridDisplay':grid}
def test_actual_sender_wraps_whole_native_ack_and_preserves_explicit_null_through_none_elision():
 n=native();payload=ack(n)
 raw,wire=flush([payload],fresh={'nativeGeometry':n})
 assert set(raw[0])=={'renderAckJSON'} and isinstance(raw[0]['renderAckJSON'],str)
 assert wire==raw
 decoded=json.loads(wire[0]['renderAckJSON'])
 assert decoded==payload
 assert decoded['gridDisplay']['nativeGeometry']['timeSeconds'] is None
 assert decoded['view']['timeSeconds'] is None and decoded['view']['selectedUids']==[]
 assert decoded['gridDisplay']['gridPixelVerified'] is False
 assert decoded['gridDisplay']['nativeGeometry']['encodedFrameProof']=='unresolved'
@pytest.mark.parametrize('kind',['ordinary','withdrawn'])
def test_no_view_ordinary_and_withdrawn_native_ack_keep_plain_object_path(kind):
 payload=ack(native() if kind=='withdrawn' else None);del payload['view']
 raw,wire=flush([payload],fresh={})
 assert 'renderAckJSON' not in raw[0] and 'nativeGeometry' not in raw[0]['gridDisplay']
 assert 'view' not in raw[0] and raw==wire
 assert raw[0]['gridDisplay']['gridPixelVerified'] is False
@pytest.mark.parametrize('kind',['ordinary','withdrawn'])
def test_real_view_ordinary_and_withdrawn_native_ack_keep_lossless_view_without_proof_repair(kind):
 payload=ack(native() if kind=='withdrawn' else None)
 raw,wire=flush([payload],fresh={})
 assert set(raw[0])=={'renderAckJSON'} and raw==wire
 decoded=json.loads(wire[0]['renderAckJSON'])
 assert decoded['view']==payload['view']
 assert decoded['view']['timeSeconds'] is None and decoded['view']['selectedUids']==[]
 assert 'nativeGeometry' not in decoded['gridDisplay']
 assert decoded['gridDisplay']=={'phase':'unsupported','reason':'stream-layout-unresolved','gridPixelVerified':False}
@pytest.mark.parametrize('kind',['nan','infinite','unsupported-value','sink'])
def test_failed_native_encoding_or_send_never_falls_back_and_does_not_drop_next_ack(kind):
 n=native()
 if kind=='nan':n['invalid']=float('nan')
 if kind=='infinite':n['invalid']=float('inf')
 if kind=='unsupported-value':n['invalid']=object()
 ordinary={'requestId':'next','ok':True}
 raw,wire=flush([ack(n),ordinary],fresh={'nativeGeometry':n},sink_fail=kind=='sink')
 assert raw==[ordinary] and wire==[ordinary]
def test_missing_native_null_key_is_not_repaired_by_sender():
 n=native();del n['timeSeconds']
 raw,wire=flush([ack(n)],fresh={'nativeGeometry':n})
 assert 'timeSeconds' not in json.loads(wire[0]['renderAckJSON'])['gridDisplay']['nativeGeometry']
