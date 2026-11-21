import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { paintMeshHighlight, SOLID_HIGHLIGHT, TINT_HIGHLIGHT } from '../src/interaction/selectionHighlight'
import { reapplyViewFades, viewFadeOpacity, type ViewFades } from '../src/geometry/viewFades'

/**
 * The selection highlight never undoes a view fade.
 *
 * The highlight swaps a mesh's material for a tinted clone and keeps the
 * original to put back. Two ways that fought the fades, both measured in the
 * browser on Tri_angle:
 *
 * - Un-highlighting put back the material captured BEFORE the fade. Select an
 *   ACU face on the geometry tab, open the mesh preview (the fade lands on the
 *   highlight clone), then select something else: the ACU face came back at
 *   opacity 1 over the mesh.
 * - The solid highlight wrote opacity 0.9 onto a slot guide or server, over
 *   the preview's 30% and over post-processing's 15%.
 *
 * `paintMeshHighlight` takes a `refade` that re-applies whatever fade is up to
 * the mesh it just painted or restored, so the fade is the last word.
 */

type Std = THREE.MeshStandardMaterial

function mesh(material: THREE.Material = new THREE.MeshStandardMaterial()): THREE.Mesh {
  return new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material)
}

const refadeWith = (fades: ViewFades) => (m: THREE.Mesh) => reapplyViewFades([m], fades)
const PREVIEW: ViewFades = { model: 1, preview: 0.3 }
const NONE: ViewFades = { model: 1, preview: 1 }
const std = (m: THREE.Mesh) => m.material as Std

describe('paintMeshHighlight', () => {
  it('without a fade it is the legacy solid highlight, and restore gives the original back', () => {
    const original = new THREE.MeshStandardMaterial()
    const m = mesh(original)
    paintMeshHighlight(m, true, 'solid', true, refadeWith(NONE))
    expect(m.material).not.toBe(original)
    expect(std(m).color.getHex()).toBe(SOLID_HIGHLIGHT.color)
    expect(std(m).opacity).toBeCloseTo(SOLID_HIGHLIGHT.opacity, 12)
    paintMeshHighlight(m, false, 'solid', true, refadeWith(NONE))
    expect(m.material).toBe(original)
    expect(original.opacity).toBe(1)
  })

  it('a solid highlight under the preview fade takes the fade, not 0.9', () => {
    const m = mesh()
    reapplyViewFades([m], PREVIEW)
    paintMeshHighlight(m, true, 'solid', true, refadeWith(PREVIEW))
    expect(std(m).color.getHex()).toBe(SOLID_HIGHLIGHT.color)
    expect(std(m).opacity).toBeCloseTo(0.3, 12)
    expect(std(m).depthWrite).toBe(false)
  })

  it('a solid highlight under the post-processing fade takes that fade', () => {
    const m = mesh()
    const pp: ViewFades = { model: 0.15, preview: 1 }
    reapplyViewFades([m], pp)
    paintMeshHighlight(m, true, 'solid', true, refadeWith(pp))
    expect(std(m).opacity).toBeCloseTo(0.15, 12)
    expect(std(m).depthWrite).toBe(false)
  })

  it('un-highlighting under the preview puts the fade on the restored material', () => {
    // Highlighted on the geometry tab (no fade), then the preview faded the
    // highlight clone, then the selection moved on.
    const original = new THREE.MeshStandardMaterial()
    const m = mesh(original)
    paintMeshHighlight(m, true, 'tint', true, refadeWith(NONE))
    reapplyViewFades([m], PREVIEW)
    paintMeshHighlight(m, false, 'tint', true, refadeWith(PREVIEW))
    expect(std(m).opacity).toBeCloseTo(0.3, 12)
    expect(std(m).transparent).toBe(true)
    expect(std(m).depthWrite).toBe(false)
    expect((m.userData as { _origMaterial?: unknown })._origMaterial).toBeUndefined()
  })

  it('a restored SHARED material is cloned before the fade writes, so it does not bleed', () => {
    // The builders cache materials by kind: two meshes share one. The fade
    // must not write into the shared one through the mesh that was restored.
    const shared = new THREE.MeshStandardMaterial()
    const a = mesh(shared)
    const b = mesh(shared)
    paintMeshHighlight(a, true, 'tint', true, refadeWith(NONE))
    reapplyViewFades([a, b], PREVIEW)
    paintMeshHighlight(a, false, 'tint', true, refadeWith(PREVIEW))
    expect(std(a).opacity).toBeCloseTo(0.3, 12)
    expect(a.material).not.toBe(shared)
    expect(shared.opacity).toBe(1)
    expect(shared.depthWrite).toBe(true)
  })

  it('a highlighted U-slot guide is capped at the fade — no 0.9 slab over the mesh', () => {
    // Guides keep their own opacity through the preview fade, so the fade
    // alone cannot bring a solid-highlighted one down; the cap does.
    const m = mesh()
    m.userData = { isRackSlotGuide: true }
    reapplyViewFades([m], PREVIEW)
    paintMeshHighlight(m, true, 'solid', true, refadeWith(PREVIEW), viewFadeOpacity(PREVIEW))
    expect(std(m).opacity).toBeLessThanOrEqual(0.3 + 1e-9)
  })

  it('tints without touching the opacity when no fade is up', () => {
    const m = mesh()
    paintMeshHighlight(m, true, 'tint', false, refadeWith(NONE))
    expect(std(m).emissive.getHex()).toBe(TINT_HIGHLIGHT.emissive)
    expect(std(m).emissiveIntensity).toBeCloseTo(TINT_HIGHLIGHT.inactive, 12)
    expect(std(m).opacity).toBe(1)
  })
})
