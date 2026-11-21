import * as THREE from 'three'
import { describe, expect, it, vi } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import {
  anchorDisplayRadius,
  anchorMarkerRadius,
  anchorMarkerTabAllows,
  anchorMarkerVisible,
  createAnchorMarkers,
  patchAnchorLocal,
  selectedPatchAnchor,
  showsAnchorMarker,
  type PatchAnchor,
} from '../src/geometry/anchorMarkers'
import { patchAnchorWorld } from '../src/geometry/patchAnchor'
import { geometryPure } from '../src/legacy/geometryBuilder'

function model(
  uid: string,
  display?: { showAnchorMarker?: boolean },
  size = { x: 0.6, y: 2, z: 1 },
): Construction {
  return {
    uid,
    meta: { name: uid, display },
    geometry: { size, location: { x: 1, y: 0, z: 2 } },
  } as unknown as Construction
}

describe('anchorMarkerRadius', () => {
  it('scales with the longest side', () => {
    expect(anchorMarkerRadius({ x: 1, y: 1, z: 1 })).toBeCloseTo(0.035, 12)
  })

  it('never shrinks below 18 mm, so a 60 mm junction still has a handle', () => {
    expect(anchorMarkerRadius({ x: 0.06, y: 0.06, z: 0.06 })).toBeCloseTo(0.018, 12)
  })

  it('never grows past 90 mm, so a 40 m wall does not get a beach ball', () => {
    expect(anchorMarkerRadius({ x: 40, y: 3, z: 0 })).toBeCloseTo(0.09, 12)
  })

  it('survives a model with no size at all', () => {
    expect(anchorMarkerRadius(undefined)).toBeCloseTo(0.018, 12)
  })
})

describe('showsAnchorMarker', () => {
  it('defaults to shown when the flag was never written', () => {
    expect(showsAnchorMarker(model('m_1'))).toBe(true)
    expect(showsAnchorMarker(model('m_1', {}))).toBe(true)
  })

  it('reads the V4 field the .cfdproj actually stores', () => {
    expect(showsAnchorMarker(model('m_1', { showAnchorMarker: false }))).toBe(false)
  })

  it('falls back to the v3 top-level flag for a model still in memory', () => {
    const legacy = { uid: 'm_2', meta: { name: 'p' }, showAnchorMarker: false } as unknown as Construction
    expect(showsAnchorMarker(legacy)).toBe(false)
  })
})

describe('anchorMarkerVisible', () => {
  const m = model('m_1')

  it('needs the model to be selected', () => {
    expect(anchorMarkerVisible(m, ['m_1'], 'geometry')).toBe(true)
    expect(anchorMarkerVisible(m, ['m_2'], 'geometry')).toBe(false)
    expect(anchorMarkerVisible(m, [], 'geometry')).toBe(false)
  })

  it('is a geometry-tab helper and nowhere else', () => {
    expect(anchorMarkerTabAllows('geometry')).toBe(true)
    for (const tab of ['mesh', 'solver', 'postprocess', 'system']) {
      expect(anchorMarkerTabAllows(tab)).toBe(false)
      expect(anchorMarkerVisible(m, ['m_1'], tab)).toBe(false)
    }
  })

  it('honours the checkbox even for the selected model', () => {
    expect(anchorMarkerVisible(model('m_1', { showAnchorMarker: false }), ['m_1'], 'geometry')).toBe(
      false,
    )
  })
})

