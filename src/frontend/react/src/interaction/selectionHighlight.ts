/**
 * Which meshes light up for the current selection.
 *
 * A plain click selects one FACE (or one patch, one server, one slot), and
 * only shift promotes it to the whole model — that is the editor's whole
 * selection grammar, and the highlight is how the user sees which of the two
 * they got. The viewport used to tint every mesh of the selected MODEL
 * whatever the selection was, so a face click lit the entire cabinet and the
 * grammar was invisible: measured in the browser, `getSelection()` said
 * `{type:'face', faceId:'right'}` while the screenshot showed the whole rack
 * green.
 *
 * The legacy decides per mesh from the selection's type
 * (`_updateSelectionHighlight`, STL_generate.html:6510-6570), and this is that
 * table. It is a pure predicate over the mesh's `userData` so the Viewport
 * effect stays a loop.
 */

import type * as THREE from 'three'
import type { TreeSelection } from './treeNodes'
import { reapplyViewFades, viewFadeOpacity, type ViewFades } from '../geometry/viewFades'

export interface HighlightSelection {
  /** The active model. */
  uid: string | null
  /** The narrow selection, or null for "the model". */
  node: TreeSelection | null
  /** Every selected model — more than one under ctrl/shift-click. */
  uids: ReadonlySet<string>
}

/** A server's U-range, for the slot rule. Null when unknown. */
export type EquipmentSpan = (equipmentId: string) => { startUnit: number; spanUnits: number } | null

export interface MeshData {
  modelId?: unknown
  faceId?: unknown
  refIds?: unknown
  regionName?: unknown
  wallIndex?: unknown
  isRackEquipment?: unknown
  equipmentId?: unknown
  isPatch?: unknown
  patchId?: unknown
  isRackSlot?: unknown
  slotIndex?: unknown
  side?: unknown
  isRackPartition?: unknown
  partitionIndex?: unknown
  [key: string]: unknown
}

export function isMeshHighlighted(
  data: MeshData,
  selection: HighlightSelection,
  equipmentSpan: EquipmentSpan,
): boolean {
  const modelId = typeof data.modelId === 'string' ? data.modelId : null
  if (!modelId) return false
  const node = selection.node

  // The whole model: every selected model lights up (multi-select included).
  if (!node || node.type === 'model') return selection.uids.has(modelId)

  // Everything narrower belongs to ONE model.
  if (modelId !== node.uid) return false

  switch (node.type) {
    case 'face':
      // `!equipmentId`: a server's face shares face ids with the rack's own.
      return data.faceId === node.faceId && !data.equipmentId
    case 'patch':
      return Array.isArray(data.refIds) && data.refIds.includes(node.patchId)
    case 'region':
      return data.regionName === node.regionName
    case 'wallRegion':
      return data.faceId === node.faceId && data.wallIndex === node.index
    case 'equipment':
      return data.isRackEquipment === true && data.equipmentId === node.equipmentId
    case 'serverFace':
      return (
        data.isRackEquipment === true &&
        data.equipmentId === node.equipmentId &&
        data.faceId === node.faceId &&
        !data.isPatch
      )
    case 'serverPatch':
      return (
        data.isRackEquipment === true &&
        data.equipmentId === node.equipmentId &&
        data.isPatch === true &&
        data.patchId === node.patchId
      )
    case 'partition':
      return data.isRackPartition === true && data.partitionIndex === node.index
    case 'rackSlot': {
      const indices = node.indices ?? (typeof node.index === 'number' ? [node.index] : [])
      if (data.isRackSlot === true) {
        return data.side === node.side && indices.includes(data.slotIndex as number)
      }
      // The server sitting in a selected slot lights with it (legacy :6558).
      if (data.isRackEquipment === true && typeof data.equipmentId === 'string') {
        const span = equipmentSpan(data.equipmentId)
        if (!span) return false
        const end = span.startUnit + span.spanUnits
        return indices.some((i) => i >= span.startUnit && i < end)
      }
      return false
    }
    case 'fitting':
      // A valve or junction is drawn by the liquid module, not by these meshes.
      return false
    default:
      return false
  }
}

/**
 * How a highlighted mesh is drawn.
 *
 * Inside a rack the meshes carry TEXTURES — the server front is a perforated
 * grille photo, a patch is a coloured rectangle on it — and an emissive tint
 * on top of a texture is invisible: a green vent lit green looks exactly like
 * a green vent. The legacy drops the maps and paints those meshes solid blue
 * (STL_generate.html:6617-6631); everything else keeps its own colour and
 * gets the emissive tint (:6632-6650).
 */
export type HighlightStyle = 'tint' | 'solid' | 'cabinet'

