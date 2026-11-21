import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
import { createMeshVisuals } from '../src/run/meshVisuals'
import { createSensorSpheres } from '../src/run/sensorSpheres'
import type { CfdProject } from '../src/schema/cfdproj'
import { paintSelectionHighlights, type HighlightSelection } from '../src/interaction/selectionHighlight'
import { applyMaterialOpacity, reapplyViewFades, type ViewFades } from '../src/geometry/viewFades'

function triangle(indexed = false) {
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3))
  if (indexed) g.setIndex([0, 1, 2])
  return g
}
function setup() {
  const root = new THREE.Group()
  root.position.set(-5, 0, -4) // editor centring must not leak into project space
  const rack = new THREE.Group()
  rack.userData.modelId = 'rack-uid'
  rack.position.set(3, 1, 2)
  root.add(rack)
  const mesh: THREE.Mesh = new THREE.Mesh(triangle(), new THREE.MeshStandardMaterial({ color: 0xff0000 }))
  rack.add(mesh)
  return { root, rack, mesh }
}
const project = { uid: 'project-uid', name: 'mixed' }

describe('project render snapshot', () => {
  it('keeps project coordinates, identity, camera and physical time through a centred viewport', () => {
    const { root } = setup()
    const camera = new THREE.PerspectiveCamera(50, 1.5, 0.1, 500)
    camera.position.set(0, 3, 8)
    const packet = buildSceneSnapshot({ root, project, camera, target: new THREE.Vector3(0, 0, 0), selectedUids: ['rack-uid'], timeSeconds: 12.5 })
    expect(packet.coordinateSystem).toEqual({ upAxis: 'Y', metersPerUnit: 1, handedness: 'right' })
    expect(packet.primitives[0]!.modelUid).toBe('rack-uid')
    expect(packet.primitives[0]!.matrix.slice(12, 15)).toEqual([3, 1, 2])
    expect(packet.primitives[0]!.positions).toEqual([0, 0, 0, 1, 0, 0, 0, 1, 0])
    expect(packet.view.camera!.position).toEqual([5, 3, 12])
    expect(packet.view.camera!.target).toEqual([5, 0, 4])
    expect(packet.view.selectedUids).toEqual(['rack-uid'])
    expect(packet.view.timeSeconds).toBe(12.5)
  })

  it('splits indexed multi-material draw groups without losing or adding faces', () => {
    const { root, mesh } = setup()
    mesh.geometry = new THREE.BoxGeometry(1, 1, 1)
    mesh.material = Array.from({ length: 6 }, (_, i) => new THREE.MeshBasicMaterial({ color: i === 0 ? 0xff0000 : 0x00ff00 }))
    mesh.geometry.setDrawRange(6, 12)
    const p = buildSceneSnapshot({ root, project })
    expect(p.primitives).toHaveLength(2)
    expect(p.primitives.map(x => x.indices.length)).toEqual([6, 6])
    expect(p.primitives[0]!.material.color).toEqual([0, 1, 0])
  })

  it('expands instanced geometry with per-instance transforms and colour', () => {
    const { root, rack, mesh } = setup()
    rack.remove(mesh)
    const instances = new THREE.InstancedMesh(triangle(true), new THREE.MeshStandardMaterial(), 2)
    instances.setMatrixAt(0, new THREE.Matrix4().makeTranslation(0, 2, 0))
    instances.setMatrixAt(1, new THREE.Matrix4().makeScale(-1, 1, 1))
    instances.setColorAt(1, new THREE.Color(0x00ff00))
    rack.add(instances)
    const p = buildSceneSnapshot({ root, project })
    expect(p.primitives).toHaveLength(2)
    expect(p.primitives[0]!.matrix.slice(12, 15)).toEqual([3, 3, 2])
    expect(p.primitives[1]!.matrix[0]).toBe(-1)
    expect(p.primitives[1]!.material.color).toEqual([0, 1, 0])
  })

  it('includes non-STL engineering detail and independent pipes but excludes editing helpers', () => {
    const { root, rack } = setup()
    const detail = new THREE.Mesh(triangle(), new THREE.MeshStandardMaterial())
    detail.userData = { liquidVisualDetail: true, exportExcluded: true, selectable: false }
    rack.add(detail)
    const pipeGroup = new THREE.Group()
    pipeGroup.userData.isPipeGroup = true
    const pipe = new THREE.Mesh(triangle(), new THREE.MeshStandardMaterial())
    pipe.userData = { isPipe: true, pipeModelId: 'pipe-uid' }
    pipeGroup.add(pipe)
    root.add(pipeGroup)
    const guide = new THREE.Mesh(triangle(), new THREE.MeshStandardMaterial())
    guide.userData = { modelId: 'rack-uid', isWaypoint: true }
    root.add(guide)
    const packet = buildSceneSnapshot({ root, project })
    expect(packet.primitives.map(p => p.modelUid)).toEqual(['rack-uid', 'rack-uid', 'pipe-uid'])
  })

  it('refuses unsupported deformation and unowned renderable geometry', () => {
    const { root, rack, mesh } = setup()
    rack.remove(mesh)
    rack.add(new THREE.SkinnedMesh(triangle(), new THREE.MeshStandardMaterial()))
    expect(() => buildSceneSnapshot({ root, project })).toThrow(/SkinnedMesh/)
    rack.clear()
    root.add(mesh)
    expect(() => buildSceneSnapshot({ root, project })).toThrow(/identity/)
  })

  it('reports bounded identity evidence for an unowned live viewport object', () => {
    const root = new THREE.Group()
    const group = new THREE.Group()
    group.name = 'pipe-layer'
    group.userData = { isPipeGroup: true, privateArbitraryPayload: 'must not be exposed' }
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(1), new THREE.MeshBasicMaterial())
    mesh.name = 'unmapped port'
    mesh.userData = { isPipe: true, pipeModelId: null }
    root.add(group); group.add(mesh)
    let error = ''
    try { buildSceneSnapshot({ root, project }) } catch(e) { error = String(e) }
    expect(error).toContain('SphereGeometry')
    expect(error).toContain('unmapped port')
    expect(error).toContain('pipe-layer')
    expect(error).toContain('"isPipeGroup":true')
    expect(error).not.toContain('must not be exposed')
  })

  it('excludes actual mesh-tab annotations while keeping physical models with the same group name', () => {
    const { root, rack } = setup()
    rack.name = 'meshVisuals'
    const visuals = createMeshVisuals({
      globalGroup: root, camera: new THREE.PerspectiveCamera(), domElement: document.createElement('div'),
      orbit: null, project: () => null, bounds: () => null, activeTab: () => 'geometry',
      dragAxes: () => ({ x: true, y: true, z: true }), requestRender() {}, onChange() {},
    })
    try {
      visuals.refresh()
      const packet = buildSceneSnapshot({ root, project })
      expect(packet.primitives).toHaveLength(1)
      expect(packet.primitives[0]!.modelUid).toBe('rack-uid')
    } finally { visuals.dispose() }
  })

  it('excludes sensor probe balls, so a project with sensors can enter Omniverse', () => {
    // Base604 and DAML-Testbed carry instrumentation probes. Their balls live
    // under globalGroup (hidden off the Solve tab) and used to abort the OV
    // snapshot with "Renderable has no project identity".
    const { root } = setup()
    const withProbes = { ...project, instrumentation: { probes: {
      sensor_1: { uid: 'sensor_1', geometry: { location: { x: 1, y: 1, z: 1 } } },
    } } } as unknown as CfdProject
    const balls = createSensorSpheres({
      camera: new THREE.PerspectiveCamera(), scene: new THREE.Scene(),
      domElement: document.createElement('div'), globalGroup: root, orbit: null,
      project: () => withProbes, activeTab: () => 'geometry', requestRender() {},
    })
    try {
      balls.refresh()
      expect(Object.keys(balls.positions())).toEqual(['sensor_1'])
      const packet = buildSceneSnapshot({ root, project })
      expect(packet.primitives.map(p => p.modelUid)).toEqual(['rack-uid'])
    } finally { balls.dispose() }
  })

  it('keeps tiled UVs, embedded textures, normal scale, colour space and alpha', () => {
    const { root, mesh } = setup()
    mesh.geometry.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 3, 0, 0, 2], 2))
    const image = { width: 1, height: 1, toDataURL: () => 'data:image/png;base64,iVBORw0KGgo=' }
    const color = new THREE.Texture(image as unknown as HTMLCanvasElement)
    color.colorSpace = THREE.SRGBColorSpace
    color.repeat.set(2, 2)
    color.wrapS = color.wrapT = THREE.RepeatWrapping
    const normal = color.clone()
    normal.colorSpace = THREE.NoColorSpace
    mesh.material = new THREE.MeshStandardMaterial({ map: color, normalMap: normal, normalScale: new THREE.Vector2(2, -1), roughness: .4, metalness: .7, opacity: .6, transparent: true, alphaTest: .2 })
    const packet = buildSceneSnapshot({ root, project })
    const p = packet.primitives[0]!
    expect(p.uvs).toEqual([0, 0, 6, 0, 0, 4])
    expect(p.material).toMatchObject({ opacity: .6, roughness: .4, metalness: .7, normalScale: [2, -1], alphaTest: .2, mapColorSpace: 'srgb', normalColorSpace: 'linear', wrapS: 'repeat', wrapT: 'repeat' })
    expect(packet.textures[p.material.map!]).toBe('data:image/png;base64,iVBORw0KGgo=')
  })

  it('preserves the colour/normal/roughness PBR set used by rack and wall assets', () => {
    const { root, mesh } = setup()
    mesh.geometry.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1], 2))
    const image = { width: 1, height: 1, toDataURL: () => 'data:image/png;base64,iVBORw0KGgo=' }
    const map = new THREE.Texture(image as unknown as HTMLCanvasElement)
    map.colorSpace = THREE.SRGBColorSpace
    const normalMap = map.clone(); normalMap.colorSpace = THREE.NoColorSpace
    const roughnessMap = normalMap.clone()
    const metalnessMap = normalMap.clone()
    mesh.material = new THREE.MeshStandardMaterial({ map, normalMap, roughnessMap, metalnessMap, roughness: .6, metalness: .8 })
    const packet = buildSceneSnapshot({ root, project })
    const m = packet.primitives[0]!.material
    expect(m).toMatchObject({ roughness: .6, metalness: .8 })
    for (const key of ['map', 'normalMap', 'roughnessMap', 'metalnessMap'] as const) {
      expect(packet.textures[m[key]!]).toBe('data:image/png;base64,iVBORw0KGgo=')
    }
    // The four maps are clones of ONE image (shared Texture.source), so the
    // transfer carries it once; colour space rides on the material, not the
    // image. Keyed by texture.uuid this used to be four copies.
    expect(new Set([m.map, m.normalMap, m.roughnessMap, m.metalnessMap]).size).toBe(1)
    expect(Object.keys(packet.textures)).toHaveLength(1)
  })

  it('rejects ambiguous or stale valve ownership instead of guessing from position', () => {
    const root = new THREE.Group()
    const mesh = new THREE.Mesh(triangle(), new THREE.MeshStandardMaterial())
    mesh.userData = { valveId: 'fitting-a', isValveConnection: true }
    root.add(mesh)
    const pipe = (uid: string) => ({ uid, cooling: { valves: [{ id: 'fitting-a' }] } })
    const ambiguous = { ...project, constructions: { pipeSegments: { p1: pipe('p1'), p2: pipe('p2') } } }
    expect(() => buildSceneSnapshot({ root, project: ambiguous })).toThrow(/Ambiguous valve identity/)
    mesh.userData.pipeId = 'p2'
    const stale = { ...project, constructions: { pipeSegments: { p1: pipe('p1') } } }
    expect(() => buildSceneSnapshot({ root, project: stale })).toThrow(/Valve owner mismatch/)
  })
})


