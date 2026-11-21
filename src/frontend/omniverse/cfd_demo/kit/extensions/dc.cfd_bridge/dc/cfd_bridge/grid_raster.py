"""Common grid's derived planar carrier and latest-view commit guards.

Inputs are immutable display observations. Nothing here writes science data,
changes source/UID authority, or supplies permission for numerical products.
"""
from __future__ import annotations
import asyncio
import bisect
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
import struct
from dataclasses import dataclass, replace



# Local opt-in diagnostics only. No native getters, callbacks, authority or repair.
_NATIVE_GRID_RECEIPT_DIAGNOSTICS = os.environ.get('DC_CFD_NATIVE_GRID_RECEIPT_DIAGNOSTICS') == 'grid-receipt-v1'
_NATIVE_GRID_RECEIPT_DIAGNOSTIC_SEEN = set()
_NATIVE_GRID_RECEIPT_DIAGNOSTIC_LIMIT = 32
_NATIVE_GRID_RECEIPT_PHASES = {'pending','unsupported','rejected','rendered','hidden'}
_NATIVE_GRID_RECEIPT_REASONS = {
    'stream-layout-unresolved','stream-layout-degenerate','raster-unobserved',
    'viewport-frame-not-current','native-grid-view-not-current',
    'native-camera-frame-not-current','native-projection-frame-not-current',
    'native-camera-changed-during-sample','native-observation-changed-during-sample',
    'native-observation-changed-during-lowering',
    'native-public-viewport-observation-unavailable','native-viewport-unavailable',
    'native-viewport-owner-not-current','native-observation-changed','grid-lowering-rejected'
}

def _native_grid_receipt_diagnostic(stage, receipt, *, refusal=None, request=None,
                                    contract=None, checkpoint=None):
    """Fixed key/enum flags from values already held at the actual predicate."""
    if (not _NATIVE_GRID_RECEIPT_DIAGNOSTICS
        or len(_NATIVE_GRID_RECEIPT_DIAGNOSTIC_SEEN) >= _NATIVE_GRID_RECEIPT_DIAGNOSTIC_LIMIT):
        return
    try:
        if stage not in ('producer','flush-send') or type(receipt) is not dict:
            return
        native = receipt.get('nativeGeometry')
        obj = native if type(native) is dict else {}
        serial = request.get('serial') if type(request) is dict else None
        revision = receipt.get('viewRevision')
        record = {
            'schema':'dcdtuni.native-grid-receipt-diagnostic/v1',
            'stage':stage,'refusal':refusal,'checkpoint':checkpoint,
            'topPhase':receipt.get('phase') if receipt.get('phase') in _NATIVE_GRID_RECEIPT_PHASES else None,
            'topReason':receipt.get('reason') if receipt.get('reason') in _NATIVE_GRID_RECEIPT_REASONS else None,
            'requestSerial':serial if type(serial) is int and 0 <= serial <= 9007199254740991 else None,
            'viewRevision':revision if type(revision) is int and 0 <= revision <= 9007199254740991 else None,
            'typedNativeContract':type(contract) is NativeCarrierContract if contract is not None else None,
            'nativeGeometryPresent':'nativeGeometry' in receipt,
            'nativeGeometryObject':type(native) is dict,
            'nativeGeometryTimeSecondsPresent':'timeSeconds' in obj,
            'nativeGeometryTimeSecondsIsNull':'timeSeconds' in obj and obj['timeSeconds'] is None,
            'topGridPixelVerifiedIsFalse':receipt.get('gridPixelVerified') is False,
            'nestedGridPixelVerifiedIsFalse':obj.get('gridPixelVerified') is False,
            'nestedEncodedFrameProofUnresolved':obj.get('encodedFrameProof') == 'unresolved',
            'diagnosticAuthority':False,'additionalNativeReads':False
        }
        key = json.dumps(record,sort_keys=True,separators=(',',':'),allow_nan=False)
        if key in _NATIVE_GRID_RECEIPT_DIAGNOSTIC_SEEN:
            return
        # Reserve before sink; failed printing cannot bypass the process cap.
        _NATIVE_GRID_RECEIPT_DIAGNOSTIC_SEEN.add(key)
        record['sequence'] = len(_NATIVE_GRID_RECEIPT_DIAGNOSTIC_SEEN)
        print('[dc.cfd_bridge] native-grid-receipt-diagnostic ' +
              json.dumps(record,sort_keys=True,separators=(',',':'),allow_nan=False))
    except Exception:
        pass  # Diagnostic failure never changes a receipt or predicate.

# The grid strips against Three's 1-px GridHelper lines, measured seen from
# above on DAML (2026-10-23, head-browser screenshots): at 1 px a line now and
# then fell between RTX's samples and dropped out; at 1.5 px every line shows.
# The gain brings a line's summed brightness over the backdrop to Three's
# (OV 38 against Three 43 grey levels per line; 1.0 gave three times Three's).
NATIVE_GRID_WIDTH_PX = 1.5
GRID_EMISSION_GAIN = 0.45


class GridRefused(ValueError):
    pass


def _clip_poly(poly, fn):
    if not poly:
        return []
    out = []
    a, da = poly[-1], fn(poly[-1])
    for b in poly:
        db = fn(b)
        if (da >= 0) != (db >= 0):
            t = da/(da-db)
            out.append(tuple(x+(y-x)*t for x,y in zip(a,b)))
        if db >= 0:
            out.append(b)
        a, da = b, db
    return out


def _clip_h(v):
    return (v[3]+v[0],v[3]-v[0],v[3]+v[1],v[3]-v[1],v[3]+v[2],v[3]-v[2],v[3]-1e-9)


def _area(poly):
    return sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(poly,poly[1:]+poly[:1]))/2