describe('patchAnchorLocal', () => {
  // A floor's top face: origin (0, H, D), right +X, up -Z (geometry_pure.js:529).
  const top = { origin: { x: 0, y: 2.7, z: 3.23 }, right: { x: 1, y: 0, z: 0 }, up: { x: 0, y: 0, z: -1 } }

  it('puts a rectangle anchor at its bottom-left corner on the face', () => {
    const p = patchAnchorLocal({ type: 'rect', x: 2, y: 1, w: 1, h: 1 }, top)!
    expect([p.x, p.y, p.z]).toEqual([2, 2.7, 3.23 - 1])
  })

  it('puts a circle anchor at its centre', () => {
    const p = patchAnchorLocal({ type: 'circle', cx: 0.5, cy: 0.25, r: 0.1 }, top)!
    expect([p.x, p.y, p.z]).toEqual([0.5, 2.7, 3.23 - 0.25])
  })

  it('has no anchor without a layout or without numbers', () => {
    expect(patchAnchorLocal({ type: 'rect', x: 1, y: 1 }, null)).toBeNull()
    expect(patchAnchorLocal({ type: 'rect' }, top)).toBeNull()
  })
})

/**
 * The ball against the blue corner cube.
 *
 * A corner cube sits exactly on the anchor of a floor, a baffle and every flat
 * rect patch, is drawn with depthTest off at renderOrder 12, and scales with
 * the camera distance (1.8%, 1.5-300 mm). WP-S: the ball scales with the
 * camera too (2.2% of dist), capped at its model-relative base radius and
 * floored at 1.2 mm, so zooming in shrinks both instead of freezing them at
 * the old world-space floors. The former "ball >= 0.9 x cube" ring rule is
 * gone: from a room overview the cube can again cover the ball.
 */
describe('anchor size, shared with the legacy editor', () => {
  it('anchorMarkerRadius IS the shared geometry_pure rule', () => {
    const pure = geometryPure()
    const sizes = [
      { x: 1, y: 1, z: 1 },
      { x: 0.06, y: 0.06, z: 0.06 },
      { x: 40, y: 3, z: 0 },
      { x: 0.6, y: 2, z: 1 },
      { x: 0.2, y: 0.3 },
      undefined,
    ]
    for (const size of sizes) expect(anchorMarkerRadius(size)).toBe(pure.anchorMarkerRadius(size))
  })
})

describe('anchorDisplayRadius', () => {
  it('holds the ball at a constant size on screen: k x the camera distance', () => {
    const k = geometryPure().MARKER_SIZE!.anchor!
    for (const dist of [0.5, 1, 4.3, 11.65, 30]) {
      expect(anchorDisplayRadius(0.09, dist)).toBeCloseTo(k * dist, 12)
    }
  })

  it('sits ON the corner cube: smaller than its half side, so the cube shows around it', () => {
    // A room overview used to give a 0.9 x cube ring -- a beach ball over a rack.
    for (const dist of [0.3, 1, 4.3, 11.65, 16]) {
      expect(anchorDisplayRadius(0.09, dist)).toBeLessThan(0.5 * geometryPure().handleCubeSize(dist))
    }
  })

  it('never degenerates, stays under the far cap, and is monotone in distance', () => {
    let prev = 0
    for (const dist of [0.01, 0.05, 0.3, 1, 2.5, 6.7, 11.65, 40, 200]) {
      const r = anchorDisplayRadius(0.09, dist)
      expect(r).toBeGreaterThanOrEqual(0.0015)
      expect(r).toBeLessThanOrEqual(0.35)
      expect(r).toBeGreaterThanOrEqual(prev)
      prev = r
    }
  })

  it('falls back to the base radius without a finite distance', () => {
    expect(anchorDisplayRadius(0.0438, NaN)).toBe(0.0438)
  })
})

