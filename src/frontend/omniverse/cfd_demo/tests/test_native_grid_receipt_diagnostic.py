"""PRIVATE opt-in diagnostic controls only; not real carb/Kit acceptance."""
import copy,json,ast,asyncio,types
from pathlib import Path
import pytest
from test_native_grid_geometry_receipt import fixture
from native_receipt_bridge import grid_raster as g

def test_diagnostic_is_off_by_default_and_never_repairs_receipt(monkeypatch,capsys):
 receipt={'phase':'unsupported','reason':'stream-layout-unresolved','gridPixelVerified':False}
 original=copy.deepcopy(receipt)
 monkeypatch.setattr(g,'_NATIVE_GRID_RECEIPT_DIAGNOSTICS',False)
 g._native_grid_receipt_diagnostic('flush-send',receipt)
 assert capsys.readouterr().out=='' and receipt==original

def test_opt_in_flags_preserve_missing_null_time_and_process_cap(monkeypatch,capsys):
 monkeypatch.setattr(g,'_NATIVE_GRID_RECEIPT_DIAGNOSTICS',True)
 monkeypatch.setattr(g,'_NATIVE_GRID_RECEIPT_DIAGNOSTIC_SEEN',set())
 absent={'phase':'unsupported','reason':'stream-layout-unresolved','gridPixelVerified':False,'nativeGeometry':{'gridPixelVerified':False,'encodedFrameProof':'unresolved'}}
 original=copy.deepcopy(absent)
 g._native_grid_receipt_diagnostic('flush-send',absent)
 row=json.loads(capsys.readouterr().out.split('diagnostic ',1)[1])
 assert row['nativeGeometryPresent'] is True and row['nativeGeometryTimeSecondsPresent'] is False
 assert row['diagnosticAuthority'] is False and absent==original
 present=copy.deepcopy(absent);present['nativeGeometry']['timeSeconds']=None
 for serial in range(80):
  g._native_grid_receipt_diagnostic('producer',present,refusal='synchronized',request={'serial':serial})
 rows=[json.loads(x.split('diagnostic ',1)[1]) for x in capsys.readouterr().out.splitlines()]
 assert len(rows)==31 and len(g._NATIVE_GRID_RECEIPT_DIAGNOSTIC_SEEN)==32
 assert all(r['nativeGeometryTimeSecondsPresent'] and r['nativeGeometryTimeSecondsIsNull'] for r in rows)
 assert 'timeSeconds' not in absent['nativeGeometry']

def test_failed_sink_still_reserves_budget_and_changes_no_receipt(monkeypatch):
 monkeypatch.setattr(g,'_NATIVE_GRID_RECEIPT_DIAGNOSTICS',True)
 monkeypatch.setattr(g,'_NATIVE_GRID_RECEIPT_DIAGNOSTIC_SEEN',set())
 receipt={'phase':'unsupported','reason':'stream-layout-unresolved','gridPixelVerified':False,'nativeGeometry':{'gridPixelVerified':False,'encodedFrameProof':'unresolved','timeSeconds':None}}
 before=copy.deepcopy(receipt);calls=[]
 def failed_sink(*args,**kwargs):
  calls.append(args)
  raise OSError('controlled sink failure')
 with monkeypatch.context() as local:
  local.setattr('builtins.print',failed_sink)
  for serial in range(80):
   g._native_grid_receipt_diagnostic('producer',receipt,refusal='synchronized',request={'serial':serial})
 assert len(calls)==32 and len(g._NATIVE_GRID_RECEIPT_DIAGNOSTIC_SEEN)==32
 assert receipt==before

@pytest.mark.parametrize('kind',['current','sink-failure'])
def test_actual_opt_in_flush_with_real_controlled_package_keeps_pre_wire_null_keys(kind,monkeypatch,capsys):
 """Controlled native sampler and event bus only; not real carb transport."""
 stage,c,current,view,obs=fixture()
 monkeypatch.setattr(g,'_NATIVE_GRID_RECEIPT_DIAGNOSTICS',True)
 monkeypatch.setattr(g,'_NATIVE_GRID_RECEIPT_DIAGNOSTIC_SEEN',set())
 try:
  original=c.display_receipt(stage,view)
  assert 'nativeGeometry' in original and original['nativeGeometry']['timeSeconds'] is None
  payload={'requestId':'controlled','ok':True,'view':copy.deepcopy(view),'gridDisplay':original}
  emitted=[]
  class Bus:
   def push(self,event,payload):emitted.append(json.loads(payload['renderAckJSON']) if set(payload)=={'renderAckJSON'} else copy.deepcopy(payload))
  class App:
   def get_message_bus_event_stream(self):return Bus()
   async def next_update_async(self):pass
  app=App()
  omni=types.SimpleNamespace(kit=types.SimpleNamespace(app=types.SimpleNamespace(get_app=lambda:app)),usd=types.SimpleNamespace(get_context=lambda:types.SimpleNamespace(get_stage=lambda:stage)))
  carb=types.SimpleNamespace(events=types.SimpleNamespace(type_from_string=lambda value:value))
  path=Path(__file__).resolve().parents[1]/'kit/extensions/dc.cfd_bridge/dc/cfd_bridge/extension.py'
  tree=ast.parse(path.read_text())
  node=next(n for cls in tree.body if isinstance(cls,ast.ClassDef) for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_flush_acks')
  namespace={'__name__':'native_receipt_bridge.extension','__package__':'native_receipt_bridge','omni':omni,'carb':carb}
  exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
  receiver=types.SimpleNamespace(_ack_queue=[payload],_ack_flushing=True,_inbound_seq=0,ACK_QUIET_FRAMES=1,ACK_MAX_WAIT_FRAMES=3,_grid_raster=c)
  capsys.readouterr();failed=[]
  def failed_sink(*args,**kwargs):
   failed.append(args[0])
   raise OSError('controlled sink failure')
  with monkeypatch.context() as local:
   if kind=='sink-failure':local.setattr('builtins.print',failed_sink)
   asyncio.run(namespace['_flush_acks'](receiver))
  text=failed if kind=='sink-failure' else capsys.readouterr().out.splitlines()
  records=[json.loads(line.split('diagnostic ',1)[1]) for line in text if 'native-grid-receipt-diagnostic ' in line]
  wire=[row for row in records if row['stage']=='flush-send']
  assert len(wire)==1 and wire[0]['nativeGeometryPresent'] is True
  assert wire[0]['nativeGeometryTimeSecondsPresent'] is True and wire[0]['nativeGeometryTimeSecondsIsNull'] is True
  assert wire[0]['diagnosticAuthority'] is False and wire[0]['additionalNativeReads'] is False
  assert len(emitted)==1 and receiver._ack_flushing is False
  sent=emitted[0]['gridDisplay']
  assert sent['phase']=='unsupported' and sent['reason']=='stream-layout-unresolved'
  assert sent['gridPixelVerified'] is False and sent['nativeGeometry']['encodedFrameProof']=='unresolved'
  assert 'timeSeconds' in sent['nativeGeometry'] and sent['nativeGeometry']['timeSeconds'] is None
 finally:c.close()