describe('selection stays outside authored render materials', () => {
  const none: HighlightSelection = { uid: null, node: null, uids: new Set() }
  const whole: HighlightSelection = { uid: 'rack-uid', node: null, uids: new Set(['rack-uid']) }
  const noFades: ViewFades = { model: 1, preview: 1 }

  function paint(rack: THREE.Group, selection: HighlightSelection, fades = noFades,
    kind: string | undefined = undefined) {
    const spanFor = (uid: string) => (equipmentId: string) => {
      if (uid !== 'rack-uid' || equipmentId !== 'server-uid') {
        throw new Error('Unknown selection fixture equipment span')
      }
      return { startUnit: 1, spanUnits: 2 }
    }
    paintSelectionHighlights(new Map([['rack-uid', rack]]), selection, spanFor, fades, () => kind)
  }
  function materials(root: THREE.Group, selectedUids: string[] = []) {
    return buildSceneSnapshot({ root, project, selectedUids })
  }

  it('exports the real pre-highlight PBR and opacity, then clears without changing native view selection', () => {
    const { root, rack, mesh } = setup()
    mesh.userData.modelId = 'rack-uid'
    const original = new THREE.MeshStandardMaterial({ color: 0x586674, emissive: 0x102030,
      emissiveIntensity: 0.2, roughness: 0.31, metalness: 0.17, opacity: 0.6, transparent: true })
    mesh.material = original
    const before = materials(root)
    paint(rack, whole)
    const highlighted = mesh.material
    expect(highlighted).not.toBe(original)
    expect(mesh.userData._origMaterial).toBe(original)
    expect((highlighted as THREE.MeshStandardMaterial).emissive.getHex()).toBe(0x00ff88)
    const selected = materials(root, ['rack-uid'])
    expect(selected.primitives).toEqual(before.primitives)
    expect(selected.textures).toEqual(before.textures)
    expect(selected.view.selectedUids).toEqual(['rack-uid'])
    expect(mesh.material).toBe(highlighted)
    expect(mesh.userData._origMaterial).toBe(original)
    paint(rack, none)
    expect(mesh.material).toBe(original)
    expect(materials(root).primitives).toEqual(before.primitives)
    expect(materials(root).view.selectedUids).toEqual([])
  })

  it('restores all actual cabinet array draw-group maps and alpha instead of the stripped green clones', () => {
    const { root, rack, mesh } = setup()
    mesh.userData = { modelId: 'rack-uid', exportName: 'cdu-body' }
    mesh.geometry = new THREE.BoxGeometry(1, 1, 1)
    const map = new THREE.Texture({ width: 1, height: 1,
      toDataURL: () => 'data:image/png;base64,iVBORw0KGgoA' } as unknown as HTMLCanvasElement)
    map.colorSpace = THREE.SRGBColorSpace
    const original = Array.from({ length: 6 }, (_, index) => new THREE.MeshStandardMaterial({
      color: index % 2 ? 0x687481 : 0x495868, map, opacity: 0.2 + index * 0.1,
      transparent: true, alphaTest: 0.02, side: THREE.DoubleSide, roughness: 0.4, metalness: 0.3 }))
    mesh.material = original
    const before = materials(root)
    paint(rack, whole, noFades, 'cdu')
    expect(mesh.userData._origMaterial).toBe(original)
    expect((mesh.material as THREE.MeshStandardMaterial[]).every(material => material.map === null)).toBe(true)
    const selected = materials(root, ['rack-uid'])
    expect(selected.primitives).toEqual(before.primitives)
    expect(selected.textures).toEqual(before.textures)
    expect(original.every(material => material.map === map)).toBe(true)
    paint(rack, none, noFades, 'cdu')
    expect(mesh.material).toBe(original)
    expect(materials(root).primitives).toEqual(before.primitives)
  })

  it('keeps a post-process fade applied after selection without authoring its temporary tint', () => {
    const { root, rack, mesh } = setup()
    mesh.userData.modelId = 'rack-uid'
    mesh.material = new THREE.MeshStandardMaterial({ color: 0x586674, opacity: 0.6, transparent: true })
    const fades = { model: 0.15, preview: 1 }
    paint(rack, whole)
    applyMaterialOpacity(root, fades.model)
    paint(rack, whole, fades)
    const highlighted = mesh.material
    const selected = materials(root, ['rack-uid'])
    expect(selected.primitives[0]!.material.opacity).toBe(0.15)
    expect(selected.primitives[0]!.material.emissive).toEqual([0, 0, 0])
    expect(mesh.material).toBe(highlighted)
    expect((highlighted as THREE.Material).opacity).toBe(0.15)
    paint(rack, none, fades)
    expect(materials(root).primitives).toEqual(selected.primitives)
  })

  it('removes the real solid-selection opacity while preserving a later equipment-inclusive preview fade', () => {
    const { root, rack, mesh } = setup()
    mesh.userData = { modelId: 'rack-uid', isRackEquipment: true, equipmentId: 'server-uid' }
    mesh.material = new THREE.MeshStandardMaterial({ color: 0x586674, opacity: 0.24, transparent: true })
    const before = materials(root)
    const equipment: HighlightSelection = { uid: 'rack-uid', uids: new Set(['rack-uid']),
      node: { type: 'equipment', uid: 'rack-uid', equipmentId: 'server-uid' } }
    paint(rack, equipment)
    expect((mesh.material as THREE.Material).opacity).toBe(0.9)
    expect(materials(root, ['rack-uid']).primitives).toEqual(before.primitives)
    const fades = { model: 1, preview: 0.3 }
    reapplyViewFades([rack], fades)
    paint(rack, equipment, fades)
    const selected = materials(root, ['rack-uid'])
    expect(selected.primitives[0]!.material.opacity).toBe(0.3)
    expect(selected.primitives[0]!.material.emissive).toEqual([0, 0, 0])
    paint(rack, none, fades)
    expect(materials(root).primitives).toEqual(selected.primitives)
  })

  it('retains an explicit non-selection material override instead of replacing it with a cached highlight source', () => {
    const { root, rack, mesh } = setup()
    mesh.userData.modelId = 'rack-uid'
    const override = new THREE.MeshStandardMaterial({ color: 0x123456, emissive: 0x0a0b0c,
      emissiveIntensity: 0.4, opacity: 0.37, transparent: true })
    const options = { root, project, materialOf: () => override }
    const before = buildSceneSnapshot(options)
    paint(rack, whole)
    const selected = buildSceneSnapshot({ ...options, selectedUids: ['rack-uid'] })
    expect(selected.primitives).toEqual(before.primitives)
    expect(selected.view.selectedUids).toEqual(['rack-uid'])
    expect(override.opacity).toBe(0.37)
  })
})
