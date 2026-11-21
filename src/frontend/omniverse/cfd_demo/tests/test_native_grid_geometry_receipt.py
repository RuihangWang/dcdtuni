"""PRIVATE controlled CPU USD receipt guards; no Kit/render/encoded proof.

These controls exercise real composed USD and typed native-contract closure.
The native sampler protocol is controlled; this is not a real camera acceptance.
"""
import copy,json,sys,types
from dataclasses import replace
from pathlib import Path
import pytest
BR=Path(__file__).resolve().parents[1]/'kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
package=types.ModuleType('native_receipt_bridge');package.__path__=[str(BR)]
sys.modules['native_receipt_bridge']=package
from native_receipt_bridge import grid_raster as g,grid_viewport as v,render_scene as r

def fixture():
 from pxr import Usd,UsdGeom
 stage=Usd.Stage.CreateInMemory();UsdGeom.Xform.Define(stage,r.ROOT)
 context={'projectUid':'controlled-project','projectContentSha256':'a'*64,'revision':2,'displayGeneration':1}
 matrix=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,-.005,0.,1.]
 grid={'visible':True,'spacingM':.6,'divisions':4,'extentM':2.4,'centerColor':[.2,.3,.4],'lineColor':[.1,.2,.3],'opacity':.6,'matrix':matrix}
 binding={'projectUid':context['projectUid'],'projectContentSha256':context['projectContentSha256'],'sceneRevision':2,'viewRevision':3}
 view={'backgroundGrid':grid,'backgroundGridRaster':{'metricVersion':2,'line':{'owner':'CONTROLLED_GL','drawingBuffer':[800,600],'css':[800,600],'pixelRatio':1,'aliasedRange':[1,1],'effectiveWidthPx':1,'coordinateFrame':'scene-world','worldToClip':matrix},'output':{'owner':'CONTROLLED_MEDIA','revision':1,'decoded':[1280,560],'css':[800,600],'layoutCss':[800,600],'objectFit':'contain'},'binding':binding},'camera':{'position':[0.,3.,8.],'target':[0.,0.,0.],'up':[0.,1.,0.],'fov':50.,'aspect':1.5,'near':.1,'far':100.},'selectedUids':[],'timeSeconds':None}
 request_view=copy.deepcopy(view);r.apply_view(stage,view);view=r.read_view(stage)
 token=json.dumps({k:view.get(k) for k in ('backgroundGrid','backgroundGridRaster','camera','timeSeconds')},sort_keys=True,separators=(',',':'),allow_nan=False)
 obs=v.NativeGridObservation(object(),object(),('ControlledViewport','','/Render/Controlled',('time',0.)),json.dumps(context,sort_keys=True,separators=(',',':')),token,r._background_grid_intent_json(grid),3,tuple(matrix),(1920,1080),(1920,1080),4)
 current={'context':copy.deepcopy(context),'observation':obs,'phase':'ready'}
 c=g.GridRasterCoordinator(lambda _:copy.deepcopy(current['context']),lambda _:None,lambda:10,native_sample=lambda _:{'phase':current['phase'],'nativeObservation':current['observation']})
 data={'points':[(-1.,0.,-.01),(1.,0.,-.01),(1.,0.,.01),(-1.,0.,.01)],'colors':[(.1,.2,.3)]*4,'counts':[4],'sourceLines':[0],'widthDomain':'native-viewport-image-pixel','encodedFrameProof':'unresolved','gridPixelVerified':False}
 contract=g.author_ribbons(stage,grid,data,'controlled-native-key',_native_observation=obs)
 c._contract=replace(contract,context=tuple(sorted(context.items())),intent=r._background_grid_intent_json(grid))
 c._serial=1;c._request={'stage':stage,'view':copy.deepcopy(request_view),'serial':1,'minimumFrame':0,'context':copy.deepcopy(context)}
 c.receipt={'phase':'unsupported','reason':'stream-layout-unresolved','viewRevision':3,'gridPixelVerified':False}
 return stage,c,current,view,obs

