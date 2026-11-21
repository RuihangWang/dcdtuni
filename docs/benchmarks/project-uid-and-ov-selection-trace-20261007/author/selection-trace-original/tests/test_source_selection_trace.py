import ast
import copy
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REL = Path('src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge')
BEFORE = ROOT / 'before' / REL / 'extension.py'
AFTER = ROOT / 'candidate' / REL / 'extension.py'
spec = importlib.util.spec_from_file_location('private_trace', ROOT / 'candidate' / REL / 'source_selection_trace.py')
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)


def build(source, paths, *, enabled=False, hidden=(), bad=(), no_stage=False, suppress=False,
          cache=None, bound=True, bus_throws=False, sink_throws=False):
    calls, sent, lines = [], [], []
    class Prim:
        def __init__(self, path): self.path = path
        def GetCustomDataByKey(self, key):
            calls.append(('custom', self.path, key))
            if self.path in bad: return None
            return self.path.rsplit('/',1)[-1] if key.endswith('primitiveId') else self.path.split('/')[-2]
    class Stage:
        def GetPrimAtPath(self, path):
            calls.append(('prim',path)); return Prim(path)
    stage = Stage()
    class Selection:
        def get_selected_prim_paths(self): calls.append(('paths',)); return paths
    class Context:
        def get_stage(self): calls.append(('stage',)); return None if no_stage else stage
        def get_selection(self): calls.append(('selection',)); return Selection()
    ctx = Context()
    class Imageable:
        def __init__(self,prim):self.prim=prim
        def ComputeVisibility(self):
            calls.append(('visible',self.prim.path));return 'invisible' if self.prim.path in hidden else 'inherited'
    pxr = types.ModuleType('pxr')
    pxr.UsdGeom = types.SimpleNamespace(Imageable=Imageable,Tokens=types.SimpleNamespace(invisible='invisible'))
    sys.modules['pxr']=pxr
    class Bus:
        def push(self,signal,payload):
            calls.append(('push',signal));sent.append(copy.deepcopy(payload))
            if bus_throws:raise RuntimeError('controlled bus failure')
    omni = types.SimpleNamespace(usd=types.SimpleNamespace(get_context=lambda:ctx),
        kit=types.SimpleNamespace(app=types.SimpleNamespace(get_app=lambda:
            types.SimpleNamespace(get_message_bus_event_stream=lambda:Bus()))))
    carb = types.SimpleNamespace(events=types.SimpleNamespace(type_from_string=lambda value:value))
    tree=ast.parse(source.read_text())
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and any(
        isinstance(x,ast.FunctionDef) and x.name=='_emit_source_pick' for x in node.body))
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('_emit_source_pick','_trace_source_selection')]
    module=ast.fix_missing_locations(ast.Module(body=methods,type_ignores=[]))
    ns={'json':json,'omni':omni,'carb':carb};exec(compile(module,str(source),'exec'),ns)
    owner=types.SimpleNamespace(_suppress_ov_pick=suppress,_render_project_hash='a'*64 if bound else None,
        _scene_revision=17,_last_ov_pick=cache,_render_primitive_ids={},_render_uid_paths={})
    for path in paths:
        if isinstance(path,str) and path.startswith('/World/RenderScene/Geometry/'):
            uid=path.split('/')[-2];pid=path.rsplit('/',1)[-1]
            owner._render_primitive_ids[pid]=uid;owner._render_uid_paths.setdefault(uid,[]).append(path)
    def sink(line):
        lines.append(line)
        if sink_throws:raise RuntimeError('controlled sink failure')
    clock=[0.0]
    def tick():clock[0]+=1;return clock[0]
    owner._source_selection_trace=trace.SourceSelectionTrace(enabled,sink=sink,clock=tick,modifiers=lambda:1)
    for name in ('_emit_source_pick','_trace_source_selection'):
        if name in ns:setattr(owner,name,types.MethodType(ns[name],owner))
    error=None
    try:owner._emit_source_pick()
    except RuntimeError as e:error=str(e)
    return {'calls':calls,'sent':sent,'cache':owner._last_ov_pick,'error':error,'lines':lines,'owner':owner}


P='/World/RenderScene/Geometry/'
PAIR=[P+'UPS/u1',P+'CDU/c1',P+'CDU/c2']

