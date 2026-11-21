from pathlib import Path
import ast,asyncio,hashlib,json,os,sys,types,contextlib,io
sys.dont_write_bytecode=True
os.sched_setaffinity(0,{38})
O=Path(__file__).parent;B=Path('/mnt/lv/home/litianyu/mycode/dcdtuni');K=B/'src/frontend/omniverse/Ref_code/kit-cae/_build/linux-x86_64/release';C=K/'extscache';I=B/'.codex-tmp/goal-20261005/integration'
rel='src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/extension.py'
old=(O/'before/extension.py').read_text();new=(O/'candidate'/rel).read_text();src=I/rel
assert src.read_text()==old,'normal source drift; no apply permitted'
assert all(not isinstance(n,(ast.Import,ast.ImportFrom)) or ('subprocess' not in ast.unparse(n) and 'Popen' not in ast.unparse(n)) for n in ast.walk(ast.parse(Path(__file__).read_text())))
constants_path=C/'omni.kit.manipulator.transform-107.0.0+62477c11/omni/kit/manipulator/transform/settings_constants.py'
constants={};exec(compile(constants_path.read_text(),str(constants_path),'exec'),constants)
public_path=C/'omni.kit.manipulator.prim.core-108.0.1+62477c11/omni/kit/manipulator/prim/core/prim_transform_manipulator.py'
public_tree=ast.parse(public_path.read_text());public_class=next(n for n in public_tree.body if isinstance(n,ast.ClassDef) and n.name=='PrimTransformManipulator')
methods=[n for n in public_class.body if isinstance(n,ast.FunctionDef) and n.name in ['_set_style','_update_manipulator_enable']]
cls=ast.ClassDef(name='LiteralInstalledStyle',bases=[],keywords=[],body=methods,decorator_list=[])
ns={'transform_c':constants['c']};exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),str(public_path),'exec'),ns)
Style=ns['LiteralInstalledStyle']
class Settings:
 def __init__(self,failed=None):self.values={'/app/transform/operation':'move','/rtx/raytracing/fractionalCutoutOpacity':False,'/rtx/pathtracing/fractionalCutoutOpacity':False};self.calls=[];self.failed=failed
 def set(self,path,value):
  self.calls.append((path,value))
  if path==self.failed:raise ValueError('controlled setting rejection')
  self.values[path]=value
class App:
 def __init__(self):self.updates=0
 async def next_update_async(self):self.updates+=1
class Window:
 def __init__(self,title):self.title=title;self.visible=True;self.dock_tab_bar_enabled=True
 def _find_viewport_layer(self,*args):return None
class Main:
 def __init__(self):self.menu=types.SimpleNamespace(visible=True);self.status=types.SimpleNamespace(visible=True)
 def get_main_menu_bar(self):return self.menu
 def get_status_bar_frame(self):return self.status

def mod(name,**attrs):
 m=types.ModuleType(name);m.__path__=[]
 for k,v in attrs.items():setattr(m,k,v)
 sys.modules[name]=m;return m

def style(settings):
 x=Style();x._manipulator=types.SimpleNamespace(enabled=True,style=None);x._enabled=True;x._prim_style_applied=False
 x._op_settings_listener=types.SimpleNamespace(selected_op=settings.values['/app/transform/operation'],translation_mode='global',rotation_mode='global')
 x._snap_settings_listener=types.SimpleNamespace(snap_enabled=False,snap_to_surface=False);x._styles={'global':{},'local':{}};x._snap_styles=x._styles
 x._set_style();return x._manipulator.enabled

