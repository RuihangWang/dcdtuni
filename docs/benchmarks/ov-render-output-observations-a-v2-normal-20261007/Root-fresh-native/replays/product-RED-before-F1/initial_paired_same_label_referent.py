from pathlib import Path
import json,runpy,sys,types,weakref
from unittest.mock import patch
W=Path(__file__).resolve().parent;O=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/ov-render-output-a-v2-root-fresh-20261007')
ns=runpy.run_path(str(O/'tests/native_a.py'));o=ns['o'];rn=ns['ns'];results=[];keep=[]
def fixture():
 v=ns['fixture']();s,a,state,setting,calls,app,w,g,run=v
 b=rn['api_mod'].ViewportAPI(a.usd_context_name,a.id,None);b.__dict__.update(a.__dict__);b._ViewportAPI__hydra_texture=types.SimpleNamespace(get_render_product_path=lambda:'/Render/CONTROLLED_PRODUCT');b._ViewportAPI__time=a.time;keep.append(b)
 w.viewport_widget.viewport_api=weakref.proxy(b)
 g['supports_viewport']=lambda p:isinstance(p,rn['api_mod'].ViewportAPI);g['supports_window']=lambda p:isinstance(p,ns['Window']);g['supports_appwindow']=lambda p:isinstance(p,ns['App'])
 return v,b
v,b=fixture();s,a,state,setting,calls,app,w,g,run=v;out=run();results.append({'kind':'initial owner mismatch despite ALL public labels/product/time equal; actual literal ViewportAPI objects and CPU USD','status':out['status'],'reason':out['reason'],'APIIdsEqual':a.id==b.id,'APIsEqual':a==b,'apiProduct':a.render_product_path,'widgetAPIProduct':b.render_product_path,'apiTime':str(a.time),'widgetAPITime':str(b.time),'claimedOwnerProduct':out.get('viewportOwner',{}).get('renderProductPath')})
v,b=fixture();s,a,state,setting,calls,app,w,g,run=v;rn['contexts'][None]=types.SimpleNamespace(get_stage=lambda:s,get_selection=lambda:types.SimpleNamespace(get_selected_prim_paths=lambda:[]));sys.modules['carb.settings'].get_settings=lambda:setting;sys.modules['omni.kit.viewport.utility'].get_active_viewport_and_window=lambda:(weakref.proxy(a),w);sys.modules['omni.appwindow'].get_default_app_window=lambda:app
c=rn['ext'].CfdBridgeExtension();c._render_uid_paths=state['uidPaths'];c._scene_revision=1;c._render_project_hash=rn['H'];c._last_scene_request_id='scene';c._display_generation=7;c._grid_raster=None;c._bounded_display_readback=rn['p'].ReadbackController(True);acks=[];c._render_ack=lambda rid,**kw:acks.append(dict(requestId=rid,**kw))
with patch.object(o,'reviewed_runtime_getters',return_value=g):c._route_render_command('getViewState',rn['payload']())
reply=acks[-1];obs=reply.get('diagnostics',{}).get('renderOutputObservations',{});results.append({'kind':'actual IExt/controller/helper route initial paired API mismatch under controlled carriers','ok':reply['ok'],'observationStatus':obs.get('status'),'observationReason':obs.get('reason'),'apiProduct':a.render_product_path,'widgetAPIProduct':b.render_product_path,'claimedOwnerProduct':obs.get('viewportOwner',{}).get('renderProductPath'),'pixelVerified':reply.get('diagnostics',{}).get('pixelVerified')})
(W/'initial-paired-same-label-referent-results.json').write_text(json.dumps({'classification':'product identity RED if unmatched actual API pair remains observed; no live/GPU/render call','results':results},indent=2)+'\n');print(json.dumps(results,indent=2))
assert out['status']=='unavailable','PRODUCT RED mismatched initial paired API accepted as observed'
assert not reply['ok'] or obs.get('status')=='unavailable','PRODUCT RED actual native route accepts unmatched paired API'
