"""Real USD ownership and controlled SDK getter/lifetime regression; no Kit/GPU/pixels."""
from pathlib import Path
import importlib.util
import sys
from types import SimpleNamespace
from pxr import Sdf,Usd,UsdGeom,UsdShade
import pytest
P=Path(__file__).resolve().parents[1]/'kit/extensions/dc.cfd_bridge/dc/cfd_bridge/active_material_parameters.py'
spec=importlib.util.spec_from_file_location('controlled_active_material_parameters',P)
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

def fixture():
 stage=Usd.Stage.CreateInMemory();g='/World/RenderScene/Geometry/p_'+'1'*24;m='/World/RenderScene/Looks/p_'+'1'*24
 geometry=UsdGeom.Mesh.Define(stage,g).GetPrim();geometry.SetCustomDataByKey('dcdtuni:modelUid','roof-fixture')
 material=UsdShade.Material.Define(stage,m);surface=UsdShade.Shader.Define(stage,m+'/Surface');surface.CreateIdAttr('UsdPreviewSurface');surface.CreateOutput('surface',Sdf.ValueTypeNames.Token);material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(),'surface');UsdShade.MaterialBindingAPI.Apply(geometry).Bind(material)
 return stage,{'stage':stage,'sceneRevision':1,'projectHash':'a'*64,'displayGeneration':1,'uidPaths':{'roof-fixture':[g]},'primitives':1}

class Handle:
 def __init__(self,events,name,valid=True):self.events=events;self.name=name;self.validity=valid
 def is_valid_interface(self):return self.validity
 def release(self):self.events.append('release-'+self.name)
class Value(Handle):
 def __init__(self,events,value,callback):super().__init__(events,'value');self.value=value;self.callback=callback
 def get_value(self):self.callback();return self.value
class Constant(Handle):
 def __init__(self,events,value,callback):super().__init__(events,'constant');self.value=value;self.callback=callback
 def get_value_as(self,_):return Value(self.events,self.value,self.callback)
class Expression(Handle):
 def __init__(self,events,value,callback):super().__init__(events,'expression',value is not None);self.value=value;self.callback=callback
 def get_kind(self):return 'constant'
 def get_interface(self,_):return Constant(self.events,self.value,self.callback)
class Arguments(Handle):
 def __init__(self,events,values,callback):super().__init__(events,'arguments');self.values=values;self.callback=callback
 def get_expression(self,field):return Expression(self.events,self.values.get(field),self.callback)
 def get_expression_as(self,_,field):return self.get_expression(field).get_interface(_)
class Call(Handle):
 def __init__(self,events,values,callback,fail):super().__init__(events,'call');self.values=values;self.callback=callback;self.fail=fail
 def get_mdl_function_definition(self):
  if self.fail:raise RuntimeError('PRIVATE_NATIVE_MESSAGE_NOT_WIRE')
  return '::rtx::UsdPreviewSurface::UsdPreviewSurface(float3)'
 def get_arguments(self):return Arguments(self.events,self.values,self.callback)
class Transaction(Handle):
 def __init__(self,events,values,callback,fail):super().__init__(events,'transaction');self.values=values;self.callback=callback;self.fail=fail
 def access_as(self,_,name):assert name=='actual-snapshot';return Call(self.events,self.values,self.callback,self.fail)
 def abort(self):self.events.append('abort')
class Backend:
 def __init__(self,events,values,callback,fail,inactive,scope,name):self.events=events;self.values=values;self.callback=callback;self.fail=fail;self.inactive=inactive;self.scope=scope;self.snapshot_name=name
 def createMdlEntity(self,path):self.events.append(('entity-path',path));return SimpleNamespace(valid=lambda:not self.inactive)
 def createMdlEntitySnapshot(self,entity):
  # 0.2.17 MdlEntitySnapshot deliberately has no valid() member.
  snapshot=SimpleNamespace(dbName=self.snapshot_name,dbScopeName=self.scope)
  assert not hasattr(snapshot,'valid');return snapshot
 def createEditingTransaction(self,scope):assert scope==self.scope;self.events.append(('transaction-scope',scope));return Transaction(self.events,self.values,self.callback,self.fail)
 def destroyMdlEntitySnapshot(self,_):self.events.append('destroy-snapshot')
 def destroyMdlEntity(self,_):self.events.append('destroy-entity')

