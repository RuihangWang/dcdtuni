import * as THREE from 'three'
import { afterEach, describe, expect, it } from 'vitest'
import type { Construction } from '../src/schema/cfdproj'
import { buildLiquidEquipmentVisual } from '../src/geometry/liquidEquipmentVisuals'
import { geometryPure, pbrMaterials } from '../src/legacy/geometryBuilder'
import { canSelectGeometryFromViewport, isSelectableHit, pickNearestHit } from '../src/interaction/picking'
import { isAdditiveSelectGesture } from '../src/interaction/rackFaceGesture'
import { paintSelectionHighlights, TINT_HIGHLIGHT } from '../src/interaction/selectionHighlight'
import { NO_VIEW_FADES } from '../src/geometry/viewFades'

function cdu(w = .6, h = 2.047, d = 1.2): Construction {
  return {
    uid: 'cdu-test', kind: 'cdu', meta: { name: 'CDU_1' },
    geometry: {
      location: { x: 0, y: 0, z: 0 }, size: { x: w, y: h, z: d },
      rotation: { x: 0, y: 0, z: 0 }, scale: { x: 1, y: 1, z: 1 },
    },
  } as Construction
}
function visual(model = cdu()) {
  const group = buildLiquidEquipmentVisual(model)!
  group.updateMatrixWorld(true)
  return group
}
function mesh(group: THREE.Group, name: string) {
  const m = group.getObjectByName(name) as THREE.Mesh
  expect(m?.isMesh, name).toBe(true)
  return m
}
function dispose(group: THREE.Group) {
  group.traverse((o) => {
    const m = o as THREE.Mesh
    if (!m.isMesh) return
    m.geometry.dispose()
    for (const material of Array.isArray(m.material) ? m.material : [m.material]) material.dispose()
  })
}
afterEach(() => pbrMaterials()._resetForTests())

describe('CDU rendered selection owner', () => {
  it('a real front ray hits the visible CDU and Shift adds its model', () => {
    const group = visual()
    try {
      const ray = new THREE.Raycaster(new THREE.Vector3(.3, 1, -.5), new THREE.Vector3(0, 0, 1))
      const hit = pickNearestHit(ray.intersectObject(group, true).filter(isSelectableHit))
      expect(hit?.object.userData.modelId).toBe('cdu-test')
      expect(isAdditiveSelectGesture({ shiftKey: true, ctrlKey: false, metaKey: false }, 'cdu')).toBe(true)
      expect(canSelectGeometryFromViewport('geometry', false)).toBe(true)
      expect(canSelectGeometryFromViewport('solver', false)).toBe(false)
      expect(canSelectGeometryFromViewport('geometry', true)).toBe(false)
    } finally { dispose(group) }
  })

  it('the visible unlit HMI picks the CDU and survives whole-model paint and restoration', () => {
    const group = visual()
    try {
      const screen = mesh(group, 'generic-cdu-hmi-screen')
      const target = new THREE.Box3().setFromObject(screen).getCenter(new THREE.Vector3())
      const ray = new THREE.Raycaster(new THREE.Vector3(target.x, target.y, -.5), new THREE.Vector3(0, 0, 1))
      const hit = pickNearestHit(ray.intersectObject(group, true).filter(isSelectableHit))
      expect(hit?.object).toBe(screen)
      expect(hit?.object.userData.modelId).toBe('cdu-test')
      const original = screen.material as THREE.MeshBasicMaterial
      expect(original).toBeInstanceOf(THREE.MeshBasicMaterial)
      paintSelectionHighlights([['cdu-test', group]], {
        uid: 'cdu-test', node: null, uids: new Set(['cdu-test']),
      }, () => () => null, NO_VIEW_FADES, () => 'cdu')
      expect(screen.material).not.toBe(original)
      expect((screen.material as THREE.MeshBasicMaterial).color.getHex()).toBe(0x66ead0)
      paintSelectionHighlights([['cdu-test', group]], {
        uid: null, node: null, uids: new Set(),
      }, () => () => null, NO_VIEW_FADES, () => 'cdu')
      expect(screen.material).toBe(original)
      expect(original.color.getHex()).toBe(0x123138)
      expect(original.toneMapped).toBe(false)
    } finally { dispose(group) }
  })

  it('ownership survives the equipment world transform', () => {
    const group = visual()
    try {
      group.position.set(6.2907375, .8, 4.0198725)
      group.rotation.y = Math.PI / 3
      group.updateMatrixWorld(true)
      const outward = new THREE.Vector3(0, 0, -1).transformDirection(group.matrixWorld)
      const target = new THREE.Vector3(.3, 1, -.01).applyMatrix4(group.matrixWorld)
      const ray = new THREE.Raycaster(target.clone().addScaledVector(outward, .5), outward.negate())
      const hit = pickNearestHit(ray.intersectObject(group, true).filter(isSelectableHit))
      expect(hit?.object.userData.modelId).toBe('cdu-test')
    } finally { dispose(group) }
  })

  it('the visible cabinet and service parts highlight and restore together', () => {
    const group = visual()
    try {
      const original = mesh(group, 'generic-cdu-cabinet').material as THREE.MeshStandardMaterial
      const originalIntensity = original.emissiveIntensity
      paintSelectionHighlights([['cdu-test', group]], {
        uid: 'cdu-test', node: null, uids: new Set(['cdu-test']),
      }, () => () => null, NO_VIEW_FADES)
      const cabinet = mesh(group, 'generic-cdu-cabinet')
      const door = mesh(group, 'generic-cdu-service-door')
      for (const m of [cabinet, door]) {
        expect((m.material as THREE.MeshStandardMaterial).emissive.getHex()).toBe(TINT_HIGHLIGHT.emissive)
        expect((m.material as THREE.MeshStandardMaterial).emissiveIntensity).toBe(TINT_HIGHLIGHT.active)
      }
      paintSelectionHighlights([['cdu-test', group]], {
        uid: null, node: null, uids: new Set(),
      }, () => () => null, NO_VIEW_FADES)
      expect(cabinet.material).toBe(original)
      expect((cabinet.material as THREE.MeshStandardMaterial).emissiveIntensity).toBe(originalIntensity)
    } finally { dispose(group) }
  })

  it('detail ownership cannot turn a helper into a model pick or pick a hidden ancestor', () => {
    const group = visual()
    try {
      const cabinet = mesh(group, 'generic-cdu-cabinet')
      expect(isSelectableHit({ object: cabinet })).toBe(true)
      cabinet.userData.selectable = false
      expect(isSelectableHit({ object: cabinet })).toBe(false)
      cabinet.userData.selectable = true
      group.visible = false
      expect(isSelectableHit({ object: cabinet })).toBe(false)
      group.visible = true
      const handle = new THREE.Mesh(new THREE.SphereGeometry(.01), new THREE.MeshBasicMaterial())
      handle.userData = { modelId: 'cdu-test', isSeedHandle: true }
      group.add(handle)
      expect(isSelectableHit({ object: handle })).toBe(true)
    } finally { dispose(group) }
  })
})