describe('createAnchorMarkers', () => {
  function setup() {
    // A parent with its own transform. The app keeps globalGroup and its parent
    // at identity, but the ball must not depend on that to sit and size right.
    const parent = new THREE.Group()
    const globalGroup = new THREE.Group()
    parent.add(globalGroup)
    const m = model('m_1', undefined, { x: 3, y: 0.05, z: 2 }) // longest 3 m -> 0.09
    const project = { constructions: { floors: { m_1: m } } } as unknown as CfdProject
    const camera = new THREE.PerspectiveCamera()
    const state: { patch: PatchAnchor | null; selected: string[]; renders: number } = {
      patch: null,
      selected: ['m_1'],
      renders: 0,
    }
    const markers = createAnchorMarkers({
      globalGroup,
      project: () => project,
      selectedUids: () => state.selected,
      activeTab: () => 'geometry',
      requestRender: () => {
        state.renders++
      },
      camera: () => camera,
      patchAnchor: () => state.patch,
    })
    const group = globalGroup.getObjectByName('anchorMarkers') as THREE.Group
    const balls = () => group.children as THREE.Mesh<THREE.SphereGeometry, THREE.MeshPhongMaterial>[]
    return { parent, globalGroup, m, camera, state, markers, balls }
  }

  it('draws over the geometry, after the edge cubes (11) and the corner cubes (12)', () => {
    const { camera, markers, balls } = setup()
    camera.position.set(1, 0, 5)
    markers.refresh()
    const [ball] = balls()
    expect(ball).toBeDefined()
    // renderOrder only sorts inside one render list: an opaque ball would be
    // drawn in the opaque pass, before and under every transparent handle.
    expect(ball!.material.transparent).toBe(true)
    expect(ball!.material.opacity).toBe(1)
    expect(ball!.material.depthTest).toBe(false)
    expect(ball!.material.depthWrite).toBe(false)
    expect(ball!.renderOrder).toBe(geometryPure().ANCHOR_RENDER_ORDER)
    expect(ball!.renderOrder).toBeGreaterThan(12)
    // The shared point-marker look, not a plain Phong ball.
    expect(ball!.material.userData.isPointMarker).toBe(true)
  })

  it('sizes the ball from the camera distance, as the handle cubes are sized', () => {
    const { camera, markers, balls } = setup()
    // location (1, 0, 2); camera 11.65 m out along +Z
    camera.position.set(1, 0, 2 + 11.65)
    markers.refresh()
    const far = balls()[0]!
    expect(far.geometry.parameters.radius).toBe(1)
    expect(far.scale.x).toBeCloseTo(anchorDisplayRadius(0.09, 11.65), 9)
    expect(far.scale.y).toBeCloseTo(far.scale.x, 12)

    camera.position.set(1, 0, 3)
    markers.refresh()
    const near = balls()[0]!
    expect(near).toBe(far)
    // 1 m out the ball keeps shrinking with the camera: same size on screen,
    // under the 0.09 base it would have been as a world-fixed ball.
    expect(near.scale.x).toBeCloseTo(anchorDisplayRadius(0.09, 1), 12)
    expect(near.scale.x).toBeLessThan(0.09)
  })

  it('re-sizes the ball on a zoom, without a refresh', () => {
    const { camera, markers, balls, state } = setup()
    camera.position.set(1, 0, 2 + 11.65)
    markers.refresh()
    const ball = balls()[0]!
    const far = ball.scale.x
    const renders = state.renders
    // Rolling in does not re-run refresh, so without updateScale the ball would
    // stay 19 cm across and cover the model it is supposed to point at.
    camera.position.set(1, 0, 2 + 1.2)
    markers.updateScale()
    expect(ball).toBe(balls()[0])
    expect(ball.scale.x).toBeCloseTo(anchorDisplayRadius(0.09, 1.2), 9)
    expect(ball.scale.x).toBeLessThan(far)
    // It runs inside the render loop, so it must NOT ask for another frame:
    // that would draw every zoom frame twice.
    expect(state.renders).toBe(renders)
  })

  it('re-sizes the selected patch ball on the same zoom', () => {
    const { camera, state, markers, balls } = setup()
    state.patch = { key: 'm_1//p_106', position: { x: 1.2, y: 0.5, z: 2 }, radius: 0.0438 }
    camera.position.set(1.2, 0.5, 2 + 4.3)
    markers.refresh()
    const patchBall = balls().find((b) => b.userData.isPatchAnchor)!
    const modelBall = balls().find((b) => !b.userData.isPatchAnchor)!
    expect(patchBall.scale.x).toBeCloseTo(anchorDisplayRadius(0.0438, 4.3), 9)
    camera.position.set(1.2, 0.5, 2 + 0.8)
    markers.updateScale()
    expect(patchBall.scale.x).toBeCloseTo(anchorDisplayRadius(0.0438, 0.8), 9)
    // the model's own ball sits 1.2 m off that point, so it re-measures too
    expect(modelBall.scale.x).toBeCloseTo(
      anchorDisplayRadius(0.09, Math.hypot(0.2, 0.5, 0.8 - 2 + 2)),
      6,
    )
  })

  it('leaves everything alone while the camera stays put', () => {
    const { camera, markers, balls, state } = setup()
    camera.position.set(1, 0, 2 + 11.65)
    markers.refresh()
    const ball = balls()[0]!
    const renders = state.renders
    markers.updateScale()
    markers.updateScale()
    expect(ball.scale.x).toBeCloseTo(anchorDisplayRadius(0.09, 11.65), 9)
    expect(state.renders).toBe(renders)
  })

  it('costs nothing when no marker is drawn', () => {
    const { camera, state, markers } = setup()
    state.selected = []
    camera.position.set(1, 0, 5)
    markers.refresh()
    const renders = state.renders
    expect(() => markers.updateScale()).not.toThrow()
    expect(state.renders).toBe(renders)
  })

  it('follows a resized model without rebuilding its mesh', () => {
    const { m, camera, markers, balls } = setup()
    camera.position.set(1, 0, 2.5)
    markers.refresh()
    const before = balls()[0]!
    ;(m.geometry as { size: unknown }).size = { x: 0.5, y: 0.5, z: 0.5 }
    markers.refresh()
    const after = balls()[0]!
    expect(after).toBe(before)
    // camera 0.5 m out: the ball scales from the distance, not the model.
    expect(after.scale.x).toBeCloseTo(anchorDisplayRadius(0.018, 0.5), 12)
  })

  it('places and sizes the patch ball the same way, on the same sphere and material', () => {
    const { camera, state, markers, balls } = setup()
    state.patch = { key: 'm_1//p_106', position: { x: 1.2, y: 0.5, z: 2 }, radius: 0.0438 }
    camera.position.set(1.2, 0.5, 2 + 4.3)
    markers.refresh()
    expect(markers.shown().sort()).toEqual(['m_1', 'patch:m_1//p_106'])
    const patchBall = balls().find((b) => b.userData.isPatchAnchor)!
    const modelBall = balls().find((b) => !b.userData.isPatchAnchor)!
    expect(patchBall.position.toArray()).toEqual([1.2, 0.5, 2])
    expect(patchBall.scale.x).toBeCloseTo(anchorDisplayRadius(0.0438, 4.3), 9)
    // The patch's own radius does not matter: every anchor ball is the same
    // size on screen, and smaller than the corner cube it sits on.
    expect(patchBall.scale.x).toBeLessThan(0.5 * geometryPure().handleCubeSize(4.3))
    expect(patchBall.renderOrder).toBe(modelBall.renderOrder)
    expect(patchBall.geometry).toBe(modelBall.geometry)
    expect(patchBall.material).toBe(modelBall.material)
    state.patch = null
    markers.refresh()
    expect(markers.shown()).toEqual(['m_1'])
  })

  it('measures the distance in world space and cancels a scaled parent', () => {
    const { parent, camera, markers, balls } = setup()
    parent.position.set(0, 0, 5)
    parent.scale.setScalar(2)
    parent.updateMatrixWorld(true)
    // location (1, 0, 2) lands at world (2, 0, 9)
    camera.position.set(2, 0, 9 + 11.65)
    markers.refresh()
    const ball = balls()[0]!
    const world = new THREE.Vector3()
    ball.getWorldScale(world)
    expect(world.x).toBeCloseTo(anchorDisplayRadius(0.09, 11.65), 9)
  })

  it('shares one sphere and disposes it once', () => {
    const { camera, state, markers, balls } = setup()
    state.patch = { key: 'm_1//p_1', position: { x: 0, y: 0, z: 0 }, radius: 0.05 }
    camera.position.set(0, 0, 3)
    markers.refresh()
    const geometry = balls()[0]!.geometry
    const spy = vi.spyOn(geometry, 'dispose')
    state.patch = null
    markers.refresh() // dropping the patch ball must not free the shared sphere
    expect(spy).not.toHaveBeenCalled()
    markers.dispose()
    expect(spy).toHaveBeenCalledTimes(1)
  })
})

