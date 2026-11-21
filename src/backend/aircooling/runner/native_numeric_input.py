"""Identity of native bytes consumed by one exact postprocess frame; not acceptance."""
import gzip,hashlib,json,math,re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

def canonical_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode("ascii")

def sha256(data):return hashlib.sha256(data).hexdigest()

def _other_field_header(path,name,head):
    """Read real optional OpenFOAM field dimensions; never infer units by name."""
    from backend.aircooling.runner.air_run_result_receipt import _COMMENTS,_FOAM_DICT,_DIMENSIONS
    if path.suffix=='.gz':
        prefix=bytearray()
        with gzip.open(path,'rb')as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                if len(prefix)<128*1024:prefix.extend(chunk[:128*1024-len(prefix)])
        head=bytes(prefix)
    text=_COMMENTS.sub('',head.decode('latin-1'));header=_FOAM_DICT.search(text)
    if header is None:raise ValueError('native field has no actual FoamFile header')
    block=header[1];foam=re.search(r'\bclass\s+([A-Za-z][A-Za-z0-9_]*)\s*;',block);obj=re.search(r'\bobject\s+([A-Za-z][A-Za-z0-9_]*)\s*;',block)
    if foam is None or obj is None or obj[1]!=name:raise ValueError('native field object/class differs from actual file')
    if foam[1]not in {'volScalarField','volVectorField','surfaceScalarField','surfaceVectorField','pointScalarField','pointVectorField'}:
        raise ValueError('unsupported native numerical field class')
    dimensional=_DIMENSIONS.search(text[header.end():])
    if dimensional is None:raise ValueError('native field has no actual dimensions')
    dims=[float(value)for value in dimensional[1].split()]
    if len(dims)!=7 or not all(math.isfinite(value)for value in dims):raise ValueError('invalid native field dimensions')
    if re.search(r'\binternalField\b',text[dimensional.end():])is None:raise ValueError('native field lacks actual internalField')
    return foam[1],dims

def native_field_units(snapshot):
    """Units from every rank's pinned actual dimensional header, never field labels."""
    known={(0,0,0,1,0,0,0):'K',(0,1,-1,0,0,0,0):'m/s',(0,2,-2,0,0,0,0):'m^2/s^2',
        (0,2,-3,0,0,0,0):'m^2/s^3',(0,2,-1,0,0,0,0):'m^2/s',(1,-1,-2,0,0,0,0):'Pa',(0,0,0,0,0,0,0):'1'}
    specs={}
    for row in snapshot['files']:
        if 'field'not in row:continue
        name=row['field'];spec=(row['foamClass'],tuple(row['dimensions']))
        if name in specs and specs[name]!=spec:raise ValueError('native field class/dimensions differ across ranks')
        specs[name]=spec
    result={}
    for name,(foam,dims)in specs.items():
        if foam not in {'volScalarField','volVectorField'}:continue
        unit=known.get(dims)or' '.join(symbol+(('^'+str(int(power)if power==int(power)else power))if power!=1 else'')for symbol,power in zip(['kg','m','s','K','mol','A','cd'],dims)if power)
        result[name]=unit
        if foam=='volVectorField':
            for suffix in ['mag','x','y','z']:result[name+suffix]=unit
        if name=='U':result['speed']=unit
    return result

def capture_native_inputs(run_dir,time_value):
    """Same exact native serial/common-MPI frame used by existing PP/receipt.

    Snapshot before and after native read, and recheck before/after grant issue.
    This is a bytes identity, NEVER execution acceptance or OO face semantics.
    """
    from backend.aircooling.runner.postprocess import _openfoam_result_layout
    from backend.aircooling.runner.air_run_result_receipt import _inside,_hash_file,_one_time_dir,_one_field_path,_field_header
    if type(time_value)not in(int,float)or not math.isfinite(time_value)or time_value<=0:
        raise ValueError('native input snapshot requires one positive exact frame')
    run=Path(run_dir).resolve(strict=True);meshes,times=_openfoam_result_layout(run,float(time_value))
    if time_value not in times:raise ValueError('selected native input frame is not common/readable')
    inputs=[]
    for rank,mesh in enumerate(meshes):
        base=mesh.parent.parent
        for name in ('points','faces','owner','neighbour','boundary'):inputs.append((_inside(run,mesh/name,label='numeric input mesh'),None))
        selected=_one_time_dir(base,float(time_value))
        names={path.name[:-3]if path.name.endswith('.gz')else path.name for path in selected.iterdir()if path.is_file()or path.is_symlink()}
        if not {'T','U'}<=names:raise ValueError('selected native frame lacks required T/U')
        if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',name)for name in names):raise ValueError('selected native field name is unsupported')
        for name in sorted(names):inputs.append((_inside(run,_one_field_path(selected,name),label='numeric input field'),name))
    # hashlib releases the GIL on large updates: files hash in parallel (Base604, 224 files / 2.7 GB: 3.1 s serial), listed in order.
    with ThreadPoolExecutor(max_workers=8)as pool:digests=list(pool.map(lambda item:_hash_file(item[0]),inputs))
    files=[]
    for(path,name),(digest,head)in zip(inputs,digests):
        if name is None:files.append({'path':path.relative_to(run).as_posix(),'sha256':digest});continue
        foam_class,dimensions=(_field_header(path,name,head)if name in {'T','U'}else _other_field_header(path,name,head))
        files.append({'path':path.relative_to(run).as_posix(),'sha256':digest,'field':name,'foamClass':foam_class,'dimensions':dimensions})
    body={'schema':'dcdtuni.native-numeric-input/v1','timeValue':float(time_value),'layout':'serial'if meshes==[run/'constant/polyMesh']else'partitioned','files':files}
    return {**body,'sha256':sha256(canonical_bytes(body))}
