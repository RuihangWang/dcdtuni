"""Actual authored USD inputs compared with loaded OpenUSD SDR shader registry.
Bare installed Kit Python REPRO also runs these; no Kit app/GPU/video claim.
"""
import importlib.util
from pathlib import Path
from pxr import Sdf,Sdr,Usd,UsdGeom,UsdShade
ROOT=Path(__file__).resolve().parents[2]/'frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge'
def module(name):
 spec=importlib.util.spec_from_file_location('tested_'+name,ROOT/(name+'.py'));value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def require_contract(reader_id,input_name):
 node=Sdr.Registry().GetShaderNodeByIdentifier(reader_id);assert node is not None
 expected=node.GetShaderInput('varname').GetTypeAsSdfType().GetSdfType()
 assert expected==Sdf.ValueTypeNames.String
 assert input_name.GetTypeName()==expected,('actual authored input type disagrees with SDR',reader_id,str(input_name.GetTypeName()),str(expected))
 assert input_name.Get()in ('displayColor','st')

def test_result_color_reader_uses_actual_sdr_varname_string_and_preserves_binding():
 from pxr import UsdShade
 mod=module('result_layers_usd');stage=Usd.Stage.CreateInMemory();prim=UsdGeom.Mesh.Define(stage,'/Controlled').GetPrim();mod._bind_material(stage,'/Material',prim,.7,vertex_colors=True)
 reader=UsdShade.Shader(stage.GetPrimAtPath('/Material/VertexColor'));require_contract('UsdPrimvarReader_float3',reader.GetInput('varname'))
 material=UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0];surface=UsdShade.Shader(stage.GetPrimAtPath('/Material/Surface'));assert material and surface.GetInput('emissiveColor').GetConnectedSource()[0].GetPrim()==reader.GetPrim();assert surface.GetInput('opacity').Get()==.7 or abs(surface.GetInput('opacity').Get()-.7)<1e-6

def scene_material(has_colors,textured):
 stage=Usd.Stage.CreateInMemory();source={'color':[.2,.3,.4],'emissive':[0,0,0],'unlit':True,'roughness':.6,'metalness':.3,'opacity':1}
 if textured:source['map']='controlled-texture'
 module('render_scene')._material(stage,'/SceneMaterial',source,{'controlled-texture':'controlled-image.png'},has_colors)
 return stage

def test_scene_color_reader_uses_actual_sdr_varname_string():
 stage=scene_material(True,False);reader=UsdShade.Shader(stage.GetPrimAtPath('/SceneMaterial/VertexColor'));require_contract('UsdPrimvarReader_float3',reader.GetInput('varname'))

def test_scene_uv_reader_uses_actual_sdr_varname_string_and_keeps_uv_texture_connection():
 stage=scene_material(False,True);reader=UsdShade.Shader(stage.GetPrimAtPath('/SceneMaterial/UV'));require_contract('UsdPrimvarReader_float2',reader.GetInput('varname'))
 texture=UsdShade.Shader(stage.GetPrimAtPath('/SceneMaterial/ColorTexture'));assert texture.GetInput('st').GetConnectedSource()[0].GetPrim()==reader.GetPrim()
