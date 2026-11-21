/**
 * The export-name prefix on one face.
 *
 * A box, ACU or CDU face names its solids `{face}_{region}` — `inlet_wall_1`,
 * `outlet_patch_2`. `prefixEnabled` drops the `{face}_` half, which is how two
 * faces are made to export under the SAME name and merge into one OpenFOAM
 * patch, or how a face whose display name already carries the meaning stops
 * repeating it.
 *
 * ## What this flag does and does not reach
 *
 * It is persisted per face (`.cfdproj` carries it; `rack_state.js:119` and the
 * V4 migration at STL_generate.html:3315 both default it to true), and the
 * legacy reads it in exactly one place: `composeFaceScopedExportName`
 * (STL_generate.html:4768), which feeds the face inspector's name preview and
 * the wall-region inspector's. The MERGED export path goes through
 * `composeModelScopedExportName` in `stl_naming.js`, which does not consult it.
 *
 * So this is a naming-preview control today in both apps. It is ported as the
 * legacy has it rather than "fixed" to reach the exporter: making the flag bite
 * would rename solids in every existing project that has it turned off, which
 * moves boundary conditions onto different surfaces.
 */

import { stlNaming } from '../legacy/stlNaming'
import * as store from '../projectStore'
import type { Construction } from '../schema/cfdproj'

/** Kinds whose faces carry a prefix at all (`toggleFacePrefix`, STL_generate.html:13597). */
const PREFIXABLE = new Set(['box', 'acu', 'cdu'])

export function facePrefixSupported(kind: unknown): boolean {
  return PREFIXABLE.has(String(kind ?? ''))
}

export interface FaceLike {
  displayName?: string
  label?: string
  prefixEnabled?: boolean
}

/** The face's own name component, sanitised for a solid name. */
export function facePrefixName(faceId: string, face: FaceLike): string {
  const naming = stlNaming()
  return naming.sanitizeName(naming.getFaceDisplayName(face) || faceId || 'face')
}

/**
 * The solid name a region of this face would export under.
 *
 * `composeFaceScopedExportName` (STL_generate.html:4768). The empty base name
 * falls back to `wall_{faceId}` there, which is what a face with no named
 * regions yet shows in the preview line.
 */
export function composeFaceScopedExportName(
  faceId: string,
  face: FaceLike,
  baseName: string,
): string {
  const cleanBase = stlNaming().sanitizeName(baseName || `wall_${faceId}`)
  if (!face.prefixEnabled) return cleanBase
  const prefix = facePrefixName(faceId, face)
  return prefix ? `${prefix}_${cleanBase}` : cleanBase
}

/** A face defaults to prefixed; only an explicit `false` turns it off. */
export function isFacePrefixEnabled(face: FaceLike | undefined): boolean {
  return face?.prefixEnabled !== false
}

interface Bag {
  [key: string]: unknown
}

export function toggleFacePrefix(uid: string, faceId: string, enabled: boolean) {
  store.edit('toggleFacePrefix', `${uid}/${faceId} prefix=${enabled}`, (draft) => {
    const hit = store.findConstruction(draft, uid)
    if (!hit) return
    // The guard is the legacy's: on a wall, floor or baffle the exporter omits
    // the face component entirely (`usesSyntheticSingleFaceExportName`), so a
    // prefix flag there would claim an effect it cannot have.
    if (!facePrefixSupported(hit.model.kind)) return
    const faces = (hit.model.geometry as Bag | undefined)?.faces as
      | Record<string, Bag>
      | undefined
    const face = faces?.[faceId]
    if (face) face.prefixEnabled = enabled
  })
}

/** The face record, or undefined — the panel needs it before it can render. */
export function faceOf(model: Construction, faceId: string): FaceLike | undefined {
  const faces = (model.geometry as Bag | undefined)?.faces as
    | Record<string, FaceLike>
    | undefined
  return faces?.[faceId]
}
