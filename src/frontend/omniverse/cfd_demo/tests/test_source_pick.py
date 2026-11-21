"""Portable CPU USD source-query orchestration; controlled map/callbacks.

No SDK copying, native Hydra ray hit, video/camera delivery or trusted browser
proof. CameraUtil conformance is an explicitly controlled CPU stand-in, while
Gf cameras, projection matrices, source authoring and USD visibility are real.
"""
import asyncio,copy,types,sys,json
from pathlib import Path
import pytest
pxr=pytest.importorskip('pxr',reason='optional CPU USD/Gf runtime is required')
from pxr import Gf,Usd,UsdGeom
EXT=Path(__file__).resolve().parents[1]/'kit/extensions/dc.cfd_bridge'
sys.path.insert(0,str(EXT))
from dc.cfd_bridge import source_pick as q,render_scene as r
@pytest.fixture(autouse=True)
def controlled_cpu_conformance(monkeypatch):
 util=types.ModuleType('pxr.CameraUtil');util.MatchHorizontally='controlled-horizontal'
 def conform(camera,policy,aspect):
  assert policy==util.MatchHorizontally
  camera.verticalAperture=camera.horizontalAperture/aspect
 util.ConformWindow=conform
 monkeypatch.setattr(pxr,'CameraUtil',util,raising=False);monkeypatch.setitem(sys.modules,'pxr.CameraUtil',util)
 globals()['CameraUtil']=util;globals()['conform']=conform
