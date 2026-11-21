import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Vec3 } from '../src/schema/cfdproj'
import { reviveKinds } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import {
  collectSeedSurfaces,
  seedEndpoints,
  type SeedConfig,
  type SeedSurface,
} from '../src/geometry/seedConfig'
import { setActiveSeedSurface } from '../src/geometry/seedActions'
import {
  createSeedDragTools,
  seedConfigForDrag,
  seedDragPlane,
  seedSurfaceKey,
} from '../src/geometry/seedDrag'

/**
 * Dragging the seed helper.
 *
 * The helper was drawn and never raycast, so the seed ANCHOR had no control
 * anywhere in the app — the panel prints it as a read-only badge, and only
 * direction and offset were writable. On a perforated tile under a rack the
 * automatic anchor (the surface centre) is inside solid geometry, and the mesh
 * then comes out missing half the domain.
 *
 * The pure half is tested directly; the drag itself runs through the real tools
 * with a stub host, because the defect was never in the arithmetic — it was
 * that nothing called it.
 */

const PROJECT = resolve(process.cwd(), '../../../case/Base_leakage/Base_leakage.cfdproj')

/**
 * The mesh sizes these numbers are worked out for: base 0.17 m, baffle level
 * [1, 1] -> a 0.085 m cell at the surface. Pinned here, not read from the
 * case file, so a sizing change to case/Base_leakage (the 6Sigma preset,
 * 7813a336) does not rewrite what the seed arithmetic is checked against.
 */
function pinMeshSizing(project: CfdProject): CfdProject {
  const rd = ((project as { runDefaults?: Record<string, unknown> }).runDefaults ??= {})
  const mc = ((rd as { meshConfig?: Record<string, unknown> }).meshConfig ??= {})
  mc.meshBaseSize = 0.17
  mc.wallBafflesRefinementLevel = [1, 1]
  return project
}

function load() {
  store.loadProject(
    pinMeshSizing(reviveKinds(JSON.parse(readFileSync(PROJECT, 'utf8')) as CfdProject)),
    '/cases/Base_leakage.cfdproj',
  )
}

/** The raised floor's perforated tile — the surface SeedInspector's tests use. */
function tile(): SeedSurface {
  const found = collectSeedSurfaces(store.getProject()).find(
    (s) => s.modelUid === 'm_51' && s.patchId === 'p_13',
  )
  if (!found) throw new Error('fixture lost its perforated tile')
  return found
}

function fresh(surface: SeedSurface): SeedSurface {
  return collectSeedSurfaces(store.getProject()).find(
    (s) => seedSurfaceKey(s) === seedSurfaceKey(surface),
  )!
}

beforeEach(() => {
  load()
  setActiveSeedSurface(null)
})

describe('seedDragPlane', () => {
  it('gives the anchor the surface’s own plane', () => {
    const surface = tile()
    const plane = seedDragPlane(surface, surface.seedConfig, 'anchor', new THREE.Vector3(0, 0, -1))
    // The tile faces +Y, so its plane is horizontal through the anchor.
    expect(plane.normal.y).toBeCloseTo(1, 6)
    expect(plane.distanceToPoint(new THREE.Vector3(9, surface.seedConfig.anchor.y, 9))).toBeCloseTo(
      0,
      6,
    )
  })

  it('gives a tip a billboard plane facing the camera', () => {
    const surface = tile()
    const camera = new THREE.Vector3(0.6, -0.8, 0).normalize()
    const plane = seedDragPlane(surface, surface.seedConfig, 'positive', camera)
    // Facing the camera, so its normal is the view direction reversed. A tip
    // moves along a LINE, and a line cannot be raycast — without the billboard
    // the pointer falls off the plane the moment the camera looks along the
    // surface.
    expect(plane.normal.dot(camera)).toBeCloseTo(-1, 6)
    const tip = seedEndpoints(surface.seedConfig).positive
    expect(plane.distanceToPoint(new THREE.Vector3(tip.x, tip.y, tip.z))).toBeCloseTo(0, 6)
  })
})

