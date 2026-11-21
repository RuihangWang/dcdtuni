"""Regression for actual Hydra GL/reversed depth matrix matching and clipping."""
from pathlib import Path
import importlib.util
import sys

import pytest
from pxr import Gf

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / "src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge"

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BRIDGE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

v = load("_native_grid_depth_viewport_test", "grid_viewport.py")
r = load("_native_grid_depth_raster_test", "grid_raster.py")

def flatten(matrix):
    return [float(matrix[i][j]) for i in range(4) for j in range(4)]

def reverse_depth(values):
    return [.5 * (values[i + 1] - values[i]) if i % 4 == 2 else value
            for i, value in enumerate(values)]

@pytest.mark.parametrize("fov,near,far", [(45, .1, 400), (60, .001, 1000), (35, 1, 30)])
def test_real_gf_camera_matches_both_complete_conventions(fov, near, far):
    camera = Gf.Camera()
    camera.SetPerspectiveFromAspectRatioAndFieldOfView(16 / 9, fov, Gf.Camera.FOVVertical)
    camera.clippingRange = Gf.Range1f(near, far)
    camera.transform = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 2, 3), 27)) * Gf.Matrix4d().SetTranslate(Gf.Vec3d(3, 4, 7))
    frustum = camera.frustum
    projection = flatten(frustum.ComputeProjectionMatrix())
    world = flatten(frustum.ComputeViewMatrix() * frustum.ComputeProjectionMatrix())
    for delivered_projection, delivered_world in [(projection, world), (reverse_depth(projection), reverse_depth(world))]:
        args = [projection, delivered_projection, world, delivered_world]
        assert v._native_frame_projection_matches(*args)
        # Every field of each matrix remains checked, including the depth column.
        for field in range(4):
            for index in range(16):
                changed = [list(value) for value in args]
                changed[field][index] += .02 * max(1, abs(changed[field][index]))
                assert not v._native_frame_projection_matches(*changed), (field, index)
    assert not v._native_frame_projection_matches(projection, reverse_depth(projection), world, world)
    assert not v._native_frame_projection_matches(projection, projection, world, reverse_depth(world))
    transposed = [projection[j * 4 + i] for i in range(4) for j in range(4)]
    assert not v._native_frame_projection_matches(projection, transposed, world, world)

@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True])
def test_nonfinite_or_boolean_observations_refuse(invalid):
    identity = flatten(Gf.Matrix4d(1))
    for field in range(4):
        args = [list(identity) for _ in range(4)]
        args[field][10] = invalid
        with pytest.raises(ValueError):
            v._native_frame_projection_matches(*args)

def test_missing_entries_refuse_even_when_both_short_vectors_agree():
    identity = flatten(Gf.Matrix4d(1))
    with pytest.raises(ValueError):
        v._native_frame_projection_matches(identity[:-1], identity[:-1], identity, identity)

@pytest.mark.parametrize("z", [-1.01, -1, -.25, 0, .5, 1, 1.01])
def test_existing_authored_gl_clip_planes_equal_fixed_reversed_planes(z):
    point = (.2, -.3, z, 1)
    planes = r._clip_h(point)
    reversed_z = .5 * (point[3] - point[2])
    assert (planes[4] >= 0 and planes[5] >= 0) == (0 <= reversed_z <= point[3])
    assert planes[4] == pytest.approx(2 * (point[3] - reversed_z), rel=0, abs=1e-15)
    assert planes[5] == pytest.approx(2 * reversed_z, rel=0, abs=1e-15)