class OriginalEmitterContract(unittest.TestCase):
    def test_default_off_actual_body_fidelity(self):
        variants=[{},dict(paths=[]),dict(paths=['/World/Unknown']),dict(hidden=(PAIR[1],)),
            dict(bad=(PAIR[1],)),dict(no_stage=True),dict(suppress=True),dict(bound=False),
            dict(cache=(17,('CDU','UPS'))),dict(bus_throws=True),
            dict(paths=[P+f'U{i}/p{i}' for i in range(1025)]),
            dict(paths=[P+'X'+'u'*254+f'/{i}'+'p'*990 for i in range(100)]),
            dict(paths=['/unknown']*65537)]
        for kwargs in variants:
            with self.subTest(variant=list(kwargs)):
                args=dict(kwargs);paths=args.pop('paths',PAIR)
                a,b=build(BEFORE,paths,**args),build(AFTER,paths,**args)
                for key in ('calls','sent','cache','error'):self.assertEqual(a[key],b[key],key)
                self.assertEqual(b['lines'],[])

    def test_enabled_observations_preserve_exact_native_effects(self):
        for kwargs,phase in [({},'bus-push-returned'),(dict(paths=[]),'bus-push-returned'),
            (dict(paths=['/World/Unknown']),'foreign-path'),(dict(hidden=(PAIR[1],)),'missing-or-hidden-prim'),
            (dict(bad=(PAIR[1],)),'binding-refused'),(dict(no_stage=True),'no-stage'),
            (dict(suppress=True),'suppressed-or-unbound'),(dict(bound=False),'suppressed-or-unbound'),
            (dict(cache=(17,('CDU','UPS'))),'dedupe'),
            (dict(paths=[P+f'U{i}/p{i}' for i in range(1025)]),'uid-cap'),
            (dict(paths=[P+'U'+str(i)+'u'*249+f'/p{i}'+'p'*980 for i in range(100)]),'payload-cap'),
            (dict(paths=['/unknown']*65537),'path-cap')]:
            with self.subTest(phase=phase):
                args=dict(kwargs);paths=args.pop('paths',PAIR)
                a,b=build(BEFORE,paths,**args),build(AFTER,paths,enabled=True,**args)
                for key in ('calls','sent','cache','error'):self.assertEqual(a[key],b[key],key)
                self.assertEqual(len(b['lines']),1)
                r=json.loads(b['lines'][0][len(trace.PREFIX):]);self.assertEqual(r['phase'],phase)
                self.assertFalse(r['gestureModifierProof']);self.assertFalse(r['transportDeliveryProof'])

    def test_baseline_missing_observation_control(self):
        # Actual before still pushes two UIDs, but provides no terminal-reason
        # observation. This is an observability RED, not selection failure.
        a=build(BEFORE,PAIR,enabled=True);b=build(AFTER,PAIR,enabled=True)
        self.assertEqual(len(a['sent'][0]['selection']),2);self.assertEqual(a['lines'],[])
        self.assertEqual(json.loads(b['lines'][0][len(trace.PREFIX):])['uidCount'],2)

    def test_full_fourteen_echo_dedupe_and_new_uid(self):
        paths=[P+f'Rack{i}/p{i}' for i in range(14)]
        a=build(AFTER,paths,enabled=True,cache=(17,tuple(sorted(f'Rack{i}' for i in range(14)))))
        self.assertEqual(a['sent'],[])
        self.assertEqual(json.loads(a['lines'][0][len(trace.PREFIX):])['phase'],'dedupe')
        b=build(AFTER,paths+[P+'CDU/c1'],enabled=True,cache=a['cache'])
        self.assertEqual(len(b['sent'][0]['selection']),15)
        self.assertEqual(b['sent'][0]['selection'][-1]['modelUid'],'CDU')

    def test_sink_failure_never_changes_product(self):
        a,b=build(BEFORE,PAIR),build(AFTER,PAIR,enabled=True,sink_throws=True)
        for key in ('calls','sent','cache','error'):self.assertEqual(a[key],b[key])

    def test_bus_throw_keeps_original_throw_and_cache(self):
        a,b=build(BEFORE,PAIR,bus_throws=True),build(AFTER,PAIR,enabled=True,bus_throws=True)
        for key in ('calls','sent','cache','error'):self.assertEqual(a[key],b[key])
        self.assertEqual(b['lines'],[])

    def test_default_off_source_restore_and_query_placement(self):
        before=ast.parse(BEFORE.read_text());after=ast.parse(AFTER.read_text())
        def method(tree,name):
            return next(n for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body
                if isinstance(n,ast.FunctionDef) and n.name==name)
        old,new=method(before,'_emit_source_pick'),copy.deepcopy(method(after,'_emit_source_pick'))
        class Restore(ast.NodeTransformer):
            def visit_Expr(self,node):
                if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=='_trace_source_selection':return None
                return self.generic_visit(node)
            def visit_For(self,node):
                self.generic_visit(node)
                if isinstance(node.iter,ast.Call) and isinstance(node.iter.func,ast.Name) and node.iter.func.id=='enumerate':
                    node.target=ast.Name(id='path',ctx=ast.Store());node.iter=ast.Name(id='paths',ctx=ast.Load())
                return node
        new=Restore().visit(new)
        self.assertEqual(ast.dump(old,include_attributes=False),ast.dump(new,include_attributes=False))
        route=method(after,'_route_render_command')
        query=next(n for n in ast.walk(route) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
            and isinstance(n.value.func,ast.Attribute) and n.value.func.attr=='_trace_source_selection'
            and n.value.args and isinstance(n.value.args[0],ast.Constant) and n.value.args[0].value=='state-query')
        self.assertIn("self._render_ack(request_id, ok=True, view=view",AFTER.read_text())
        text=AFTER.read_text();self.assertLess(text.index("self._render_ack(request_id, ok=True, view=view"),text.index("self._trace_source_selection('state-query'"))
        self.assertEqual(text.count("selection_paths = ctx.get_selection().get_selected_prim_paths()"),1)