export function highlightStyle(node: TreeSelection | null): HighlightStyle {
  switch (node?.type) {
    case 'rackSlot':
    case 'equipment':
    case 'serverFace':
    case 'serverPatch':
      return 'solid'
    default:
      return 'tint'
  }
}

/** The legacy's solid highlight (:6626-6631). */
export const SOLID_HIGHLIGHT = { color: 0x0055ff, emissive: 0x0044dd, emissiveIntensity: 1.5, opacity: 0.9 }
/** The tint the React viewport has always used for a model. */
export const TINT_HIGHLIGHT = { emissive: 0x00ff88, active: 0.8, inactive: 0.35 }
/** Whole CDU: diffuse colour reads on side panels as well as the service door. */
export const CABINET_HIGHLIGHT = {
  color: 0x66ead0, emissive: 0x184d42, active: 0.45, inactive: 0.2, roughness: 0.62,
}

/** What `paintMeshHighlight` keeps on a mesh between a highlight and its end. */
interface HighlightUserData {
  /** The material the highlight replaced, put back when it ends. */
  _origMaterial?: THREE.Material | THREE.Material[]
  /** Whether that material was the mesh's OWN (`modelOpacity.ownMaterials`). */
  _origOwned?: boolean
  _opacityOwned?: boolean
  _highlightStyle?: HighlightStyle
}

/**
 * Paint one mesh for the selection, or put it back.
 *
 * A highlight is a material SWAP: the mesh gets a clone to tint (or paint solid
 * blue, `highlightStyle`) and the original is kept to restore — never tinted in
 * place, because the builders cache materials by kind and one write would light
 * every model sharing it.
 *
 * `refade` re-applies whatever view fade is up — the mesh preview's or
 * post-processing's (`geometry/viewFades.ts`) — to the mesh just painted or
 * restored, and it is what makes the fade the last word. Without it, measured
 * on Tri_angle in the browser:
 *
 * - un-highlighting put back the material captured BEFORE the fade (a face
 *   selected on the geometry tab, then the mesh preview opened, then the
 *   selection moved on: that face came back at opacity 1 over the mesh);
 * - the solid highlight wrote opacity 0.9 over the preview's 30% and over
 *   post-processing's 15%.
 *
 * `opacityCap` is the opacity the fade shows the geometry at (1 with no fade).
 * The solid highlight never paints above it: a U-slot GUIDE keeps its own
 * opacity through the preview fade (`applyOpacityToMeshes`), so without the
 * cap a selected slot stayed a 0.9 blue slab over the mesh patch behind it.
 *
 * Ownership travels with the swap: the clone is this mesh's alone, so a fade
 * may write straight into it, while a restored builder-cached material is
 * shared — marked un-owned again so the next fade clones it rather than writing
 * through it into every mesh that shares it.
 */
