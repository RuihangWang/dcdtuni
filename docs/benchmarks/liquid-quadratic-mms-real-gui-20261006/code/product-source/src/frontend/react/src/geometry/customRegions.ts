/**
 * How an imported STL's solids group the patches drawn on them.
 *
 * A multi-solid ASCII STL names one OpenFOAM boundary per `solid` block, so on
 * a `custom` model the REGION is the organising unit — not the six projection
 * faces every other kind uses. The feature tree hangs patches under regions and
 * the inspector adds them per region, so both need the same grouping; it lives
 * here, free of the store and of Three.js, so it can be tested against the
 * legacy's `getTreeHtmlForModel` custom branch (STL_generate.html:7907) without
 * loading a renderer.
 */

import type { Construction } from '../schema/cfdproj'

type AnyRecord = Record<string, unknown>

export interface RegionPatch {
  id: string
  name?: string
  color?: string
  type?: string
  regionName?: string | null
}

export interface RegionPatchEntry {
  faceId: string
  patch: RegionPatch
}

/** `null` is the WHOLE model, which the legacy keys as `__whole__`. */
export function customRegionKey(regionName: string | null | undefined): string {
  return regionName || '__whole__'
}

/**
 * The solid names an imported STL brought with it, in file order.
 *
 * V4 nests them under `customMesh`; the flat `solidRegions` is the pre-V4 home
 * and is still what a factory seed carries before the migrator runs.
 */
export function solidRegionsOf(model: Construction): string[] {
  const mesh = (model as unknown as AnyRecord).customMesh as AnyRecord | undefined
  const nested = mesh?.solidRegions
  if (Array.isArray(nested)) return nested as string[]
  const flat = (model as unknown as AnyRecord).solidRegions
  return Array.isArray(flat) ? (flat as string[]) : []
}

function facesOf(model: Construction): Record<string, { patches?: RegionPatch[] }> | null {
  const faces = (model.geometry as AnyRecord | undefined)?.faces
  if (!faces || Array.isArray(faces)) return null
  return faces as Record<string, { patches?: RegionPatch[] }>
}

/** Every patch on the model, across all six faces, in face order. */
export function allModelPatches(model: Construction): RegionPatchEntry[] {
  const faces = facesOf(model)
  if (!faces) return []
  return Object.entries(faces).flatMap(([faceId, face]) =>
    (face.patches ?? []).map((patch) => ({ faceId, patch })),
  )
}

/**
 * Region key → the patches mounted on it.
 *
 * A region with no patches still gets an entry: it is where the FIRST patch on
 * that solid is added from, so dropping empty ones would leave a freshly
 * imported mesh with nothing to click. A model whose STL carried no named
 * solids gets the single `__whole__` entry for the same reason. A patch naming
 * a region the mesh no longer has keeps its own entry rather than vanishing —
 * losing it would strand a boundary condition with no way back to it.
 */
export function customRegionGroups(model: Construction): Map<string, RegionPatchEntry[]> {
  const grouped = new Map<string, RegionPatchEntry[]>()
  for (const region of solidRegionsOf(model)) grouped.set(region, [])
  if (grouped.size === 0) grouped.set('__whole__', [])
  for (const entry of allModelPatches(model)) {
    const key = customRegionKey(entry.patch.regionName)
    const list = grouped.get(key)
    if (list) list.push(entry)
    else grouped.set(key, [entry])
  }
  return grouped
}