def test_current_composed_native_success_preserves_encoded_status_and_owns_copy():
 stage,c,current,view,obs=fixture()
 try:
  result=c.display_receipt(stage,view);native=result['nativeGeometry']
  assert result['phase']=='unsupported' and result['reason']=='stream-layout-unresolved'
  assert native['phase']=='synchronized' and native['gridPixelVerified'] is False and native['encodedFrameProof']=='unresolved'
  assert native['binding']==view['backgroundGridRaster']['binding']
  native['grid']['spacingM']=.7
  assert c.display_receipt(stage,view)['nativeGeometry']['grid']['spacingM']==.6
  assert 'nativeGeometry' not in c.receipt
 finally:c.close()

@pytest.mark.parametrize('kind',['missing-contract','wire-contract','foreign-stage','new-source','new-view','new-owner','new-api','new-projection','new-resolution','missing-frame','behind-minimum','hidden','wrong-echo','missing-raster'])
def test_native_receipt_declines_stale_incomplete_foreign_inputs(kind):
 from pxr import Usd
 stage,c,current,view,obs=fixture()
 try:
  if kind=='missing-contract':c._contract=None
  if kind=='wire-contract':c._contract=copy.copy(c._contract).__dict__
  if kind=='foreign-stage':stage=Usd.Stage.CreateInMemory()
  if kind=='new-source':current['context']['revision']=3
  if kind=='new-view':current['observation']=replace(obs,view_revision=4)
  if kind=='new-owner':current['observation']=replace(obs,owner=('other','','/Render/Other',('time',0.)))
  if kind=='new-api':current['observation']=replace(obs,api=object())
  if kind=='new-projection':current['observation']=replace(obs,projection=(0.,)*16)
  if kind=='new-resolution':current['observation']=replace(obs,resolution=(960,540))
  if kind=='missing-frame':current['phase']='pending'
  if kind=='behind-minimum':c._request['minimumFrame']=10
  if kind=='hidden':view['backgroundGrid']['visible']=False
  if kind=='wrong-echo':view['backgroundGrid']['spacingM']=.7
  if kind=='missing-raster':del view['backgroundGridRaster']
  assert 'nativeGeometry' not in c.display_receipt(stage,view)
 finally:c.close()

@pytest.mark.parametrize('kind',['points','colors','opacity','transform','extra-child','ancestor-material','ancestor-visibility','authored-camera','authored-view'])
def test_actual_composed_geometry_and_material_tamper_remove_success(kind):
 from pxr import Gf,Sdf,UsdGeom,UsdShade
 stage,c,current,view,obs=fixture()
 try:
  assert 'nativeGeometry' in c.display_receipt(stage,view)
  stroke=stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Stroke')
  if kind=='points':UsdGeom.Mesh(stroke).GetPointsAttr().Set([Gf.Vec3f(9,0,9)]*4)
  if kind=='colors':UsdShade.Shader(stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Looks/Grid/Surface')).GetInput('emissiveColor').Set(Gf.Vec3f(1,0,0))
  if kind=='opacity':UsdShade.Shader(stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Looks/Grid/Surface')).GetInput('opacity').Set(.1)
  if kind=='transform':UsdGeom.Xformable(stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT)).GetOrderedXformOps()[0].Set(Gf.Matrix4d().SetTranslate(Gf.Vec3d(0,1,0)))
  if kind=='extra-child':UsdGeom.Xform.Define(stage,r.BACKGROUND_GRID_ROOT+'/Foreign')
  if kind=='ancestor-material':
   material=UsdShade.Material.Define(stage,'/World/ForeignMaterial')
   ancestor=stage.GetPrimAtPath('/World/RenderScene')
   UsdShade.MaterialBindingAPI.Apply(ancestor).Bind(material,bindingStrength=UsdShade.Tokens.strongerThanDescendants)
  if kind=='ancestor-visibility':UsdGeom.Imageable(stage.GetPrimAtPath('/World/RenderScene')).MakeInvisible()
  if kind=='authored-camera':UsdGeom.Camera(stage.GetPrimAtPath(r.CAMERA)).GetFocalLengthAttr().Set(99.)
  if kind=='authored-view':
   authored=json.loads(stage.GetPrimAtPath(r.ROOT).GetCustomDataByKey('dcdtuni:viewJson'))
   authored['camera']['position'][0]=1.
   stage.GetPrimAtPath(r.ROOT).SetCustomDataByKey('dcdtuni:viewJson',json.dumps(authored))
  assert 'nativeGeometry' not in c.display_receipt(stage,view)
 finally:c.close()
