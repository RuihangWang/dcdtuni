"""The Kit-native .cfdproj path end to end on CPU (pxr only, no Kit, no GPU).

usdgen.build_scene authors a real case; every texture it binds must be a file of
the shared pool whose bytes hash to the manifest's sha256 — no USDresource, no
bakes, no copies.
"""
import hashlib
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'kit/extensions/dc.cfd_bridge'))
from dc.cfd_bridge import emitters, material_library as lib, usdgen  # noqa: E402
from dc.cfd_bridge.cfdproj import read_cfdproj  # noqa: E402

pxr = pytest.importorskip('pxr')
from pxr import Usd, UsdGeom, UsdShade  # noqa: E402

REPO = Path(__file__).resolve().parents[5]
CASES = ['case/Base_leakage/Base_leakage.cfdproj', 'case/DAML-Testbed/DMAL_Testbed-patched.cfdproj']


def build(case):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    scene = read_cfdproj(str(REPO / case))
    groups = usdgen.build_scene(stage, scene, emitters.emit)
    return stage, scene, groups


def bound_files(stage):
    files = set()
    looks = stage.GetPrimAtPath(usdgen.LOOKS)
    for material in looks.GetChildren():
        for shader in material.GetChildren():
            s = UsdShade.Shader(shader)
            if s and s.GetInput('file') and s.GetInput('file').Get():
                files.add(s.GetInput('file').Get().path)
    return files


@pytest.mark.parametrize('case', CASES)
def test_every_bound_texture_is_a_verified_pool_file(case):
    stage, scene, _ = build(case)
    files = bound_files(stage)
    assert files, 'a textured scene binds pool files'
    index = lib.file_index()
    for path in files:
        p = Path(path)
        assert p.is_absolute() and p.is_relative_to(lib.POOL_ROOT), path
        rel = p.relative_to(lib.POOL_ROOT).as_posix()
        assert hashlib.sha256(p.read_bytes()).hexdigest() == index[rel]['sha256'], rel
    assert not any('USDresource' in f or '/baked/' in f for f in files)


def test_every_mesh_is_bound_to_a_manifest_material():
    stage, scene, _ = build(CASES[0])
    proto = stage.GetPrimAtPath('/__Prototypes')
    meshes = [m for m in Usd.PrimRange(proto, Usd.PrimAllPrimsPredicate) if m.IsA(UsdGeom.Mesh)]
    assert meshes
    for mesh in meshes:
        binding = UsdShade.MaterialBindingAPI(mesh).GetDirectBinding().GetMaterialPath()
        assert str(binding).startswith(usdgen.LOOKS + '/'), mesh.GetPath()
        assert UsdGeom.PrimvarsAPI(mesh).GetPrimvar('st').IsDefined(), mesh.GetPath()


def test_slab_floor_and_ceiling_are_different_surfaces():
    stage, scene, groups = build(CASES[0])

    def material_of(name):
        placement = groups[usdgen._safe_name(name)][0]
        ref = stage.GetPrimAtPath(placement + '/_ref')
        target = ref.GetMetadata('references').GetAddedOrExplicitItems()[0].primPath
        mesh = next(m for m in Usd.PrimRange(stage.GetPrimAtPath(target), Usd.PrimAllPrimsPredicate)
                    if m.IsA(UsdGeom.Mesh))
        return str(UsdShade.MaterialBindingAPI(mesh).GetDirectBinding().GetMaterialPath())

    floor, ceiling = material_of('Room_Floor'), material_of('Room_Ceiling')
    assert floor.split('/')[-1].startswith('floor__') and ceiling.split('/')[-1].startswith('ceiling__')


def test_the_electric_box_door_binds_its_pool_picture():
    stage, _, _ = build(CASES[1])
    assert str(lib.POOL_ROOT / 'electric-box/panel_lamps.jpg') in bound_files(stage)
    assert str(lib.POOL_ROOT / 'grilles/four_way_diffuser.jpg') in bound_files(stage)


PNG_1PX = ('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aM1kAAAAASUVORK5CYII=')


def test_an_embedded_picture_lands_in_the_owned_cache(monkeypatch, tmp_path):
    cache = tmp_path / 'render-textures'
    monkeypatch.setenv('DCDTUNI_RENDER_TEXTURE_CACHE', str(cache))
    stage = Usd.Stage.CreateInMemory()
    path = usdgen.material_for(stage, 'face-image', picture=PNG_1PX, fallback='box')
    texture = UsdShade.Shader(stage.GetPrimAtPath(path + '/ColorTexture'))
    asset = Path(texture.GetInput('file').Get().path)
    assert asset.parent == cache.resolve() and asset.suffix == '.png'


def test_without_an_owned_cache_an_embedded_picture_falls_back_instead_of_failing(monkeypatch):
    monkeypatch.delenv('DCDTUNI_RENDER_TEXTURE_CACHE', raising=False)
    stage = Usd.Stage.CreateInMemory()
    path = usdgen.material_for(stage, 'face-image', picture=PNG_1PX, fallback='box')
    assert path.split('/')[-1].startswith('box__')