class BoundedTrace(unittest.TestCase):
    def test_exact_true_gate_no_clock_or_input_when_off(self):
        def forbidden():raise AssertionError('must not call')
        for value in (None,False,0,1,'true','1',{},[]):
            t=trace.SourceSelectionTrace(value,clock=forbidden,modifiers=forbidden,sink=forbidden)
            t.observe('bus-push-returned',paths=PAIR);self.assertEqual(t._emitted,0)

    def test_public_installed_interface_adapter_no_subscription_or_setter(self):
        calls=[]
        module=types.ModuleType('carb');module.__path__=[]
        inp=types.ModuleType('carb.input')
        for name,value in [('SHIFT',1),('CONTROL',2),('ALT',4),('SUPER',8)]:
            setattr(inp,'KEYBOARD_MODIFIER_FLAG_'+name,value)
        inp.acquire_input_interface=lambda:types.SimpleNamespace(
            get_global_modifier_flags=lambda mask:calls.append(mask) or 1)
        module.input=inp
        old={name:sys.modules.get(name) for name in ('carb','carb.input')}
        try:
            sys.modules['carb']=module;sys.modules['carb.input']=inp
            self.assertEqual(trace.public_modifier_flags(),1)
            self.assertEqual(calls,[15])
        finally:
            for name,value in old.items():
                if value is None:sys.modules.pop(name,None)
                else:sys.modules[name]=value

    def test_readonly_public_flags_and_invalid_types(self):
        for raw,expected in ((1,1),(15,15),(0,0),(True,None),(-1,None),(1.0,None),(2**32,None)):
            lines=[];t=trace.SourceSelectionTrace(True,sink=lines.append,clock=lambda:0,modifiers=lambda:raw)
            t.observe('state-query',paths=PAIR,uids=['UPS','CDU']);r=json.loads(lines[0][len(trace.PREFIX):])
            self.assertEqual(r['modifierFlags'],expected);self.assertFalse(r['gestureModifierProof'])
            self.assertEqual(r['samplePhase'],'query')

    def test_cap_rate_and_bad_sink_reserve_before_api(self):
        now=[0.0];inputs=[];lines=[]
        t=trace.SourceSelectionTrace(True,sink=lines.append,clock=lambda:now[0],modifiers=lambda:inputs.append(1) or 1)
        t.observe('dedupe');now[0]=.1;t.observe('dedupe');self.assertEqual(len(lines),1)
        for i in range(200):now[0]=i+1;t.observe('dedupe')
        self.assertEqual(len(lines),128);self.assertEqual(len(inputs),128)
        t=trace.SourceSelectionTrace(True,sink=lambda line:(_ for _ in ()).throw(RuntimeError()),clock=lambda:now[0],modifiers=lambda:inputs.append(1) or 1)
        t.observe('dedupe');t.observe('dedupe');self.assertEqual(t._emitted,1)

    def test_huge_unicode_paths_uid_counts_and_first_refusal(self):
        lines=[];long='界'*1000000
        t=trace.SourceSelectionTrace(True,sink=lines.append,clock=lambda:0,modifiers=lambda:3)
        t.observe('foreign-path',paths=[long]*20,uids={long+str(i):'p' for i in range(20)},
            refused_index=19,cached_identity=(17,(long,)*20),scene_revision=18,project_hash='a'*64)
        raw=lines[0];r=json.loads(raw[len(trace.PREFIX):])
        self.assertLessEqual(len(raw.encode()),4096);self.assertEqual(r['pathCount'],20)
        self.assertEqual(r['uidCount'],20);self.assertEqual(r['cachedUidCount'],20)
        self.assertEqual(r['refusedIndex'],19);self.assertLessEqual(len(r['refusedPath'].encode()),192)
        self.assertTrue(r['truncated']);self.assertEqual(r['cachedSceneRevision'],17)

    def test_input_failure_invalid_clock_and_unknown_phase(self):
        lines=[]
        def fail():raise RuntimeError('not serialized')
        t=trace.SourceSelectionTrace(True,sink=lines.append,clock=lambda:0,modifiers=fail);t.observe('dedupe')
        r=json.loads(lines[0][len(trace.PREFIX):]);self.assertFalse(r['modifierSampleAvailable']);self.assertIsNone(r['shiftAtSample'])
        for now in (float('nan'),float('inf'),True,'0'):
            trace.SourceSelectionTrace(True,sink=lines.append,clock=lambda:now,modifiers=fail).observe('dedupe')
        self.assertEqual(len(lines),1);t.observe('fake-authorized-phase');self.assertEqual(len(lines),1)

if __name__=='__main__':
    if '--baseline-red' in sys.argv:
        sys.argv.remove('--baseline-red');a=build(BEFORE,PAIR,enabled=True)
        print('ACTUAL BEFORE pushed UIDs:',[x['modelUid'] for x in a['sent'][0]['selection']],flush=True)
        assert a['lines'], 'EXPECTED OBSERVABILITY RED: original callback has no bounded terminal-reason trace'
    unittest.main(verbosity=2)
