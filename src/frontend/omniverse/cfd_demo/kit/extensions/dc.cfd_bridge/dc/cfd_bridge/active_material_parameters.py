"""Prepared inspection only: runs inside an already active Kit after Root review.
No USD/settings/GPU writes, module loading, compilation, bake, network or file I/O.
This reads the renderer material-backend function arguments, not final GPU pixels.
The normal caller is an optional sampler of the existing default-off readback.
"""
import json
import math
import re

_GEOMETRY=re.compile(r'^/World/RenderScene/Geometry/(p_[0-9a-f]{24})$')
_FIELDS=('enable_opacity','enable_specular_transmission')

def _observe_active_parameters(expected_context, context_reader, operation=None):
    """Resolve only the requested Roof UID in the existing native registry.
    context_reader is the existing native getViewState context producer at
    extension.py1203-1207. No caller-supplied geometry or guessed Surface path.
    Caller persists the returned bounded JSON in P; this function writes nothing.
    """
    from pxr import UsdShade
    assert type(expected_context) is dict and set(expected_context)=={'sceneRevision','projectHash','primitives','roofModelUid'}
    before=context_reader()
    stage=before['stage']
    uid=expected_context['roofModelUid']
    assert stage is not None and isinstance(uid,str) and 1 <= len(uid) <= 128
    pins=('sceneRevision','projectHash','primitives')
    assert {k:before[k] for k in pins}=={k:expected_context[k] for k in pins},'scene-context-changed'
    generation=before['displayGeneration']
    paths=tuple(before['uidPaths'].get(uid,()))
    assert 1 <= len(paths) <= 4 and len(set(paths))==len(paths),'roof-registry-missing-or-fanout'
    bindings=[]
    for geometry_path in paths:
        assert isinstance(geometry_path,str) and _GEOMETRY.fullmatch(geometry_path),'not-owned-geometry-path'
        geometry=stage.GetPrimAtPath(geometry_path)
        assert geometry and geometry.GetCustomDataByKey('dcdtuni:modelUid')==uid,'wrong-owned-geometry'
        material,relationship=UsdShade.MaterialBindingAPI(geometry).ComputeBoundMaterial()
        assert material and relationship,'native-bound-material-unavailable'
        material_path=str(material.GetPath())
        assert material_path.startswith('/World/RenderScene/Looks/') and len(material_path) <= 256,'not-owned-material'
        assert [str(p) for p in relationship.GetTargets()]==[material_path],'ambiguous-material-binding'
        # Inspect the surface that RTX is offered, not the universal fallback.
        output=material.GetSurfaceOutput('mdl')
        native=bool(output and output.HasConnectedSource())
        if not native:output=material.GetSurfaceOutput()
        connections=list(output.GetAttr().GetConnections()) if output else []
        assert len(connections)==1,'surface-connection-unavailable'
        surface_path=str(connections[0].GetPrimPath())
        surface=stage.GetPrimAtPath(surface_path)
        if native:
            assert surface_path==material_path+'/FractionalSurface' and str(connections[0])==surface_path+'.outputs:out','not-owned-surface'
            shader=UsdShade.Shader(surface)
            assert shader and shader.GetImplementationSource()=='sourceAsset' and shader.GetSourceAsset('mdl').path and shader.GetSourceAssetSubIdentifier('mdl')=='DcdtuniFractionalPreview','unsupported-MDL-shader'
        else:
            assert str(connections[0])==surface_path+'.outputs:surface' and surface_path.startswith(material_path+'/'),'not-owned-surface'
            assert surface and surface.GetAttribute('info:id').Get()=='UsdPreviewSurface','unsupported-USD-shader'
        binding=dict(modelUid=uid,geometryPath=geometry_path,materialPath=material_path,surfacePath=surface_path)
        if native:binding['sourceType']='mdl'
        bindings.append(binding)
    records=[]
    for binding in bindings:
        native=binding.get('sourceType')=='mdl'
        record=dict(binding)
        if native:
            if operation is not None:operation[0]='module-inspect'
            shader=UsdShade.Shader(stage.GetPrimAtPath(binding['surfacePath']))
            loaded=_mdl_module_loaded(shader.GetSourceAsset('mdl'))
            assert type(loaded) is bool,'unexpected-module-state'
            record.update(moduleLoaded=loaded,backendStatus='pending-module' if not loaded else 'unavailable',
                backendReason='module-not-loaded' if not loaded else 'backend-unavailable',
                parameters={field:{'status':'unavailable','reason':'not-read'} for field in ('opacity','opacityThreshold')},
                finalCutout={'status':'unavailable','reason':'compiled-material-not-exposed'},observation=OBSERVATION)
            if not loaded:
                records.append(record)
                continue  # Do not create an entity or load/compile an absent module.
        entity=snapshot=transaction=call=arguments=ov=None
        try:
            if operation is not None:operation[0]='backend-import'
            ov,sdk=_backend()
            if operation is not None:operation[0]='entity-create'
            entity=ov.createMdlEntity(binding['surfacePath'])
            if operation is not None:operation[0]='entity-check'
            assert entity and entity.valid(),'active-entity-unavailable'
            if operation is not None:operation[0]='snapshot-create'
            snapshot=ov.createMdlEntitySnapshot(entity)
            if operation is not None:operation[0]='snapshot-check'
            # A global neuray scope is the legal empty string; the snapshot API has no valid().
            assert snapshot and isinstance(snapshot.dbName,str) and snapshot.dbName and isinstance(snapshot.dbScopeName,str),'active-snapshot-unavailable'
            if operation is not None:operation[0]='transaction-create'
            transaction=sdk.attach_itransaction(ov.createEditingTransaction(snapshot.dbScopeName))
            if operation is not None:operation[0]='transaction-check'
            assert transaction and transaction.is_valid_interface(),'active-read-transaction-unavailable'
            if operation is not None:operation[0]='function-access'
            call=transaction.access_as(sdk.IFunction_call,snapshot.dbName)
            if operation is not None:operation[0]='function-check'
            assert call and call.is_valid_interface(),'active-function-unavailable'
            if operation is not None:operation[0]='function-name'
            function=call.get_mdl_function_definition()
            assert isinstance(function,str) and function,'unexpected-function-identity'
            function_name=function.split('(',1)[0]
            assert len(function_name) <= 1024,'function-name-limit'
            if operation is not None:operation[0]='arguments-get'
            arguments=call.get_arguments()
            if operation is not None:operation[0]='arguments-check'
            assert arguments and arguments.is_valid_interface(),'active-arguments-unavailable'
            flags={}
            for field in (('opacity','opacityThreshold') if native else _FIELDS):
                expression=constant=value=None
                try:
                    if operation is not None:operation[0]='expression-get'
                    expression=arguments.get_expression(field)
                    if not expression or not expression.is_valid_interface() or expression.get_kind()!=sdk.IExpression.Kind.EK_CONSTANT:
                        flags[field]={'status':'unavailable','reason':'not-constant-or-absent'}
                        continue
                    constant=expression.get_interface(sdk.IExpression_constant)
                    if not constant or not constant.is_valid_interface():
                        flags[field]={'status':'unavailable','reason':'not-constant-or-absent'}
                        continue
                    if operation is not None:operation[0]='value-get'
                    value=constant.get_value_as(sdk.IValue_float if native else sdk.IValue_bool)
                    if not value or not value.is_valid_interface():
                        flags[field]={'status':'unavailable','reason':'not-float' if native else 'not-boolean'}
                        continue
                    if operation is not None:operation[0]='value-read'
                    scalar=value.get_value()
                    if native:
                        assert type(scalar) in (float,int) and math.isfinite(scalar) and 0 <= scalar <= 1,'unexpected-float-value'
                        scalar=float(scalar)
                    else:
                        assert type(scalar) is bool,'unexpected-boolean-value'
                    flags[field]={'status':'observed','value':scalar}
                finally:
                    if value is not None:value.release()
                    if constant is not None:constant.release()
                    if expression is not None:expression.release()
            if native:
                record.update(materialBackendFunctionName=function_name,backendStatus='observed',backendReason='none',parameters=flags)
                records.append(record)
            else:
                records.append(dict(binding,materialBackendFunctionName=function_name,flags=flags,observation=OBSERVATION))
        except Exception as exc:
            if not native:raise
            _log_failure(operation,exc)
            # Preserve the observed module/binding, but never substitute USD
            # authored inputs or a default for unavailable backend arguments.
            records.append(record)
        finally:
            try:
                try:
                    if arguments is not None:arguments.release()
                finally:
                    if call is not None:call.release()
            finally:
                try:
                    if transaction is not None:
                        try:transaction.abort()
                        finally:transaction.release()
                finally:
                    try:
                        if snapshot is not None:ov.destroyMdlEntitySnapshot(snapshot)
                    finally:
                        if entity is not None:ov.destroyMdlEntity(entity)
    after=context_reader()
    assert after['stage'] is stage and {k:after[k] for k in pins}=={k:expected_context[k] for k in pins} and after['displayGeneration']==generation and tuple(after['uidPaths'].get(uid,()))==paths,'scene-context-changed'
    result={'context':dict(expected_context,displayGeneration=generation),'records':records,'sourceContextStable':True,
            'compiledGPUProgramRead':False,'encodedPixelsRead':False,'settingsAndUSDMutated':False}
    result['renderer']=_renderer_readback()
    assert len(json.dumps(result,allow_nan=False,separators=(',',':')).encode()) <= 16384,'reply-limit'
    return result