describe('CDU display geometry authority', () => {
  it.each([[.6, 2.047, 1.2], [.08, .12, .08], [1.4, 2.4, .8]])(
    'cabinet/feet stay inside the vertical envelope at %s x %s x %s',
    (w, h, d) => {
      const model = cdu(w, h, d)
      const before = JSON.stringify(model)
      const group = visual(model)
      try {
        const cabinet = new THREE.Box3().setFromObject(mesh(group, 'generic-cdu-cabinet'))
        expect(cabinet.min.y).toBeGreaterThanOrEqual(-1e-7)
        expect(cabinet.max.y).toBeLessThanOrEqual(h + 1e-7)
        expect(cabinet.max.z).toBeLessThanOrEqual(d + 1e-7)
        group.traverse((o) => {
          const m = o as THREE.Mesh
          if (!m.isMesh) return
          expect(m.userData).toMatchObject({
            modelId: model.uid, liquidVisualDetail: true, exportExcluded: true, selectable: true,
          })
          expect(m.userData.exportName).toBeUndefined()
          expect(m.userData.isHelper).toBeUndefined()
          for (const attr of Object.values(m.geometry.attributes)) {
            expect(Array.from(attr.array).every(Number.isFinite), m.name).toBe(true)
          }
          const bounds = new THREE.Box3().setFromObject(m)
          expect(bounds.min.y, m.name).toBeGreaterThanOrEqual(-1e-7)
          expect(bounds.max.y, m.name).toBeLessThanOrEqual(h + 1e-7)
          // Only front service fittings may project outward; no hidden rear pipe.
          expect(bounds.max.z, m.name).toBeLessThanOrEqual(d + 1e-7)
        })
        expect(JSON.stringify(model)).toBe(before)
      } finally { dispose(group) }
    },
  )

  it('port collars terminate inward at the exact unchanged logical frame', () => {
    const model = cdu()
    const group = visual(model)
    try {
      for (const port of ['supply', 'return'] as const) {
        const frame = geometryPure().liquidEquipmentPortFrame(model, port, 'top')!
        const marker = mesh(group, 'generic-cdu-' + port + '-marker')
        const bounds = new THREE.Box3().setFromObject(marker)
        expect(bounds.max.z).toBeCloseTo(frame.localPosition.z, 7)
        expect(marker.position.x).toBeCloseTo(frame.localPosition.x, 12)
        expect(marker.position.y).toBeCloseTo(frame.localPosition.y, 12)
        expect(bounds.min.z).toBeLessThan(frame.localPosition.z)
      }
    } finally { dispose(group) }
  })

  it('rack manifold decoration remains click-through for its existing detailed selection contract', () => {
    const model = {
      ...cdu(), uid: 'rack-test', kind: 'rack',
      cooling: { type: 'liquid' },
    } as Construction
    const group = visual(model)
    try {
      group.traverse((o) => {
        const m = o as THREE.Mesh
        if (!m.isMesh) return
        expect(m.userData.selectable).toBe(false)
        expect(isSelectableHit({ object: m })).toBe(false)
      })
    } finally { dispose(group) }
  })
})
