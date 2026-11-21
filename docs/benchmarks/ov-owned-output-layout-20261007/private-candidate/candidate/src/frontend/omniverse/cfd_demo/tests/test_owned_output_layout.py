"""Controlled public UI carriers: lifecycle/coherence, never native/pixel proof."""
from pathlib import Path
import ast,copy,importlib.util,sys,types,unittest
BR=Path(__file__).resolve().parents[1]/'kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
package=types.ModuleType('owned_bridge');package.__path__=[str(BR)];sys.modules['owned_bridge']=package
from owned_bridge import output_layout as o
M=[float(i%5==0) for i in range(16)]
class Frame:
 def __init__(self):self.screen_position_x=13.;self.screen_position_y=17.;self.computed_content_width=800.;self.computed_content_height=600.;self.computed_width=800.;self.computed_height=600.;self.clears=0
 def __enter__(self):return self
 def __exit__(self,*_):pass
 def clear(self):self.clears+=1
class Stack(Frame):
 def __init__(self):super().__init__();self.destroyed=False
 def destroy(self):self.destroyed=True
class Provider:
 width=800;height=600;is_reference_valid=True
 def __init__(self):self.destroy_calls=0;self.received=[]
 def set_image_data(self,*args,**kwargs):self.received.append((args,kwargs))
 def destroy(self):self.destroy_calls+=1
class Image(Frame):
 fail=False
 def __init__(self,provider,**kwargs):
  if Image.fail:Image.fail=False;raise RuntimeError('controlled UI create failure')
  super().__init__();self.fill_policy=kwargs.get('fill_policy','legacy');self.alignment=kwargs.get('alignment','legacy')
  self.destroyed=False
 def destroy(self):self.destroyed=True
UI=types.SimpleNamespace(ZStack=Stack,ImageWithProvider=Image,ImageProvider=Provider,IwpFillPolicy=types.SimpleNamespace(IWP_PRESERVE_ASPECT_FIT='fit'),Alignment=types.SimpleNamespace(CENTER='center'))
# Controlled public protocol for portable unit tests. The separate private
# SDK harness reuses these tests with the literal reviewed public class bodies.
class Base:
 def __init__(self,_):self.provider=None;self.image=None;self.stack=None
 @property
 def image_provider(self):return self.provider
 @image_provider.setter
 def image_provider(self,p):self.provider=p;self.image=Image(p)
 def create(self,frame,previous):
  p=previous.image_provider;self.destroy();self.provider=p;self.image=Image(p);self.stack=Stack();return self.stack
 def destroy(self):
  if self.image is not None:self.image.destroy()
  if self.stack is not None:self.stack.destroy()
  self.provider=self.image=self.stack=None
class Widget:
 @property
 def display_delegate(self):return self._ViewportWidget__display_delegate
 @display_delegate.setter
 def display_delegate(self,delegate):
  self._ViewportWidget__ui_frame.clear();delegate.create(self._ViewportWidget__ui_frame,self.display_delegate);self._ViewportWidget__display_delegate=delegate
class API:
 def __init__(self,stage):
  self.stage=stage;self.id='controlled-public-id';self.usd_context_name='';self.camera_path='/World/RenderCamera';self.render_product_path='/Render/Controlled';self.time=0;self.view=M[:];self.projection=M[:];self.transform=M[:];self.resolution=(800,600);self.full_resolution=(800,600);self.frame_info={'frame_number':3,'resolution':[800,600],'view':M[:],'projection':M[:]}
class App:
 def get_dpi_scale(self):return 1.25
 def get_ui_scale(self):return 1.5
