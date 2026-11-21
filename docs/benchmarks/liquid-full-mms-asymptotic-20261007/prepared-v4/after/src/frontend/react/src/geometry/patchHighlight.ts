/**
 * Painting a selected patch purple, in both patch viewers.
 *
 * The two viewers hold their geometry differently, so "highlight this patch"
 * means two different things:
 *
 *   - MESH PREVIEW builds one `THREE.Mesh` and one `THREE.LineSegments` PER
 *     patch, so highlighting is what the legacy does at `mpHighlightPatch`
 *     (STL_generate.html:24667): swap the material colour and restore it from
 *     `userData` on toggle-off.
 *   - POST-PROCESSING draws every patch as ONE geometry with a shared field
 *     attribute, so there is no per-patch material to paint. The selected
 *     patches are drawn a second time instead, as a purple overlay built from
 *     the same position buffer and the selected patches' slice of the index
 *     array.
 *
 * The overlay needs to know where each patch's triangles start in that index
 * array, and the array is FILTERED — `filterIndices` in `ppRenderer.ts` drops
 * the triangles of hidden patches while keeping the point buffer whole. So the
 * offsets depend on the hidden set, and `patchTriangleRanges` reproduces that
 * walk exactly. `visibleTriangleTotal` exists so a caller can check its own
 * arithmetic against the geometry it is about to slice: if the two disagree the
 * surface was built from a different hidden set, and painting the range anyway
 * would highlight a DIFFERENT patch than the one selected — a wrong picture
 * that looks entirely plausible.
 */

import * as THREE from 'three'
import { PATCH_HIGHLIGHT_COLOR } from './patchListDom'

export interface TrianglePatch {
  name: string
  nTriangles: number
}

export interface TriangleRange {
  /** First triangle of the patch, counting from the start of the index array. */
  firstTriangle: number
  nTriangles: number
}

/**
 * Where each drawn patch sits in the filtered index array.
 *
 * Hidden patches are absent from the map, not zero-length: they contribute no
 * triangles at all, and an entry for them would invite a caller to slice an
 * empty range out of the middle of another patch's triangles.
 */
export function patchTriangleRanges(
  patches: readonly TrianglePatch[],
  hidden: ReadonlySet<string>,
): Map<string, TriangleRange> {
  const out = new Map<string, TriangleRange>()
  let cursor = 0
  for (const p of patches) {
    if (hidden.has(p.name)) continue
    out.set(p.name, { firstTriangle: cursor, nTriangles: p.nTriangles })
    cursor += p.nTriangles
  }
  return out
}

/** Total triangles the surface draws — what `geometry.index.count / 3` must be. */
export function visibleTriangleTotal(
  patches: readonly TrianglePatch[],
  hidden: ReadonlySet<string>,
): number {
  let n = 0
  for (const p of patches) if (!hidden.has(p.name)) n += p.nTriangles
  return n
}

/**
 * Which patch a raycast hit belongs to.
 *
 * `THREE.Intersection.faceIndex` on an indexed geometry counts triangles from
 * the start of the index array, which is what `patchTriangleRanges` measures.
 */
export function patchAtTriangle(
  patches: readonly TrianglePatch[],
  hidden: ReadonlySet<string>,
  faceIndex: number,
): string | null {
  if (!Number.isInteger(faceIndex) || faceIndex < 0) return null
  let cursor = 0
  for (const p of patches) {
    if (hidden.has(p.name)) continue
    if (faceIndex < cursor + p.nTriangles) return p.name
    cursor += p.nTriangles
  }
  return null
}

/** The index slice covering a set of patches, in ascending order. */
export function selectionIndices(
  index: ArrayLike<number>,
  ranges: Map<string, TriangleRange>,
  names: Iterable<string>,
): Uint32Array {
  const picked: TriangleRange[] = []
  for (const n of names) {
    const r = ranges.get(n)
    if (r) picked.push(r)
  }
  picked.sort((a, b) => a.firstTriangle - b.firstTriangle)
  let total = 0
  for (const r of picked) total += r.nTriangles * 3
  const out = new Uint32Array(total)
  let w = 0
  for (const r of picked) {
    const from = r.firstTriangle * 3
    const to = from + r.nTriangles * 3
    for (let i = from; i < to; i++) out[w++] = index[i] as number
  }
  return out
}

interface HighlightUserData {
  _origPatchColor?: number
  _origPatchEmissive?: number
  _origPatchEmissiveIntensity?: number
}

/**
 * Paint one patch mesh, or put its own colour back.
 *
 * A material swap, not an emissive tint: the legacy tried the tint first and it
 * was invisible on a blue-based MeshPhong surface, which is the comment left at
 * STL_generate.html:24635. The emissive bump on top keeps the highlight lit
 * under any lighting rig.
 */
export function setPatchHighlight(object: THREE.Object3D | null | undefined, on: boolean) {
  const mesh = object as THREE.Mesh | null | undefined
  const material = mesh?.material as THREE.MeshPhongMaterial | undefined
  if (!material?.color) return
  const data = mesh!.userData as HighlightUserData
  if (on) {
    if (data._origPatchColor === undefined) {
      data._origPatchColor = material.color.getHex()
      if (material.emissive) {
        data._origPatchEmissive = material.emissive.getHex()
        data._origPatchEmissiveIntensity = material.emissiveIntensity ?? 1
      }
    }
    material.color.setHex(PATCH_HIGHLIGHT_COLOR)
    if (material.emissive) {
      material.emissive.setHex(PATCH_HIGHLIGHT_COLOR)
      material.emissiveIntensity = 0.35
    }
  } else {
    // Never highlighted: restoring here would paint it the shader's default.
    if (data._origPatchColor === undefined) return
    material.color.setHex(data._origPatchColor)
    if (material.emissive && data._origPatchEmissive !== undefined) {
      material.emissive.setHex(data._origPatchEmissive)
      material.emissiveIntensity = data._origPatchEmissiveIntensity ?? 1
    }
    data._origPatchColor = undefined
    data._origPatchEmissive = undefined
  }
  material.needsUpdate = true
}

interface EdgeUserData {
  _origEdgeColor?: number
}

export function setEdgeHighlight(object: THREE.Object3D | null | undefined, on: boolean) {
  const lines = object as THREE.LineSegments | null | undefined
  const material = lines?.material as THREE.LineBasicMaterial | undefined
  if (!material?.color) return
  const data = lines!.userData as EdgeUserData
  if (on) {
    if (data._origEdgeColor === undefined) data._origEdgeColor = material.color.getHex()
    material.color.setHex(PATCH_HIGHLIGHT_COLOR)
  } else if (data._origEdgeColor !== undefined) {
    material.color.setHex(data._origEdgeColor)
    data._origEdgeColor = undefined
  }
  material.needsUpdate = true
}
