import * as THREE from 'three'
import { beforeAll, describe, expect, it } from 'vitest'

// The shared snap core (js/transform_tools.js) after the 2026-09-22 rewrite:
// snapping works on the NOMINAL box (location + size + rotation, the box the
// STL export writes), never on rendered bounds; a snapped face carries the
// SAME double as the face it met; the snap radius is a fixed number of screen
// pixels; a held target survives until the pointer is 2.5 radii away; and the
// gap audit finds and closes the seams a drag left behind.

interface Model {
  uid: string
  kind: string
  meta: { name: string; enabled?: boolean }
  geometry: {
    location: { x: number; y: number; z: number }
    size: { x: number; y: number; z: number }
    quaternion?: { x: number; y: number; z: number; w: number }
  }
}

type Tools = ReturnType<typeof import('../src/legacy/transformTools').createTransformTools>

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let mod: any

beforeAll(async () => {
  ;(window as unknown as { THREE: typeof THREE }).THREE = THREE
  await import('../../shared/js/transform_tools.js')
  mod = (window as unknown as { transformTools: unknown }).transformTools
})

function box(uid: string, x: number, z: number, w = 0.6, d = 1.2, h = 2.0, y = 0): Model {
  return { uid, kind: 'rack', meta: { name: uid }, geometry: { location: { x, y, z }, size: { x: w, y: h, z: d } } }
}

/** Tools over a plain model list, with a rendered root that carries a 1 mm door plane. */
function toolsFor(models: Model[], metresPerPixel: number | null = null): Tools {
  const roots = new Map<string, THREE.Object3D>()
  for (const m of models) {
    const g = new THREE.Group()
    g.userData = { modelId: m.uid }
    const body = new THREE.Mesh(new THREE.BoxGeometry(m.geometry.size.x, m.geometry.size.y, m.geometry.size.z))
    body.position.set(m.geometry.size.x / 2, m.geometry.size.y / 2, m.geometry.size.z / 2)
    g.add(body)
    // the rack door: a plane 1 mm in front of the inlet face, as geometry_scene.js builds it
    const door = new THREE.Mesh(new THREE.PlaneGeometry(m.geometry.size.x, m.geometry.size.y))
    door.position.set(m.geometry.size.x / 2, m.geometry.size.y / 2, -0.001)
    door.userData = { modelId: m.uid, isDoor: true }
    g.add(door)
    g.position.set(m.geometry.location.x, m.geometry.location.y, m.geometry.location.z)
    g.updateMatrixWorld(true)
    roots.set(m.uid, g)
  }
  return mod.createTransformTools({
    state: () => ({ geometry: { bounds: { xMin: { enabled: false }, xMax: { enabled: false }, yMin: { enabled: false }, yMax: { enabled: false }, zMin: { enabled: false }, zMax: { enabled: false } } } }),
    uiState: () => ({ view: { gridSpacing: 0.1 } }),
    allModels: () => models,
    renderRoots: () => [...roots.values()],
    getRenderRootByModelId: (id: string) => roots.get(id) ?? null,
    isModelEnabled: () => true,
    getModelById: (id: string) => models.find((m) => m.uid === id) ?? null,
    snapPixelWorld: metresPerPixel == null ? undefined : () => metresPerPixel,
  })
}