def canonical_grid(grid):
    """Use the actual canonical lower once per intent; never duplicate its math."""
    from pxr import UsdGeom
    from . import render_scene
    stage = render_scene._background_grid_stage(grid)
    curves = UsdGeom.BasisCurves(stage.GetPrimAtPath(render_scene.BACKGROUND_GRID_ROOT+'/Lines'))
    points = tuple(tuple(float(x) for x in p) for p in curves.GetPointsAttr().Get())
    colors = tuple(tuple(float(x) for x in p) for p in UsdGeom.PrimvarsAPI(curves).GetPrimvar('displayColor').Get())
    return points, colors


def calculate_ribbons(grid, points, colors, world_to_output, raster, presentation):
    """Pure numeric/Gf work. No stage is touched from a worker thread."""
    from pxr import Gf
    if len(raster) != 2 or any(type(x) is not int or x <= 0 for x in raster) or raster[0] > 1600 or raster[1] > 900:
        raise GridRefused('invalid actual decoded raster')
    if not isinstance(presentation,dict) or presentation.get('metricVersion') != 2:
        raise GridRefused('unsupported presentation metric')
    line,output = presentation['line'],presentation['output']
    width_gl = line['effectiveWidthPx']
    if (type(width_gl) not in (int,float) or not math.isfinite(width_gl) or not 0 < width_gl <= 8
            or line.get('coordinateFrame') != 'scene-world' or output.get('objectFit') != 'contain'):
        raise GridRefused('invalid actual line/presentation observation')
    for values in (line['drawingBuffer'],line['css'],output['css'],output['layoutCss']):
        if len(values) != 2 or any(type(x) not in (int,float) or not math.isfinite(x) or x <= 0 for x in values):
            raise GridRefused('presentation size unavailable')
    ref_values = line['worldToClip']
    if len(ref_values) != 16 or any(type(x) not in (int,float) or not math.isfinite(x) for x in ref_values):
        raise GridRefused('reference clip transform unavailable')
    if tuple(raster) != tuple(output['decoded']):
        raise GridRefused('presentation belongs to another decoded raster')
    reference_scale = tuple(a/b for a,b in zip(line['css'],line['drawingBuffer']))
    contain = min(output['layoutCss'][0]/raster[0],output['layoutCss'][1]/raster[1])
    output_scale = tuple(contain*a/b for a,b in zip(output['css'],output['layoutCss']))
    if len(world_to_output) != 16 or any(type(x) not in (int,float) or not math.isfinite(x) for x in world_to_output):
        raise GridRefused('invalid live output projection')
    if len(points) != len(colors) or len(points) != 4*(grid['divisions']+1):
        raise GridRefused('canonical centerline/color inventory mismatch')
    plane = Gf.Matrix4d(*grid['matrix'])
    m = plane * Gf.Matrix4d(*world_to_output)
    reference = plane * Gf.Matrix4d(*ref_values)
    half = max(abs(p[0]) for p in points)
    def clip(xz):
        return Gf.Vec4d(xz[0],0,xz[1],1)*m
    def ndc(xz):
        p = clip(xz)
        if p[3] <= 1e-9:
            raise GridRefused('grid point behind camera')
        return p[0]/p[3],p[1]/p[3]
    poly = [(-half,-half),(half,-half),(half,half),(-half,half)]
    for i in range(7):
        poly = _clip_poly(poly,lambda p,i=i:_clip_h(clip(p))[i])
    empty = {'points':[],'colors':[],'counts':[],'sourceLines':[]}
    if len(poly) < 3:
        return empty
    domain = [ndc(p) for p in poly]
    if abs(_area(domain)) < 1e-12:
        raise GridRefused('edge-on grid plane has no finite-area raster carrier')
    if _area(domain) < 0:
        domain.reverse()
    def unproject(u,v):
        a,b,c,d = m[0][0]-u*m[0][3],m[2][0]-u*m[2][3],m[0][1]-v*m[0][3],m[2][1]-v*m[2][3]
        e,f = u*m[3][3]-m[3][0],v*m[3][3]-m[3][1]
        det = a*d-b*c
        if abs(det) < 1e-14*max(abs(a*d),abs(b*c),1):
            raise GridRefused('unbounded horizon inverse')
        x,z = (e*d-b*f)/det,(a*f-e*c)/det
        if not all(map(math.isfinite,(x,z))) or max(abs(x),abs(z)) > half+1e-6:
            raise GridRefused('grid escaped canonical extent')
        if min(_clip_h(clip((x,z)))) < -1e-7:
            raise GridRefused('grid escaped visible output frustum')
        return x,0.,z
    W,H = raster
    k = [p[2] for p in points[::4]]
    xlo,xhi = min(p[0] for p in poly),max(p[0] for p in poly)
    zlo,zhi = min(p[1] for p in poly),max(p[1] for p in poly)
    hiz = range(max(0,bisect.bisect_left(k,zlo-1e-7)-1),min(len(k),bisect.bisect_right(k,zhi+1e-7)+1))
    hix = range(max(0,bisect.bisect_left(k,xlo-1e-7)-1),min(len(k),bisect.bisect_right(k,xhi+1e-7)+1))
    candidates = sorted([4*i for i in hiz]+[4*i+2 for i in hix])
    vertices,vertex_colors,counts,source_lines = [],[],[],[]
    for j in candidates:
        a,b = (points[j][0],points[j][2]),(points[j+1][0],points[j+1][2])
        ca,cb,low,high = clip(a),clip(b),0.,1.
        for fa,fb in zip(_clip_h(ca),_clip_h(cb)):
            if fa < 0 and fb < 0:
                low,high = 1,0
                break
            if (fa < 0) != (fb < 0):
                t = fa/(fa-fb)
                if fa < 0:
                    low = max(low,t)
                else:
                    high = min(high,t)
        if low >= high:
            continue
        aa = tuple(x+(y-x)*low for x,y in zip(a,b))
        bb = tuple(x+(y-x)*high for x,y in zip(a,b))
        u,v = ndc(aa),ndc(bb)
        dx,dy = (v[0]-u[0])*W/2,(v[1]-u[1])*H/2
        length = math.hypot(dx,dy)
        if length < 1e-8:
            continue
        # A GL-normal strip, mapped through the actual reference canvas CSS
        # axes, has CSS normal width w*det(S)/|S*t|. Match that perpendicular
        # width through the actual video's contain+axis presentation. Tangents
        # are obtained independently from each real projection; no DPR guess.
        ra,rb = (Gf.Vec4d(p[0],0,p[1],1)*reference for p in (aa,bb))
        if min(ra[3],rb[3]) <= 1e-9:
            raise GridRefused('reference line is outside finite presentation')
        rx=(rb[0]/rb[3]-ra[0]/ra[3])*line['drawingBuffer'][0]/2
        ry=(rb[1]/rb[3]-ra[1]/ra[3])*line['drawingBuffer'][1]/2
        reference_length=math.hypot(rx,ry)
        css_length=math.hypot(reference_scale[0]*rx,reference_scale[1]*ry)
        if reference_length <= 1e-9 or css_length <= 1e-12:
            raise GridRefused('reference projected line is degenerate')
        width_css=width_gl*reference_scale[0]*reference_scale[1]*reference_length/css_length
        width_px=width_css*math.hypot(output_scale[0]*dx,output_scale[1]*dy)/length/(output_scale[0]*output_scale[1])
        if not math.isfinite(width_px) or not 0 < width_px <= 64:
            raise GridRefused('presentation-equivalent decoded width exceeds bound')
        nx,ny = -dy/length*width_px/W,dx/length*width_px/H
        ribbon = [(u[0]+nx,u[1]+ny),(v[0]+nx,v[1]+ny),(v[0]-nx,v[1]-ny),(u[0]-nx,u[1]-ny)]
        for e0,e1 in zip(domain,domain[1:]+domain[:1]):
            ribbon = _clip_poly(ribbon,lambda p,e0=e0,e1=e1:(e1[0]-e0[0])*(p[1]-e0[1])-(e1[1]-e0[1])*(p[0]-e0[0]))
        if len(ribbon) < 3 or abs(_area(ribbon)) < 1e-16:
            continue
        local = [unproject(*p) for p in ribbon]
        vertices.extend(local)
        vertex_colors.extend([colors[j]]*len(local))
        counts.append(len(local))
        source_lines.append(j//2)
    return {'points':vertices,'colors':vertex_colors,'counts':counts,'sourceLines':source_lines}


def _fingerprint(data):
    h = hashlib.sha256()
    for name in ('points','colors'):
        values = [float(x) for p in data[name] for x in p]
        h.update(struct.pack('<'+'f'*len(values),*values))
    h.update(struct.pack('<'+'i'*len(data['counts']),*data['counts']))
    return h.hexdigest()


@dataclass(frozen=True)
class CarrierContract:
    stage: object
    key: str
    expected: tuple
    paths: tuple
    context: tuple | None = None
    intent: str | None = None


@dataclass(frozen=True)
class NativeCarrierContract:
    stage: object
    key: str
    expected: tuple
    paths: tuple
    observation: object
    context: tuple | None = None
    intent: str | None = None


def _stable_value(value):
    # Immutable content encoding; wrapper repr can contain process addresses.
    if value is None or type(value) in (bool,int,float,str):
        return (type(value).__name__,value)
    if isinstance(value,dict):
        return ('dict',tuple(sorted((k,_stable_value(v)) for k,v in value.items())))
    if hasattr(value,'isExplicit') and hasattr(value,'explicitItems'):
        names=('explicitItems','addedItems','prependedItems','appendedItems','deletedItems','orderedItems')
        return (type(value).__name__,bool(value.isExplicit),tuple((n,_stable_value(getattr(value,n)))for n in names))
    try:
        return (type(value).__name__,tuple(_stable_value(v)for v in value))
    except TypeError:
        text=str(value)
        if ' object at 0x' in text:
            raise GridRefused('unsupported composed value encoding')
        return (type(value).__name__,text)


def _composed_contract(stage, expected_paths=None):
    """Bounded owned-subtree composed values, not root-layer bytes/tags.

    Reject arcs/instances and unknown children before touching their contents.
    No stage flatten, model traversal, asset open or session-layer mutation.
    """
    from pxr import Usd,UsdGeom,UsdShade
    from . import render_scene
    root = stage.GetPrimAtPath(render_scene.BACKGROUND_GRID_ROOT)
    if not root:
        raise GridRefused('owned grid carrier absent')
    if expected_paths is None:
        expected_paths = tuple(str(p.GetPath()) for p in Usd.PrimRange(root))
    if len(expected_paths) > 32:
        raise GridRefused('owned grid subtree inventory exceeds bound')
    inventory = set(expected_paths)
    rows = []
    for path in expected_paths:
        prim = stage.GetPrimAtPath(path)
        if (not prim or prim.HasAuthoredReferences() or prim.HasPayload() or prim.IsInstance()
                or prim.IsInstanceProxy() or {str(c.GetPath()) for c in prim.GetAllChildren()}
                   != {p for p in inventory if path == p.rsplit('/',1)[0]}):
            raise GridRefused('owned grid composed inventory changed')
        # repr stores immutable value/type evidence; no values are printed.
        metadata = tuple(sorted((k,type(v).__name__,_stable_value(v)) for k,v in prim.GetAllMetadata().items()))
        props = []
        for prop in sorted(prim.GetProperties(),key=lambda p:p.GetName()):
            pm = tuple(sorted((k,type(v).__name__,_stable_value(v)) for k,v in prop.GetAllMetadata().items()))
            if isinstance(prop,Usd.Attribute):
                times = prop.GetTimeSamples()
                values = tuple((t,type(prop.Get(t)).__name__,_stable_value(prop.Get(t))) for t in times)
                value = prop.Get()
                body = ('attribute',str(prop.GetTypeName()),type(value).__name__,_stable_value(value),
                        tuple(map(str,prop.GetConnections())),values)
            else:
                body = ('relationship',tuple(map(str,prop.GetTargets())))
            props.append((prop.GetName(),pm,body))
        image = UsdGeom.Imageable(prim)
        xform = UsdGeom.Xformable(prim)
        inherited = (str(image.ComputeVisibility()),str(image.ComputePurpose())) if image else None
        world = _stable_value(xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())) if xform else None
        # Resolve only this owned Gprim's binding; an ancestor's stronger
        # binding can change rendered material without altering local props.
        bound = None
        if UsdGeom.Gprim(prim):
            purposes = (UsdShade.Tokens.allPurpose,UsdShade.Tokens.preview,UsdShade.Tokens.full)
            binding = UsdShade.MaterialBindingAPI(prim)
            materials = []
            for purpose in purposes:
                material,relationship = binding.ComputeBoundMaterial(materialPurpose=purpose)
                materials.append((str(purpose),str(material.GetPath()) if material else None,
                                  str(relationship.GetPath()) if relationship else None))
            bound = tuple(materials)
        rows.append((path,prim.IsActive(),prim.IsLoaded(),str(prim.GetTypeName()),metadata,tuple(props),inherited,world,bound))
    return tuple(rows),tuple(expected_paths)