def flat(m):return[float(m[i][j])for i in range(4)for j in range(4)]
def fixture(tmp_path,kind='box',reverse=False):
 uid='CDU1' if kind=='cdu' else 'BOX1';M=[float(i%5==0)for i in range(16)]
 material=dict(color=[.4,.5,.6],emissive=[0,0,0],opacity=1,roughness=.7,metalness=.4,doubleSided=True,unlit=False)
 primitives=[dict(id=f'{uid}/face/{i}',modelUid=uid,name=f'body-face-{i}',kind='triangles',positions=[-.5,0,0,.5,0,0,0,1,0],indices=[0,1,2],matrix=M[:],visible=True,material=copy.deepcopy(material))for i in range(4)]
 binding=dict(projectUid='Q_PROJECT',projectContentSha256='a'*64,sceneRevision=7,viewRevision=9)
 output=dict(owner='actual-test-video-owner',revision=3,decoded=[1192,796],css=[1191.2,796],layoutCss=[1191,796],objectFit='contain')
 raster=dict(metricVersion=2,line=dict(owner='reference',drawingBuffer=[1191,796],css=[1191.2,796],pixelRatio=1,aliasedRange=[1,1],effectiveWidthPx=1,coordinateFrame='scene-world',worldToClip=M[:]),output=output,binding=binding)
 grid=dict(visible=True,extentM=9,divisions=10,spacingM=.9,matrix=M[:],centerColor=[.2,.2,.2],lineColor=[.1,.1,.1],opacity=.6)
 view=dict(camera=dict(position=[8,7,10],target=[0,0,0],up=[0,1,0],fov=45,aspect=16/9,near=.1,far=400),selectedUids=[],timeSeconds=None,backgroundGrid=grid,backgroundGridRaster=raster)
 packet=dict(schema='dcdtuni.render-scene/v1',project=dict(uid='Q_PROJECT',name='query CPU'),coordinateSystem=dict(upAxis='Y',handedness='right',metersPerUnit=1),primitives=primitives,textures={},view=view,warnings=[])
 stage=Usd.Stage.CreateInMemory();receipt=r.author_scene(stage,packet,tmp_path)
 cam=UsdGeom.Camera(stage.GetPrimAtPath(r.CAMERA));av=cam.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).GetInverse();ac=cam.GetCamera(Usd.TimeCode.Default());conform(ac,CameraUtil.MatchHorizontally,16/9);ap=ac.frustum.ComputeProjectionMatrix()
 class API:
  def map_ndc_to_texture_pixel(self,ndc):
   # Sentinel mapping tests orchestration and passed NDC, not Hydra rasterization.
   self.last_ndc=tuple(ndc);return self.map_pixel,self
  def request_query(self,pixel,callback,**kwargs):
   assert kwargs['add_outline'] is False and kwargs['y_down'] is True
   self.query_count+=1;self.query_args=(pixel,kwargs);self.callback=callback
   if self.schedule is not None:self.schedule(callback,pixel)
   return self.accepted
  @property
  def frame_info(self):
   if self.on_frame:self.on_frame(self)
   projection=flat(self.ap*Gf.Matrix4d(1,0,0,0,0,1,0,0,0,0,-.5,0,0,0,.5,1)) if reverse else flat(self.ap)
   return dict(frame_number=self.frame_number,resolution=list(self.resolution),view=flat(self.av),projection=projection)
 api=API();api.stage=stage;api.camera_path=r.CAMERA;api.time=Usd.TimeCode.Default();api.full_resolution=api.resolution=(1920,1080);api.av=av;api.ap=ap;api.frame_number=10;api.on_frame=None;api.query_count=0;api.accepted=True;api.map_pixel=(137,251)
 paths=list(receipt['uidPaths'][uid]);api.schedule=lambda cb,p:asyncio.get_running_loop().call_soon(cb,paths[0],(0,0,0),p)
 frame=types.SimpleNamespace(screen_position_x=0,screen_position_y=0,computed_width=1192,computed_height=796,computed_content_width=1192,computed_content_height=796)
 delegate=types.SimpleNamespace(size=(1192,796));widget=types.SimpleNamespace(viewport_api=api,display_delegate=delegate)
 app=types.SimpleNamespace(get_position=lambda:(0,0),get_size=lambda:(1192,796),get_dpi_scale=lambda:1,get_ui_scale=lambda:1)
 window=types.SimpleNamespace(viewport_widget=widget,frame=frame,app_window=app)
 workspace=types.SimpleNamespace(get_main_window_width=lambda:1192,get_main_window_height=lambda:796,get_dpi_scale=lambda:1)
 getters=dict(supports_viewport=lambda a:a is api,supports_window=lambda w:w is window,supports_appwindow=lambda a:a is app,
  is_app_window_attached=lambda a:True,get_framebuffer_width=lambda a:1192,get_framebuffer_height=lambda a:796)
 settings=types.SimpleNamespace(get=lambda k:{'/exts/omni.kit.livestream.app/primaryStream/streamType':'webrtc','/exts/omni.kit.livestream.app/primaryStream/signalPort':8302}.get(k))
 state=dict(api=api,window=window,view=view,context=dict(projectUid='Q_PROJECT',sceneRevision=7,projectHash='a'*64,displayGeneration=11,uidPaths=receipt['uidPaths'],primitiveIds={p['id']:uid for p in primitives}))
 runtime=lambda:(api,window,app,workspace,getters,settings,lambda:(state['api'],state['window']))
 req=dict(binding=dict(projectUid='Q_PROJECT',projectHash='a'*64,sceneRevision=7,viewRevision=9),owner=output['owner'],rasterRevision=3,decoded=[1192,796],point=[.5,.5],gestureId=1,signalPort=8302)
 return locals()

def invoke(f,controller=None):
 controller=controller or q.SourcePickController();before=f['stage'].GetRootLayer().ExportToString()
 result=asyncio.run(controller.query(f['req'],f['runtime'],lambda:f['state']['context'],lambda:f['state']['view'],timeout=.01))
 assert f['stage'].GetRootLayer().ExportToString()==before
 return result
@pytest.mark.parametrize('kind',['box','cdu'])
@pytest.mark.parametrize('part',range(4))
def test_owned_parts_map_to_one_current_uid_without_usd_selection(tmp_path,kind,part):
 f=fixture(tmp_path,kind);f['api'].schedule=lambda cb,p:asyncio.get_running_loop().call_soon(cb,f['paths'][part],(0,0,0),p)
 result=invoke(f);assert result['status']=='hit';assert result['event']['modelUid']==f['uid'];assert result['event']['action']=={'type':'pick'}
 assert f['api'].last_ndc==pytest.approx((0,0));assert f['api'].query_args[0]==(137,251)
 assert f['api'].query_args[1]['add_outline'] is False and f['api'].query_args[1]['y_down'] is True
@pytest.mark.parametrize('reverse',[False,True])
def test_controlled_gl_or_recognized_reverse_frame(tmp_path,reverse):
 assert invoke(fixture(tmp_path,reverse=reverse))['status']=='hit'
