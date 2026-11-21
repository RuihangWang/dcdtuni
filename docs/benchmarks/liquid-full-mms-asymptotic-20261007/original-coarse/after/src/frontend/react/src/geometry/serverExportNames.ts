/**
 * The STL solid names a rack server and its faces export as.
 *
 * Shown read-only in the panels, and read-only for a reason: the name is
 * DERIVED from the rack name, the server name, the face and the patch index,
 * and it IS the OpenFOAM patch name. Recomputing it here rather than storing it
 * is what stops a boundary condition from ending up on a surface nobody meant.
 *
 * The functions live in the SHARED modules — `getServerFaceExportName` in
 * `js/geometry_pure.js:446`, `getRackEquipmentExportStem` in
 * `js/stl_naming.js:128` — so the panel and the exporter can never disagree.
 * The earlier port looked `getServerFaceExportName` up on `window.stlNaming`,
 * which does not carry it; the optional call returned undefined and the panel
 * showed an empty box.
 *
 * Both take an equipment VIEW, not the raw V4 server: the naming code reads
 * `.name`, `.kind` and `.startUnit`, which on a V4 server live at `meta.name`,
 * `type` and `geometry.slotPosition`. `getRackServerViews` is the proxy that
 * maps them (js/rack_state.js:357).
 */

import type { Construction } from '../schema/cfdproj'
import { geometryPure } from '../legacy/geometryBuilder'
import { stlNaming } from '../legacy/stlNaming'

interface EquipmentView {
  id: string
  name?: string
  kind?: string
  startUnit?: number
  spanUnits?: number
}

interface NamingExtras {
  getRackServerViews(model: Construction): EquipmentView[]
  getRackEquipmentExportStem(model: Construction, equipment: EquipmentView): string
  sanitizeName(text: string | null | undefined): string
  /** "U33 ~ U34", or '' when the rack holds no such server (js/stl_naming.js). */
  getRackServerSlotRangeLabel(model: Construction, equipmentId: string): string
}

interface PureExtras {
  getServerFaceExportName(
    model: Construction,
    equipment: EquipmentView,
    faceId: string,
    opts?: { isPatch?: boolean; patchIndex?: number; isWall?: boolean },
  ): string
}

/** The proxy view of one server, or null when the rack does not hold it. */
export function serverView(model: Construction, equipmentId: string): EquipmentView | null {
  try {
    const naming = stlNaming() as unknown as NamingExtras
    return naming.getRackServerViews(model).find((v) => v.id === equipmentId) ?? null
  } catch {
    // The shared modules attach on import; without a window there is no name to
    // show, and an empty box is better than a panel that fails to render.
    return null
  }
}

export function serverFaceExportName(
  model: Construction,
  equipmentId: string,
  faceId: string,
  opts?: { isPatch?: boolean; patchIndex?: number; isWall?: boolean },
): string {
  const view = serverView(model, equipmentId)
  if (!view) return ''
  try {
    return (geometryPure() as unknown as PureExtras).getServerFaceExportName(
      model,
      view,
      faceId,
      opts,
    )
  } catch {
    return ''
  }
}

/**
 * The name the SERVER itself exports as — its front face.
 *
 * A fan-server's front is an `_inlet_patch` (a flow boundary); anything else
 * exports a plain `_front` wall. Ports `getRackEquipmentExportName`
 * (STL_generate.html:5992).
 */
export function serverEquipmentExportName(model: Construction, equipmentId: string): string {
  const view = serverView(model, equipmentId)
  if (!view) return ''
  try {
    const naming = stlNaming() as unknown as NamingExtras
    const stem = naming.getRackEquipmentExportStem(model, view)
    return view.kind === 'fanServer'
      ? naming.sanitizeName(`${stem}_inlet_patch`)
      : `${stem}_front`
  } catch {
    return ''
  }
}

/**
 * "U3 ~ U6" — the slot range a server covers, as the equipment panel states it.
 *
 * The label is the SHARED module's (`getRackServerSlotRangeLabel`), which the
 * legacy panels print too, so the two editors cannot word a range differently.
 * '' when the rack does not hold the server or its start slot is unknown.
 */
export function serverSlotRange(model: Construction, equipmentId: string): string {
  try {
    const naming = stlNaming() as unknown as NamingExtras
    return naming.getRackServerSlotRangeLabel(model, equipmentId) || ''
  } catch {
    return ''
  }
}
