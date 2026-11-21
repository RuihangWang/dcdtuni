/**
 * STL Preview: the pure half.
 *
 * The preview shows what the STL WRITER will emit, not what the modelling view
 * draws. Those two are different pictures of the same project: a rack's twenty
 * frame panels render as twenty meshes and export as one `{rack}_frame` solid,
 * a wall split by patches renders as one wall and exports as several parts, and
 * a floor's remaining area exports under a name no modelling mesh carries. So
 * the preview is built by running the exporter, parsing the ASCII STL back, and
 * drawing ONE mesh per `solid` block — which is the only way to see the solid
 * names, the region split and the triangle counts before a mesh run turns them
 * into OpenFOAM patches.
 *
 * Everything here is a function of its arguments so it can be tested without a
 * scene: the STL parser, the region colour, and the visibility rules. The live
 * store and the exporter calls are in `stlPreview.ts`.
 */

import * as THREE from 'three'

/** One `solid <name>` block of the exported STL. */
export interface PreviewRegion {
  /** The solid name — this string becomes the OpenFOAM patch name. */
  name: string
  geometry: THREE.BufferGeometry
  triangleCount: number
  /** Preview-only tint. Never written to the STL. */
  color: string
}

export interface PreviewEntry {
  modelId: string
  modelName: string
  kind: string
  stlText: string
  regions: PreviewRegion[]
  triangleCount: number
}

/**
 * Per-model and per-region preview overrides.
 *
 * Handed to the shared geometry module as `uiState.previewVisibility` /
 * `previewRegionVisibility` / `previewRegionReverse`, which MUTATES it
 * (`ensurePreviewVisibilityState`, geometry_scene.js:806) and reads
 * `regionReverse` back out when it writes the STL. The reverse flags therefore
 * reach the exported bytes, not just the preview: flipping one here flips that
 * solid's facet winding in every export path.
 */
export interface PreviewFlags {
  visibility: Record<string, boolean>
  regionVisibility: Record<string, Record<string, boolean>>
  regionReverse: Record<string, Record<string, boolean>>
}

export function emptyFlags(): PreviewFlags {
  return { visibility: {}, regionVisibility: {}, regionReverse: {} }
}

/**
 * Split an ASCII STL into one geometry per `solid` block.
 *
 * Ported from `parseAsciiStlRegions` (STL_generate.html:4403), including its
 * two rejections: a NUL byte means the text is really a binary STL, and a file
 * with no `facet normal` / `endsolid` is not one this can split. Both return
 * an empty list rather than throwing, because a model whose exporter produced
 * nothing must not take the whole preview down with it.
 *
 * Vertices are read straight out of the text rather than through STLLoader:
 * the point of this view is to show exactly what was WRITTEN, so a
 * loader-normalised copy would hide the very defects it exists to catch.
 */
export function parseAsciiStlRegions(
  stlText: string,
): { name: string; geometry: THREE.BufferGeometry }[] {
  if (!stlText || stlText.includes('\0')) return []
  if (!/facet\s+normal/i.test(stlText) || !/endsolid/i.test(stlText)) return []

  const lines = stlText.split(/\r?\n/)
  const regions: { name: string; vertices: number[] }[] = []
  let current: { name: string; vertices: number[] } | null = null

  for (const raw of lines) {
    const line = raw.trim()
    if (!line) continue

    const solidMatch = line.match(/^solid\s*(.*)$/i)
    if (solidMatch) {
      const name = (solidMatch[1] || '').trim() || `solid_${regions.length + 1}`
      current = { name, vertices: [] }
      regions.push(current)
      continue
    }
    if (/^endsolid\b/i.test(line)) {
      current = null
      continue
    }
    if (!current) continue

    const vertex = line.match(/^vertex\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)$/i)
    if (vertex) {
      current.vertices.push(
        parseFloat(vertex[1]!),
        parseFloat(vertex[2]!),
        parseFloat(vertex[3]!),
      )
    }
  }

  return regions
    .filter((region) => region.vertices.length >= 9)
    .map((region) => {
      const geometry = new THREE.BufferGeometry()
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(region.vertices, 3))
      geometry.computeVertexNormals()
      geometry.computeBoundingBox()
      return { name: region.name, geometry }
    })
}