def test_same_uid_repeat_and_empty_do_not_queue_modifier_intent(tmp_path):
 f=fixture(tmp_path);c=q.SourcePickController();assert invoke(f,c)['status']=='hit'
 f['req']['gestureId']=2;assert invoke(f,c)['status']=='hit';assert invoke(f,c)['reason']=='source-pick-replayed'
 f['req']['gestureId']=3;f['api'].schedule=lambda cb,p:asyncio.get_running_loop().call_soon(cb,'',(0,0,0),p)
 result=invoke(f,c);assert result['status']=='miss' and 'event'not in result
@pytest.mark.parametrize('fault',['hidden','foreign','old-owner','old-view','old-hash','fb-decoded','native-scale','frame-canvas','outside-native','outside-texture','timeout','query-rejected','callback-pixel','default-lens'])
def test_refuses_unmapped_stale_hidden_foreign_and_failed_queries(tmp_path,fault):
 f=fixture(tmp_path)
 if fault=='hidden':UsdGeom.Imageable(f['stage'].GetPrimAtPath(f['paths'][0])).MakeInvisible()
 elif fault=='foreign':f['api'].schedule=lambda cb,p:asyncio.get_running_loop().call_soon(cb,'/World/RenderResultLayers/x',(0,0,0),p)
 elif fault=='old-owner':f['req']['owner']='retired'
 elif fault=='old-view':f['req']['binding']['viewRevision']=8
 elif fault=='old-hash':f['req']['binding']['projectHash']='b'*64
 elif fault=='fb-decoded':f['getters']['get_framebuffer_width']=lambda a:1191
 elif fault=='native-scale':f['app'].get_ui_scale=lambda:2
 elif fault=='frame-canvas':f['delegate'].size=(1190,796)
 elif fault=='outside-native':f['frame'].screen_position_x=1000
 elif fault=='outside-texture':f['api'].map_pixel=(-1,251)
 elif fault=='timeout':f['api'].schedule=None
 elif fault=='query-rejected':f['api'].accepted=False
 elif fault=='callback-pixel':f['api'].schedule=lambda cb,p:asyncio.get_running_loop().call_soon(cb,f['paths'][0],(0,0,0),(p[0]+1,p[1]))
 elif fault=='default-lens':f['api'].ap=Gf.Matrix4d(1,0,0,0,0,1,0,0,0,0,-1,-1,0,0,-2,0)
 result=invoke(f);assert result['status']=='refused' and 'event'not in result
 if fault=='fb-decoded':assert result['reason']=='source-pick-decoded-framebuffer-size-mismatch' and len(json.dumps(result).encode())<8192
@pytest.mark.parametrize('fault',['closing','scene','owner','uid','projection','last-camera-source'])
def test_closes_after_late_callbacks_and_public_getters(tmp_path,fault):
 f=fixture(tmp_path);c=q.SourcePickController()
 if fault=='last-camera-source':
  count=[0]
  def getter(api):
   count[0]+=1
   if count[0]==2:f['state']['context']['sceneRevision']=8
  f['api'].on_frame=getter
 else:
  def schedule(cb,p):
   def late():
    if fault=='closing':c.close()
    elif fault=='scene':f['state']['context']['sceneRevision']=8
    elif fault=='owner':f['state']['window']=types.SimpleNamespace()
    elif fault=='uid':f['state']['context']['primitiveIds'][f['primitives'][0]['id']]='foreign'
    elif fault=='projection':f['api'].ap=Gf.Matrix4d(f['api'].ap)*Gf.Matrix4d(2,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)
    cb(f['paths'][0],(0,0,0),p)
   asyncio.get_running_loop().call_soon(late)
  f['api'].schedule=schedule
 result=invoke(f,c);assert result['status']=='refused' and 'event'not in result
@pytest.mark.parametrize('field,value',[('point',[float('nan'),.5]),('gestureId',True),('gestureId',0),('decoded',[1192,0]),('signalPort',0)])
def test_request_bounds(field,value,tmp_path):
 f=fixture(tmp_path);f['req'][field]=value
 with pytest.raises(ValueError):q.parse_request(f['req'])
