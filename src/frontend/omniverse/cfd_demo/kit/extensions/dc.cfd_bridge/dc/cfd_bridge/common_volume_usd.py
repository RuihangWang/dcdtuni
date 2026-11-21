"""Original USD wiring for the existing installed Kit CAE/IndeX operator.

CPU authoring is atomic with the other common result layers. The source field
is already a wp.Volume, so the installed operator explicitly bypasses DAV
voxelization. No rescale API can override the public common LUT/domain.
"""
from pathlib import Path
import hashlib,json

def _api(prim,*names):
 from pxr import Sdf
 prim.SetMetadata('apiSchemas',Sdf.TokenListOp.CreateExplicit(list(names)))

def author_common_volume(stage,packet,nvdb_path,root_path,lut_bytes):
 from pxr import Gf,Sdf,UsdGeom,UsdShade,UsdVol,Vt
 volume=packet['volume'];grid=volume['grid'];lut=volume['lut']
 # Include the WHOLE canonical packet identity: a prior async operator cannot
 # target a later frame/LUT using the same prim path or its cache key.
 identity=hashlib.sha256(json.dumps(packet,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')).hexdigest()
 root=root_path+'/Volumes/v_'+identity
 UsdGeom.Xform.Define(stage,root)
 if not volume['visible']:
  UsdGeom.Imageable(stage.GetPrimAtPath(root)).MakeInvisible()
  return root
 if not isinstance(nvdb_path,dict)or set(nvdb_path)!={'scalar','validity'}or any(not isinstance(v,str)or not Path(v).is_file()for v in nvdb_path.values()):raise ValueError('verified common scalar/mask NanoVDB carriers are unavailable')
 dims=grid['dimensions'];origin=grid['originMeters'];spacing=grid['spacingMeters']
 dataset=stage.DefinePrim(root+'/Source','CaeDataSet');_api(dataset,'CaeVtkImageDataAPI')
 for name,type_,value in [('origin',Sdf.ValueTypeNames.Float3,Gf.Vec3f(*origin)),('spacing',Sdf.ValueTypeNames.Float3,Gf.Vec3f(*spacing)),('minExtent',Sdf.ValueTypeNames.Int3,Gf.Vec3i(0,0,0)),('maxExtent',Sdf.ValueTypeNames.Int3,Gf.Vec3i(*(n-1 for n in dims)))]:
  dataset.CreateAttribute('cae:vtk:'+name,type_,custom=False).Set(value)
 field=stage.DefinePrim(root+'/Source/Scalar','CaeNvdbFieldArray');_api(field,'CaeNanoVDBFieldArrayAPI')
 field.CreateAttribute('fileNames',Sdf.ValueTypeNames.AssetArray,custom=False).Set([Sdf.AssetPath(nvdb_path['scalar'])])
 field.CreateAttribute('fieldAssociation',Sdf.ValueTypeNames.Token,custom=False).Set('vertex')
 field.CreateAttribute('cae:nanovdb_field_array:origin',Sdf.ValueTypeNames.Int3,custom=False).Set(Gf.Vec3i(0,0,0))
 field.CreateAttribute('cae:nanovdb_field_array:dims',Sdf.ValueTypeNames.Int3,custom=False).Set(Gf.Vec3i(*dims))
 dataset.CreateRelationship('field:scalar',custom=False).SetTargets([field.GetPath()])
 mask=stage.DefinePrim(root+'/Source/Validity','CaeNvdbFieldArray');_api(mask,'CaeNanoVDBFieldArrayAPI')
 mask.CreateAttribute('fileNames',Sdf.ValueTypeNames.AssetArray,custom=False).Set([Sdf.AssetPath(nvdb_path['validity'])])
 mask.CreateAttribute('fieldAssociation',Sdf.ValueTypeNames.Token,custom=False).Set('vertex')
 mask.CreateAttribute('cae:nanovdb_field_array:origin',Sdf.ValueTypeNames.Int3,custom=False).Set(Gf.Vec3i(0,0,0))
 mask.CreateAttribute('cae:nanovdb_field_array:dims',Sdf.ValueTypeNames.Int3,custom=False).Set(Gf.Vec3i(*dims))
 dataset.CreateRelationship('field:validity',custom=False).SetTargets([mask.GetPath()])
 geometry=UsdVol.Volume.Define(stage,root+'/Volume');prim=geometry.GetPrim()
 _api(prim,'CaeVizOperatorAPI','CaeVizIndeXVolumeAPI','CaeVizDatasetSelectionAPI:source','CaeVizDatasetVoxelizationAPI:source','CaeVizFieldSelectionAPI:colors','CaeVizFieldSelectionAPI:validity')
 prim.CreateAttribute('cae:viz:operator:enabled',Sdf.ValueTypeNames.Bool,custom=False).Set(True)
 prim.CreateRelationship('cae:viz:dataset_selection:source:target',custom=False).SetTargets([dataset.GetPath()])
 prim.CreateRelationship('cae:viz:field_selection:colors:target',custom=False).SetTargets([field.GetPath()])
 prim.CreateAttribute('cae:viz:field_selection:colors:mode',Sdf.ValueTypeNames.Token,custom=False).Set('unchanged')
 prim.CreateRelationship('cae:viz:field_selection:validity:target',custom=False).SetTargets([mask.GetPath()])
 prim.CreateAttribute('cae:viz:field_selection:validity:mode',Sdf.ValueTypeNames.Token,custom=False).Set('unchanged')
 prim.CreateAttribute('omni:rtx:skip',Sdf.ValueTypeNames.Bool).Set(True)
 prim.CreateAttribute('nvindex:composite',Sdf.ValueTypeNames.Bool).Set(False)
 prim.CreateAttribute('nvindex:type',Sdf.ValueTypeNames.Token).Set('vdb')
 prim.CreateAttribute('outputs:volume',Sdf.ValueTypeNames.Token)
 prim.SetCustomDataByKey('cae:viz:block_sampling_distance_update',True)
 # A shared nominal ray step derives from this actual grid and the existing
 # public Three resolution quality policy. Rendering images may still differ
 # between engines; numeric node coordinates/LUT/opacity are common.
 size=max((n-1)*h for n,h in zip(dims,spacing));steps=min(max(dims[0],64)*3,768)
 prim.SetCustomDataByKey('nvindex.renderSettings:samplingDistance',Vt.Float(size/(steps*.5)))
 bounds=[origin,[a+(n-1)*h for a,n,h in zip(origin,dims,spacing)]]
 geometry.CreateExtentAttr().Set([Gf.Vec3f(*point)for point in bounds])
 importer=stage.DefinePrim(root+'/Volume/Importer','FieldAsset');geometry.CreateFieldRelationship('importer',importer.GetPath())
 material=UsdShade.Material.Define(stage,root+'/Volume/Material')
 colormap=stage.DefinePrim(root+'/Volume/Material/Colormap','Colormap')
 colormap.CreateAttribute('outputs:colormap',Sdf.ValueTypeNames.Token)
 colormap.CreateAttribute('colormapSource',Sdf.ValueTypeNames.String).Set('rgbaPoints')
 colormap.CreateAttribute('rgbaPoints',Sdf.ValueTypeNames.Float4Array).Set([Gf.Vec4f(*(c/255 for c in rgb),1)for rgb in lut_bytes(lut['palette'])])
 colormap.CreateAttribute('xPoints',Sdf.ValueTypeNames.FloatArray).Set([i/255 for i in range(256)])
 colormap.CreateAttribute('domain',Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(0.,1.))
 colormap.CreateAttribute('domainBoundaryMode',Sdf.ValueTypeNames.Token).Set('clampToEdge')
 loader=UsdShade.Shader.Define(stage,root+'/Volume/Material/DataLoader');loader.CreateIdAttr().Set('nv::omni::cae::index.PythonComputeTask')
 loader.CreateInput('enabled',Sdf.ValueTypeNames.Bool).Set(False)
 material.CreateOutput('nvindex:compute',Sdf.ValueTypeNames.Token).ConnectToSource(loader.CreateOutput('compute',Sdf.ValueTypeNames.Token))
 shader=UsdShade.Shader.Define(stage,root+'/Volume/Material/VolumeShader')
 shader.SetSourceAsset(str(Path(__file__).with_name('common_volume.xac')),'xac')
 shader.CreateInput('colormap',Sdf.ValueTypeNames.Token).ConnectToSource(colormap.GetAttribute('outputs:colormap').GetPath())
 for index,(name,type_,value)in enumerate([('origin',Sdf.ValueTypeNames.Float3,Gf.Vec3f(*origin)),('spacing',Sdf.ValueTypeNames.Float3,Gf.Vec3f(*spacing)),('dimensions',Sdf.ValueTypeNames.Float3,Gf.Vec3f(*dims)),('sentinel',Sdf.ValueTypeNames.Float,float(grid['sentinel'])),('domain',Sdf.ValueTypeNames.Float2,Gf.Vec2f(*lut['range'])),('opacity',Sdf.ValueTypeNames.Float,float(lut['opacity'])),('flatDomain',Sdf.ValueTypeNames.Float,1. if lut['range'][0]==lut['range'][1]else 0.)]):
  attr=shader.CreateInput(name,type_).GetAttr();attr.Set(value);attr.SetCustomDataByKey('nvindex.param',index)
 material.CreateOutput('nvindex:volume',Sdf.ValueTypeNames.Token).ConnectToSource(shader.CreateOutput('volume',Sdf.ValueTypeNames.Token))
 UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)
 prim.SetCustomDataByKey('dcdtuni:commonGridJson',json.dumps(grid,sort_keys=True))
 prim.SetCustomDataByKey('dcdtuni:rawLutDomain',Gf.Vec2d(*lut['range']))
 prim.SetCustomDataByKey('dcdtuni:fieldName',volume['field']['name']);prim.SetCustomDataByKey('dcdtuni:fieldUnit',volume['field']['unit'])
 return root

def retire_common_volumes(stage,root_path):
 """Disable old operators before deleting their uniquely owned paths."""
 root=stage.GetPrimAtPath(root_path)
 if root:
  from pxr import Usd
  for prim in Usd.PrimRange(root):
   for name in ['nvindex:composite','cae:viz:operator:enabled']:
    attr=prim.GetAttribute(name)
    if attr and attr.Get():attr.Set(False)