class Contract(unittest.TestCase):
 def fixture(self,enabled=True):
  stage=object();api=API(stage);app=App();frame=Frame();provider=Provider();previous=Base(None);previous.image_provider=provider;widget=Widget();widget._ViewportWidget__ui_frame=frame;widget._ViewportWidget__display_delegate=previous;widget.viewport_api=api
  window=types.SimpleNamespace(viewport_widget=widget,app_window=app);context={'uid':'project','sceneRevision':1,'generation':7};view={'selectedUids':[],'timeSeconds':None};current={'api':api,'window':window};count={'factory':0}
  getters={'supports_viewport':lambda a:a.__class__ is API,'supports_window':lambda _:True,'supports_appwindow':lambda a:a.__class__ is App,'is_app_window_attached':lambda _:True,'get_framebuffer_width':lambda _:800,'get_framebuffer_height':lambda _:600}
  runtime={'getters':getters,'make_delegate':lambda:o._delegate(UI,Base),'supports_provider':lambda p:isinstance(p,Provider),'expected_policy':'fit','expected_alignment':'center'}
  def factory():count['factory']+=1;return runtime
  owner=o.OwnedOutputLayout(enabled,factory)
  def prepare():return owner.prepare(current['api'],current['window'],stage,'/World/RenderCamera',lambda:copy.deepcopy(context),lambda:copy.deepcopy(view),lambda:(current['api'],current['window']))
  return types.SimpleNamespace(**locals())
 def update(self,f):
  d=f.widget.display_delegate;opaque=object();d.update(f.api,opaque,None,None,presentation_key=31,metadata=None);return opaque
 def test_defaultoff_and_wire_values_do_zero_sdk_work(self):
  for enabled in [False,None,0,1,'true',{}, {'enabled':True}]:
   f=self.fixture(enabled);self.assertFalse(f.prepare());self.assertIsNone(f.owner.capture_current(f.api,f.window,f.app));f.owner.close();self.assertEqual(f.count['factory'],0);self.assertEqual(f.frame.clears,0)
 def test_actual_public_setter_delegate_update_restore_borrowed_provider(self):
  f=self.fixture();self.assertTrue(f.prepare());d=f.widget.display_delegate;self.assertIs(d.frame,f.frame);opaque=self.update(f);self.assertIs(f.provider.received[-1][0][0],opaque)
  sample=f.owner.observe_native_current(f.api,f.window,f.app);self.assertIsNotNone(sample);self.assertEqual(sample['measurements']['scales'],[1.25,1.5]);self.assertFalse(sample['pixelVerified']);self.assertEqual(sample['layoutProof'],'unresolved');self.assertIsNone(f.owner.capture_current(f.api,f.window,f.app))
  f.owner.close();self.assertIs(f.widget.display_delegate,f.previous);self.assertEqual(f.provider.destroy_calls,0);self.assertIs(f.previous.image_provider,f.provider)
 def test_external_delegate_replacement_is_not_overwritten_on_close(self):
  f=self.fixture();f.prepare();other=Base(None);other.image_provider=f.provider;f.widget.display_delegate=other;clears=f.frame.clears;f.owner.close();self.assertIs(f.widget.display_delegate,other);self.assertEqual(f.frame.clears,clears);self.assertEqual(f.provider.destroy_calls,0)
 def test_failed_actual_setter_create_restores_previous_cleared_frame(self):
  f=self.fixture();Image.fail=True;self.assertFalse(f.prepare());self.assertIs(f.widget.display_delegate,f.previous);self.assertGreater(f.frame.clears,0);self.assertIs(f.previous.image_provider,f.provider);self.assertEqual(f.provider.destroy_calls,0)
 def test_same_numeric_state_different_api_referent_retires(self):
  f=self.fixture();f.prepare();self.update(f);old=f.owner.current_lease();f.current['api']=API(f.stage);f.widget.viewport_api=f.current['api'];self.assertIsNone(f.owner.observe_native_current(f.api,f.window,f.app));self.assertTrue(f.prepare());self.update(f);new=f.owner.current_lease();self.assertFalse(o.same_layout_lease(old,new));f.owner.close()
 def test_view_advance_retires_same_owner_epoch(self):
  f=self.fixture();f.prepare();self.update(f);old=f.owner.current_lease();f.view['selectedUids']=['new'];self.assertIsNone(f.owner.current_lease());f.prepare();self.assertFalse(o.same_layout_lease(old,f.owner.current_lease()));f.owner.close()
 def test_changed_public_values_and_missing_stream_never_grant(self):
  for mut in [lambda f:setattr(f.provider,'width',0),lambda f:setattr(f.provider,'height',True),lambda f:setattr(f.provider,'is_reference_valid',False),lambda f:setattr(f.widget.display_delegate.image,'fill_policy','stretch'),lambda f:setattr(f.widget.display_delegate.frame,'computed_content_width',float('nan')),lambda f:setattr(f.window,'app_window',App()),lambda f:setattr(f.api,'resolution',(True,600))]:
   f=self.fixture();f.prepare();self.update(f);mut(f);self.assertIsNone(f.owner.observe_native_current(f.api,f.window,f.app));self.assertIsNone(f.owner.capture_current(f.api,f.window,f.app));f.owner.close()
 def test_late_identity_getter_precedes_final_frame_context_fence(self):
  for kind in ['context','frame','time','view']:
   f=self.fixture();f.prepare();self.update(f);calls=[0]
   def width(_):
    calls[0]+=1
    if calls[0]==2:
     if kind=='context':f.context['sceneRevision']=2
     elif kind=='frame':f.api.frame_info['frame_number']=4
     elif kind=='time':f.api.time=1
     else:f.view['selectedUids']=['changed']
    return 800
   f.getters['get_framebuffer_width']=width;self.assertIsNone(f.owner.observe_native_current(f.api,f.window,f.app));f.owner.close()
 def test_install_callback_mutation_restores_and_refuses(self):
  for mode in ('context','view','active-window','app-window','same-label-api'):
   f=self.fixture();original=f.runtime['make_delegate']
   def make():
    d=original();create=d.create
    def callback(*args,**kwargs):
     result=create(*args,**kwargs)
     if mode=='context':f.context['sceneRevision']=2
     elif mode=='view':f.view['selectedUids']=['changed']
     elif mode=='active-window':f.current['window']=types.SimpleNamespace(viewport_widget=f.widget,app_window=f.app)
     elif mode=='app-window':f.window.app_window=App()
     else:f.current['api']=API(f.stage)
     return result
    d.create=callback;return d
   f.runtime['make_delegate']=make;self.assertFalse(f.prepare());self.assertIs(f.widget.display_delegate,f.previous);self.assertEqual(f.provider.destroy_calls,0)
 def test_plain_equal_looking_lease_is_not_same_owner(self):
  a=o.LayoutLease(object(),1,'same');b=o.LayoutLease(object(),1,'same');self.assertFalse(o.same_layout_lease(a,b));self.assertFalse(o.same_layout_lease(a,{'owner':a.owner,'generation':1,'key':'same'}))
if __name__=='__main__':unittest.main(verbosity=2)
