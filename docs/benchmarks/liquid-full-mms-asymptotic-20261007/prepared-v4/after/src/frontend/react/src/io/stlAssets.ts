/**
 * Parsed STL assets for `custom` models.
 *
 * A custom model carries its mesh INSIDE the project as base64
 * (`customMesh.stlBase64`), and the scene builder draws it only when a parsed
 * entry for that model's uid is in the geometry cache — otherwise it falls
 * through to the box renderer. So a project with imported geometry opens as a
 * room full of grey boxes, silently, and exports as boxes too. Filling that
 * cache on load is what this module is for.
 *
 * `parseAsciiStlRegions` is the other half. A multi-solid ASCII STL is how this
 * repo names OpenFOAM patches — one `solid <name>` block per boundary — so the
 * region list is not decoration: it is the patch table the export depends on.
 * Binary STLs cannot carry names, hence the early-out on a NUL byte.
 */

import * as THREE from 'three'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import type { CfdProject, Construction } from '../schema/cfdproj'
import { buildCustomModel, geometryCache } from '../geometry/customMesh'
import * as store from '../projectStore'

export interface StlRegion {
  name: string
  geometry: THREE.BufferGeometry
}

export interface StlAsset {
  baseGeometry: THREE.BufferGeometry
  regions: StlRegion[]
}

/** One entry per `solid` block, in file order. Empty for a binary STL. */
export function parseAsciiStlRegions(stlText: string): StlRegion[] {
  if (!stlText || stlText.includes('\0')) return []
  if (!/facet\s+normal/i.test(stlText) || !/endsolid/i.test(stlText)) return []

  const regions: { name: string; vertices: number[] }[] = []
  let current: { name: string; vertices: number[] } | null = null

  for (const raw of stlText.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue

    const solid = line.match(/^solid\s*(.*)$/i)
    if (solid) {
      const name = (solid[1] ?? '').trim() || `solid_${regions.length + 1}`
      current = { name, vertices: [] }
      regions.push(current)
      continue
    }
    if (/^endsolid\b/i.test(line)) {
      current = null
      continue
    }
    if (!current) continue

    const vertex = line.match(/^vertex\s+(\S+)\s+(\S+)\s+(\S+)$/i)
    if (vertex) {
      current.vertices.push(
        parseFloat(vertex[1]!),
        parseFloat(vertex[2]!),
        parseFloat(vertex[3]!),
      )
    }
  }

  return regions
    // Three vertices is the smallest thing that is a triangle; anything less is
    // a stray `solid` header, not a region.
    .filter((r) => r.vertices.length >= 9)
    .map((r) => {
      const geometry = new THREE.BufferGeometry()
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(r.vertices, 3))
      geometry.computeVertexNormals()
      geometry.computeBoundingBox()
      return { name: r.name, geometry }
    })
}

export function parseStlAsset(buffer: ArrayBuffer, stlText = ''): StlAsset {
  const baseGeometry = new STLLoader().parse(buffer)
  baseGeometry.computeBoundingBox()
  baseGeometry.computeVertexNormals()
  return { baseGeometry, regions: parseAsciiStlRegions(stlText) }
}

export function bufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  // Chunked: `String.fromCharCode(...bytes)` on a multi-megabyte STL blows the
  // argument limit and throws RangeError.
  let binary = ''
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK))
  }
  return btoa(binary)
}

export function base64ToBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes.buffer
}

type CustomModel = Construction & {
  customMesh?: { stlBase64?: string | null; offset?: { x: number; y: number; z: number } }
  customSTLBase64?: string | null
}

/** The stored STL for a model, from either the V4 home or the flat legacy one. */
export function stlOf(model: Construction): string | null {
  const m = model as CustomModel
  return m.customMesh?.stlBase64 ?? m.customSTLBase64 ?? null
}

/**
 * Re-parse every custom model's STL into the cache, reusing what is already
 * there.
 *
 * Keyed by uid AND checked against the stored base64, so an undo that restores
 * the same mesh reuses the parse (megabytes of triangles) while a model whose
 * STL was replaced is re-read.
 */
