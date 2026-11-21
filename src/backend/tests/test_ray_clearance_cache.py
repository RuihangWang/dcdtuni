"""Scalar/NumPy semantic controls; imports actual review/product source, no replica kernel."""
import builtins,importlib.util,math,os,random,struct,sys
from pathlib import Path
import numpy as np
import pytest
from backend.aircooling.runner import stlgen_runner as product

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
CANDIDATE=os.environ.get('DCDTUNI_RAY_CANDIDATE');runner=load('backend.aircooling.runner._ray_review_candidate',CANDIDATE) if CANDIDATE else product
BASELINE=os.environ.get('DCDTUNI_RAY_BASELINE');baseline=load('backend.aircooling.runner._ray_review_baseline',BASELINE) if BASELINE else product

def bits(value):return None if value is None else struct.pack('>d',value).hex()
def outcome(fn):
 try:return ('return',bits(fn()))
 except Exception as exc:return ('raise',type(exc).__name__,str(exc))
def tri(z=1.):return ((0.,0.,z),(1.,0.,z),(0.,1.,z))
def scene(values):return [('original.stl','owner_'+str(i%3),t) for i,t in enumerate(values)]

def test_batch_hits_match_original_scalar_per_facet_at_edges_parallel_epsilon_and_signed_directions():
 rng=random.Random(63701);triangles=[tri(),tuple(reversed(tri())),tri(1e-9),tri(1e-9+1e-20),((0.,0.,1.),(1e-9,0.,1.),(0.,1.,1.)),((0.,0.,1.),(0.,0.,1.),(0.,0.,1.))]
 triangles += [tuple(tuple(rng.uniform(-3,3) for _ in range(3)) for _ in range(3)) for _ in range(240)]
 origins=[(.2,.2,0.),(0.,0.,0.),(1.,0.,0.),(.5,.5,0.),(math.nextafter(.5,math.inf),.5,0.),(-0.,0.,1.)]
 directions=[(0.,0.,1.),(-0.,0.,-1.),(1.,0.,0.),(1.,2.,3.),(1e-10,0.,1e-10),(0.,0.,0.)]
 for origin in origins:
  for direction in directions:
   for triangle in triangles:
    prepared=runner._make_scene_ray_cache(scene([triangle]));assert prepared._np is not None
    result=runner._ray_triangle_distances_batch(np,origin,direction,prepared._v0,prepared._edge1,prepared._edge2)
    actual=float(result[0]) if len(result) else None
    assert bits(actual)==bits(baseline._ray_triangle_intersection(origin,direction,triangle))

@pytest.mark.parametrize('ignored',[set(),{('original.stl','owner_0')},{('unknown.stl','owner_0')},{('original.stl','owner_0'),('original.stl','owner_1'),('original.stl','owner_2')}])
def test_nearest_chunk_boundary_order_and_ignored_surface_identity(ignored):
 rows=scene([tri(2.+i*.0001) for i in range(runner._RAY_BATCH_TRIANGLES+19)])
 rows[runner._RAY_BATCH_TRIANGLES]=('original.stl','owner_2',tri(.7))
 prepared=runner._make_scene_ray_cache(rows);assert prepared._np is not None
 for origin,direction in [((.2,.2,0.),(0.,0.,1.)),((.2,.2,4.),(0.,0.,-1.)),((.2,.2,0.),(1.,0.,0.))]:
  assert bits(runner._nearest_ray_clearance(origin,direction,prepared,ignored))==bits(baseline._nearest_ray_clearance(origin,direction,rows,ignored))

@pytest.mark.parametrize('value',[float('nan'),float('inf'),-float('inf'),1e308])
def test_nonfinite_or_overflow_falls_back_to_original_scalar_semantics(value):
 bad=((value,0.,1.),(-value,0.,1.),(0.,1.,1.));rows=scene([bad,tri()]);prepared=runner._make_scene_ray_cache(rows)
 assert prepared._np is None
 assert outcome(lambda:runner._nearest_ray_clearance((.2,.2,0.),(0.,0.,1.),prepared,set()))==outcome(lambda:baseline._nearest_ray_clearance((.2,.2,0.),(0.,0.,1.),rows,set()))

@pytest.mark.parametrize('direction',[(float('nan'),0.,1.),(float('inf'),0.,1.),(0,0,1),(0.,0.,-0.)])
def test_unsupported_ray_preserves_baseline_semantics(direction):
 rows=scene([tri()]);prepared=runner._make_scene_ray_cache(rows)
 assert outcome(lambda:runner._nearest_ray_clearance((.2,.2,0.),direction,prepared,set()))==outcome(lambda:baseline._nearest_ray_clearance((.2,.2,0.),direction,rows,set()))


def test_epoch_arrays_and_vertex_owners_are_detached_and_immutable():
 mutable=[['old.stl','owned',[[0.,0.,1.],[1.,0.,1.],[0.,1.,1.]]]]
 old=runner._make_scene_ray_cache(mutable);assert old._np is not None
 for arr in [old._v0,old._edge1,old._edge2,old._surface_ids]:
  with pytest.raises(ValueError):arr.setflags(write=True)
 mutable[0][2][0][2]=mutable[0][2][1][2]=mutable[0][2][2][2]=2.;mutable[0][1]='new-owner'
 new=runner._make_scene_ray_cache(mutable)
 assert bits(runner._nearest_ray_clearance((.2,.2,0.),(0.,0.,1.),old,set()))==bits(1.)
 assert bits(runner._nearest_ray_clearance((.2,.2,0.),(0.,0.,1.),new,set()))==bits(2.)
 assert old._surface_refs==(('old.stl','owned'),) and new._surface_refs==(('old.stl','new-owner'),)
 assert list(old)[0][2][0][2]==1.


def test_missing_numpy_and_batch_exception_fall_back_without_weaker_predicate(monkeypatch):
 rows=scene([tri()]);real_import=builtins.__import__
 def blocked(name,*a,**k):
  if name=='numpy':raise ImportError('controlled optional dependency absence')
  return real_import(name,*a,**k)
 with monkeypatch.context() as context:
  context.setattr(builtins,'__import__',blocked);prepared=runner._make_scene_ray_cache(rows);assert prepared._np is None
  assert bits(runner._nearest_ray_clearance((.2,.2,0.),(0.,0.,1.),prepared,set()))==bits(1.)
 prepared=runner._make_scene_ray_cache(rows)
 def refuse(*args,**kwargs):raise RuntimeError('controlled batch failure')
 monkeypatch.setattr(runner,'_ray_triangle_distances_batch',refuse)
 assert bits(runner._nearest_ray_clearance((.2,.2,0.),(0.,0.,1.),prepared,set()))==bits(1.)


def test_two_sided_and_anchor_candidate_order_remain_exact():
 rows=scene([tri(2.),tuple(reversed(tri(-2.)))])
 prepared=runner._make_scene_ray_cache(rows);sheet=[tri(0.)];direction=(0.,0.,1.)
 assert runner._two_sided_clearance((.2,.2,0.),direction,prepared,set())==baseline._two_sided_clearance((.2,.2,0.),direction,rows,set())
 assert runner._best_sheet_seed_anchor(sheet,direction,.1,prepared,set())==baseline._best_sheet_seed_anchor(sheet,direction,.1,rows,set())