OBSERVATION='active-material-backend-parameters-not-final-GPU-program-or-pixels'
FIELD_BYTES=6144


def _mdl_module_loaded(asset):
    # Official installed omni.kit.usd.mdl 1.1.8 usage: query only. No Add/Get,
    # shader discovery, forced module load, compilation or registry mutation.
    import omni.UsdMdl as UsdMdl
    from pxr import Ar,Sdf
    resolved=asset.resolvedPath or str(Ar.GetResolver().Resolve(asset.path))
    return UsdMdl.RegistryUtils.IsModuleLoaded(Sdf.AssetPath(asset.path,resolved))

def _renderer_readback():
    modes=('RaytracedLighting','PathTracing','RealTimePathTracing','MinimalRendering')
    unknown={'status':'unavailable','reason':'not-public-value'}
    result={'mode':dict(unknown),'raytracingFractionalCutoutOpacity':dict(unknown),'pathtracingFractionalCutoutOpacity':dict(unknown)}
    try:
        import carb.settings
        settings=carb.settings.get_settings()
        mode=settings.get('/rtx/rendermode')
        if mode in modes:result['mode']={'status':'observed','value':mode}
        for name,path in (('raytracingFractionalCutoutOpacity','/rtx/raytracing/fractionalCutoutOpacity'),
                          ('pathtracingFractionalCutoutOpacity','/rtx/pathtracing/fractionalCutoutOpacity')):
            value=settings.get(path)
            if type(value) is bool:result[name]={'status':'observed','value':value}
    except Exception:
        pass  # No exception text, settings mutation or substitute defaults.
    return result

