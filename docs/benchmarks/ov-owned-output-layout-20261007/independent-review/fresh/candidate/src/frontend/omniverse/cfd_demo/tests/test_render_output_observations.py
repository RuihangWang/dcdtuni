"""Controlled readonly getter contracts; no running Kit/native/GPU/pixel proof."""
from pathlib import Path
import copy
import importlib.util
import json
import types
import unittest
P=Path(__file__).resolve().parents[1]/'kit/extensions/dc.cfd_bridge/dc/cfd_bridge/render_output_observations.py'
spec=importlib.util.spec_from_file_location('controlled_render_output_observations',P)
o=importlib.util.module_from_spec(spec);spec.loader.exec_module(o)
M=[float(i%5==0)for i in range(16)]
class App:
 def get_size(self):return types.SimpleNamespace(x=1600,y=638)
 def get_width(self):return 1600
 def get_height(self):return 638
 def get_dpi_scale(self):return 1.25
 def get_ui_scale(self):return 1.5
class Contract(unittest.TestCase):
 def fixture(self):
  stage=object();context={'stage':stage,'sceneRevision':1,'projectHash':'a'*64,'displayGeneration':7};view={'selectedUids':[],'timeSeconds':None}
  api=types.SimpleNamespace(stage=stage,time=0,id='CONTROLLED_OWNER',usd_context_name='',camera_path='/World/RenderCamera',render_product_path='/Render/Controlled',view=M[:],projection=M[:],transform=M[:],resolution=(1600,900),full_resolution=(1600,900),fill_frame=False,frame_info={'frame_number':17,'resolution':[1600,900],'view':M[:],'projection':M[:],'resource':object()})
  api.frame_info['projection'][0]=2
  frame=types.SimpleNamespace(screen_position_x=23,screen_position_y=41,computed_width=1600.,computed_height=638.,computed_content_width=1600.,computed_content_height=638.)
  window=types.SimpleNamespace(frame=frame,viewport_widget=types.SimpleNamespace(viewport_api=api,name='',visible=True));app=App()
  g={'supports_viewport':lambda _:True,'supports_window':lambda _:True,'supports_appwindow':lambda _:True,'is_app_window_attached':lambda _:True,'get_framebuffer_width':lambda _:1600,'get_framebuffer_height':lambda _:638}
  run=lambda:o.sample_render_output_observations(api,window,lambda:app,g,'/World/RenderCamera',lambda:context,lambda:copy.deepcopy(view),lambda:(api,window))
  return api,window,app,g,context,view,run
 def test_projection_units_and_no_authority(self):
  *_,run=self.fixture();a=run();self.assertEqual(a['status'],'observed');self.assertNotEqual(a['viewportCanvas']['projection'],a['deliveredViewportFrame']['projection']);self.assertEqual(a['layoutProof'],'unresolved');self.assertEqual(a['frameSourceBindingProof'],'unresolved');self.assertIsNone(a['viewportWindow']['innerContentRect']);self.assertEqual(a['viewportWindow']['position'],[23,41]);self.assertNotIn('resource',json.dumps(a))
 def test_strict_ordinals(self):
  for v in [True,-1,9007199254740992,17.,'17',None,float('nan')]:
   with self.subTest(v=v):
    api,*_,run=self.fixture();api.frame_info['frame_number']=v;self.assertEqual(run()['deliveredViewportFrame']['status'],'unavailable')
 def test_dimensions_and_matrices(self):
  for key,v in [('resolution',[0,900]),('resolution',[16384,16384]),('resolution',[True,2]),('view',[1]*15),('projection',[float('inf')]*16)]:
   with self.subTest(key=key,v=v):
    api,*_,run=self.fixture();api.frame_info[key]=v;self.assertEqual(run()['deliveredViewportFrame']['status'],'unavailable')
 def test_current_change_with_same_pointer(self):
  for mutation in [lambda v:v[4].update(sceneRevision=2),lambda v:v[0].frame_info.update(frame_number=18),lambda v:setattr(v[1].frame,'computed_width',1400.),lambda v:v[5].update(selectedUids=['new'])]:
   v=self.fixture();v[3]['get_framebuffer_width']=lambda _:mutation(v) or 1600;self.assertEqual(v[6]()['status'],'unavailable')
 def test_active_window_switch_and_time(self):
  v=self.fixture();n=[0]
  def pair():
   n[0]+=1
   return (v[0],v[1]) if n[0]==1 else (v[0],types.SimpleNamespace(frame=v[1].frame,viewport_widget=v[1].viewport_widget))
  self.assertEqual(o.sample_render_output_observations(v[0],v[1],lambda:v[2],v[3],'/World/RenderCamera',lambda:v[4],lambda:v[5],pair)['reason'],'changed-during-observation')
  v=self.fixture();v[3]['get_framebuffer_width']=lambda _:setattr(v[0],'time',1) or 1600
  self.assertEqual(v[6]()['reason'],'changed-during-observation')
 def test_same_id_proxy_replacement_not_self_proof(self):
  v=self.fixture();old=v[0];replacement=types.SimpleNamespace(**old.__dict__)
  v[3]['get_framebuffer_width']=lambda _:setattr(v[1].viewport_widget,'viewport_api',replacement) or 1600
  self.assertEqual(v[6]()['reason'],'owner-mismatch')
 def test_final_metadata_getter_cannot_late_change_context(self):
  for kind in ['context','time']:
   v=self.fixture();old=v[0]
   class API:
    calls=0
    def __getattr__(self,key):
     if key=='frame_info':
      self.calls+=1
      if self.calls==2:
       if kind=='context':v[4]['sceneRevision']=2
       else:old.time=1
     return getattr(old,key)
   api=API();v[1].viewport_widget.viewport_api=api
   self.assertEqual(o.sample_render_output_observations(api,v[1],lambda:v[2],v[3],'/World/RenderCamera',lambda:v[4],lambda:v[5],lambda:(api,v[1]))['reason'],'changed-during-observation')
 def test_initial_same_labels_different_referent(self):
  v=self.fixture();replacement=types.SimpleNamespace(**v[0].__dict__)
  v[1].viewport_widget.viewport_api=replacement
  self.assertEqual(v[6]()['reason'],'owner-mismatch')
 def test_late_stored_proxy_getter_before_final_frame(self):
  v=self.fixture();api=v[0]
  class Widget:
   calls=0;name='';visible=True
   @property
   def viewport_api(self):
    self.calls+=1
    if self.calls==8:api.frame_info['frame_number']=18
    return api
  v[1].viewport_widget=Widget()
  self.assertEqual(v[6]()['reason'],'changed-during-observation')
 def test_late_window_frame_getter_before_final_frame(self):
  v=self.fixture();old=v[1]
  class Window:
   calls=0;viewport_widget=old.viewport_widget
   @property
   def frame(self):
    self.calls+=1
    if self.calls==2:v[0].frame_info['frame_number']=18
    return old.frame
  w=Window()
  self.assertEqual(o.sample_render_output_observations(v[0],w,lambda:v[2],v[3],'/World/RenderCamera',lambda:v[4],lambda:v[5],lambda:(v[0],w))['reason'],'changed-during-observation')
 def test_standard_weakref_identity_only(self):
  import weakref
  class A:pass
  a=A();b=A();pa=weakref.proxy(a);pb=weakref.proxy(b)
  self.assertTrue(o._same_public_api(a,pa));self.assertTrue(o._same_public_api(pa,a));self.assertTrue(o._same_public_api(pa,pa))
  self.assertFalse(o._same_public_api(a,b));self.assertFalse(o._same_public_api(pa,pb))
 def test_default_gate_unknown(self):
  v=self.fixture();self.assertEqual(o.sample_render_output_observations(v[0],v[1],lambda:v[2],None,'/World/RenderCamera',lambda:v[4],lambda:v[5],lambda:(v[0],v[1]))['reason'],'unsupported-runtime')
 def test_unreviewed_class_guard_no_coercion(self):
  for key in ['supports_viewport','supports_window','supports_appwindow']:
   for value in [False,None,1,'true']:
    v=self.fixture();v[3][key]=lambda _,value=value:value
    self.assertEqual(v[6]()['reason'],'unsupported-runtime')
 def test_byte_cap(self):
  o._bounded({'x':'x'*(6144-8)})
  with self.assertRaises(ValueError):o._bounded({'x':'x'*(6145-8)})
if __name__=='__main__':unittest.main()
