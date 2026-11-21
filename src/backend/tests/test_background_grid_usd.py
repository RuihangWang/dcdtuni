"""Actual installed Kit CPU OpenUSD tests, controlled view data only."""
import copy,importlib.util,json,math,tempfile,unittest
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
SOURCE=REPO/'src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/render_scene.py'
spec=importlib.util.spec_from_file_location('actual_grid_render_scene',SOURCE)
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
GOLDEN=REPO/'src/frontend/react/test/fixtures/background-grid-real-three.json'
def packet(view):
    return {'schema':'dcdtuni.render-scene/v1','project':{'uid':'controlled-grid','name':'view only'},'coordinateSystem':{'upAxis':'Y','metersPerUnit':1,'handedness':'right'},'primitives':[{'id':'box/triangle','modelUid':'box','name':'controlled','kind':'triangles','positions':[0,0,0,1,0,0,0,1,0],'indices':[0,1,2],'matrix':[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],'visible':True,'material':{'color':[1,0,0],'emissive':[0,0,0],'opacity':1,'roughness':1,'metalness':0,'doubleSided':True,'unlit':True}}],'textures':{},'view':view,'warnings':[]}
class ActualUsdGrid(unittest.TestCase):
    def setUp(self):
        from pxr import Usd
        self.stage=Usd.Stage.CreateInMemory();self.tmp=tempfile.TemporaryDirectory();self.cases=json.loads(GOLDEN.read_text())['cases']
    def tearDown(self):self.tmp.cleanup()
    def author(self,view):return r.author_scene(self.stage,packet(view),Path(self.tmp.name))
    def test_actual_three_all_vertex_bytes_colors_transform_and_material(self):
        from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade
        count=0
        for case in self.cases:
            view=case['view'];receipt=self.author(view)
            self.assertEqual(set(receipt['uidPaths']),{'box'});self.assertEqual(receipt['primitives'],1)
            grid=self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT);geo=UsdGeom.BasisCurves(self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Lines'))
            self.assertFalse(grid.HasCustomDataKey('dcdtuni:modelUid'));self.assertFalse(grid.HasCustomDataKey('dcdtuni:primitiveId'))
            points=[component for p in geo.GetPointsAttr().Get()for component in p];self.assertEqual(points,case['positions'])
            colors=[component for p in UsdGeom.PrimvarsAPI(geo).GetPrimvar('displayColor').Get()for component in p];self.assertEqual(colors,case['colors'])
            count+=len(points)+len(colors)
            self.assertEqual(geo.GetTypeAttr().Get(),'linear');self.assertEqual(geo.GetWrapAttr().Get(),'nonperiodic')
            self.assertEqual(list(geo.GetCurveVertexCountsAttr().Get()),[2]*(len(points)//6))
            self.assertEqual(UsdGeom.Primvar(geo.GetWidthsAttr()).GetInterpolation(),'constant')
            self.assertAlmostEqual(geo.GetWidthsAttr().Get()[0],.001,places=8)
            matrix=UsdGeom.Xformable(grid).ComputeLocalToWorldTransform(0)
            for sample in case['samples']:
                i=sample['index'];actual=matrix.Transform(Gf.Vec3d(*case['positions'][i:i+3]))
                for a,b in zip(actual,sample['projectPosition']):self.assertAlmostEqual(a,b,places=9)
            material=UsdShade.MaterialBindingAPI(geo.GetPrim()).ComputeBoundMaterial()[0]
            shader=UsdShade.Shader(material.GetPrim().GetChild('Surface'))
            self.assertAlmostEqual(shader.GetInput('opacity').Get(),view['backgroundGrid']['opacity'],places=7)
            self.assertTrue(shader.GetInput('emissiveColor').HasConnectedSource())
            reader=UsdShade.Shader(material.GetPrim().GetChild('VertexColor'))
            self.assertEqual(reader.GetInput('varname').GetTypeName(),Sdf.ValueTypeNames.String)
            self.assertEqual(reader.GetInput('varname').Get(),'displayColor')
            self.assertEqual(UsdGeom.GetStageUpAxis(self.stage),'Y');self.assertEqual(UsdGeom.GetStageMetersPerUnit(self.stage),1)
        print('ACTUAL_THREE_USD_COMPONENT_EQUALITY_CHECKS',count)
    def test_hide_spacing_refresh_roundtrip_preserve_objects_results_and_source(self):
        from pxr import Usd,UsdGeom
        initial=copy.deepcopy(self.cases[3]['view']);receipt=self.author(initial)
        physical=self.stage.GetPrimAtPath(receipt['uidPaths']['box'][0]);layer=physical.GetPrimStack()[0].layer
        before=Usd.Stage.CreateInMemory();from pxr import Sdf
        Sdf.CreatePrimInLayer(before.GetRootLayer(),physical.GetPath().GetParentPath());Sdf.CopySpec(layer,physical.GetPath(),before.GetRootLayer(),physical.GetPath());physical_text=before.GetRootLayer().ExportToString()
        result=UsdGeom.Xform.Define(self.stage,r.RESULT_ROOT+'/ControlledKeep').GetPrim();result.SetCustomDataByKey('controlledSourceMarker','accepted-source-stays-independent')
        hidden=copy.deepcopy(initial);hidden['backgroundGrid']['visible']=False
        r.apply_view(self.stage,hidden);grid=self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT)
        self.assertEqual(UsdGeom.Imageable(grid).ComputeVisibility(),'invisible');self.assertFalse(self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Lines'))
        self.assertEqual(r.read_view(self.stage)['backgroundGrid'],hidden['backgroundGrid'])
        changed=copy.deepcopy(self.cases[5]['view']);r.apply_view(self.stage,changed)
        self.assertEqual(r.read_view(self.stage,[r.BACKGROUND_GRID_ROOT+'/Lines'])['selectedUids'],[])
        self.assertEqual(r.read_view(self.stage)['backgroundGrid'],changed['backgroundGrid'])
        self.assertEqual(self.stage.GetPrimAtPath(r.RESULT_ROOT+'/ControlledKeep').GetCustomDataByKey('controlledSourceMarker'),'accepted-source-stays-independent')
        after=Usd.Stage.CreateInMemory();Sdf.CreatePrimInLayer(after.GetRootLayer(),physical.GetPath().GetParentPath());Sdf.CopySpec(layer,physical.GetPath(),after.GetRootLayer(),physical.GetPath());self.assertEqual(after.GetRootLayer().ExportToString(),physical_text)
        legacy={k:v for k,v in initial.items()if k!='backgroundGrid'};r.apply_view(self.stage,legacy)
        self.assertFalse(self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT));self.assertNotIn('backgroundGrid',r.read_view(self.stage))
    def test_invalid_view_never_changes_existing_stage(self):
        view=copy.deepcopy(self.cases[3]['view']);self.author(view);literal=self.stage.GetRootLayer().ExportToString()
        for mutation in [lambda g:g.update(visible=1),lambda g:g.update(spacingM=float('nan')),lambda g:g.update(spacingM=-1),lambda g:g.update(extentM=g['extentM']+1),lambda g:g.update(divisions=3999),lambda g:g.update(opacity=float('inf')),lambda g:g.update(modelUid='invented'),lambda g:g['matrix'].__setitem__(0,True)]:
            value=copy.deepcopy(view);mutation(value['backgroundGrid'])
            with self.assertRaises(ValueError):r.apply_view(self.stage,value)
            self.assertEqual(self.stage.GetRootLayer().ExportToString(),literal)
    def test_view_grid_copy_failure_restores_previous_grid(self):
        from pxr import Sdf
        view=copy.deepcopy(self.cases[3]['view']);self.author(view);literal=self.stage.GetRootLayer().ExportToString();original=Sdf.CopySpec;calls=[0]
        def failing(*args):
            calls[0]+=1
            if calls[0]==2:return False
            return original(*args)
        Sdf.CopySpec=failing
        try:
            changed=copy.deepcopy(self.cases[5]['view'])
            with self.assertRaises(RuntimeError):r.apply_view(self.stage,changed)
        finally:Sdf.CopySpec=original
        self.assertEqual(calls[0],3)
        self.assertEqual(self.stage.GetRootLayer().ExportToString(),literal)
if __name__=='__main__':unittest.main()
