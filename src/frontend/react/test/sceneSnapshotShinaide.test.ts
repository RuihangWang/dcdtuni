import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { expect, it } from 'vitest'
import { createSceneBuilder, emptyRegistries } from '../src/interaction/sceneBuilder'
import { setLiquidModeActive } from '../src/liquid/liquidModeState'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
import type { CfdProject } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'

it('converts the actual Shinaide air/liquid structural scene with CDU details and stable model identities', () => {
  const raw = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json'), 'utf8'))
  const project = stateV4().normalizeStateV4(raw) as unknown as CfdProject
  const globalGroup = new THREE.Group()
  const wrapperGroup = new THREE.Group()
  wrapperGroup.add(globalGroup)
  const registries = emptyRegistries()
  setLiquidModeActive(true)
  const builder = createSceneBuilder({ globalGroup, wrapperGroup, registries, selectedUid: () => null, project: () => project, webglAvailable: false })
  try {
    builder.rebuild()
    const result = buildSceneSnapshot({ root: globalGroup, project, helpers: registries.renderHelpers })
    const ids = new Set(result.primitives.map(p => p.modelUid))
    for (const bucket of ['cdus', 'racks', 'acus'] as const) {
      for (const [uid, model] of Object.entries(project.constructions?.[bucket] ?? {})) {
        if (model.meta.enabled) expect(ids.has(uid), `${bucket} ${uid}`).toBe(true)
      }
    }
    expect(result.primitives.length).toBeGreaterThan(registries.renderMeshes.length)
  } finally {
    builder.clear()
    setLiquidModeActive(false)
  }
})

it('resolves the real Shinaide tee connection to its pipe and fitting identities', () => {
  const project = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json'), 'utf8')) as CfdProject
  const root = new THREE.Group()
  const connection = new THREE.Mesh(new THREE.TubeGeometry(new THREE.LineCurve3(new THREE.Vector3(), new THREE.Vector3(1, 0, 0)), 8, .01, 6), new THREE.MeshStandardMaterial())
  connection.userData = { isValveConnection: true, valveId: 'tee_1778058997173' }
  root.add(connection)
  const packet = buildSceneSnapshot({ root, project })
  expect(packet.primitives).toHaveLength(1)
  expect(packet.primitives[0]!).toMatchObject({ modelUid: 'm_31', equipmentUid: 'tee_1778058997173' })
})

it('marks the surfaces inside a rack cabinet, lining or looking out, and only those, for the cabinet occlusion', () => {
  const raw = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json'), 'utf8'))
  const project = stateV4().normalizeStateV4(raw) as unknown as CfdProject
  const globalGroup = new THREE.Group()
  const wrapperGroup = new THREE.Group()
  wrapperGroup.add(globalGroup)
  const registries = emptyRegistries()
  const builder = createSceneBuilder({ globalGroup, wrapperGroup, registries, selectedUid: () => null, project: () => project, webglAvailable: false })
  try {
    builder.rebuild()
    const meshes: THREE.Mesh[] = []
    globalGroup.traverse(o => { if ((o as THREE.Mesh).isMesh) meshes.push(o as THREE.Mesh) })
    // Racks merge their frame into one exportName; the shell and the front/back
    // strips carry the face they lie on, the cavity's own walls do not.
    const frame = meshes.filter(m => m.userData.isRackFrame)
    const inner = frame.filter(m => m.userData.faceId === undefined)
    const outer = frame.filter(m => m.userData.faceId !== undefined)
    const chassis = meshes.filter(m => m.userData.isRackEquipment && ['top', 'bottom', 'left', 'right'].includes(m.userData.faceId))
    expect(inner.length).toBeGreaterThan(0)
    expect(outer.length).toBeGreaterThan(0)
    expect(chassis.length).toBeGreaterThan(0)
    expect(inner.every(m => m.userData.cabinetInterior === 'lining')).toBe(true)
    expect(chassis.every(m => m.userData.cabinetInterior === 'lining')).toBe(true)
    // A set-back server face looks out of the opening; a flush one is not inside at all.
    const faces = meshes.filter(m => m.userData.isRackEquipment && ['inlet', 'outlet'].includes(m.userData.faceId))
    expect(faces.length).toBeGreaterThan(0)
    expect(faces.every(m => m.userData.cabinetInterior === undefined || m.userData.cabinetInterior === 'opening')).toBe(true)
    expect(faces.some(m => m.userData.cabinetInterior === 'opening')).toBe(true)
    expect(outer.some(m => m.userData.cabinetInterior)).toBe(false)
    expect(meshes.filter(m => m.userData.cabinetInterior).every(m => m.userData.isRackFrame || m.userData.isRackEquipment || m.userData.isBaseWall)).toBe(true)
  } finally {
    builder.clear()
  }
})
