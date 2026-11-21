import * as THREE from 'three'
import { beforeAll, describe, expect, it } from 'vitest'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'

// The shared module draws the wall-ACU supply streams in both editors. It reads
// THREE and window.geometryPure at call time, so both are installed first.
type SupplyFlowAnim = {
  rebuild(models: unknown[], parent: unknown): void
  tick(now: number): boolean
  dispose(): void
  setVisible(visible: boolean): void
  isVisible(): boolean
  count(): number
}
let anim: SupplyFlowAnim

const wallAcu = {
  uid: 'm_wall',
  meta: { enabled: true },
  geometry: { faces: { front: { patches: [{ id: 'p_1', w: 0.6, physics: { role: 'supply' } }] } } },
}

beforeAll(async () => {
  const g = globalThis as unknown as Record<string, unknown>
  g.THREE = THREE
  const w = window as unknown as Record<string, unknown>
  w.__supplyFlowSelfDrive = false
  w.geometryPure = {
    isWallMountedAcu: (m: { uid: string }) => m.uid === 'm_wall',
    wallAcuPatchCentreWorld: () => new THREE.Vector3(1, 2, 3),
    wallAcuSupplyDirectionWorld: () => new THREE.Vector3(0, -1, 1),
  }
  await import('../../shared/js/supply_flow_anim.js')
  anim = w.supplyFlowAnim as SupplyFlowAnim
})

function streamGroups(parent: THREE.Group): THREE.Object3D[] {
  return parent.children.filter((c) => c.name.startsWith('supplyFlow:'))
}

describe('supply stream visibility', () => {
  it('builds visible streams by default', () => {
    const parent = new THREE.Group()
    anim.setVisible(true)
    anim.rebuild([wallAcu], parent)
    expect(anim.count()).toBe(1)
    expect(streamGroups(parent).map((g) => g.visible)).toEqual([true])
    expect(anim.tick(1000)).toBe(true)
  })

  it('hides the existing streams and stops animating them', () => {
    const parent = new THREE.Group()
    anim.setVisible(true)
    anim.rebuild([wallAcu], parent)
    anim.setVisible(false)
    expect(streamGroups(parent).map((g) => g.visible)).toEqual([false])
    expect(anim.tick(1000)).toBe(false)
  })

  it('keeps a rebuild made while hidden hidden', () => {
    const parent = new THREE.Group()
    anim.setVisible(false)
    anim.rebuild([wallAcu], parent)
    expect(streamGroups(parent).map((g) => g.visible)).toEqual([false])
    anim.setVisible(true)
    expect(streamGroups(parent).map((g) => g.visible)).toEqual([true])
  })

  it('dispose removes the streams from the scene (rack detail)', () => {
    const parent = new THREE.Group()
    anim.setVisible(true)
    anim.rebuild([wallAcu], parent)
    anim.dispose()
    expect(anim.count()).toBe(0)
    expect(streamGroups(parent)).toEqual([])
  })
})


describe('supply streams in the Omniverse snapshot', () => {
  it('are display helpers: a project with a wall ACU can enter Omniverse', () => {
    // DAML-Testbed: the chevron cones carried no project identity and aborted
    // the OV snapshot ("Renderable has no project identity", ConeGeometry).
    const root = new THREE.Group()
    const rack = new THREE.Group()
    rack.userData.modelId = 'rack-uid'
    rack.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial()))
    root.add(rack)
    anim.setVisible(true)
    anim.rebuild([wallAcu], root)
    try {
      expect(streamGroups(root)).toHaveLength(1)
      const packet = buildSceneSnapshot({ root, project: { uid: 'project-uid', name: 'wall-acu' } })
      expect(new Set(packet.primitives.map(p => p.modelUid))).toEqual(new Set(['rack-uid']))
    } finally { anim.dispose() }
  })
})
