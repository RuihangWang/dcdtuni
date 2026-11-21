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
import struct
from dataclasses import dataclass, replace


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
    if contract is None or contract.stage != stage or contract.key != key:
        return False
    try:
        actual,_ = _composed_contract(stage,contract.paths)
        return actual == contract.expected
    except Exception:
        return False


def author_ribbons(stage, grid, data, key):
    """Atomic replacement of this decoration only; old bytes restored on failure."""
    from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade
    from . import render_scene
    root = render_scene.BACKGROUND_GRID_ROOT
    work = Usd.Stage.CreateInMemory()
    parent = UsdGeom.Xform.Define(work,root)
    parent.AddTransformOp().Set(Gf.Matrix4d(*grid['matrix']))
    parent.GetPrim().SetCustomDataByKey('dcdtuni:viewDecoration',True)
    parent.GetPrim().SetCustomDataByKey('dcdtuni:backgroundGridIntentJson',render_scene._background_grid_intent_json(grid))
    parent.GetPrim().SetCustomDataByKey('dcdtuni:rasterCarrierKey',key)
    parent.GetPrim().SetCustomDataByKey('dcdtuni:rasterCarrierDigest',_fingerprint(data))
    mesh = UsdGeom.Mesh.Define(work,root+'/Stroke')
    mesh.CreateSubdivisionSchemeAttr('none')
    mesh.CreatePointsAttr([Gf.Vec3f(*p) for p in data['points']])
    mesh.CreateFaceVertexCountsAttr(data['counts'])
    mesh.CreateFaceVertexIndicesAttr(list(range(len(data['points']))))
    mesh.CreateDoubleSidedAttr(True)
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('displayColor',Sdf.ValueTypeNames.Color3fArray,'vertex').Set([Gf.Vec3f(*p) for p in data['colors']])
    mesh.GetPrim().SetCustomDataByKey('dcdtuni:viewDecoration',True)
    source = {'color':[1,1,1],'emissive':[0,0,0],'opacity':grid['opacity'],'roughness':1,'metalness':0,'doubleSided':True,'unlit':True}
    material = render_scene._material(work,root+'/Looks/Grid',source,{},True)
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
    expected,paths = _composed_contract(work)
    contract = CarrierContract(stage,key,expected,paths)
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
            if not carrier_matches(stage,key,contract):
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
    def __init__(self, current_context, sample, frame_revision, calculate=calculate_ribbons, layout_lease=lambda: None):
        self.current_context = current_context
        self.sample = sample
        self.frame_revision = frame_revision
        self.calculate = calculate
        self.layout_lease = layout_lease
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
        if raster is None:
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
        if not same_layout_lease(self._contract_layout_lease, self.layout_lease()):
            return None
        if contract is None or contract.stage != stage or tuple(sorted(self.current_context(stage).items())) != contract.context:
            return None
        if view is not None:
            if view.get('backgroundGridRaster') is None:
                return None
            from . import render_scene
            if render_scene._background_grid_intent_json(view.get('backgroundGrid')) != contract.intent:
                return None
        return contract

    def notify(self):
        if self._request and self.receipt.get('phase') == 'pending':
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

    async def _drain(self):
        from . import render_scene
        while not self._closed:
            request = self._request
            if request is None or not self._current(request):
                return
            try:
                capture = self._capture(request)
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
