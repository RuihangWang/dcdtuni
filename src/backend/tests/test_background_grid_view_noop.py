"""Actual CPU USD public view tests; no stub authoring or permission forgery."""
import copy,json,unittest
from backend.tests import test_background_grid_usd as h
from pxr import Gf,Sdf,Tf,Usd,UsdGeom
r=h.r
class GridThinView(h.ActualUsdGrid):
    def setUp(self):
        super().setUp();self.view=copy.deepcopy(self.cases[3]['view']);self.author(self.view)
    def grid_events(self,action):
        events=[]
        def changed(notice,sender):
            events.extend(str(p)for p in list(notice.GetResyncedPaths())+list(notice.GetChangedInfoOnlyPaths())if str(p).startswith(r.BACKGROUND_GRID_ROOT))
        key=Tf.Notice.Register(Usd.Notice.ObjectsChanged,changed,self.stage)
        try:action()
        finally:key.Revoke()
        return events
    def test_same_grid_camera_selection_time_thin_updates_do_not_rebuild_grid(self):
        def update():
            for i in range(4):
                view=copy.deepcopy(self.view);view['camera']={'position':[4+i*.01,3,5],'target':[0,0,0],'up':[0,1,0],'fov':60,'aspect':16/9,'near':.01,'far':2000};view['selectedUids']=['box']if i%2 else[];view['timeSeconds']=float(i);r.apply_view(self.stage,view)
                self.assertEqual(json.loads(self.stage.GetPrimAtPath(r.ROOT).GetCustomDataByKey('dcdtuni:viewJson')),view)
                actual=r.read_view(self.stage)
                for field in ['backgroundGrid','timeSeconds']:self.assertEqual(actual[field],view[field])
                for actual_value,expected in zip(actual['camera']['position'],view['camera']['position']):self.assertAlmostEqual(actual_value,expected,places=12)
        self.assertEqual(self.grid_events(update),[])
    def test_actual_missing_visible_parts_force_rebuild_with_same_intent(self):
        for suffix in ['/Lines','/Looks/Grid','/Looks/Grid/VertexColor','']:
            with self.subTest(suffix=suffix):
                self.stage.RemovePrim(r.BACKGROUND_GRID_ROOT+suffix)
                self.assertTrue(self.grid_events(lambda:r.apply_view(self.stage,self.view)))
                self.assertTrue(self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Lines'))
                self.assertEqual(self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT).GetCustomDataByKey('dcdtuni:viewDecoration'),True)
    def test_actual_foreign_marker_visibility_and_binding_force_rebuild(self):
        parent=self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT);parent.SetCustomDataByKey('dcdtuni:viewDecoration',False)
        self.assertTrue(self.grid_events(lambda:r.apply_view(self.stage,self.view)))
        parent=self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT);parent.SetCustomDataByKey('dcdtuni:backgroundGridIntentJson','foreign-intent')
        self.assertTrue(self.grid_events(lambda:r.apply_view(self.stage,self.view)))
        parent=self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT);UsdGeom.Xformable(parent).GetOrderedXformOps()[0].Set(Gf.Matrix4d(1))
        self.assertTrue(self.grid_events(lambda:r.apply_view(self.stage,self.view)))
        parent=self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT);UsdGeom.Imageable(parent).MakeInvisible()
        self.assertTrue(self.grid_events(lambda:r.apply_view(self.stage,self.view)))
        self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Lines').GetRelationship('material:binding').ClearTargets(True)
        self.assertTrue(self.grid_events(lambda:r.apply_view(self.stage,self.view)))
    def test_hidden_first_change_same_and_legacy_removal_keep_actual_presence(self):
        hidden=copy.deepcopy(self.view);hidden['backgroundGrid']['visible']=False
        self.assertTrue(self.grid_events(lambda:r.apply_view(self.stage,hidden)))
        self.assertFalse(self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT+'/Lines'))
        changed=copy.deepcopy(hidden);changed['selectedUids']=['box'];self.assertEqual(self.grid_events(lambda:r.apply_view(self.stage,changed)),[])
        other=copy.deepcopy(self.cases[5]['view']);other['backgroundGrid']['visible']=False
        self.assertTrue(self.grid_events(lambda:r.apply_view(self.stage,other)))
        legacy={k:v for k,v in hidden.items()if k!='backgroundGrid'};self.assertTrue(self.grid_events(lambda:r.apply_view(self.stage,legacy)));self.assertFalse(self.stage.GetPrimAtPath(r.BACKGROUND_GRID_ROOT));self.assertEqual(self.grid_events(lambda:r.apply_view(self.stage,legacy)),[])
        self.assertTrue(self.grid_events(lambda:r.apply_view(self.stage,self.view)))
    def test_failed_changed_grid_restores_intent_and_permits_true_old_noop(self):
        literal=self.stage.GetRootLayer().ExportToString();original=Sdf.CopySpec;calls=[0]
        def failing(*args):
            calls[0]+=1
            if calls[0]==2:return False
            return original(*args)
        Sdf.CopySpec=failing
        try:
            with self.assertRaises(RuntimeError):r.apply_view(self.stage,copy.deepcopy(self.cases[5]['view']))
        finally:Sdf.CopySpec=original
        self.assertEqual(calls[0],3);self.assertEqual(self.stage.GetRootLayer().ExportToString(),literal);self.assertEqual(self.grid_events(lambda:r.apply_view(self.stage,self.view)),[])
    def test_invalid_view_cannot_bypass_gate_with_current_grid_marker(self):
        literal=self.stage.GetRootLayer().ExportToString();bad=copy.deepcopy(self.view);bad['backgroundGrid']['matrix'][0]=True
        with self.assertRaises(ValueError):r.apply_view(self.stage,bad)
        self.assertEqual(self.stage.GetRootLayer().ExportToString(),literal)
if __name__=='__main__':unittest.main()