def carrier_matches(stage, key, contract=None):
    """Only intended writer work retained outside USD can authorize a no-op."""
    if type(contract) is not CarrierContract or contract.stage != stage or contract.key != key:
        return False
    try:
        actual,_ = _composed_contract(stage,contract.paths)
        return actual == contract.expected
    except Exception:
        return False


def native_carrier_matches(stage, key, contract=None):
    """Internal native texture geometry never satisfies encoded parity."""
    if type(contract) is not NativeCarrierContract or contract.stage != stage or contract.key != key:
        return False
    try:
        actual,_ = _composed_contract(stage,contract.paths)
        return actual == contract.expected
    except Exception:
        return False


def author_ribbons(stage, grid, data, key, _native_observation=None):
    """Atomic replacement of this decoration only; old bytes restored on failure."""
    from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade
    from . import render_scene
    root = render_scene.BACKGROUND_GRID_ROOT
    work = Usd.Stage.CreateInMemory()
    parent = UsdGeom.Xform.Define(work,root)
    parent.AddTransformOp().Set(Gf.Matrix4d(*grid['matrix']))
    parent.GetPrim().SetCustomDataByKey('dcdtuni:viewDecoration',True)
    parent.GetPrim().SetCustomDataByKey('dcdtuni:backgroundGridIntentJson',render_scene._background_grid_intent_json(grid))
    native = _native_observation is not None
    if native:
        from .grid_viewport import NativeGridObservation
        if (type(_native_observation) is not NativeGridObservation
            or data.get('widthDomain') != 'native-viewport-image-pixel'
            or data.get('encodedFrameProof') != 'unresolved' or data.get('gridPixelVerified') is not False):
            raise GridRefused('native grid domain is not an encoded receipt')
    prefix = 'nativeGrid' if native else 'raster'
    parent.GetPrim().SetCustomDataByKey('dcdtuni:'+prefix+'CarrierKey',key)
    parent.GetPrim().SetCustomDataByKey('dcdtuni:'+prefix+'CarrierDigest',_fingerprint(data))
    if native:
        parent.GetPrim().SetCustomDataByKey('dcdtuni:nativeGridWidthDomain','native-viewport-image-pixel')
    # Three.js draws these lines alpha-blended over the viewport backdrop. Here
    # they were 0.6-cutout strips whose emission never showed: the strips faced
    # away from the camera (native_grid_strips now orients them), so the grid
    # read only by specular glints of the key light -- black in the room's
    # shadow, gone seen from above (2026-10-23: under 3 grey levels of contrast
    # against Three's 22-42). Each colour (the ordinary lines, the centre line)
    # is an opaque strip set with a constant emission: Three's blend of the line
    # over Kit's constant backdrop, the one colour behind the grid, with no
    # specular lobe, so it reads the same from every side and in shadow.
    from pathlib import Path
    scene = stage.GetPrimAtPath(render_scene.ROOT)
    mdl_asset = scene.GetCustomDataByKey('dcdtuni:fractionalMaterialAsset') if scene else None
    mdl_asset = mdl_asset or str(Path(render_scene.__file__).with_name('DcdtuniFractionalPreview.mdl'))
    alpha = float(grid['opacity'])
    backdrop = render_scene.backdrop_linear()
    groups, start = {}, 0
    for count in data['counts']:
        group = groups.setdefault(tuple(data['colors'][start]), ([], []))
        group[0].extend(data['points'][start:start+count])
        group[1].append(count)
        start += count
    # The most used colour (the ordinary lines) is Stroke; the centre line Stroke1.
    for index, (colour, (points, counts)) in enumerate(sorted(groups.items(), key=lambda item: -len(item[1][1]))):
        suffix = str(index) if index else ''
        mesh = UsdGeom.Mesh.Define(work,root+'/Stroke'+suffix)
        mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreatePointsAttr([Gf.Vec3f(*p) for p in points])
        mesh.CreateFaceVertexCountsAttr(counts)
        mesh.CreateFaceVertexIndicesAttr(list(range(len(points))))
        mesh.CreateDoubleSidedAttr(True)
        mesh.GetPrim().SetCustomDataByKey('dcdtuni:viewDecoration',True)
        source = {'color':[GRID_EMISSION_GAIN*(alpha*c+(1-alpha)*b) for c,b in zip(colour,backdrop)],'emissive':[0,0,0],'opacity':1.0,
                  'roughness':1,'metalness':0,'doubleSided':True,'unlit':True,'emissionOnly':True}
        material = render_scene._material(work,root+'/Looks/Grid'+suffix,source,{},False,mdl_asset=mdl_asset)
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
    expected,paths = _composed_contract(work)
    contract = (NativeCarrierContract(stage,key,expected,paths,_native_observation) if native
                else CarrierContract(stage,key,expected,paths))
    backup = Sdf.Layer.CreateAnonymous()
    had_old = bool(stage.GetRootLayer().GetPrimAtPath(root))
    parent_path = str(Sdf.Path(root).GetParentPath())
    had_parent = bool(stage.GetRootLayer().GetPrimAtPath(parent_path))
    Sdf.CreatePrimInLayer(backup,Sdf.Path(root).GetParentPath())
    if had_old and not Sdf.CopySpec(stage.GetRootLayer(),root,backup,root):
        raise GridRefused('grid backup failed')
    # Own root edits explicitly; a caller's active session edit target is not
    # permission to alter that foreign layer during replacement/rollback.
    with Usd.EditContext(stage,stage.GetRootLayer()):
        try:
            if had_old:
                stage.RemovePrim(root)
            Sdf.CreatePrimInLayer(stage.GetRootLayer(),Sdf.Path(root).GetParentPath())
            if not Sdf.CopySpec(work.GetRootLayer(),root,stage.GetRootLayer(),root):
                raise GridRefused('grid commit failed')
            if not (native_carrier_matches if native else carrier_matches)(stage,key,contract):
                raise GridRefused('stronger composed layer prevents owned grid contract')
        except Exception:
            stage.RemovePrim(root)
            if had_old:
                Sdf.CopySpec(backup,root,stage.GetRootLayer(),root)
            if not had_parent:
                stage.RemovePrim(parent_path)
            raise
    return contract