describe('nominal-box snapping', () => {
  it('ignores the 1 mm door plane: rendered bounds equal the nominal box', () => {
    const a = box('a', 1, 2)
    const roots: THREE.Object3D[] = []
    const tools = toolsFor([a])
    // the built root, fetched through the same context the tools use
    const ctxRoot = (mod as { createTransformTools: unknown }) && null
    void ctxRoot
    void roots
    const nominal = tools.getModelAABB(a as never)!
    expect(nominal.min.z).toBe(2)
    expect(nominal.max.z).toBe(3.2)
    // rendered bounds skip the door plane at z = -0.001
    const g = new THREE.Group()
    g.userData = { modelId: 'x' }
    const door = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 2))
    door.position.set(0.3, 1, -0.001)
    door.userData = { modelId: 'x', isDoor: true }
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.6, 2, 1.2))
    body.position.set(0.3, 1, 0.6)
    g.add(door)
    g.add(body)
    g.updateMatrixWorld(true)
    const rb = tools.getRenderRootWorldBounds(g)!
    // Float32 vertex buffers: 1e-7 is the rendered precision, far under the 1 mm door
    expect(rb.min.z).toBeCloseTo(0, 6)
    expect(rb.max.z).toBeCloseTo(1.2, 6)
  })

  it('face snap lands the moving box EXACTLY on the neighbour face (same double)', () => {
    const a = box('a', 0, 0)
    const b = box('b', 0.6017, 0) // dragged to 1.7 mm past a's right face
    const tools = toolsFor([a, b], 0.002) // 2 mm per pixel -> 20 mm radius
    tools.initSnapCache()
    const d = tools.computeProximitySnap(['b'], true, true)
    b.geometry.location.x = mod.quantize(b.geometry.location.x + d.x)
    expect(b.geometry.location.x).toBe(0.6)
    expect(b.geometry.location.x).toBe(a.geometry.location.x + a.geometry.size.x)
  })

  it('front-to-back (Z) snap closes the door-plane gap the old render-bounds snap left', () => {
    const a = box('a', 0, 0)
    const b = box('b', 0, 1.2025) // 2.5 mm behind a
    const tools = toolsFor([a, b], 0.002)
    tools.initSnapCache()
    const d = tools.computeProximitySnap(['b'], true, false)
    expect(mod.quantize(b.geometry.location.z + d.z)).toBe(1.2)
  })

  it('the snap radius is 10 screen pixels, clamped to [5 mm, 0.5 m]', () => {
    const a = box('a', 0, 0)
    const b = box('b', 0.63, 0) // 30 mm off
    const near = toolsFor([a, b], 0.002) // radius 20 mm: no snap
    near.initSnapCache()
    expect(near.computeProximitySnap(['b'], true, true).x).toBe(0)
    const far = toolsFor([a, b], 0.004) // radius 40 mm: snaps
    far.initSnapCache()
    expect(mod.quantize(b.geometry.location.x + far.computeProximitySnap(['b'], true, true).x)).toBe(0.6)
    expect(near.snapThreshold()).toBeCloseTo(0.02, 9)
    expect(toolsFor([a, b], 0.0001).snapThreshold()).toBeCloseTo(0.005, 9)
    expect(toolsFor([a, b], 1).snapThreshold()).toBeCloseTo(0.5, 9)
  })

  it('holds a snapped target until the raw box is 2.5 radii away', () => {
    const a = box('a', 0, 0)
    const b = box('b', 0.61, 0)
    const tools = toolsFor([a, b], 0.002) // radius 20 mm
    tools.initSnapCache()
    expect(tools.computeProximitySnap(['b'], true, true).x).toBeCloseTo(-0.01, 9) // engaged
    b.geometry.location.x = 0.64 // 40 mm off: inside the 50 mm release band -> still held
    expect(tools.computeProximitySnap(['b'], true, true).x).toBeCloseTo(-0.04, 9)
    expect(tools.snapLocks().x).toEqual({ target: 0.6, side: 'min' })
    b.geometry.location.x = 0.66 // 60 mm: released
    expect(tools.computeProximitySnap(['b'], true, true).x).toBe(0)
    expect(tools.snapLocks().x).toBeNull()
  })

  it('a rotated neighbour offers its true corners, not the anchor', () => {
    const a = box('a', 0, 0)
    const s = Math.SQRT1_2
    a.geometry.quaternion = { x: 0, y: s, z: 0, w: s } // +90° about Y: +X -> -Z, +Z -> +X
    const aabb = mod.getModelAABB(a)
    expect(aabb.min.x).toBeCloseTo(0, 9)
    expect(aabb.max.x).toBeCloseTo(1.2, 9) // the 1.2 m depth now runs along +X
    expect(aabb.min.z).toBeCloseTo(-0.6, 9) // the 0.6 m width now runs along -Z
    expect(aabb.max.z).toBeCloseTo(0, 9)
  })
})

describe('gap audit', () => {
  it('finds sub-5 mm seams on the one axis the boxes face each other across', () => {
    const a = box('a', 0, 0)
    const b = box('b', 0.6012, 0) // 1.2 mm
    const c = box('c', 0, 1.2004) // 0.4 mm behind a
    const d = box('d', 3, 3) // far away
    const gaps = mod.findModelGaps([a, b, c, d], 0.005)
    expect(gaps.map((g: { a: string; b: string; axis: string }) => `${g.a}-${g.b}:${g.axis}`)).toEqual(['a-c:z', 'a-b:x'])
    expect(gaps[0].gap).toBeCloseTo(0.0004, 9)
  })

  it('closes them by moving the smaller model onto the larger one, quantised', () => {
    const a = box('a', 0, 0, 0.6, 1.2, 2.2)
    const b = box('b', 0.6012, 0) // smaller (2.0 m tall) -> moves
    const moves = mod.closeModelGaps([a, b], 0.005)
    expect(moves).toHaveLength(1)
    expect(moves[0].uid).toBe('b')
    expect(b.geometry.location.x).toBe(0.6)
    expect(mod.findModelGaps([a, b], 0.005)).toHaveLength(0)
  })

  it('quantize rounds to the micrometre so met faces print the same STL string', () => {
    expect(mod.quantize(3.5999999999999996)).toBe(3.6)
    expect(mod.quantize(0.1 + 0.2)).toBe(0.3)
  })
})
