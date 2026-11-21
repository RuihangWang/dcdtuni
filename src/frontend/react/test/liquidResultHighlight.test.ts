import * as THREE from 'three'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { setLiquidResultHighlight } from '../src/liquid/liquidResultHighlight'
import { setSceneMeshes } from '../src/viewportBus'

/**
 * The results tab's own selection highlight: translucent copies of the selected
 * object's meshes, placed where the originals are, tagged as helpers (so the
 * Omniverse snapshot skips them), never touching the originals' materials, and
 * repainted after a scene rebuild.
 */

type Host = { globalGroup?: THREE.Object3D; __viewportRender?: () => void }
const host = window as unknown as Host

function mesh(userData: Record<string, unknown>, at: [number, number, number]) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial())
  m.userData = userData
  m.position.set(...at)
  return m
}

function overlay(root: THREE.Object3D) {
  return root.getObjectByName('liquidPostprocessHighlight') as THREE.Group | undefined
}

let root: THREE.Group
let renders = 0

beforeEach(() => {
  root = new THREE.Group()
  root.position.set(10, 0, 0) // the overlay is placed in root-local space
  host.globalGroup = root
  renders = 0
  host.__viewportRender = () => { renders += 1 }
})

afterEach(() => {
  setLiquidResultHighlight(null)
  delete host.globalGroup
  delete host.__viewportRender
})

describe('setLiquidResultHighlight', () => {
  it('copies the valve meshes of the selection over the originals and leaves the originals alone', () => {
    const pipe = new THREE.Group()
    pipe.userData = { isPipeGroup: true, pipeModelId: 'm_32' }
    const tube = mesh({ isPipe: true, pipeModelId: 'm_32' }, [0, 1, 0])
    const valveGroup = new THREE.Group()
    valveGroup.userData = { isValve: true, valveId: 'valve_1', pipeId: 'm_32' }
    valveGroup.position.set(2, 0, 0)
    const body = mesh({}, [0, 0, 3])
    valveGroup.add(body)
    pipe.add(tube, valveGroup)
    root.add(pipe)
    const originalMaterial = body.material

    const lit = setLiquidResultHighlight({ uid: 'm_32', node: { type: 'fitting', uid: 'm_32', fittingId: 'valve_1' } })

    expect(lit).toBe(1)
    const group = overlay(root)!
    expect(group.userData.isHelper).toBe(true)
    const copy = group.children.find((c) => (c as THREE.Mesh).isMesh) as THREE.Mesh
    expect(copy.userData.isHelper).toBe(true)
    expect(copy.geometry).toBe(body.geometry)
    expect(body.material).toBe(originalMaterial)
    // Same world place as the valve body: root (10,0,0) + valve group (2,0,0) + body (0,0,3).
    root.updateMatrixWorld(true)
    expect(new THREE.Vector3().setFromMatrixPosition(copy.matrixWorld).toArray()).toEqual([12, 0, 3])
    expect(renders).toBeGreaterThan(0)
  })

  it('marks a small object with a ring of constant screen size, on the object, drawn over everything', () => {
    // A 25 mm valve is a few pixels wide at room scale: the translucent copy alone
    // is invisible there (found in the browser check), so a marker locates it.
    const valveGroup = new THREE.Group()
    valveGroup.userData = { isValve: true, valveId: 'valve_1', pipeId: 'm_32' }
    valveGroup.position.set(2, 0, 0)
    valveGroup.add(mesh({}, [0, 0, 3]))
    root.add(valveGroup)

    setLiquidResultHighlight({ uid: 'm_32', node: { type: 'fitting', uid: 'm_32', fittingId: 'valve_1' } })

    const group = overlay(root)!
    const marker = group.children.find((c) => c.userData.isLiquidPostprocessMarker) as THREE.Sprite
    expect(marker).toBeInstanceOf(THREE.Sprite)
    expect(marker.userData.isHelper).toBe(true)
    const material = marker.material as THREE.SpriteMaterial
    expect(material.sizeAttenuation).toBe(false)
    expect(material.depthTest).toBe(false)
    root.updateMatrixWorld(true)
    expect(new THREE.Vector3().setFromMatrixPosition(marker.matrixWorld).toArray()).toEqual([12, 0, 3])
    // The marker is not a mesh copy: the lit-mesh count stays the object's own.
    expect(group.children.filter((c) => (c as THREE.Mesh).isMesh)).toHaveLength(1)
  })

  it('puts the marker on the object, not in the empty middle of an L-shaped pipe', () => {
    const a = mesh({ isPipe: true, pipeModelId: 'm_32' }, [0, 0, 0])
    const mid = mesh({ isPipe: true, pipeModelId: 'm_32' }, [2, 0, 0])
    const b = mesh({ isPipe: true, pipeModelId: 'm_32' }, [4, 0, 0])
    const c = mesh({ isPipe: true, pipeModelId: 'm_32' }, [4, 0, 4])
    root.add(a, mid, b, c)
    setLiquidResultHighlight({ uid: 'm_32', node: null })
    const marker = overlay(root)!.children.find((o) => o.userData.isLiquidPostprocessMarker)!
    root.updateMatrixWorld(true)
    const at = new THREE.Vector3().setFromMatrixPosition(marker.matrixWorld)
    // The union box centre (12, 0, 2) is off the pipe; the nearest lit mesh is the middle one.
    expect(at.toArray()).toEqual([12, 0, 0])
  })

  it('lights a whole pipe with its fittings, skips hidden meshes, and clears on null', () => {
    const tube = mesh({ isPipe: true, pipeModelId: 'm_32' }, [0, 0, 0])
    const cap = mesh({ isBlindEndCap: true, pipeId: 'm_32' }, [1, 0, 0])
    const hidden = mesh({ isPipe: true, pipeModelId: 'm_32' }, [2, 0, 0])
    hidden.visible = false
    const other = mesh({ isPipe: true, pipeModelId: 'm_31' }, [3, 0, 0])
    root.add(tube, cap, hidden, other)

    expect(setLiquidResultHighlight({ uid: 'm_32', node: null })).toBe(2)
    expect(setLiquidResultHighlight(null)).toBe(0)
    expect(overlay(root)).toBeUndefined()
  })

  it('follows a scene rebuild: the new meshes of the selection are lit again', () => {
    const rack = new THREE.Group()
    rack.userData = { modelId: 'm_1' }
    rack.add(mesh({ isRackEquipment: true, equipmentId: 'e_2' }, [0, 0, 0]))
    root.add(rack)
    const selection = { uid: 'm_1', node: { type: 'equipment' as const, uid: 'm_1', equipmentId: 'e_2' } }
    expect(setLiquidResultHighlight(selection)).toBe(1)

    // A rebuild replaces the rack's meshes; the viewport then announces the new scene.
    root.remove(rack)
    const rebuilt = new THREE.Group()
    rebuilt.userData = { modelId: 'm_1' }
    rebuilt.add(mesh({ isRackEquipment: true, equipmentId: 'e_2' }, [0, 0, 0]), mesh({ isRackEquipment: true, equipmentId: 'e_2' }, [0, 1, 0]))
    root.add(rebuilt)
    setSceneMeshes([])

    expect(overlay(root)!.children.filter((c) => (c as THREE.Mesh).isMesh)).toHaveLength(2)
    expect(root.children.filter((c) => c.name === 'liquidPostprocessHighlight')).toHaveLength(1)
  })
})
