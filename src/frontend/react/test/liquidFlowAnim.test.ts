import * as THREE from 'three'
import { beforeAll, describe, expect, it } from 'vitest'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'

// The shared module draws the liquid flow arrows in both editors. It reads
// THREE at call time, so the global is installed first.
type Spec = {
  tracks: { points: number[][] }[]
  arrows: { track: number; s: number; sMin: number; sMax: number; dir: 1 | -1; radius: number; length: number; color: number; speed: number; lift?: number }[]
  density?: number
  animate?: boolean
  speedScale?: number
}
type LiquidFlowAnim = {
  rebuild(spec: Spec, parent: THREE.Object3D): void
  tick(now: number, camera?: { x: number; y: number; z: number }): boolean
  dispose(): void
  setVisible(visible: boolean): void
  setOptions(o: { animate?: boolean; speedScale?: number }): void
  isVisible(): boolean
  count(): number
  instance(i: number): { position: number[]; direction: number[]; scale: number[]; color: number }
}
let anim: LiquidFlowAnim

beforeAll(async () => {
  ;(globalThis as unknown as Record<string, unknown>).THREE = THREE
  ;(window as unknown as Record<string, unknown>).__liquidFlowSelfDrive = false
  await import('../../shared/js/liquid_flow_anim.js')
  anim = (window as unknown as { liquidFlowAnim: LiquidFlowAnim }).liquidFlowAnim
})

// Instance matrices are Float32: positions compare to 1e-6 m.
// An L-shaped track: 1 m along +x, then 1 m along +z.
const L = { points: [[0, 0, 0], [1, 0, 0], [1, 0, 1]] }
const arrow = (over: Partial<Spec['arrows'][number]> = {}): Spec['arrows'][number] =>
  ({ track: 0, s: 0.5, sMin: 0.25, sMax: 0.75, dir: 1, radius: 0.02, length: 0.06, color: 0xff8800, speed: 0.1, ...over })
const close = (a: number[], b: number[]) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i]!, 6))