def setup(monkeypatch,values=None,callback=lambda:None,fail=False,inactive=False,scope='',name='actual-snapshot'):
 stage,context=fixture();events=[];values=values or {'enable_opacity':False,'enable_specular_transmission':True}
 backend=Backend(events,values,callback,fail,inactive,scope,name);sdk=SimpleNamespace(attach_itransaction=lambda t:t,IFunction_call=object,IExpression=SimpleNamespace(Kind=SimpleNamespace(EK_CONSTANT='constant')),IExpression_constant=object,IValue_bool=object)
 messages=[];monkeypatch.setitem(sys.modules,'carb',SimpleNamespace(log_warn=messages.append,messages=messages))
 monkeypatch.setattr(a,'_backend',lambda:(backend,sdk));return stage,context,events

def test_real_usd_binding_observed_false_and_cleanup(monkeypatch):
 stage,c,e=setup(monkeypatch);before=stage.GetRootLayer().ExportToString();r=a.sample(lambda:c,'roof-fixture')
 assert r['status']=='observed' and r['records'][0]['flags']['enable_opacity']=={'status':'observed','value':False}
 assert r['records'][0]['flags']['enable_specular_transmission']=={'status':'observed','value':True}
 assert r['records'][0]['surfacePath']=='/World/RenderScene/Looks/p_'+'1'*24+'/Surface'
 assert e[-4:]==['abort','release-transaction','destroy-snapshot','destroy-entity']
 assert stage.GetRootLayer().ExportToString()==before and r['compiledGPUProgramRead'] is False
 assert ('transaction-scope','') in e and sys.modules['carb'].messages==[]
 # Both legal global and named scopes retain the same lifecycle and values.
 _,named,named_events=setup(monkeypatch,scope='actual-active-scope');named_result=a.sample(lambda:named,'roof-fixture')
 assert named_result['status']=='observed' and ('transaction-scope','actual-active-scope') in named_events
 assert named_events[-4:]==['abort','release-transaction','destroy-snapshot','destroy-entity']

def test_absent_bool_is_unavailable_not_default_false(monkeypatch):
 _,c,_=setup(monkeypatch,{'enable_opacity':False,'enable_specular_transmission':None});r=a.sample(lambda:c,'roof-fixture')
 assert r['status']=='observed' and r['records'][0]['flags']['enable_specular_transmission']=={'status':'unavailable','reason':'not-constant-or-absent'}

def test_inactive_or_throwing_backend_never_serializes_native_exception(monkeypatch):
 for options,expected_stage in [({'inactive':True},'entity-check'),({'fail':True},'function-name'),({'name':''},'snapshot-check'),({'scope':None},'snapshot-check'),({'name':3},'snapshot-check')]:
  _,c,e=setup(monkeypatch,**options);r=a.sample(lambda:c,'roof-fixture')
  assert r==a._unavailable('backend-unavailable') and 'PRIVATE' not in str(r)
  assert e[-1]=='destroy-entity'
  messages=sys.modules['carb'].messages
  assert len(messages)==1 and 'last-stage='+expected_stage in messages[0]
  assert 'PRIVATE' not in messages[0] and '/World' not in messages[0]
  if options.get('fail'):assert e[-4:]==['abort','release-transaction','destroy-snapshot','destroy-entity']

def test_wrong_owned_uid_refuses_before_backend(monkeypatch):
 stage,c,e=setup(monkeypatch);stage.GetPrimAtPath(c['uidPaths']['roof-fixture'][0]).SetCustomDataByKey('dcdtuni:modelUid','foreign')
 assert a.sample(lambda:c,'roof-fixture')['status']=='unavailable' and e==[]

