"""mesh_identity_sha256 reuses the digest of an unchanged mesh and notices every change to its files."""
import os

from backend.aircooling.runner import openfoam_patch_area as opa


def _mesh(root, ranks=0):
    dirs = [root / "constant/polyMesh"] if not ranks else [root / f"processor{r}/constant/polyMesh" for r in range(ranks)]
    for d in dirs:
        d.mkdir(parents=True)
        for name in ("points", "faces", "owner", "neighbour", "boundary"):
            (d / name).write_bytes(f"{d}:{name}\n".encode() * 10)
    return dirs


def _fresh(path):
    opa._IDENTITY_MEMO.clear()
    return opa.mesh_identity_sha256(path)


def test_memo_returns_the_plain_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(opa, "_SETTLED_NS", 0)     # these files were just written; take them as settled
    _mesh(tmp_path)
    first = _fresh(tmp_path)
    assert opa.mesh_identity_sha256(tmp_path) == first and len(opa._IDENTITY_MEMO) == 1


def test_a_rewrite_with_the_same_size_changes_the_digest(tmp_path):
    (d,) = _mesh(tmp_path)
    before = _fresh(tmp_path)
    data = (d / "boundary").read_bytes()
    (d / "boundary").write_bytes(data.replace(b"boundary", b"BOUNDARY"))
    after = opa.mesh_identity_sha256(tmp_path)
    assert after != before and after == _fresh(tmp_path)


def test_restored_times_do_not_hide_a_change(tmp_path):
    (d,) = _mesh(tmp_path)
    before = _fresh(tmp_path)
    st = (d / "owner").stat()
    (d / "owner").write_bytes((d / "owner").read_bytes()[::-1])
    os.utime(d / "owner", ns=(st.st_atime_ns, st.st_mtime_ns))     # the change time still moves
    assert opa.mesh_identity_sha256(tmp_path) != before


def test_partitioned_mesh(tmp_path):
    dirs = _mesh(tmp_path, ranks=3)
    before = _fresh(tmp_path)
    assert opa.mesh_identity_sha256(tmp_path) == before
    (dirs[2] / "points").write_bytes(b"moved\n")
    assert opa.mesh_identity_sha256(tmp_path) != before


def test_files_written_just_before_the_read_are_not_memoised(tmp_path):
    """File times advance in coarse ticks: a same-size rewrite in the tick of the read keeps the key, so a digest
    of files younger than _SETTLED_NS is never stored (it was, and a rewrite returned the old digest)."""
    (d,) = _mesh(tmp_path)
    first = _fresh(tmp_path)
    assert not opa._IDENTITY_MEMO
    (d / "points").write_bytes((d / "points").read_bytes().replace(b"points", b"POINTS"))
    assert opa.mesh_identity_sha256(tmp_path) != first
