"""Physical input batch identity is separate from cfdrun payload identity."""
import hashlib
from pathlib import Path
import pytest
from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot, HybridCfdrunSnapshot

def batch(case):
    from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256
    return surface_batch_sha256(case)

def surface(case,name='wall.stl',data=b'solid wall\nendsolid wall\n'):
    path=case/'Mesh/constant/triSurface'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path

def test_batch_digest_covers_names_bytes_and_sorted_nested_namespace(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    surface(a,'nested/one.stl',b'one');surface(a,'two.stl',b'two')
    surface(b,'two.stl',b'two');surface(b,'nested/one.stl',b'one')
    original=batch(a);assert original==batch(b)
    surface(b,'two.stl',b'changed');assert original!=batch(b)
    surface(b,'two.stl',b'two');(b/'Mesh/constant/triSurface/two.stl').rename(b/'Mesh/constant/triSurface/renamed.stl')
    assert original!=batch(b)

def test_empty_batch_creation_does_not_change_content_identity(tmp_path):
    original=batch(tmp_path);(tmp_path/'Mesh/constant/triSurface').mkdir(parents=True)
    assert original==batch(tmp_path)

@pytest.mark.parametrize('kind',['file','directory','root'])
def test_batch_refuses_symlink_members_or_namespace(tmp_path,kind):
    case=tmp_path/'case';case.mkdir();target=tmp_path/'owned-target';target.mkdir();(target/'one.stl').write_bytes(b'one')
    if kind=='root':
        (case/'Mesh/constant').mkdir(parents=True);(case/'Mesh/constant/triSurface').symlink_to(target,target_is_directory=True)
    else:
        surface(case);path=case/'Mesh/constant/triSurface/alias'
        path.symlink_to(target/'one.stl' if kind=='file' else target,target_is_directory=kind=='directory')
    with pytest.raises(ValueError,match='symlink'):batch(case)

@pytest.mark.parametrize('value',[True,'A'*64,'short'])
def test_snapshot_refuses_invalid_batch_identity_without_changing_payload(tmp_path,value):
    with pytest.raises(ValueError,match='STL batch'):
        CfdrunSnapshot(b'{"schemaVersion":2}',tmp_path,tmp_path/'model/unit.cfdrun',stl_batch_sha256=value)

def test_owned_stl_metadata_roundtrips_and_child_keeps_parent_identity(tmp_path):
    payload=b'{"schemaVersion":4,"runId":"owned","sourceProject":{"contentHash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}'
    sha=batch(tmp_path);source=CfdrunSnapshot(payload,tmp_path,tmp_path/'model/unit.cfdrun',stl_batch_sha256=sha)
    assert source.read_bytes()==payload and source.sha256==hashlib.sha256(payload).hexdigest()
    child=HybridCfdrunSnapshot(payload,tmp_path,tmp_path/('hybrid-mesh-'+('b'*16)+'.cfdrun'),parent_input=source,mesh_sha256='b'*64,stl_batch_sha256=sha)
    assert child.stl_batch_sha256==source.stl_batch_sha256
    with pytest.raises(ValueError,match='STL batch'):
        HybridCfdrunSnapshot(payload,tmp_path,child.logical_path,parent_input=source,mesh_sha256='b'*64,stl_batch_sha256='c'*64)


@pytest.mark.parametrize('mutation',['file','directory'])
def test_batch_refuses_mutation_while_reading_actual_files(tmp_path,monkeypatch,mutation):
    from backend.aircooling.runner import cfdrun_stl_identity as identity
    path=surface(tmp_path,data=b'actual captured bytes')
    original=identity.hashlib.sha256;changed=False
    class Hash:
        def __init__(self,*args,**kwargs):self.inner=original(*args,**kwargs)
        def update(self,data):
            nonlocal changed
            self.inner.update(data)
            if not changed:
                changed=True
                if mutation=='file':
                    with path.open('ab') as stream:stream.write(b' changed')
                else:surface(tmp_path,'late.stl',b'late member')
        def digest(self):return self.inner.digest()
        def hexdigest(self):return self.inner.hexdigest()
    monkeypatch.setattr(identity.hashlib,'sha256',Hash)
    with pytest.raises(ValueError,match='changed'):batch(tmp_path)


def test_batch_inventory_change_refuses_even_with_unchanged_directory_metadata(tmp_path,monkeypatch):
    from backend.aircooling.runner import cfdrun_stl_identity as identity
    import stat
    path=surface(tmp_path,data=b'actual capture')
    real_stat,real_fstat,real_hash=identity.os.stat,identity.os.fstat,identity.hashlib.sha256
    real_path_stat=Path.stat
    class StableDirectoryMetadata:
        def __init__(self,value):self.value=value
        def __getattr__(self,name):
            if name in ('st_mtime_ns','st_ctime_ns'):return 0
            if name=='st_size':return 4096
            return getattr(self.value,name)
    def metadata(value):return StableDirectoryMetadata(value) if stat.S_ISDIR(value.st_mode) else value
    monkeypatch.setattr(identity.os,'stat',lambda *a,**k:metadata(real_stat(*a,**k)))
    monkeypatch.setattr(identity.os,'fstat',lambda *a,**k:metadata(real_fstat(*a,**k)))
    # pathlib on this runtime caches its stat accessor, so control it explicitly.
    monkeypatch.setattr(Path,'stat',lambda self,*a,**k:metadata(real_path_stat(self,*a,**k)))
    changed=False
    class Hash:
        def __init__(self,*a,**k):self.inner=real_hash(*a,**k)
        def update(self,data):
            nonlocal changed
            self.inner.update(data)
            if not changed:
                changed=True;surface(tmp_path,'late.stl',b'late batch member')
        def digest(self):return self.inner.digest()
        def hexdigest(self):return self.inner.hexdigest()
    monkeypatch.setattr(identity.hashlib,'sha256',Hash)
    with pytest.raises(ValueError,match='changed'):batch(tmp_path)