describe('seedConfigForDrag', () => {
  const state = (side: 'anchor' | 'positive' | 'negative', surface: SeedSurface) => ({
    side,
    anchor: new THREE.Vector3(
      surface.seedConfig.anchor.x,
      surface.seedConfig.anchor.y,
      surface.seedConfig.anchor.z,
    ),
    direction: new THREE.Vector3(
      surface.seedConfig.direction.x,
      surface.seedConfig.direction.y,
      surface.seedConfig.direction.z,
    ),
    offset: surface.seedConfig.offset,
  })

  it('slides the anchor to the hit, projected onto the surface', () => {
    const surface = tile()
    const y = surface.seedConfig.anchor.y
    // A hit 0.3 m off the plane: the projection has to pull it back, or both
    // seed points end up on the same side once the offset is applied.
    const cfg = seedConfigForDrag(surface, state('anchor', surface), new THREE.Vector3(3, y + 0.3, 4))
    expect(cfg.anchor.x).toBeCloseTo(3, 6)
    expect(cfg.anchor.z).toBeCloseTo(4, 6)
    expect(cfg.anchor.y).toBeCloseTo(y, 6)
    expect(cfg.offset).toBeCloseTo(surface.seedConfig.offset, 9)
  })

  it('turns a positive-tip drag into the distance along the rod', () => {
    const surface = tile()
    const a = surface.seedConfig.anchor
    const cfg = seedConfigForDrag(
      surface,
      state('positive', surface),
      new THREE.Vector3(a.x + 9, a.y + 0.5, a.z - 9),
    )
    // Only the component along the direction counts — the rod is rigid, and the
    // 9 m sideways is the pointer sliding along the billboard plane.
    expect(cfg.offset).toBeCloseTo(0.5, 6)
    expect(cfg.anchor).toEqual(surface.seedConfig.anchor)
  })

  it('measures the negative tip the other way round', () => {
    const surface = tile()
    const a = surface.seedConfig.anchor
    const cfg = seedConfigForDrag(
      surface,
      state('negative', surface),
      new THREE.Vector3(a.x, a.y - 0.4, a.z),
    )
    expect(cfg.offset).toBeCloseTo(0.4, 6)
  })

  it('never lets the pair collapse onto the surface', () => {
    const surface = tile()
    const a = surface.seedConfig.anchor
    const cfg = seedConfigForDrag(surface, state('positive', surface), new THREE.Vector3(a.x, a.y, a.z))
    // A zero offset would put both points ON the surface, leaving the mesher
    // with no point in either half.
    expect(cfg.offset).toBeCloseTo(surface.offsetMin, 9)
    expect(cfg.offset).toBeGreaterThan(0)
  })
})

// ---------------------------------------------------------------------------
// The drag itself
// ---------------------------------------------------------------------------

class StubElement {
  style: { cursor: string } = { cursor: '' }
  getBoundingClientRect() {
    return { left: 0, top: 0, width: 800, height: 600 } as DOMRect
  }
  setPointerCapture() {}
  releasePointerCapture() {}
}

/**
 * A camera looking DOWN AT AN ANGLE, close in.
 *
 * Two constraints fix this. Looking straight down puts the hub and both tips on
 * the same screen pixel — the first version of this test aimed at the hub, hit
 * the tip that was 42 mm above it, and measured a tip drag while claiming to
 * test the anchor. And looking edge-on makes the ray parallel to the surface
 * plane, which no anchor drag can follow. 45° sees all three separately.
 *
 * The frustum is 0.3 m tall because the whole helper is about 85 mm long.
 */
function angledCamera(surface: SeedSurface): THREE.OrthographicCamera {
  return closeCamera(surface, { x: 3, y: 3, z: 0 })
}

