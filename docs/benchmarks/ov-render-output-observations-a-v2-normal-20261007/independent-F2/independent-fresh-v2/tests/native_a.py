from pathlib import Path
import copy, importlib.util, json, math, runpy, sys, types, weakref
from unittest.mock import patch
T=Path(__file__).resolve().parents[1]
D=T/'diagnostic-harness'
ns=runpy.run_path(str(D/'tests/native_runtime.py'))
from controlled_bridge import render_output_observations as o
from pxr import Gf
checks=[]
def check(label,fn): fn();checks.append(label)
class App:
 def __init__(self):self.size=(1600,638);self.dpi=1.25;self.ui=1.5
 def get_size(self):return types.SimpleNamespace(x=self.size[0],y=self.size[1])
 def get_width(self):return self.size[0]
 def get_height(self):return self.size[1]
 def get_dpi_scale(self):return self.dpi
 def get_ui_scale(self):return self.ui
class Window:
 def __init__(self,a):
  self.viewport_widget=types.SimpleNamespace(viewport_api=weakref.proxy(a),name='',visible=True)
  self.frame=types.SimpleNamespace(screen_position_x=23,screen_position_y=41,computed_width=1600.,computed_height=638.,computed_content_width=1600.,computed_content_height=638.)
def fixture():
 s,a,state,setting,calls,_=ns['fixture'](); app=App();w=Window(a)
 texture=a._ViewportAPI__viewport_texture
 texture.frame_info={'frame_number':17,'resolution':(1600,900),'view':[a.view[i][j]for i in range(4)for j in range(4)],'projection':[a.projection[i][j]for i in range(4)for j in range(4)],'private_resource':object()}
 texture.frame_info['projection'][0]*=2 # controlled actual metadata differs from UI-canvas projection
 a._ViewportAPI__hydra_texture=types.SimpleNamespace(get_render_product_path=lambda:'/Render/CONTROLLED_PRODUCT')
 a._ViewportAPI__fill_frame=False
 getters={'supports_viewport':lambda _:True,'supports_window':lambda _:True,'supports_appwindow':lambda _:True,'is_app_window_attached':lambda x:True,'get_framebuffer_width':lambda x:app.size[0],'get_framebuffer_height':lambda x:app.size[1]}
 def view():return ns['r'].read_view(s,[])
 def run(getters=getters,app_window=lambda:app,context=lambda:state,view=view):return o.sample_render_output_observations(weakref.proxy(a),w,app_window,getters,ns['r'].CAMERA,context,view,lambda:(weakref.proxy(a),w))
 return s,a,state,setting,calls,app,w,getters,run

def positive():
 s,a,state,setting,calls,app,w,g,run=fixture();before=s.GetRootLayer().ExportToString();out=run();assert out['status']=='observed',out
 assert out['layoutProof']==out['frameSourceBindingProof']=='unresolved';assert out['deliveredViewportFrame']['projection']!=out['viewportCanvas']['projection'];assert out['deliveredViewportFrame']['resolution']==[1600,900];assert out['framebuffer']['size']==[1600,638];assert out['applicationWindow']['dpiScale']==1.25
 assert out['viewportWindow']['position']==[23,41] and out['viewportWindow']['innerContentRect']is None
 assert 'private_resource'not in json.dumps(out) and s.GetRootLayer().ExportToString()==before
 (T/'controlled-observed.json').write_text(json.dumps(out,indent=2))
check('actual literal ViewportAPI getters + real CPU USD + explicitly controlled metadata/IRenderer: distinct projections/raw dimensions, no source/layout authority',positive)

def frame_boundaries():
 bad=[True,-1,9007199254740992,float('nan'),float('inf'),17.,'17',None]
 for v in bad:
  s,api,state,setting,calls,app,w,g,run=fixture();api._ViewportAPI__viewport_texture.frame_info['frame_number']=v
  assert run()['deliveredViewportFrame']['status']=='unavailable'
 for key,vals in [('resolution',[(0,900),(1600,False),(16385,2),(16384,16384),(1.5,2),(1,),('1',2)]),('projection',[[1]*15,[True]*16,[float('nan')]*16,[1e19]*16]),('view',[[1]*17])]:
  for v in vals:
   s,api,state,setting,calls,app,w,g,run=fixture();api._ViewportAPI__viewport_texture.frame_info[key]=v;assert run()['deliveredViewportFrame']['status']=='unavailable'
 s,api,state,setting,calls,app,w,g,run=fixture();api._ViewportAPI__viewport_texture.frame_info={};assert run()['deliveredViewportFrame']['reason']=='missing-public-value'
check('strict frame ordinal/resolution/matrix boundaries preserve unavailable, never normalize',frame_boundaries)

