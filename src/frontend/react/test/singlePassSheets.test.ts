import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { isPlanar, singlePassSheets } from '../src/geometry/singlePassSheets'

// A transparent DoubleSide material draws in two passes and re-derives its program each pass, every frame; a flat
// sheet draws the same in one pass, a volume does not.

const glass = () => new THREE.MeshStandardMaterial({ transparent: true, opacity: 0.55, side: THREE.DoubleSide })

describe('single-pass sheets', () => {
  it('sets one pass for flat transparent double-sided meshes only', () => {
    const root = new THREE.Group()
    const door = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 2), glass())
    door.rotation.y = 0.7
    const box = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), glass())
    const opaque = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshStandardMaterial({ side: THREE.DoubleSide }))
    const front = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshStandardMaterial({ transparent: true, opacity: 0.5 }))
    root.add(door, box, opaque, front)
    expect(singlePassSheets([root])).toBe(1)
    expect((door.material as THREE.Material).forceSinglePass).toBe(true)
    expect((box.material as THREE.Material).forceSinglePass).toBe(false)
    expect((opaque.material as THREE.Material).forceSinglePass).toBe(false)
    expect((front.material as THREE.Material).forceSinglePass).toBe(false)
    expect(singlePassSheets([root])).toBe(0)                      // nothing left to do
  })

  it('tells a plane from a curved sheet and from two parallel faces', () => {
    const tilted = new THREE.PlaneGeometry(2, 1, 3, 3)
    tilted.rotateX(0.4).rotateZ(1.1).translate(3, -2, 5)
    expect(isPlanar(tilted)).toBe(true)
    const curved = new THREE.CylinderGeometry(1, 1, 1, 12, 1, true, 0, Math.PI / 2)
    expect(isPlanar(curved)).toBe(false)
    const slab = new THREE.BoxGeometry(1, 1, 0.01)                // a thin panel with thickness
    expect(isPlanar(slab)).toBe(false)
    const noNormals = new THREE.BufferGeometry()
    noNormals.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0], 3))
    expect(isPlanar(noNormals)).toBe(true)
  })
})
