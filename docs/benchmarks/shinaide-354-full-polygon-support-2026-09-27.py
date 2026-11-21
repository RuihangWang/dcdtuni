"""Read-only projected polygon/triangle support for Shinaide con_f/floor ROI."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from backend.aircooling.runner.tools.mesh_source_support_probe import (
    _read_ascii_stl_solid, _read_boundary_ranges, _read_faces, _read_foam_array,
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--mesh', type=Path, required=True, help='OpenFOAM Mesh directory')
parser.add_argument('--cfdrun', type=Path, required=True)
parser.add_argument('--spec', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--rank', type=int, default=0)
parser.add_argument('--roi', type=float, nargs=6,
                    default=(1.485, 0.775, 2.809, 1.545, 0.825, 2.825))
args = parser.parse_args()
if args.rank < 0:
    parser.error('--rank must be nonnegative')
mesh = args.mesh
tri_surface = mesh / 'constant/triSurface'
poly = mesh / f'processor{args.rank}/constant/polyMesh'
spec = json.loads(args.spec.read_text())
manifest = json.loads(args.cfdrun.read_text())
source_uids = {}
for solid in ('con_f', 'Raised_Floor'):
    matches = [row for row in manifest['geometryExport']['sourceSurfaces'].values()
               if row['solidName'] == solid]
    if len(matches) != 1:
        raise ValueError(f'expected one source UID for {solid}, found {len(matches)}')
    source_uids[solid] = matches[0]['sourceFaceUid']
source = {
    'con_f': ('merged_wall_baffle.stl', (1, 2)),
    'Raised_Floor': ('wallBaffle_floor_Raised_Floor.stl', (0, 2)),
}
source_triangles = {}
for solid, (filename, axes) in source.items():
    tris = _read_ascii_stl_solid(tri_surface / filename, solid)
    source_triangles[solid] = [[(p[axes[0]], p[axes[1]]) for p in tri] for tri in tris]

def area(poly):
    return abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in
                   zip(poly, poly[1:]+poly[:1]))) / 2 if len(poly)>=3 else 0.0

def clip(subject, triangle):
    orientation = 1 if sum(a[0]*b[1]-a[1]*b[0] for a,b in
                           zip(triangle, triangle[1:]+triangle[:1])) >= 0 else -1
    out = list(subject)
    for a,b in zip(triangle, triangle[1:]+triangle[:1]):
        def signed(p):
            return orientation*((b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]))
        before = out
        out = []
        if not before: break
        prev = before[-1]
        dp = signed(prev)
        for point in before:
            dc = signed(point)
            inp, inc = dp >= -1e-13, dc >= -1e-13
            if inp != inc:
                denom = dp-dc
                if abs(denom) > 1e-30:
                    t = dp/denom
                    out.append((prev[0]+t*(point[0]-prev[0]),
                                prev[1]+t*(point[1]-prev[1])))
            if inc: out.append(point)
            prev,dp=point,dc
    return out

def support(points, solid):
    axes = source[solid][1]
    face = list(dict.fromkeys((p[axes[0]], p[axes[1]]) for p in points))
    centre = (sum(p[0] for p in face)/len(face), sum(p[1] for p in face)/len(face))
    face.sort(key=lambda p: math.atan2(p[1]-centre[1], p[0]-centre[0]))
    turns = [((face[(i+1)%len(face)][0]-face[i][0])
              *(face[(i+2)%len(face)][1]-face[(i+1)%len(face)][1])
              -(face[(i+1)%len(face)][1]-face[i][1])
              *(face[(i+2)%len(face)][0]-face[(i+1)%len(face)][0]))
             for i in range(len(face))]
    if any(t < -1e-12 for t in turns) and any(t > 1e-12 for t in turns):
        raise ValueError(f'non-convex sampled face outside this diagnostic scope: {face}')
    face_area = area(face)
    if face_area <= 1e-14:
        raise ValueError(f'near-zero projected face area: {solid} {points}')
    intersections = [area(clip(face, tri)) for tri in source_triangles[solid]]
    covered = sum(intersections)
    if covered > face_area + 1e-8:
        raise ValueError(f'overlapping source triangles: {solid} {covered} > {face_area}')
    return {'face_area_m2':face_area,'covered_area_m2':min(face_area,covered),
            'outside_area_m2':max(0.0,face_area-covered),
            'covered_fraction':min(1.0,covered/face_area)}

# Independent clipping controls and source-triangulation non-overlap check.
unit = [(0, 0), (1, 0), (1, 1), (0, 1)]
left = [(0, 0), (1, 0), (1, 1)]
right = [(0, 0), (1, 1), (0, 1)]
if abs(area(clip(unit, left)) + area(clip(unit, right)) - 1) > 1e-12:
    raise ValueError('two complementary triangles must cover the unit square')
if area(clip([(2, 0), (3, 0), (3, 1), (2, 1)], left)) > 1e-12:
    raise ValueError('disjoint rectangle must have zero intersection')
if abs(area(clip(unit, list(reversed(left)))) - .5) > 1e-12:
    raise ValueError('triangle orientation changed intersection area')
source_pair_max_overlap = {}
for solid, triangles in source_triangles.items():
    source_pair_max_overlap[solid] = max(
        (area(clip(a, b)) for index, a in enumerate(triangles)
         for b in triangles[index + 1:]), default=0.0)
    if source_pair_max_overlap[solid] > 1e-10:
        raise ValueError(f'overlapping source triangles exceed this diagnostic scope: {solid}')

# Read exactly the final six faces selected by the prior explicit fixture.
coords = _read_foam_array(poly/'points','d',3)
points = [tuple(coords[i:i+3]) for i in range(0,len(coords),3)]
offsets,labels = _read_faces(poly/'faces')
owners = _read_foam_array(poly/'owner','i')
neighbours = _read_foam_array(poly/'neighbour','i')
ranges = _read_boundary_ranges(poly/'boundary')
patch_to_solid = {item['patch']:item['solid'] for item in spec['sourcePatches']}
requested = set(spec['cells'])
final = []
for face_id in range(len(neighbours),len(owners)):
    cell = owners[face_id]
    if cell not in requested: continue
    patch = next((name for lo,hi,name in ranges if lo<=face_id<hi),None)
    if patch not in patch_to_solid: continue
    solid = patch_to_solid[patch]
    vertices = [points[labels[i]] for i in range(offsets[face_id],offsets[face_id+1])]
    final.append({'cell':cell,'face':face_id,'patch':patch,'sourceFaceUid':source_uids[solid],
                  **support(vertices,solid)})
if len(final)!=spec['expectedMappedFaces']:
    raise ValueError(f'expected {spec["expectedMappedFaces"]} final faces, got {len(final)}')

roi=tuple(args.roi)
zone_to_solid={3:'con_f',4:'Raised_Floor'}
stages={}
for stage in sorted((mesh/'iface_dbg').glob(f'*.{args.rank}.txt')):
    unique={}
    for line in stage.read_text().splitlines():
        if ' | own=' not in line:continue
        match=re.search(r'patch=(-?\d+) zone=(-?\d+)',line)
        if not match:continue
        zone=int(match.group(2))
        if zone not in zone_to_solid:continue
        vertices=tuple(tuple(map(float,p.split())) for p in
                       re.findall(r'\(([-\d.eE+ ]+)\)',line))
        if len(vertices)<3:continue
        centre=tuple(sum(p[i] for p in vertices)/len(vertices) for i in range(3))
        if not all(roi[i]<=centre[i]<=roi[i+3] for i in range(3)):continue
        key=(zone,tuple(sorted(vertices)))
        solid=zone_to_solid[zone]
        unique[key]={'zone':zone,'sourceFaceUid':source_uids[solid],
                     'centroid_m':centre,**support(vertices,solid)}
    stages[stage.stem.split('.')[0]]=list(unique.values())

report={
    'status':'diagnostic_only',
    'input_cfdrun_sha256':hashlib.sha256(args.cfdrun.read_bytes()).hexdigest(),
    'source_surface_count':len(manifest['geometryExport']['sourceSurfaces']),
    'source_uids':{solid:source_uids[solid] for solid in source},
    'roi_m':roi,
    'rank':args.rank,
    'source_stl_sha256':{fn:hashlib.sha256((tri_surface/fn).read_bytes()).hexdigest()
                         for fn in {source[solid][0] for solid in source}},
    'method':'orthogonal projection; clip each convex sampled face polygon by original STL triangles in 2D, sum nonoverlapping intersections',
    'synthetic_clip_controls_passed':3,
    'source_triangle_pair_max_overlap_m2':source_pair_max_overlap,
    'scope_limit':'Only convex mapped con_f/Raised_Floor ROI faces; no global UID-to-zone mesh ownership, normal offset/whole-mesh guarantee, or checkMesh acceptance.',
    'final':final,
    'stages':stages,
}
args.output.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'final':[(x['cell'],x['patch'],x['outside_area_m2'],x['covered_fraction']) for x in final],
                  'stages':{stage:{'faces':len(rows),'unsupported':sum(x['outside_area_m2']>1e-10 for x in rows),
                                   'outside_area_m2':sum(x['outside_area_m2'] for x in rows)}
                            for stage,rows in stages.items()}},indent=2))