def identity():
 for field,vals in [('sceneRevision',[True,-1,9007199254740992,1.,'1']),('displayGeneration',[True,-1,9007199254740992]),('projectHash',['bad',None])]:
  for v in vals:
   s,a,state,setting,calls,app,w,g,run=fixture();state[field]=v;assert run()['status']=='unavailable'
 for alter in [lambda a,w:setattr(a,'_ViewportAPI__viewport_id','bad\x00'),lambda a,w:setattr(w.viewport_widget,'viewport_api',types.SimpleNamespace(stage=None)),lambda a,w:setattr(a._ViewportAPI__viewport_texture,'camera_path','/World/Other'),lambda a,w:setattr(a,'_ViewportAPI__hydra_texture',types.SimpleNamespace(get_render_product_path=lambda:'z'*513))]:
  s,a,state,setting,calls,app,w,g,run=fixture();alter(a,w);assert run()['status']=='unavailable'
check('strict current context/API/public string/product/paired wrapper identity negatives',identity)

def mutation_fences():
 alterations=[lambda v:v[2].update(sceneRevision=2),lambda v:v[2].update(projectHash='b'*64),lambda v:v[2].update(displayGeneration=8),lambda v:v[2].update(stage=ns['Usd'].Stage.CreateInMemory()),lambda v:setattr(v[1]._ViewportAPI__viewport_texture,'camera_path','/World/Other'),lambda v:v[1]._ViewportAPI__viewport_texture.frame_info.update(frame_number=18),lambda v:v[1]._ViewportAPI__viewport_texture.frame_info['projection'].__setitem__(0,.125),lambda v:setattr(v[5],'size',(1600,590)),lambda v:setattr(v[6].frame,'computed_width',1400.),lambda v:setattr(v[6].viewport_widget,'visible',False),lambda v:setattr(v[1],'_ViewportAPI__time',ns['Usd'].TimeCode(1))]
 for alter in alterations:
  v=fixture();g=v[7];called=[0]
  def width(app):
   called[0]+=1
   if called[0]==1:alter(v)
   return 1600
  g['get_framebuffer_width']=width;out=v[8]();assert out['status']=='unavailable',out
 s,a,state,setting,calls,app,w,g,run=fixture();newapp=App();n=[0]
 def owner():n[0]+=1;return app if n[0]==1 else newapp
 assert run(app_window=owner)['reason']=='changed-during-observation'
 s,a,state,setting,calls,app,w,g,run=fixture();n=[0]
 def view():
  n[0]+=1;v=ns['r'].read_view(s,[])
  if n[0]>1:v['selectedUids']=['changed']
  return v
 assert run(view=view)['reason']=='changed-during-observation'
check('after-all-getters source/frame/camera/viewport/app/rect/visibility/full-view fences',mutation_fences)

def unavailable():
 s,a,state,setting,calls,app,w,g,run=fixture();assert run(getters=None)['reason']=='unsupported-runtime'
 assert run(app_window=lambda:None)['applicationWindow']['reason']=='headless-or-unattached'
 g['is_app_window_attached']=lambda _:False;assert run()['framebuffer']['reason']=='headless-or-unattached'
 g['is_app_window_attached']=lambda _:True;app.size=(0,638);assert run()['applicationWindow']['status']=='unavailable'and run()['framebuffer']['status']=='unavailable'
 s,a,state,setting,calls,app,w,g,run=fixture();app.dpi=True;assert run()['applicationWindow']['status']=='unavailable'
 del w.frame.computed_width;assert run()['viewportWindow']['status']=='unavailable'
 assert o._bounded({'x':'x'*(6144-8)}) and len(json.dumps({'x':'x'*(6144-8)},separators=(',',':')).encode())==6144
 try:o._bounded({'x':'x'*(6145-8)});raise AssertionError('must reject')
 except ValueError:pass
check('headless/unattached/zero/missing/type/cap unavailable; no requested-size fallback',unavailable)