def test_late_context_change_discards_flags(monkeypatch):
 _,c,e=setup(monkeypatch);backend,sdk=a._backend();backend.callback=lambda:c.update(displayGeneration=2)
 assert a.sample(lambda:c,'roof-fixture')['reason']=='source-context-changed' and e[-1]=='destroy-entity'

def test_no_target_never_imports_backend(monkeypatch):
 monkeypatch.setattr(a,'_backend',lambda:(_ for _ in ()).throw(AssertionError('must not access')))
 assert a.sample(lambda:{},None)==a._unavailable('target-missing')


def mdl_setup(monkeypatch, values=None, loaded=True):
 stage,c,e=setup(monkeypatch,values={'opacity':.6,'opacityThreshold':0.0} if values is None else values)
 backend,sdk=a._backend();sdk.IValue_float=type('ControlledFloat',(),{})
 material=UsdShade.Material(stage.GetPrimAtPath('/World/RenderScene/Looks/p_'+'1'*24))
 native=UsdShade.Shader.Define(stage,str(material.GetPath())+'/FractionalSurface')
 native.CreateImplementationSourceAttr(UsdShade.Tokens.sourceAsset)
 native.SetSourceAsset(Sdf.AssetPath('controlled-native.mdl'),'mdl')
 native.SetSourceAssetSubIdentifier('DcdtuniFractionalPreview','mdl')
 native.CreateOutput('out',Sdf.ValueTypeNames.Token)
 material.CreateSurfaceOutput('mdl').ConnectToSource(native.ConnectableAPI(),'out')
 def module_read(asset):
  e.append(('module-path',asset.path));return loaded
 monkeypatch.setattr(a,'_mdl_module_loaded',module_read)
 monkeypatch.setattr(a,'_renderer_readback',lambda:{'mode':{'status':'observed','value':'RealTimePathTracing'},'raytracingFractionalCutoutOpacity':{'status':'observed','value':True},'pathtracingFractionalCutoutOpacity':{'status':'observed','value':True}})
 return stage,c,e

def test_real_mdl_output_priority_and_constant_backend_opacity_without_compiled_claim(monkeypatch):
 stage,c,e=mdl_setup(monkeypatch);before=stage.GetRootLayer().ExportToString();r=a.sample(lambda:c,'roof-fixture');record=r['records'][0]
 assert record['surfacePath'].endswith('/FractionalSurface') and record['sourceType']=='mdl'
 assert ('entity-path',record['surfacePath']) in e and ('module-path','controlled-native.mdl') in e
 assert not any(type(x) is tuple and x[0]=='entity-path' and x[1].endswith('/Surface') for x in e)
 assert record['moduleLoaded'] is True and record['backendStatus']=='observed'
 assert record['parameters']['opacity']=={'status':'observed','value':.6}
 assert record['parameters']['opacityThreshold']=={'status':'observed','value':0.0}
 assert record['finalCutout']=={'status':'unavailable','reason':'compiled-material-not-exposed'}
 assert not record.get('flags') and r['compiledGPUProgramRead'] is False and r['encodedPixelsRead'] is False
 assert e[-4:]==['abort','release-transaction','destroy-snapshot','destroy-entity']
 assert stage.GetRootLayer().ExportToString()==before

def test_not_loaded_module_is_explicit_pending_and_never_enters_backend(monkeypatch):
 stage,c,e=mdl_setup(monkeypatch,loaded=False);before=stage.GetRootLayer().ExportToString()
 monkeypatch.setattr(a,'_backend',lambda:(_ for _ in ()).throw(AssertionError('must not enter backend')))
 r=a.sample(lambda:c,'roof-fixture');record=r['records'][0]
 assert record['moduleLoaded'] is False and record['backendStatus']=='pending-module' and record['backendReason']=='module-not-loaded'
 assert 'materialBackendFunctionName' not in record
 assert all(value=={'status':'unavailable','reason':'not-read'} for value in record['parameters'].values())
 assert e==[('module-path','controlled-native.mdl')]
 assert stage.GetRootLayer().ExportToString()==before

