/**
 * A picture on a PATCH: a return grille style, a louvred supply outlet.
 *
 * Same storage as a face texture (`textureDataUrl` embedded / `textureUrl`
 * served) plus `texturePreset`, one of the presets the shared
 * js/patch_textures.js registry serves from /textures/grilles. The shared
 * geometry builder reads all three, so both editors draw the same thing.
 */

import '../../../shared/js/patch_textures.js'
import { updatePatch } from './patchPhysics'

export interface PatchTexturePreset {
  id: string
  roles: string[]
  url: string
  labelKey: string
}

interface Registry {
  presets: PatchTexturePreset[]
  byId(id: string): PatchTexturePreset | null
  forRole(role: string | null): PatchTexturePreset[]
  sourceFor(patch: unknown): string | null
}

function registry(): Registry {
  return (window as unknown as { patchTexturePresets: Registry }).patchTexturePresets
}

export function patchTexturePresets(role: string | null): PatchTexturePreset[] {
  return registry().forRole(role)
}

export function patchTextureSource(patch: unknown): string | null {
  return registry().sourceFor(patch)
}

export function patchTexturePresetId(patch: { texturePreset?: unknown } | null | undefined): string {
  return typeof patch?.texturePreset === 'string' ? patch.texturePreset : ''
}

/** Pick a preset ('' clears). A custom picture is dropped: the preset now shows. */
export function setPatchTexturePreset(uid: string, patchId: string, presetId: string) {
  updatePatch(uid, patchId, 'texturePreset', presetId || undefined)
  updatePatch(uid, patchId, 'textureDataUrl', undefined)
  updatePatch(uid, patchId, 'textureUrl', undefined)
}

/** A picture from disk (data URL) or a served path. */
export function setPatchTexture(uid: string, patchId: string, src: string) {
  const isData = src.startsWith('data:')
  updatePatch(uid, patchId, isData ? 'textureDataUrl' : 'textureUrl', src)
  updatePatch(uid, patchId, isData ? 'textureUrl' : 'textureDataUrl', undefined)
  updatePatch(uid, patchId, 'texturePreset', undefined)
}

export function clearPatchTexture(uid: string, patchId: string) {
  updatePatch(uid, patchId, 'textureDataUrl', undefined)
  updatePatch(uid, patchId, 'textureUrl', undefined)
  updatePatch(uid, patchId, 'texturePreset', undefined)
}
