from pathlib import Path
import sys,types,importlib,json
from pxr import Usd,UsdGeom
root=Path(__file__).resolve().parents[1]/'runtime/src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
for name in ['omni','omni.ext','omni.usd','omni.kit','omni.kit.app','carb','carb.events','carb.eventdispatcher']:
 m=types.ModuleType(name);m.__path__=[];sys.modules[name]=m
 if '.' in name:setattr(sys.modules[name.rsplit('.',1)[0]],name.rsplit('.',1)[1],m)
sys.modules['omni.ext'].IExt=object
for name in ['dc','dc.cfd_bridge']:
 m=types.ModuleType(name);m.__path__=[str(root if name.endswith('cfd_bridge') else root.parent)];sys.modules[name]=m
mod=importlib.import_module('dc.cfd_bridge.extension')
stage=Usd.Stage.CreateInMemory();prim=UsdGeom.Xform.Define(stage,'/World/RenderScene').GetPrim();prim.SetCustomDataByKey('dcdtuni:viewJson',json.dumps({'selectedUids':[],'timeSeconds':None}))
ctx=types.SimpleNamespace(get_stage=lambda:stage,get_selection=lambda:types.SimpleNamespace(get_selected_prim_paths=lambda:[]))
sys.modules['omni.usd'].get_context=lambda:ctx
c=mod.CfdBridgeExtension();c._render_uid_paths={};c._scene_revision=1;c._render_project_hash='a'*64;c._last_scene_request_id='scene';acks=[];c._render_ack=lambda rid,**kw:acks.append(dict(requestId=rid,**kw))
c._route_render_command('getViewState',json.dumps({'requestId':'red','diagnostics':{'profile':'bounded-display-v1','expectedSceneRevision':1,'expectedProjectHash':'a'*64}}))
print(json.dumps(acks,sort_keys=True))
assert acks[-1]['ok'], 'baseline actual native route rejects diagnostic exact shape'