def test_native_nonconstant_parameter_is_not_replaced_by_authored_default(monkeypatch):
 _,c,e=mdl_setup(monkeypatch,values={'opacity':None,'opacityThreshold':0.0})
 r=a.sample(lambda:c,'roof-fixture');record=r['records'][0]
 assert record['parameters']['opacity']=={'status':'unavailable','reason':'not-constant-or-absent'}
 assert 'value' not in record['parameters']['opacity']
 assert e[-4:]==['abort','release-transaction','destroy-snapshot','destroy-entity']

def test_native_backend_failure_retains_module_evidence_and_cleanup_without_exception_text(monkeypatch):
 _,c,e=mdl_setup(monkeypatch);backend,_=a._backend();backend.fail=True
 r=a.sample(lambda:c,'roof-fixture');record=r['records'][0]
 assert record['moduleLoaded'] is True and record['backendStatus']=='unavailable' and record['backendReason']=='backend-unavailable'
 assert 'materialBackendFunctionName' not in record and 'PRIVATE' not in str(r)
 assert all(value=={'status':'unavailable','reason':'not-read'} for value in record['parameters'].values())
 assert e[-4:]==['abort','release-transaction','destroy-snapshot','destroy-entity']
 assert len(sys.modules['carb'].messages)==1 and 'PRIVATE' not in sys.modules['carb'].messages[0]

def test_late_module_phase_change_discards_pending_record(monkeypatch):
 _,c,e=mdl_setup(monkeypatch,loaded=False)
 def read(asset):c.update(displayGeneration=2);return False
 monkeypatch.setattr(a,'_mdl_module_loaded',read)
 r=a.sample(lambda:c,'roof-fixture')
 assert r['status']=='unavailable' and r['reason']=='source-context-changed' and 'records' not in r

def test_loaded_native_boolean_is_not_coerced_into_float_observation(monkeypatch):
 _,c,e=mdl_setup(monkeypatch,values={'opacity':False,'opacityThreshold':0.0})
 r=a.sample(lambda:c,'roof-fixture');record=r['records'][0]
 assert record['backendStatus']=='unavailable' and record['parameters']['opacity']=={'status':'unavailable','reason':'not-read'}
 assert e[-4:]==['abort','release-transaction','destroy-snapshot','destroy-entity']


@pytest.mark.parametrize('kind',['call','direct-call'])
def test_native_texture_expression_never_attempts_constant_cast(monkeypatch,kind):
 stage,c,e=mdl_setup(monkeypatch);before=stage.GetRootLayer().ExportToString()
 class TextureExpression(Handle):
  def __init__(self):super().__init__(e,'texture-expression')
  def get_kind(self):return kind
  def get_interface(self,_):raise RuntimeError('NONCONSTANT_MUST_NOT_CAST')
 class TextureArguments(Arguments):
  def get_expression(self,field):
   if field=='opacity':return TextureExpression()
   return super().get_expression(field)
  def get_expression_as(self,_,field):raise RuntimeError('OLD_CONSTANT_CAST_FAILURE')
 monkeypatch.setattr(sys.modules[__name__],'Arguments',TextureArguments)
 arguments=TextureArguments(e,{'opacityThreshold':0.0},lambda:None)
 with pytest.raises(RuntimeError,match='OLD_CONSTANT_CAST_FAILURE'):
  arguments.get_expression_as(object,'opacity')
 r=a.sample(lambda:c,'roof-fixture');record=r['records'][0]
 assert record['moduleLoaded'] is True and record['backendStatus']=='observed'
 assert record['parameters']['opacity']=={'status':'unavailable','reason':'not-constant-or-absent'}
 assert record['parameters']['opacityThreshold']=={'status':'observed','value':0.0}
 assert 'value' not in record['parameters']['opacity']
 assert record['finalCutout']=={'status':'unavailable','reason':'compiled-material-not-exposed'}
 assert 'release-texture-expression' in e
 assert e[-4:]==['abort','release-transaction','destroy-snapshot','destroy-entity']
 assert stage.GetRootLayer().ExportToString()==before and sys.modules['carb'].messages==[]