export function paintMeshHighlight(
  mesh: THREE.Mesh,
  on: boolean,
  style: HighlightStyle,
  active: boolean,
  refade?: (mesh: THREE.Mesh) => void,
  opacityCap = 1,
): void {
  const mat = mesh.material
  const ud = mesh.userData as HighlightUserData
  const kept = ud._origMaterial
  const list = (materials: THREE.Material | THREE.Material[]) =>
    Array.isArray(materials) ? materials : [materials]
  const dispose = (materials: THREE.Material | THREE.Material[]) =>
    list(materials).forEach(material => material.dispose())
  const restore = () => {
    if (!kept) return
    dispose(mat)
    mesh.material = kept
    delete ud._origMaterial
    delete ud._highlightStyle
    delete mesh.userData._selectionViewFades
    if (ud._origOwned) ud._opacityOwned = true
    else delete ud._opacityOwned
    delete ud._origOwned
    refade?.(mesh)
  }
  if (!on) {
    restore()
    return
  }
  // Only cabinet contrast adds array support. Generic tint/solid retain their
  // previous policy, including when a cabinet array is demoted to a face.
  if (Array.isArray(mat) && style !== 'cabinet') {
    restore()
    return
  }
  const paintable = (material: THREE.Material) => style === 'cabinet'
    ? Boolean((material as THREE.MeshStandardMaterial).color)
    : Boolean((material as THREE.MeshStandardMaterial).emissive)
  if (!list(mat).some(paintable)) {
    // A Basic material can take the whole-CDU colour, but ordinary face tint
    // remains its existing emissive-only policy. Restore when demoting.
    restore()
    return
  }
  if (!kept) {
    ud._origMaterial = mat
    ud._origOwned = ud._opacityOwned === true
  }
  // A whole-cabinet paint removes maps. Promoting/demoting the selection must
  // start from the retained PBR material, not from that already stripped clone.
  if (!kept || ud._highlightStyle !== style) {
    const opacity = list(mat).map(material => ({
      opacity: material.opacity, transparent: material.transparent,
      depthWrite: material.depthWrite, forceSinglePass: material.forceSinglePass,
    }))
    if (kept) dispose(mat)
    const original = ud._origMaterial!
    mesh.material = Array.isArray(original)
      ? original.map(material => material.clone()) : original.clone()
    list(mesh.material).forEach((material, index) => {
      const live = opacity[index]
      if (live) Object.assign(material, live)
    })
    ud._opacityOwned = true
    ud._highlightStyle = style
  }
  for (const material of list(mesh.material)) {
    const m = material as THREE.MeshStandardMaterial
    if (!paintable(material)) continue
    if (style === 'cabinet') {
      m.map = null
      m.normalMap = null
      m.roughnessMap = null
      m.metalnessMap = null
      m.emissiveMap = null
      m.aoMap = null
      m.lightMap = null
      m.bumpMap = null
      m.color.setHex(CABINET_HIGHLIGHT.color)
      if (m.emissive) {
        m.emissive.setHex(CABINET_HIGHLIGHT.emissive)
        m.emissiveIntensity = active ? CABINET_HIGHLIGHT.active : CABINET_HIGHLIGHT.inactive
      }
      if ('metalness' in m) m.metalness = 0
      if ('roughness' in m) m.roughness = CABINET_HIGHLIGHT.roughness
      if ('envMapIntensity' in m) m.envMapIntensity = 0
      // Opacity, alpha masks, side and depth policy remain the actual model's.
      m.needsUpdate = true
    } else if (style === 'solid') {
      m.map = null
      m.normalMap = null
      m.color.setHex(SOLID_HIGHLIGHT.color)
      m.emissive.setHex(SOLID_HIGHLIGHT.emissive)
      m.emissiveIntensity = SOLID_HIGHLIGHT.emissiveIntensity
      m.opacity = Math.min(SOLID_HIGHLIGHT.opacity, opacityCap)
      m.transparent = true
      m.needsUpdate = true
    } else {
      m.emissive.setHex(TINT_HIGHLIGHT.emissive)
      m.emissiveIntensity = active ? TINT_HIGHLIGHT.active : TINT_HIGHLIGHT.inactive
    }
  }
  refade?.(mesh)
}

/**
 * Paint the whole scene for a selection, under whatever view fade is up.
 *
 * The viewport's highlight pass, out of the effect so it has ONE body for its
 * two callers: the effect (the selection or the scene changed) and the fade
 * controller (a fade landed — `createViewFadeController`). The second is the
 * one that was missing: a slot highlighted on the geometry tab stayed a 0.9
 * blue slab over the mesh when the preview fade landed afterwards, because
 * nothing re-ran the highlight with the new cap.
 *
 * Every selected model is tinted, the ACTIVE one brighter; which MESHES of it
 * light up is the narrow selection's business (`isMeshHighlighted`).
 */
export function paintSelectionHighlights(
  roots: Iterable<readonly [string, THREE.Object3D]>,
  selection: HighlightSelection,
  equipmentSpan: (uid: string) => EquipmentSpan,
  fades: ViewFades,
  modelKind: (uid: string) => string | undefined = () => undefined,
): void {
  const refade = (mesh: THREE.Mesh) => reapplyViewFades([mesh], fades)
  const cap = viewFadeOpacity(fades)
  for (const [uid, root] of roots) {
    const wholeModel = !selection.node || selection.node.type === 'model'
    const powerCabinet = root.userData.equipmentVisualRole === 'pdu' ||
      root.userData.equipmentVisualRole === 'ups'
    const powerFace = powerCabinet &&
      selection.node?.type === 'face' && selection.node.uid === uid
    const readableCabinet = wholeModel && (modelKind(uid) === 'cdu' || powerCabinet)
    const style: HighlightStyle = readableCabinet || powerFace
      ? 'cabinet' : highlightStyle(selection.node)
    const active = uid === selection.uid
    const span = equipmentSpan(uid)
    root.traverse((o) => {
      const mesh = o as THREE.Mesh
      if (!mesh.isMesh) return
      const on = isMeshHighlighted(mesh.userData as MeshData, selection, span)
      // Snapshot authoring uses the retained source material, with the same
      // non-selection fade that this painter applies after its temporary tint.
      if (on) mesh.userData._selectionViewFades = { ...fades }
      else delete mesh.userData._selectionViewFades
      const surfaceStyle = style === 'cabinet' &&
        !(mesh.userData.exportName || mesh.userData.liquidVisualDetail || mesh.userData.equipmentVisualDetail)
        ? highlightStyle(selection.node) : style
      paintMeshHighlight(mesh, on, surfaceStyle, active, refade, cap)
    })
  }
}