describe('selectedPatchAnchor', () => {
  const acu = {
    uid: 'm_59',
    kind: 'acu',
    meta: { name: 'ACU_return' },
    geometry: {
      location: { x: 1, y: 0.5, z: 0.2 },
      size: { x: 0.6, y: 1.8, z: 0.9 },
      faces: { left: { id: 'left', patches: [{ id: 'p_106', type: 'rect', x: 0.1, y: 0.2, w: 0.3, h: 0.4 }] } },
    },
  } as unknown as Construction

  it('takes the point the handles are drawn from, keyed by model, server and patch', () => {
    const at = { x: 1.259, y: 0.948, z: 0.593 }
    const a = selectedPatchAnchor({ type: 'patch', uid: 'm_59', faceId: 'left', patchId: 'p_106' }, acu, () => at)
    expect(a).toEqual({ key: 'm_59//p_106', position: at, radius: anchorMarkerRadius(acu.geometry?.size) })
  })

  it('gives a SERVER patch its ball too — the rack keys it by the server', () => {
    const rack = { ...acu, uid: 'm_22', kind: 'rack' } as unknown as Construction
    const at = { x: 0.3, y: 1.3, z: -0.4 }
    const a = selectedPatchAnchor(
      { type: 'serverPatch', uid: 'm_22', equipmentId: 'e_8', faceId: 'inlet', patchId: 'p_9' },
      rack,
      () => at,
    )
    expect(a?.key).toBe('m_22/e_8/p_9')
    expect(a?.position).toEqual(at)
  })

  it('has no layout fallback for a server patch: the rack face does not hold it', () => {
    const rack = { ...acu, uid: 'm_22', kind: 'rack' } as unknown as Construction
    expect(
      selectedPatchAnchor({ type: 'serverPatch', uid: 'm_22', equipmentId: 'e_8', patchId: 'p_9' }, rack, () => null),
    ).toBeNull()
  })

  it('falls back to the face layout for a model patch the handles do not cover', () => {
    const a = selectedPatchAnchor({ type: 'patch', uid: 'm_59', patchId: 'p_106' }, acu, () => null)
    const want = patchAnchorWorld(acu, 'left', { type: 'rect', x: 0.1, y: 0.2, w: 0.3, h: 0.4 })!
    expect(a?.position.x).toBeCloseTo(want.x, 12)
    expect(a?.position.y).toBeCloseTo(want.y, 12)
    expect(a?.position.z).toBeCloseTo(want.z, 12)
  })

  it('is nothing for a selection that is not a patch, or not this model’s', () => {
    const at = () => ({ x: 0, y: 0, z: 0 })
    expect(selectedPatchAnchor({ type: 'face', uid: 'm_59', faceId: 'left' }, acu, at)).toBeNull()
    expect(selectedPatchAnchor({ type: 'patch', uid: 'm_60', patchId: 'p_106' }, acu, at)).toBeNull()
    expect(selectedPatchAnchor(null, acu, at)).toBeNull()
    expect(selectedPatchAnchor({ type: 'patch', uid: 'm_59', patchId: 'p_106' }, null, at)).toBeNull()
  })
})