/**
 * A camera looking ACROSS the rod, for the tip drags.
 *
 * A tip follows a BILLBOARD plane, so aiming at a world point only lands on
 * that point when the plane contains it — which needs the view direction
 * perpendicular to the rod. The tile's seed direction is +Y, so this looks
 * along −Z. (`angledCamera` cannot do the tip tests for that reason, and this
 * one cannot do the hub tests: its ray is parallel to the tile's plane, which
 * is exactly the view from which no anchor drag is possible, in this app or the
 * legacy.)
 */
function edgeOnCamera(surface: SeedSurface): THREE.OrthographicCamera {
  return closeCamera(surface, { x: 0, y: 0, z: 3 })
}

function closeCamera(surface: SeedSurface, from: Vec3): THREE.OrthographicCamera {
  const camera = new THREE.OrthographicCamera(-0.2, 0.2, 0.15, -0.15, 0.01, 100)
  const c = surface.center
  camera.position.set(c.x + from.x, c.y + from.y, c.z + from.z)
  camera.lookAt(c.x, c.y, c.z)
  camera.updateMatrixWorld(true)
  camera.updateProjectionMatrix()
  return camera
}

/** Where a world point lands on the stub's 800×600 canvas. */
function screenOf(camera: THREE.Camera, point: { x: number; y: number; z: number }) {
  const ndc = new THREE.Vector3(point.x, point.y, point.z).project(camera)
  return { clientX: ((ndc.x + 1) / 2) * 800, clientY: ((1 - ndc.y) / 2) * 600 }
}

function pointer(at: { clientX: number; clientY: number }, button = 0): PointerEvent {
  return { ...at, button, pointerId: 7 } as unknown as PointerEvent
}

function move(at: { clientX: number; clientY: number }) {
  window.dispatchEvent(Object.assign(new Event('pointermove'), at, { pointerId: 7 }))
}

function release() {
  window.dispatchEvent(Object.assign(new Event('pointerup'), { pointerId: 7 }))
}

interface Rig {
  tools: ReturnType<typeof createSeedDragTools>
  camera: THREE.OrthographicCamera
  element: StubElement
  orbit: { enabled: boolean }
  repositioned: SeedConfig[]
  frames: number
}

/**
 * Stand-ins for the helper's three meshes.
 *
 * 10 mm spheres: small enough that the hub and the tips 42 mm away do not
 * overlap on screen, which is the whole point of picking the right one.
 */
function rig(surface: SeedSurface, camera = angledCamera(surface)): Rig {
  const element = new StubElement()
  const orbit = { enabled: true }
  const geometry = new THREE.SphereGeometry(0.01, 12, 12)
  const { anchor, positive, negative } = seedEndpoints(surface.seedConfig)
  const handles = (
    [
      ['anchor', anchor],
      ['positive', positive],
      ['negative', negative],
    ] as const
  ).map(([side, at]) => {
    const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial())
    mesh.position.set(at.x, at.y, at.z)
    mesh.userData = { isSeedHandle: true, seedSide: side }
    mesh.updateMatrixWorld(true)
    return mesh
  })

  const state: Rig = {
    camera,
    element,
    orbit,
    repositioned: [],
    frames: 0,
    tools: createSeedDragTools({
      camera,
      domElement: element as unknown as HTMLElement,
      orbit,
      surface: () => fresh(surface),
      handles: () => handles,
      reposition: (cfg) => state.repositioned.push(cfg),
      render: () => (state.frames += 1),
      enabled: () => true,
    }),
  }
  return state
}

function cfgOf(surface: SeedSurface) {
  return fresh(surface).seedConfig
}

