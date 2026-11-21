from pathlib import Path
import inspect,json,runpy,sys,types,weakref
from unittest.mock import patch
W=Path(__file__).resolve().parent
O=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/render-output-independent-fresh-v2-20261007')
ns=runpy.run_path(str(O/'tests/native_a.py'))
o=ns['o'];rns=ns['ns'];events=[];results=[]
class Widget:
 def __init__(self,api):self.api=api;self.ref=weakref.proxy(api);self.calls=0;self.name='';self.visible=True
 @property
 def viewport_api(self):
  self.calls+=1
  if self.calls==8:
   caller=inspect.currentframe().f_back
   self.api._ViewportAPI__viewport_texture.frame_info['frame_number']=18
   events.append({'getterCall':self.calls,'caller':caller.f_code.co_filename,'line':caller.f_lineno,'mutation':'delivered frame_number17->18 after final_context read'})
  return self.ref

def fixture():
 v=ns['fixture']();s,a,state,setting,calls,app,w,g,run=v;w.viewport_widget=Widget(a)
 g['supports_viewport']=lambda p:isinstance(p,rns['api_mod'].ViewportAPI)
 g['supports_window']=lambda p:isinstance(p,ns['Window'])
 g['supports_appwindow']=lambda p:isinstance(p,ns['App'])
 return v
s,a,state,setting,calls,app,w,g,run=fixture();out=run();results.append({'kind':'actual helper/literal installed API/real CPU USD/controlled last public getter mutation','status':out['status'],'reason':out['reason'],'returnedFrameNumber':out.get('deliveredViewportFrame',{}).get('frameNumber'),'currentFrameNumber':a.frame_info['frame_number'],'publicGetterCalls':w.viewport_widget.calls,'helperFile':o.__file__})
s,a,state,setting,calls,app,w,g,run=fixture();rns['contexts'][None]=types.SimpleNamespace(get_stage=lambda:s,get_selection=lambda:types.SimpleNamespace(get_selected_prim_paths=lambda:[]))
sys.modules['carb.settings'].get_settings=lambda:setting;sys.modules['omni.kit.viewport.utility'].get_active_viewport_and_window=lambda:(weakref.proxy(a),w);sys.modules['omni.appwindow'].get_default_app_window=lambda:app
c=rns['ext'].CfdBridgeExtension();c._render_uid_paths=state['uidPaths'];c._scene_revision=1;c._render_project_hash=rns['H'];c._last_scene_request_id='scene';c._display_generation=7;c._grid_raster=None;c._bounded_display_readback=rns['p'].ReadbackController(True);acks=[];c._render_ack=lambda rid,**kw:acks.append(dict(requestId=rid,**kw))
with patch.object(o,'reviewed_runtime_getters',return_value=g):c._route_render_command('getViewState',rns['payload']())
reply=acks[-1];obs=reply.get('diagnostics',{}).get('renderOutputObservations',{});results.append({'kind':'actual production IExt/controller/helper route with literal API/real CPU USD; controlled carriers','ok':reply['ok'],'observationStatus':obs.get('status'),'observationReason':obs.get('reason'),'returnedFrameNumber':obs.get('deliveredViewportFrame',{}).get('frameNumber'),'currentFrameNumber':a.frame_info['frame_number'],'publicGetterCalls':w.viewport_widget.calls,'pixelVerified':reply.get('diagnostics',{}).get('pixelVerified'),'layoutProof':obs.get('layoutProof')})
(W/'late-public-getter-pin-results.json').write_text(json.dumps({'classification':'product coherence RED if observed accepts17 while current18; no live/native renderer/GPU; controlled public getter mutation','events':events,'results':results},indent=2)+'\n')
print(json.dumps({'events':events,'results':results},indent=2))
assert results[0]['status']=='unavailable','PRODUCT RED helper accepts stale final metadata after last public getter'
assert not reply['ok'] or obs.get('status')=='unavailable','PRODUCT RED actual IExt route accepts stale final metadata'