export function restoreGeometryCache(
  project: CfdProject | null,
  cache: Map<string, unknown>,
  models: Construction[],
) {
  if (!project) return
  const previous = new Map(cache)
  const seen = new Set<string>()
  for (const model of models) {
    if (model.kind !== 'custom') continue
    const stl = stlOf(model)
    if (!stl) continue
    seen.add(model.uid)
    const existing = previous.get(model.uid) as (StlAsset & { _stl?: string }) | undefined
    if (existing && existing._stl === stl) {
      cache.set(model.uid, existing)
      continue
    }
    try {
      const buffer = base64ToBuffer(stl)
      const text = new TextDecoder().decode(new Uint8Array(buffer))
      const asset = parseStlAsset(buffer, text) as StlAsset & { _stl?: string }
      asset._stl = stl
      cache.set(model.uid, asset)
    } catch (err) {
      // A model whose STL will not parse must not take the whole scene with
      // it — it falls back to the box renderer, which is what it would have
      // done anyway before this cache existed.
      console.error('[stl] failed to parse stored mesh for', model.uid, err)
    }
  }
  for (const uid of [...cache.keys()]) {
    if (!seen.has(uid)) cache.delete(uid)
  }
}

/**
 * Put an already-parsed asset into the cache under a model's uid.
 *
 * The `_stl` marker is what `restoreGeometryCache` compares against, so seeding
 * without it means the very next rebuild re-parses megabytes of triangles that
 * were just read from the file the user picked.
 */
export function cacheStlAsset(
  cache: Map<string, unknown>,
  uid: string,
  asset: StlAsset,
  stlBase64: string,
) {
  ;(asset as StlAsset & { _stl?: string })._stl = stlBase64
  cache.set(uid, asset)
}

export interface ImportedStl {
  name: string
  base: { W: number; D: number; H: number }
  customOffset: { x: number; y: number; z: number }
  customSTLBase64: string
  solidRegions: string[]
  asset: StlAsset
}

/**
 * Read a picked .stl file into the fields a `custom` model needs.
 *
 * `customOffset` is the NEGATED bounding-box minimum, so the mesh sits with its
 * corner on the model's own origin: without it a file authored far from the
 * origin places its model somewhere else entirely, and the anchor the user
 * drags has nothing to do with what they see.
 */
export async function readStlFile(file: File): Promise<ImportedStl> {
  const [buffer, text] = await Promise.all([file.arrayBuffer(), file.text()])
  const asset = parseStlAsset(buffer, text)
  const bb = asset.baseGeometry.boundingBox
  if (!bb) throw new Error('STL bounding box could not be computed')
  return {
    name: file.name.replace(/\.stl$/i, ''),
    base: { W: bb.max.x - bb.min.x, D: bb.max.y - bb.min.y, H: bb.max.z - bb.min.z },
    customOffset: { x: -bb.min.x, y: -bb.min.y, z: -bb.min.z },
    customSTLBase64: bufferToBase64(buffer),
    solidRegions: asset.regions.map((r) => r.name),
    asset,
  }
}

/**
 * Read a picked .stl and add it to the project as a new `custom` model.
 *
 * The port of `window.importCustomSTL` (STL_generate.html:15349). It lives here
 * rather than beside the other model writers because the two halves of an
 * import are here: `readStlFile` above, and the geometry cache the scene
 * builder already reaches this module for. Seeding that cache is not an
 * optimisation only — the region list the inspector's per-region buttons are
 * built from comes out of it, and re-reading a 40 MB mesh to learn what was
 * just parsed is a visible stall between the file dialog closing and the model
 * appearing.
 *
 * The model is built by `customMesh.buildCustomModel`, which owns the seed's
 * exact legacy shape. Nothing here validates the STL beyond what the parse
 * threw: a file with no bounding box has no size to give the model, and the
 * caller shows the failure rather than adding a zero-sized box to the room.
 */
export async function importCustomStl(
  file: File,
): Promise<{ ok: boolean; uid?: string; error?: string }> {
  const project = store.getProject()
  if (!project) return { ok: false, error: 'no project loaded' }
  try {
    const imported = await readStlFile(file)
    const { bucket, model } = buildCustomModel(project, imported)
    const cache = geometryCache()
    if (cache) cacheStlAsset(cache, model.uid, imported.asset, imported.customSTLBase64)
    store.addModel(bucket, model)
    return { ok: true, uid: model.uid }
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
}

/**
 * The import writer on `window`, under the legacy app's own name.
 *
 * Same reason `patchActions.ts` publishes its own: the two editors have to be
 * drivable by one browser snippet so their output can be diffed, and
 * `window.importCustomSTL` is how the legacy exposes this.
 */
if (typeof window !== 'undefined') {
  ;(window as unknown as { __stlImport?: unknown }).__stlImport = {
    importCustomStl,
    readStlFile,
    parseAsciiStlRegions,
  }
}
