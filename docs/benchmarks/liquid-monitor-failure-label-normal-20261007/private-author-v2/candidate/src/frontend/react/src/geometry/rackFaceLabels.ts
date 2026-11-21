/**
 * What the React editor calls a rack's sides, U-slot sides, server faces and
 * blanking-panel sides: by the air they pass, never by the geometric key.
 *
 * front = 进风面 / Inlet, back = 出风面 / Outlet. The rule and the key table
 * live ONCE, in `shared/js/rack_state.js` (`rackFaceLabel`), which the legacy
 * editor calls too — this file only hands it the React translator. The side
 * keys (`front`/`back`) stay what the project stores and what every selection,
 * patch id and solid name uses; only the words on screen follow the role.
 *
 * Scoped to things MOUNTED IN A RACK. An ACU's or a box's `front` is a
 * geometric face with no airflow role, and the ViewCube's F/B are camera
 * presets; neither goes through here.
 */

import { rackState } from '../legacy/cfdprojModules'
import { t, type TranslationKey } from '../i18n'

const translate = (key: string) => t(key as TranslationKey)

/** 'front' -> 'inlet', 'back' -> 'outlet'; a role or 'top' comes back as it is. */
export function rackFaceRole(faceId: string): string {
  return rackState().rackFaceRole(faceId)
}

/** The translated name: 进风面 (Inlet) / Outlet / 顶面 (Top)… */
export function rackFaceLabel(faceId: string): string {
  return rackState().rackFaceLabel(faceId, translate)
}

/** The colour picker's label for an inlet or outlet face (a side or its role). */
export function rackFaceColorLabel(faceId: string): string {
  const key = rackState().rackFaceColorLabelKey(faceId)
  return key ? translate(key) : rackFaceLabel(faceId)
}