def _backend():
    import omni.mdl.neuraylib
    from omni.mdl import pymdlsdk
    return omni.mdl.neuraylib.get_neuraylib(),pymdlsdk

def _bounded(value):
    text=json.dumps(value,allow_nan=False,separators=(',',':'))
    if len(text.encode())>FIELD_BYTES:raise ValueError('reply-limit')
    return json.loads(text)

def _unavailable(reason):
    return {'status':'unavailable','reason':reason,'observation':OBSERVATION}

_STAGES=frozenset(('source-context','backend-import','entity-create','entity-check','snapshot-create','snapshot-check',
    'transaction-create','transaction-check','function-access','function-check','function-name','arguments-get',
    'arguments-check','expression-get','value-get','value-read','module-inspect'))
_ERROR_CLASSES=frozenset(('ImportError','ModuleNotFoundError','AssertionError','AttributeError','TypeError',
    'RuntimeError','ValueError','OSError'))
_FAILURE_TAGS=frozenset(('active-entity-unavailable','active-snapshot-unavailable','active-read-transaction-unavailable',
    'active-function-unavailable','active-arguments-unavailable','scene-context-changed'))

def _log_failure(operation,error):
    # Last attempted fixed stage only; exception text, assets and credentials never enter the log or wire.
    stage=operation[0] if operation and operation[0] in _STAGES else 'unknown'
    error_class=type(error).__name__
    if error_class not in _ERROR_CLASSES:error_class='NativeError'
    tag=str(error) if isinstance(error,AssertionError) and str(error) in _FAILURE_TAGS else 'none'
    try:
        import carb
        carb.log_warn('[dc.cfd_bridge] readonly active material unavailable last-stage='+stage+' class='+error_class+' tag='+tag)
    except Exception:
        pass  # Missing logging must not change the fixed unavailable result or cleanup.

def sample(context_reader, roof_uid):
    """Owned callback only; no new protocol arguments, execution or writer."""
    if roof_uid is None:return _unavailable('target-missing')
    operation=['source-context']
    try:
        current=context_reader()
        paths=current['uidPaths'].get(roof_uid,())
        if not paths:return _unavailable('target-not-acknowledged')
        if len(paths)>4:return _unavailable('target-fanout')
        expected={k:current[k] for k in ('sceneRevision','projectHash','primitives')}
        expected['roofModelUid']=roof_uid
        result=_observe_active_parameters(expected,context_reader,operation)
        return _bounded(dict(result,status='observed',reason='none',observation=OBSERVATION))
    except ImportError as exc:
        _log_failure(operation,exc)
        return _unavailable('backend-unavailable')
    except AssertionError as exc:
        _log_failure(operation,exc)
        # Fixed internal tags only. Never serialize exception or asset values.
        tag=str(exc)
        if tag=='scene-context-changed':return _unavailable('source-context-changed')
        if tag in ('active-entity-unavailable','active-snapshot-unavailable','active-read-transaction-unavailable','active-function-unavailable','active-arguments-unavailable'):
            return _unavailable('backend-unavailable')
        return _unavailable('invalid-public-value')
    except ValueError as exc:
        _log_failure(operation,exc)
        return _unavailable('reply-limit' if str(exc)=='reply-limit' else 'invalid-public-value')
    except Exception as exc:
        _log_failure(operation,exc)
        return _unavailable('backend-unavailable')