def run(source,failed=None):
 settings=Settings(failed);app=App();viewport=Window('Viewport');console=Window('Console');main=Main()
 csettings=mod('carb.settings',get_settings=lambda:settings);mod('carb',settings=csettings)
 ui=mod('omni.ui',Workspace=types.SimpleNamespace(get_windows=lambda:[viewport,console]))
 capp=mod('omni.kit.app',get_app=lambda:app);mod('omni.kit.mainwindow',get_main_window=lambda:main)
 vwindow=mod('omni.kit.viewport.window',get_viewport_window_instances=lambda:[viewport]);vp=mod('omni.kit.viewport',window=vwindow)
 kit=mod('omni.kit',app=capp,viewport=vp);omni=mod('omni',kit=kit,ui=ui)
 t=ast.parse(source);bridge=next(n for n in t.body if isinstance(n,ast.ClassDef))
 f=next(n for n in bridge.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_hide_ui')
 ns={'omni':omni};exec(compile(ast.fix_missing_locations(ast.Module(body=[f],type_ignores=[])),'actual-_hide_ui-source','exec'),ns)
 out=io.StringIO()
 with contextlib.redirect_stdout(out):asyncio.run(ns['_hide_ui'](types.SimpleNamespace()))
 return {'settings':settings.values,'calls':settings.calls,'updates':app.updates,'gizmoEnabledByInstalledStyle':style(settings),'viewportVisible':viewport.visible,'consoleVisible':console.visible,'menuVisible':main.menu.visible,'statusVisible':main.status.visible,'stdout':out.getvalue()}

before=run(old);after=run(new)
assert before['gizmoEnabledByInstalledStyle'] is True and after['gizmoEnabledByInstalledStyle'] is False
assert before['settings']['/rtx/raytracing/fractionalCutoutOpacity'] is False and before['settings']['/rtx/pathtracing/fractionalCutoutOpacity'] is False
assert after['settings']['/rtx/raytracing/fractionalCutoutOpacity'] is True and after['settings']['/rtx/pathtracing/fractionalCutoutOpacity'] is True
assert before['updates']==after['updates']==95
for key in ['viewportVisible','consoleVisible','menuVisible','statusVisible']:assert before[key]==after[key]
assert after['viewportVisible'] is True and after['consoleVisible'] is False
assert [x for x in after['calls'] if x not in before['calls']]==[('/app/transform/operation','select'),('/rtx/raytracing/fractionalCutoutOpacity',True),('/rtx/pathtracing/fractionalCutoutOpacity',True)]
failures=[]
for path in ['/app/transform/operation','/rtx/raytracing/fractionalCutoutOpacity','/rtx/pathtracing/fractionalCutoutOpacity']:
 got=run(new,path);expected=after['settings'].copy();expected[path]=before['settings'][path]
 assert got['settings']==expected and got['updates']==95 and got['viewportVisible'] is True
 assert 'display setting unavailable: '+path in got['stdout']
 assert got['calls']==after['calls']
 failures.append({'failedPath':path,'result':got})
old_tree=ast.parse(old);new_tree=ast.parse(new)
old_cl=next(n for n in old_tree.body if isinstance(n,ast.ClassDef));new_cl=next(n for n in new_tree.body if isinstance(n,ast.ClassDef))
for cl in [old_cl,new_cl]:cl.body=[n for n in cl.body if not isinstance(n,ast.AsyncFunctionDef) or n.name!='_hide_ui']
assert ast.dump(old_tree,include_attributes=False)==ast.dump(new_tree,include_attributes=False),'scope expanded beyond hide_ui'
assert src.read_text()==old,'normal source changed'
result={'scope':'CPU-only actual _hide_ui bodies + literal installed public _set_style/_update_manipulator_enable methods with controlled carriers. No Kit/native SDK/GPU APIs or process spawned. Material pixels not tested.', 'cpu':list(os.sched_getaffinity(0)),'publicSource':{'path':str(public_path),'sha256':hashlib.sha256(public_path.read_bytes()).hexdigest()},'constantsSource':{'path':str(constants_path),'sha256':hashlib.sha256(constants_path.read_bytes()).hexdigest()},'before':before,'after':after,'settingRejectionControls':failures,'normalSourceHeld':True,'onlyHideUiASTDelta':True,'passed':True}
(O/'cpu-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'passed':True,'negativeToPositiveGizmo':[before['gizmoEnabledByInstalledStyle'],after['gizmoEnabledByInstalledStyle']],'fractionalRTPT':[after['settings']['/rtx/raytracing/fractionalCutoutOpacity'],after['settings']['/rtx/pathtracing/fractionalCutoutOpacity']],'independentFailureControls':len(failures),'updatesHeld':95,'normalSourceHeld':True,'sourceScopeOnlyHideUi':True}))