def source_gate():
 # Actual files are hashed; bindings themselves are explicitly controlled.
 # No native module import/interface/GPU operation occurs in this CPU test.
 byname={}
 for name,base,_ in o.REVIEWED_SOURCES:
  root=Path('/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2')/base
  candidates={'omni.kit.widget.viewport.api':'omni/kit/widget/viewport/api.py','omni.kit.viewport.window.window':'omni/kit/viewport/window/window.py','omni.hydratexture':'omni/hydratexture/__init__.py','omni.appwindow':'omni/appwindow/__init__.py','omni.kit.renderer.bind':'omni/kit/renderer/bind/__init__.py','omni.ui':'omni/ui/__init__.py'}
  byname[name]=types.SimpleNamespace(__file__=str(root/candidates[name]))
 getters=types.SimpleNamespace(is_app_window_attached=lambda _:True,get_framebuffer_width=lambda _:1600,get_framebuffer_height=lambda _:638)
 byname['omni.kit.renderer.bind'].get_renderer_interface=lambda:getters
 byname['omni.kit.widget.viewport.api'].ViewportAPI=ns['api_mod'].ViewportAPI
 byname['omni.kit.viewport.window.window'].ViewportWindow=Window
 byname['omni.appwindow'].IAppWindow=App
 with patch.object(o.importlib,'import_module',side_effect=lambda n:byname[n]):assert o.reviewed_runtime_getters()is not None
 byname['omni.appwindow'].__file__='/unreviewed/omni/appwindow/__init__.py'
 with patch.object(o.importlib,'import_module',side_effect=lambda n:byname[n]):assert o.reviewed_runtime_getters()is None
 # Actual drift refused by exact digest. No installed file is modified.
 with patch.object(o.importlib,'import_module',side_effect=lambda n:byname[n]),patch.object(o.hashlib,'sha256',return_value=types.SimpleNamespace(hexdigest=lambda:'0'*64)):
  assert o.reviewed_runtime_getters()is None
check('actual installed bytes/type definitions reviewed; controlled interface only; unknown module/path/hash fail closed',source_gate)

def route():
 s,a,state,setting,calls,app,w,g,run=fixture();ns['contexts'][None]=types.SimpleNamespace(get_stage=lambda:s,get_selection=lambda:types.SimpleNamespace(get_selected_prim_paths=lambda:[]))
 sys.modules['carb.settings'].get_settings=lambda:setting;sys.modules['omni.kit.viewport.utility'].get_active_viewport_and_window=lambda:(weakref.proxy(a),w);sys.modules['omni.appwindow'].get_default_app_window=lambda:app
 c=ns['ext'].CfdBridgeExtension();c._render_uid_paths=state['uidPaths'];c._scene_revision=1;c._render_project_hash=ns['H'];c._last_scene_request_id='scene';c._display_generation=7;c._grid_raster=None;c._bounded_display_readback=ns['p'].ReadbackController(True);acks=[];c._render_ack=lambda rid,**kw:acks.append(dict(requestId=rid,**kw))
 with patch.object(o,'reviewed_runtime_getters',return_value=g):c._route_render_command('getViewState',ns['payload']())
 reply=acks[-1];assert reply['ok'],reply;obs=reply['diagnostics']['renderOutputObservations'];assert obs['status']=='observed' and obs['frameSourceBindingProof']=='unresolved';assert reply['diagnostics']['pixelVerified']is False
 (T/'controlled-route-reply.json').write_text(json.dumps(reply,indent=2))
 # App/media/ordinary ACK source and authorization behavior unchanged.
 oldlen=len(acks)
 with patch.object(o,'reviewed_runtime_getters',side_effect=AssertionError('plain may not load getters')):
  c._route_render_command('getViewState',json.dumps({'requestId':'plain-new'}))
 assert len(acks)==oldlen+1 and 'diagnostics'not in acks[-1]
 c._bounded_display_readback=ns['p'].ReadbackController(False)
 with patch.object(o,'reviewed_runtime_getters',side_effect=AssertionError('disabled route may not load native getters')):
  c._route_render_command('getViewState',ns['payload']())
 assert not acks[-1]['ok'] and acks[-1]['error']=='bounded-display:disabled'
 # Final existing authored-camera/source guards also cover work performed by A.
 c._bounded_display_readback=ns['p'].ReadbackController(True);g['get_framebuffer_width']=lambda _:state.update(projectHash='b'*64) or 1600
 c._render_project_hash=ns['H'];state['projectHash']=ns['H']
 # mutation the actual native context uses, not merely fixture-state
 g['get_framebuffer_width']=lambda _:setattr(c,'_render_project_hash','b'*64) or 1600
 with patch.object(o,'reviewed_runtime_getters',return_value=g):c._route_render_command('getViewState',ns['payload']())
 assert not acks[-1]['ok'] and acks[-1]['error']=='bounded-display:source-context-changed'
check('actual IExt diagnostics → controller → A → bounded ACK; ordinary branch and existing final source fences',route)
print(json.dumps({'scope':'real CPU USD/literal installed ViewportAPI + actual production IExt/controller; renderer/frame values CONTROLLED, no live/GPU/pixel proof','count':len(checks),'checks':checks},indent=2))
(T/'native-a-results.json').write_text(json.dumps({'count':len(checks),'checks':checks},indent=2)+'\n')
