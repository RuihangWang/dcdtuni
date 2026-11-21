import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import {
  applyMeshPreviewOrder,
  baseOrder,
  RENDER_LAYERS,
  RENDER_ORDER_DEFAULTS,
  reorderChips,
} from '../src/ui/renderOrder'

describe('baseOrder', () => {
  it('gives the TOP chip the highest renderOrder', () => {
    expect(baseOrder(0, 5)).toBe(200)
    expect(baseOrder(4, 5)).toBe(0)
  })

  it('leaves 50 between layers for a layer to offset inside its own band', () => {
    expect(baseOrder(0, 2) - baseOrder(1, 2)).toBe(50)
  })
})

describe('RENDER_ORDER_DEFAULTS', () => {
  it('names only layers the table has', () => {
    for (const keys of Object.values(RENDER_ORDER_DEFAULTS)) {
      for (const k of keys) expect(RENDER_LAYERS[k]).toBeDefined()
    }
  })

  it('gives the mesh tab two chips and the postprocess tab five', () => {
    expect(RENDER_ORDER_DEFAULTS.meshPreview).toEqual(['meshPreview', 'geometry'])
    expect(RENDER_ORDER_DEFAULTS.postprocess).toHaveLength(5)
  })
})

describe('reorderChips', () => {
  const order = ['a', 'b', 'c']

  it('moves a chip above the target', () => {
    expect(reorderChips(order, 'c', 'a', true)).toEqual(['c', 'a', 'b'])
  })

  it('moves a chip below the target', () => {
    // The index has to be measured AFTER the chip is pulled out, or a downward
    // move lands one position short and the list looks almost right.
    expect(reorderChips(order, 'a', 'c', false)).toEqual(['b', 'c', 'a'])
  })

  it('is a copy, never the same array', () => {
    const out = reorderChips(order, 'a', 'a', true)
    expect(out).toEqual(order)
    expect(out).not.toBe(order)
  })

  it('ignores a key the list does not have', () => {
    expect(reorderChips(order, 'z', 'a', true)).toEqual(order)
  })
})

describe('applyMeshPreviewOrder', () => {
  it('puts the wireframe one above its own faces, inside the layer band', () => {
    const group = new THREE.Group()
    const mesh = new THREE.Mesh(new THREE.BufferGeometry())
    const edges = new THREE.LineSegments(new THREE.BufferGeometry())
    group.add(mesh, edges)

    applyMeshPreviewOrder(['meshPreview', 'geometry'], group)
    expect(mesh.renderOrder).toBe(50)
    expect(edges.renderOrder).toBe(51)

    // Dropped below geometry: the whole band moves, the +1 stays.
    applyMeshPreviewOrder(['geometry', 'meshPreview'], group)
    expect(mesh.renderOrder).toBe(0)
    expect(edges.renderOrder).toBe(1)
  })

  it('survives having no preview group', () => {
    expect(() => applyMeshPreviewOrder(['meshPreview', 'geometry'], null)).not.toThrow()
  })
})