describe('dragging the hub', () => {
  it('slides the anchor to where the pointer is, and leaves ONE undo entry', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const { tools, camera } = rig(surface)
    const before = store.undoDepth()
    const start = cfgOf(surface).anchor
    // Three frames, as a real drag emits, each aimed at a point ON the surface
    // plane — so the anchor must land exactly there.
    const path = [0.05, 0.09, 0.12].map((d) => ({ x: start.x + d, y: start.y, z: start.z - d }))

    expect(tools.begin(pointer(screenOf(camera, start)))).toBe(true)
    for (const at of path) move(screenOf(camera, at))
    release()

    const after = cfgOf(surface).anchor
    const last = path[path.length - 1]!
    expect(after.x).toBeCloseTo(last.x, 4)
    expect(after.z).toBeCloseTo(last.z, 4)
    // Still ON the tile: the anchor slides across the surface, never off it.
    expect(after.y).toBeCloseTo(start.y, 6)
    // Three frames, ONE undo entry. Per-frame entries would make undoing a
    // single drag three presses.
    expect(store.undoDepth() - before).toBe(1)
  })

  it('moves the drawn helper on every frame, without waiting for React', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const state = rig(surface)
    const start = cfgOf(surface).anchor

    state.tools.begin(pointer(screenOf(state.camera, start)))
    move(screenOf(state.camera, { x: start.x + 0.05, y: start.y, z: start.z }))
    move(screenOf(state.camera, { x: start.x + 0.1, y: start.y, z: start.z }))
    release()

    expect(state.repositioned.length).toBe(2)
    expect(state.frames).toBe(2)
    expect(state.repositioned[1]!.anchor.x).toBeCloseTo(start.x + 0.1, 4)
  })

  it('undo takes the whole drag back, including the override itself', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const { tools, camera } = rig(surface)
    const start = cfgOf(surface).anchor
    const wasCustom = surface.isCustom

    tools.begin(pointer(screenOf(camera, start)))
    move(screenOf(camera, { x: start.x + 0.1, y: start.y, z: start.z }))
    release()
    expect(cfgOf(surface).anchor.x).toBeCloseTo(start.x + 0.1, 4)

    store.undo()
    expect(cfgOf(surface).anchor.x).toBeCloseTo(start.x, 6)
    // And the surface is back to what it was — not left carrying an override
    // that happens to equal the default, which would light the 重置 button and
    // write a seed block into the .cfdrun the project never asked for.
    expect(fresh(surface).isCustom).toBe(wasCustom)
  })

  it('a click that never moved is not an edit', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const { tools, camera, orbit } = rig(surface)
    const before = store.undoDepth()

    expect(tools.begin(pointer(screenOf(camera, cfgOf(surface).anchor)))).toBe(true)
    // The camera must be off, or the drag orbits the scene as well.
    expect(orbit.enabled).toBe(false)
    release()

    expect(store.undoDepth()).toBe(before)
    expect(fresh(surface).isCustom).toBe(surface.isCustom)
    expect(orbit.enabled).toBe(true)
  })

  it('declines a pointer that is not over a handle', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const { tools } = rig(surface)
    expect(tools.begin(pointer({ clientX: 5, clientY: 5 }))).toBe(false)
    expect(tools.dragging()).toBe(false)
  })

  it('declines the right button, so the context menu still works', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const { tools, camera } = rig(surface)
    expect(tools.begin(pointer(screenOf(camera, cfgOf(surface).anchor), 2))).toBe(false)
  })

  it('does nothing while the tools are disabled', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const camera = angledCamera(surface)
    const hub = new THREE.Mesh(new THREE.SphereGeometry(0.01, 12, 12), new THREE.MeshBasicMaterial())
    const { anchor } = seedEndpoints(surface.seedConfig)
    hub.position.set(anchor.x, anchor.y, anchor.z)
    hub.userData = { isSeedHandle: true, seedSide: 'anchor' }
    hub.updateMatrixWorld(true)
    const tools = createSeedDragTools({
      camera,
      domElement: new StubElement() as unknown as HTMLElement,
      surface: () => fresh(surface),
      handles: () => [hub],
      reposition: () => {},
      render: () => {},
      // A running mesh job, a non-editor tab, or the helper switched off.
      enabled: () => false,
    })
    expect(tools.begin(pointer(screenOf(camera, anchor)))).toBe(false)
  })
})