describe('liquid flow arrows', () => {
  it('builds one tagged instanced mesh, one instance per arrow and density step', () => {
    const parent = new THREE.Group()
    anim.setVisible(true)
    anim.rebuild({ tracks: [L], arrows: [arrow(), arrow({ s: 1.5, sMin: 1.25, sMax: 1.75 })], density: 3 }, parent)
    expect(anim.count()).toBe(6)
    const meshes = parent.children.flatMap(c => c.children).filter(c => (c as THREE.InstancedMesh).isInstancedMesh)
    expect(meshes).toHaveLength(1)
    expect(meshes[0]!.userData.isLiquidFlow).toBe(true)
    expect(meshes[0]!.parent!.userData.isLiquidFlow).toBe(true)
    // Display helpers never block a pick.
    const hits: THREE.Intersection[] = []
    meshes[0]!.raycast(new THREE.Raycaster(new THREE.Vector3(0.5, 1, 0), new THREE.Vector3(0, -1, 0)), hits)
    expect(hits).toEqual([])
  })

  it('stands still at the cell when not animated, and points along the flow', () => {
    const parent = new THREE.Group()
    anim.rebuild({ tracks: [L], arrows: [arrow(), arrow({ s: 1.5, sMin: 1.25, sMax: 1.75, dir: -1 })], animate: false }, parent)
    anim.tick(0)
    close(anim.instance(0).position, [0.5, 0, 0])
    close(anim.instance(0).direction, [1, 0, 0])
    // Past the bend, on the second leg, flowing back toward the corner.
    close(anim.instance(1).position, [1, 0, 0.5])
    close(anim.instance(1).direction, [0, 0, -1])
    expect(anim.instance(0).color).toBe(0xff8800)
    close(anim.instance(0).scale, [0.02, 0.06, 0.02])
    anim.tick(10_000)
    close(anim.instance(0).position, [0.5, 0, 0])
  })

  it('moves along the track at its speed and wraps inside its own interval', () => {
    const parent = new THREE.Group()
    anim.rebuild({ tracks: [L], arrows: [arrow({ speed: 0.1 })], animate: true, speedScale: 1 }, parent)
    anim.tick(0)
    const x0 = anim.instance(0).position[0]!
    anim.tick(1000) // one second at 0.1 m/s
    expect(anim.instance(0).position[0]! - x0).toBeCloseTo(0.1, 6)
    anim.tick(3000) // 0.3 m from 0.5 wraps inside [0.25, 0.75]: 0.8 -> 0.3
    expect(anim.instance(0).position[0]).toBeCloseTo(0.3, 6)
    // A reversed arrow moves the other way; the speed scale multiplies.
    anim.rebuild({ tracks: [L], arrows: [arrow({ dir: -1 })], animate: true, speedScale: 2 }, parent)
    anim.tick(0)
    anim.tick(500)
    expect(anim.instance(0).position[0]).toBeCloseTo(0.4, 6)
    anim.setOptions({ animate: false })
    anim.tick(9000)
    expect(anim.instance(0).position[0]).toBeCloseTo(0.5, 6)
  })

  it('rides on the side of its pipe that faces the camera', () => {
    const parent = new THREE.Group()
    anim.setVisible(true)
    anim.rebuild({ tracks: [L], arrows: [arrow({ lift: 0.05 }), arrow({ s: 1.5, sMin: 1.25, sMax: 1.75, lift: 0.05 })], animate: false }, parent)
    // Seen from above, an arrow sits on the crown of the pipe...
    anim.tick(0, { x: 0.5, y: 10, z: 0 })
    close(anim.instance(0).position, [0.5, 0.05, 0])
    // ...and from the side, on the near side; the offset is across the pipe, never along it.
    anim.tick(0, { x: 0.5, y: 0, z: -10 })
    close(anim.instance(0).position, [0.5, 0, -0.05])
    anim.tick(0, { x: 10, y: 0, z: 1.5 })
    close(anim.instance(1).position, [1.05, 0, 0.5])
    // Looking straight down the pipe there is no "near side": the crown is used.
    anim.tick(0, { x: 10, y: 0, z: 0 })
    close(anim.instance(0).position, [0.5, 0.05, 0])
    // Without a camera (first frame), the crown.
    anim.tick(0)
    close(anim.instance(0).position, [0.5, 0.05, 0])
    close(anim.instance(0).direction, [1, 0, 0])
  })

  it('hides without animating, and a rebuild while hidden stays hidden', () => {
    const parent = new THREE.Group()
    anim.setVisible(true)
    anim.rebuild({ tracks: [L], arrows: [arrow()] }, parent)
    anim.setVisible(false)
    expect(anim.tick(1000)).toBe(false)
    anim.rebuild({ tracks: [L], arrows: [arrow()] }, parent)
    expect(parent.children.every(c => !c.visible)).toBe(true)
    anim.setVisible(true)
    expect(anim.tick(1000)).toBe(true)
  })

  it('dispose removes the arrows', () => {
    const parent = new THREE.Group()
    anim.rebuild({ tracks: [L], arrows: [arrow()] }, parent)
    anim.dispose()
    expect(parent.children).toHaveLength(0)
    expect(anim.count()).toBe(0)
    expect(anim.tick(0)).toBe(false)
  })

  it('stays out of the Omniverse scene snapshot', () => {
    const root = new THREE.Group()
    const box = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial())
    box.userData.modelId = 'm_1'
    root.add(box)
    anim.setVisible(true)
    anim.rebuild({ tracks: [L], arrows: [arrow()] }, root)
    anim.tick(0)
    const packet = buildSceneSnapshot({ root, project: { uid: 'p' } })
    expect(packet.primitives).toHaveLength(1)
    expect(packet.primitives[0]!.modelUid).toBe('m_1')
    anim.dispose()
  })
})