class GridRasterCoordinator:
    """Latest immutable request; ownership is checked after every awaited phase."""
    def __init__(self, current_context, sample, frame_revision, calculate=calculate_ribbons, layout_lease=lambda: None,
                 native_sample=None, native_calculate=None):
        self.current_context = current_context
        self.sample = sample
        self.frame_revision = frame_revision
        self.calculate = calculate
        self.layout_lease = layout_lease
        self.native_sample = native_sample
        if native_calculate is None and native_sample is not None:
            from .native_grid_geometry import native_grid_strips
            native_calculate = native_grid_strips
        self.native_calculate = native_calculate
        self._contract_layout_lease = None
        self._pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='dc-grid-raster')
        self._closed = False
        self._request = None
        self._task = None
        self._serial = 0
        self._identity = None
        self._owner = None
        self._last_view = -1
        self._output_revision = -1
        self._output_observation = None
        self._canonical = None
        self._contract = None
        self.receipt = {'phase':'pending','reason':'raster-unobserved'}

    def _retire_invalid_current_observation(self, stage, view):
        # An invalid measurement is not permission to keep an older carrier.
        # Only primitive identities from this already accepted owner/context and
        # a newer view may withdraw it. Foreign/stale/incomplete inputs cannot.
        if self._closed or type(view) is not dict or self._owner is None:
            return
        raster = view.get('backgroundGridRaster')
        if type(raster) is not dict:
            return
        binding, output = raster.get('binding'), raster.get('output')
        if type(binding) is not dict or type(output) is not dict:
            return
        revision, output_revision = binding.get('viewRevision'), output.get('revision')
        scene_revision = binding.get('sceneRevision')
        if (any(type(value) is not int or not 0 <= value <= 9007199254740991
                for value in (revision, output_revision, scene_revision))
            or revision <= self._last_view or output_revision < self._output_revision
            or output.get('owner') != self._owner):
            return
        try:
            context = self.current_context(stage)
            identity = (stage.GetRootLayer().identifier, context['projectUid'],
                        context['projectContentSha256'], context['revision'], context.get('displayGeneration'))
        except (ValueError, RuntimeError, KeyError, AttributeError):
            return
        if (identity != self._identity or binding.get('projectUid') != context['projectUid']
            or binding.get('projectContentSha256') != context['projectContentSha256']
            or binding['sceneRevision'] != context['revision']):
            return
        self._serial += 1
        self._request = None
        self._contract = None
        # Withdrawal retires this newer view even though its measurement is
        # unusable. Keep only proven primitive floors, never the invalid data.
        self._last_view = revision
        if output_revision > self._output_revision or output != self._output_observation:
            self._output_revision = output_revision
            self._output_observation = None
        self.receipt = {'phase':'rejected','reason':'grid-lowering-rejected','viewRevision':revision}

    def validate_binding(self, stage, view):
        from . import render_scene
        try:
            render_scene.validate_view(view)
        except (ValueError, TypeError):
            self._retire_invalid_current_observation(stage, view)
            raise
        raster = view.get('backgroundGridRaster')
        if raster is None:
            return
        context = self.current_context(stage)
        binding = raster['binding']
        if (binding['projectUid'] != context['projectUid']
            or binding['projectContentSha256'] != context['projectContentSha256']
            or binding['sceneRevision'] != context['revision']):
            raise GridRefused('decoration observation belongs to another accepted scene')
        identity = (stage.GetRootLayer().identifier,context['projectUid'],context['projectContentSha256'],context['revision'],context.get('displayGeneration'))
        if self._identity == identity and self._owner is not None:
            if self._owner != raster['output']['owner']:
                raise GridRefused('decoration media owner changed without a new accepted scene')
            if binding['viewRevision'] <= self._last_view:
                raise GridRefused('decoration observation is not a newer view')
            output = raster['output']
            if output['revision'] < self._output_revision:
                raise GridRefused('decoration output observation revision moved backwards')
            if output['revision'] == self._output_revision and output != self._output_observation:
                raise GridRefused('decoration output observation changed without a new revision')

    def offer(self, stage, view):
        if self._closed:
            raise GridRefused('decoration coordinator is closed')
        self.validate_binding(stage,view)
        self._serial += 1
        raster = view.get('backgroundGridRaster')
        self._request = None
        context = self.current_context(stage)
        if self._contract is not None and (self._contract.stage != stage or self._contract.context != tuple(sorted(context.items()))):
            self._contract = None
        if raster is not None:
            self._identity = (stage.GetRootLayer().identifier,context['projectUid'],context['projectContentSha256'],context['revision'],context.get('displayGeneration'))
            self._owner = raster['output']['owner']
            self._last_view = raster['binding']['viewRevision']
            self._output_revision = raster['output']['revision']
            self._output_observation = json.loads(json.dumps(raster['output'],allow_nan=False))
        if not view.get('backgroundGrid',{}).get('visible'):
            self._contract = None
            self.receipt = {'phase':'hidden'}
            return
        if raster is None and self.native_sample is None:
            self._contract = None
            self.receipt = {'phase':'pending','reason':'raster-unobserved'}
            return
        self._request = {'stage':stage,'view':json.loads(json.dumps(view,allow_nan=False)),
                         'serial':self._serial,'minimumFrame':self.frame_revision(), 'context':dict(context)}
        self.receipt = {'phase':'pending','reason':'viewport-frame-not-current','viewRevision':self._last_view}
        self._start()

    def carrier_contract(self, stage, view=None):
        from .output_layout import same_layout_lease
        contract = self._contract
        if contract is None:
            return None
        native = type(contract) is NativeCarrierContract
        if native:
            if self.native_sample is None:
                return None
            from .grid_viewport import same_native_grid_observation
            fresh = self.native_sample(stage)
            if fresh.get('phase') != 'ready' or not same_native_grid_observation(contract.observation, fresh.get('nativeObservation')):
                return None
        elif not same_layout_lease(self._contract_layout_lease, self.layout_lease()):
            return None
        if contract is None or contract.stage != stage or tuple(sorted(self.current_context(stage).items())) != contract.context:
            return None
        if view is not None:
            if not native and view.get('backgroundGridRaster') is None:
                return None
            from . import render_scene
            if render_scene._background_grid_intent_json(view.get('backgroundGrid')) != contract.intent:
                return None
        return contract

    def _request_camera_matches(self, stage, view):
        """Reproduce the existing authored camera; no readback-vector tolerance."""
        from pxr import Gf, UsdGeom
        from . import render_scene
        c = view.get('camera')
        if not c:
            return False
        camera = UsdGeom.Camera(stage.GetPrimAtPath(render_scene.CAMERA))
        if not camera:
            return False
        matrix = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*c['position']),
            Gf.Vec3d(*c['target']),Gf.Vec3d(*c['up'])).GetInverse()
        f32 = lambda value: struct.unpack('f',struct.pack('f',value))[0]
        aperture = 20.955
        return (camera.GetLocalTransformation(0) == matrix
            and camera.ComputeLocalToWorldTransform(0) == matrix
            and camera.GetVerticalApertureAttr().Get(0) == f32(aperture)
            and camera.GetHorizontalApertureAttr().Get(0) == f32(aperture*c['aspect'])
            and camera.GetFocalLengthAttr().Get(0) == f32(aperture/(2*math.tan(math.radians(c['fov'])/2)))
            and camera.GetClippingRangeAttr().Get(0) == Gf.Vec2f(c['near'],c['far']))

    def display_receipt(self, stage, view):
        """Separate native world-plane sync; encoded raster receipt stays intact.

        Only a current typed writer contract and complete composed/native closure
        can add this field. Stored tags or a previous wire receipt grant nothing.
        Called again immediately before an already-successful ACK is pushed.
        """
        base = dict(self.receipt)
        base.pop('nativeGeometry', None)
        request, contract = self._request, self._contract
        checkpoint = 'initial-contract'
        def finish(refusal):
            _native_grid_receipt_diagnostic('producer',base,refusal=refusal,
                request=request,contract=contract,checkpoint=checkpoint)
            return base
        # Splitting the original OR expressions preserves evaluation order and
        # short-circuit reads; each decline now has a diagnostic-only fixed enum.
        if base.get('phase') != 'unsupported' or base.get('gridPixelVerified') is not False:
            return finish('encoded-status-not-eligible')
        if not request or request['stage'] != stage:
            return finish('request-stage-not-current')
        if not self._current(request):
            return finish('request-context-not-current')
        if type(contract) is not NativeCarrierContract or contract.stage != stage:
            return finish('typed-native-contract-absent')
        if contract.context != tuple(sorted(request['context'].items())):
            return finish('native-contract-context-not-current')
        try:
            from . import render_scene
            from .grid_viewport import same_native_grid_observation
            checkpoint = 'validate-accepted-view'
            render_scene.validate_view(view)
            raster, grid = view.get('backgroundGridRaster'), view.get('backgroundGrid')
            if not raster or not grid or not grid['visible']:
                return finish('accepted-grid-or-raster-absent')
            token = lambda v: json.dumps({k:v.get(k) for k in
                ('backgroundGrid','backgroundGridRaster','camera','timeSeconds')},
                sort_keys=True,separators=(',',':'),allow_nan=False)
            checkpoint = 'authored-request-token'
            authored_view = json.loads(stage.GetPrimAtPath(render_scene.ROOT).GetCustomDataByKey('dcdtuni:viewJson'))
            if token(authored_view) != token(request['view']):
                return finish('authored-request-token-not-current')
            checkpoint = 'accepted-observation-token'
            if token(view) != contract.observation.view_token:
                return finish('accepted-observation-token-not-current')
            checkpoint = 'native-grid-intent'
            if contract.intent != render_scene._background_grid_intent_json(grid):
                return finish('native-grid-intent-not-current')
            checkpoint = 'exact-authored-camera'
            if not self._request_camera_matches(stage,request['view']):
                return finish('exact-authored-camera-matrix-or-lens-not-current')
            binding, context = raster['binding'], request['context']
            checkpoint = 'accepted-binding'
            if (binding['projectUid'] != context['projectUid']
                or binding['projectContentSha256'] != context['projectContentSha256']
                or binding['sceneRevision'] != context['revision']
                or binding['viewRevision'] != contract.observation.view_revision):
                return finish('accepted-binding-not-current')
            checkpoint = 'first-native-closure'
            first = self._capture_native(request)
            if (first.get('phase') != 'ready'
                or not same_native_grid_observation(contract.observation,first.get('nativeObservation'))
                or not native_carrier_matches(stage,contract.key,contract)):
                return finish('first-native-observation-or-composed-carrier-not-current')
            checkpoint = 'final-native-closure'
            final = self._capture_native(request)
            observation = final.get('nativeObservation')
            if (final.get('phase') != 'ready'
                or not same_native_grid_observation(first['nativeObservation'],observation)
                or not same_native_grid_observation(contract.observation,observation)
                or not native_carrier_matches(stage,contract.key,contract)
                or not self._current(request) or self._contract is not contract
                or not self._request_camera_matches(stage,request['view'])
                or token(json.loads(stage.GetPrimAtPath(render_scene.ROOT).GetCustomDataByKey('dcdtuni:viewJson'))) != token(request['view'])):
                return finish('final-native-observation-camera-or-composed-closure-not-current')
            digest = lambda value: hashlib.sha256(json.dumps(value,sort_keys=True,
                separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
            checkpoint = 'construct-receipt'
            receipt = {'schema':'dcdtuni.native-grid-geometry/v1',
                'phase':'synchronized','domain':'native-world-plane',
                'binding':binding,'grid':grid,
                'requestedCamera':request['view']['camera'],'acceptedCamera':view['camera'],
                'timeSeconds':view.get('timeSeconds'),
                'nativeSource':{'ownerSha256':digest(observation.owner),
                    'projectionSha256':digest(observation.projection),
                    'frameNumber':observation.frame_number,
                    'resolution':list(observation.resolution),
                    'fullResolution':list(observation.full_resolution)},
                'carrierSha256':digest(contract.expected),
                'gridPixelVerified':False,'encodedFrameProof':'unresolved'}
            checkpoint = 'receipt-schema'
            schema = json.loads(render_scene.SCHEMA_PATH.with_name(
                'render-grid-native-geometry.schema.json').read_text())
            render_scene._validate(receipt,schema)
            base['nativeGeometry'] = json.loads(json.dumps(receipt,allow_nan=False))
        except Exception:
            return finish('exception-at-checkpoint')
        return finish('synchronized')

    def notify(self):
        request = self._request
        if not request or not self._current(request):
            return
        if self.receipt.get('phase') == 'pending':
            self._start()
        elif type(self._contract) is NativeCarrierContract and self.native_sample is not None:
            # Encoded parity stays unsupported; native metadata changes still
            # need one writer refresh. Ordinary later frames do not recompute.
            from .grid_viewport import same_native_grid_observation
            fresh = self._capture_native(request)
            if fresh.get('phase') != 'ready' or not same_native_grid_observation(
                    self._contract.observation, fresh.get('nativeObservation')):
                self.receipt = {'phase':'pending','reason':fresh.get('reason') or 'native-observation-changed',
                                'viewRevision':self._last_view,'gridPixelVerified':False}
                self._start()

    def _start(self):
        if not self._closed and self._request is not None and (self._task is None or self._task.done()):
            self._task = asyncio.ensure_future(self._drain())

    def _current(self, request):
        if self._closed or self._request is not request or request['serial'] != self._serial:
            return False
        try:
            return self.current_context(request['stage']) == request['context']
        except (ValueError,RuntimeError):
            return False

    def _capture(self, request):
        if self.frame_revision() <= request['minimumFrame']:
            return {'phase':'pending','reason':'viewport-frame-not-current'}
        return self.sample(request['stage'])

    def _capture_native(self, request):
        from . import render_scene
        from .grid_viewport import (NativeGridObservation,
            _NATIVE_GRID_COHORT_DIAGNOSTICS, native_grid_cohort_request)
        if self.frame_revision() <= request['minimumFrame']:
            return {'phase':'pending','reason':'viewport-frame-not-current'}
        if _NATIVE_GRID_COHORT_DIAGNOSTICS:
            with native_grid_cohort_request(request['view'],request['context'],request['serial']):
                capture = self.native_sample(request['stage'])
        else:
            capture = self.native_sample(request['stage'])
        observation = capture.get('nativeObservation')
        raster = request['view'].get('backgroundGridRaster')
        revision = raster['binding']['viewRevision'] if raster else None
        if (capture.get('phase') != 'ready' or type(observation) is not NativeGridObservation
            or observation.context != json.dumps(request['context'],sort_keys=True,separators=(',',':'),allow_nan=False)
            or observation.intent != render_scene._background_grid_intent_json(request['view']['backgroundGrid'])
            or observation.view_revision != revision):
            return {'phase':'pending','reason':capture.get('reason') or 'native-grid-view-not-current'}
        return capture

    async def _lower_native(self, request, encoded_reason):
        from . import render_scene
        from .grid_viewport import (same_native_grid_observation,
            _NATIVE_GRID_PENDING_DIAGNOSTICS, _native_grid_pending_diagnostic,
            _native_grid_pending_frame)
        capture = self._capture_native(request)
        if capture['phase'] != 'ready':
            if _NATIVE_GRID_PENDING_DIAGNOSTICS:
                try:
                    _native_grid_pending_diagnostic('lowering','lower-capture','pending',
                        capture['reason'],serial=request['serial'],categories=('capture-not-ready',))
                except Exception:
                    pass
            if self._current(request):
                self.receipt = {'phase':'pending','reason':capture['reason'],'viewRevision':self._last_view,
                                'gridPixelVerified':False}
            return
        observation = capture['nativeObservation']
        grid = request['view']['backgroundGrid']
        intent = render_scene._background_grid_intent_json(grid)
        key = json.dumps({'domain':'native-viewport-image-pixel','context':request['context'],
            'owner':observation.owner,'view':observation.view_token,'grid':grid,
            'projection':observation.projection,'resolution':observation.resolution,
            'fullResolution':observation.full_resolution,'widthNativePx':NATIVE_GRID_WIDTH_PX},
            sort_keys=True,separators=(',',':'),allow_nan=False)
        if not native_carrier_matches(request['stage'],key,self.carrier_contract(request['stage'])):
            if self._canonical is None or self._canonical[0] != intent:
                self._canonical = (intent,canonical_grid(grid))
            points,colors = self._canonical[1]
            result = await asyncio.get_running_loop().run_in_executor(self._pool,self.native_calculate,
                grid,points,colors,observation.projection,observation.resolution,NATIVE_GRID_WIDTH_PX)
            if not self._current(request):
                return
            final = self._capture_native(request)
            if (final.get('phase') != 'ready' or not same_native_grid_observation(observation,final.get('nativeObservation'))):
                if _NATIVE_GRID_PENDING_DIAGNOSTICS:
                    try:
                        frames = {'first':_native_grid_pending_frame(observation.owner,
                            observation.resolution,observation.full_resolution,
                            {'frameNumber':observation.frame_number})}
                        candidate = final.get('nativeObservation')
                        if type(candidate) is type(observation):
                            frames['final'] = _native_grid_pending_frame(candidate.owner,
                                candidate.resolution,candidate.full_resolution,
                                {'frameNumber':candidate.frame_number})
                        _native_grid_pending_diagnostic('lowering','lower-final-capture','pending',
                            'native-observation-changed-during-lowering',frames,serial=request['serial'],
                            categories=('final-capture-not-ready-or-observation-mismatch',))
                    except Exception:
                        pass
                self.receipt = {'phase':'pending','reason':'native-observation-changed-during-lowering',
                                'viewRevision':self._last_view,'gridPixelVerified':False}
                return
            if not self._current(request):
                return
            contract = author_ribbons(request['stage'],grid,result,key,_native_observation=final['nativeObservation'])
            self._contract = replace(contract,context=tuple(sorted(request['context'].items())),intent=intent)
            self._contract_layout_lease = None
        # Authored native geometry supplies no decoded stream/Three pixel proof.
        if _NATIVE_GRID_PENDING_DIAGNOSTICS:
            try:
                _native_grid_pending_diagnostic('lowering','lower-encoded-proof','unsupported',
                    encoded_reason,{'first':_native_grid_pending_frame(observation.owner,
                        observation.resolution,observation.full_resolution,
                        {'frameNumber':observation.frame_number})},serial=request['serial'],
                    categories=('native-carrier-available-encoded-proof-unresolved',))
            except Exception:
                pass
        self.receipt = {'phase':'unsupported','reason':encoded_reason,
                        'viewRevision':self._last_view,'gridPixelVerified':False}

    async def _drain(self):
        from . import render_scene
        while not self._closed:
            request = self._request
            if request is None or not self._current(request):
                return
            try:
                capture = self._capture(request)
                if self.native_sample is not None and self.frame_revision() > request['minimumFrame'] and (
                        request['view'].get('backgroundGridRaster') is None
                        or (capture['phase'] != 'ready' and capture['reason'].startswith('stream-layout'))):
                    await self._lower_native(request, capture.get('reason') or 'stream-layout-unresolved')
                    if not self._current(request):
                        continue
                    return
                if capture['phase'] != 'ready':
                    if self._current(request):
                        phase = 'unsupported' if capture['reason'].startswith('stream-layout') else 'pending'
                        self.receipt = {'phase':phase,'reason':capture['reason'],'viewRevision':self._last_view}
                    return
                from .output_layout import same_layout_lease
                captured_lease = capture.get('layoutLease')
                if not same_layout_lease(captured_lease, self.layout_lease()):
                    self._contract = None
                    self._contract_layout_lease = None
                    self.receipt = {'phase':'unsupported','reason':'stream-layout-unresolved','viewRevision':self._last_view}
                    return
                view = request['view']
                grid,raster = view['backgroundGrid'],view['backgroundGridRaster']
                key = json.dumps({'context':request['context'],'mediaOwner':raster['output']['owner'],
                    'outputPresentation':raster['output'],'metricVersion':raster['metricVersion'],'metric':raster['line'],'grid':grid,
                    'projection':capture['projection'],'layout':capture['observations'].get('imageNdcToOutputNdc'),
                    'layoutOwnerEpoch':captured_lease.key},
                    sort_keys=True,separators=(',',':'),allow_nan=False)
                if carrier_matches(request['stage'],key,self.carrier_contract(request['stage'])):
                    self.receipt = {'phase':'rendered','viewRevision':self._last_view}
                    return
                intent = render_scene._background_grid_intent_json(grid)
                if self._canonical is None or self._canonical[0] != intent:
                    self._canonical = (intent,canonical_grid(grid))
                points,colors = self._canonical[1]
                result = await asyncio.get_running_loop().run_in_executor(self._pool,self.calculate,
                    grid,points,colors,capture['projection'],raster['output']['decoded'],{'metricVersion':raster['metricVersion'],'line':raster['line'],'output':raster['output']})
                if not self._current(request):
                    continue
                final_capture = self._capture(request)
                if (final_capture['phase'] != 'ready'
                        or not same_layout_lease(captured_lease, final_capture.get('layoutLease'))
                        or not same_layout_lease(captured_lease, self.layout_lease())
                        or final_capture['projection'] != capture['projection']
                        or final_capture['observations'].get('imageNdcToOutputNdc') != capture['observations'].get('imageNdcToOutputNdc')):
                    # Wait for a coherent fresh public receipt. Do not commit a
                    # projection from before the decode/layout/camera transition.
                    self.receipt = {'phase':'pending','reason':'viewport-frame-not-current','viewRevision':self._last_view}
                    return
                if not self._current(request):
                    continue
                contract = author_ribbons(request['stage'],grid,result,key)
                contract = replace(contract,context=tuple(sorted(request['context'].items())),intent=intent)
                self._contract = contract
                self._contract_layout_lease = captured_lease
                self.receipt = {'phase':'rendered','viewRevision':self._last_view}
                return
            except Exception:
                # Native/CPU errors remain bounded; no arbitrary payload leaks.
                # An old failure cannot poison the latest accepted request.
                if not self._current(request):
                    continue
                self.receipt = {'phase':'rejected','reason':'grid-lowering-rejected','viewRevision':self._last_view}
                return

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._request = None
        self._serial += 1
        self._canonical = None
        self._contract = None
        self._contract_layout_lease = None
        if self._task is not None:
            self._task.cancel()
        self._pool.shutdown(wait=False,cancel_futures=True)