describe('dragging a tip', () => {
  it('grabs the TIP, not the hub 42 mm away, and changes only the offset', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const { tools, camera } = rig(surface, edgeOnCamera(surface))
    const start = cfgOf(surface)
    const tip = seedEndpoints(start).positive

    expect(tools.begin(pointer(screenOf(camera, tip)))).toBe(true)
    // Out along the rod: the tile faces +Y, so its seed direction is +Y.
    move(screenOf(camera, { x: tip.x, y: start.anchor.y + 0.12, z: tip.z }))
    release()

    const after = cfgOf(surface)
    expect(after.offset).toBeCloseTo(0.12, 4)
    // The rod is rigid: the anchor does not move with the tip.
    expect(after.anchor.x).toBeCloseTo(start.anchor.x, 6)
    expect(after.anchor.y).toBeCloseTo(start.anchor.y, 6)
    expect(after.anchor.z).toBeCloseTo(start.anchor.z, 6)
  })

  it('the negative tip pushes the pair apart too, not through the surface', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const { tools, camera } = rig(surface, edgeOnCamera(surface))
    const start = cfgOf(surface)
    const tip = seedEndpoints(start).negative

    expect(tools.begin(pointer(screenOf(camera, tip)))).toBe(true)
    move(screenOf(camera, { x: tip.x, y: start.anchor.y - 0.09, z: tip.z }))
    release()

    expect(cfgOf(surface).offset).toBeCloseTo(0.09, 4)
  })

  it('will not collapse the pair onto the surface', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const { tools, camera } = rig(surface, edgeOnCamera(surface))
    const start = cfgOf(surface)
    const tip = seedEndpoints(start).positive

    tools.begin(pointer(screenOf(camera, tip)))
    // Dragged all the way back ONTO the anchor: the offset the pointer asks
    // for is zero, which would put both seed points on the surface and leave
    // the mesher with no point in either half.
    move(screenOf(camera, start.anchor))
    release()

    expect(cfgOf(surface).offset).toBeCloseTo(surface.offsetMin, 6)
  })

  it('reads a tip dragged PAST the anchor as distance, not as a negative', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const { tools, camera } = rig(surface, edgeOnCamera(surface))
    const start = cfgOf(surface)
    const tip = seedEndpoints(start).positive

    tools.begin(pointer(screenOf(camera, tip)))
    move(screenOf(camera, { x: tip.x, y: start.anchor.y - 0.2, z: tip.z }))
    release()

    // The rod is symmetric — pulling one tip through the surface pushes the
    // pair apart by that much, it does not invert them. `Math.abs`, as the
    // legacy has it (STL_generate.html:18131).
    expect(cfgOf(surface).offset).toBeCloseTo(0.2, 4)
  })
})

describe('a drag whose surface changes underneath it', () => {
  it('is abandoned rather than committed onto the new selection', () => {
    const surface = tile()
    setActiveSeedSurface(surface)
    const start = cfgOf(surface).anchor
    let current: SeedSurface | null = fresh(surface)
    const camera = angledCamera(surface)
    const hub = new THREE.Mesh(new THREE.SphereGeometry(0.01, 12, 12), new THREE.MeshBasicMaterial())
    hub.position.set(start.x, start.y, start.z)
    hub.userData = { isSeedHandle: true, seedSide: 'anchor' }
    hub.updateMatrixWorld(true)
    const tools = createSeedDragTools({
      camera,
      domElement: new StubElement() as unknown as HTMLElement,
      surface: () => current,
      handles: () => [hub],
      reposition: () => {},
      render: () => {},
      enabled: () => true,
    })
    const before = store.undoDepth()

    tools.begin(pointer(screenOf(camera, start)))
    // The tree selection moves to another surface mid-drag.
    current = null
    move(screenOf(camera, { x: start.x + 0.1, y: start.y, z: start.z }))
    release()

    expect(cfgOf(surface).anchor.x).toBeCloseTo(start.x, 6)
    expect(store.undoDepth()).toBe(before)
  })
})