/** FNV-1a, as `hashPreviewKey` (STL_generate.html:3829). */
export function hashPreviewKey(value: string | null | undefined): number {
  let hash = 2166136261
  const text = String(value || '')
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

/**
 * A region's preview tint, derived from its name.
 *
 * Derived rather than assigned round-robin so the same solid keeps the same
 * colour across a regenerate: a colour that moves when the preview is rebuilt
 * makes "did this region change?" unanswerable by eye.
 */
export function previewRegionColor(modelId: string, regionName: string, index = 0): string {
  const hash = hashPreviewKey(`${modelId}:${regionName}:${index}`)
  const hue = (hash % 360) / 360
  const saturation = 0.58 + ((hash >> 8) % 18) / 100
  const lightness = 0.46 + ((hash >> 16) % 16) / 100
  return `#${new THREE.Color().setHSL(hue, saturation, lightness).getHexString()}`
}

/**
 * Whether the model is shown at all.
 *
 * `enabled` is the model's own `meta.enabled`: an override is only ever stored
 * once the user touches a checkbox, and until then the preview follows the
 * modelling view rather than showing objects the user has hidden.
 */
export function isPreviewModelVisible(flags: PreviewFlags, uid: string, enabled: boolean): boolean {
  const stored = flags.visibility[uid]
  return typeof stored === 'boolean' ? stored : enabled
}

export function isPreviewRegionVisible(
  flags: PreviewFlags,
  uid: string,
  regionName: string,
): boolean {
  const stored = flags.regionVisibility[uid]?.[regionName]
  if (typeof stored === 'boolean') return stored
  // A model hidden as a whole hides regions that have no override of their own.
  if (flags.visibility[uid] === false) return false
  return true
}

export function isPreviewRegionReversed(
  flags: PreviewFlags,
  uid: string,
  regionName: string,
): boolean {
  return !!flags.regionReverse[uid]?.[regionName]
}

export function anyRegionVisible(
  flags: PreviewFlags,
  uid: string,
  regionNames: string[],
  enabled: boolean,
): boolean {
  if (!regionNames.length) return isPreviewModelVisible(flags, uid, enabled)
  return regionNames.some((name) => isPreviewRegionVisible(flags, uid, name))
}

export function allRegionsVisible(
  flags: PreviewFlags,
  uid: string,
  regionNames: string[],
  enabled: boolean,
): boolean {
  if (!regionNames.length) return isPreviewModelVisible(flags, uid, enabled)
  return regionNames.every((name) => isPreviewRegionVisible(flags, uid, name))
}

/** Show or hide a model AND every one of its regions, as the tree checkbox does. */
export function setModelVisible(
  flags: PreviewFlags,
  uid: string,
  regionNames: string[],
  checked: boolean,
): void {
  flags.visibility[uid] = checked
  const map = (flags.regionVisibility[uid] ??= {})
  for (const name of regionNames) map[name] = checked
}

/**
 * Show or hide ONE region.
 *
 * The model-level flag is re-derived from the regions afterwards, so the
 * model's own checkbox reads "all of them" rather than drifting out of step
 * with the boxes below it (`togglePreviewRegionVisible`, STL_generate.html:11618).
 *
 * ## The one place this does NOT copy the legacy
 *
 * The legacy writes the single override, then sets the model flag to
 * `allRegionsVisible`. Its `isPreviewRegionVisible` falls back to that model
 * flag for any region with no override of its own — so unticking ONE region
 * drops the model flag to false, which silently hides every OTHER region, and
 * `isAnyPreviewRegionVisible` then reports false and `doRebuildStlPreview`
 * skips the whole model. Hiding one solid makes the object disappear.
 *
 * Every region's CURRENT visibility is therefore materialised into the
 * override map first. The stored shape and the predicates are unchanged — a
 * blob written here still reads correctly in the legacy — but no region is
 * left depending on a model flag that is about to flip under it.
 */
export function setRegionVisible(
  flags: PreviewFlags,
  uid: string,
  regionName: string,
  checked: boolean,
  regionNames: string[],
  enabled: boolean,
): void {
  const map = (flags.regionVisibility[uid] ??= {})
  for (const name of regionNames) {
    if (typeof map[name] !== 'boolean') map[name] = isPreviewRegionVisible(flags, uid, name)
  }
  map[regionName] = checked
  flags.visibility[uid] = allRegionsVisible(flags, uid, regionNames, enabled)
}

export function setRegionReversed(
  flags: PreviewFlags,
  uid: string,
  regionName: string,
  checked: boolean,
): void {
  ;(flags.regionReverse[uid] ??= {})[regionName] = checked
}

/**
 * Seed the preview from the modelling view, on entry to the mode.
 *
 * Every per-region override is dropped: they were made against the PREVIOUS
 * preview, whose region names may no longer exist after an edit, and a stale
 * override on a renamed solid hides a region the user never hid.
 */
export function syncFlagsFromModels(
  flags: PreviewFlags,
  models: { uid: string; enabled: boolean }[],
): void {
  for (const model of models) {
    flags.visibility[model.uid] = model.enabled
    flags.regionVisibility[model.uid] = {}
  }
}

/** Totals for the preview root panel. */
export function previewSummary(entries: PreviewEntry[]): {
  regions: number
  triangles: number
} {
  return {
    regions: entries.reduce((sum, e) => sum + e.regions.length, 0),
    triangles: entries.reduce((sum, e) => sum + e.triangleCount, 0),
  }
}
