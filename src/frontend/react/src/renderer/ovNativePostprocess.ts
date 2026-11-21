/**
 * What the shared postprocess panel asks OV to show, in OV's own terms.
 *
 * Omniverse realises a result with kit-cae's native tools on the Kit host:
 * the server converts the selected run/time and signs a display grant, Kit
 * imports it (omni.cae), and then draws
 *   - the surface field with the Faces operator (the dataset's external faces),
 *   - the volume with NVIDIA IndeX,
 *   - one slice plane and one streamline seed sphere (GPU Warp integration).
 * The browser never ships computed geometry for these; it only says which run,
 * which time, which field, which colour domain and where the plane/seed sit.
 *
 * Domains are RAW units (K for temperature, m/s for speed), the units the
 * panel stores; the panel converts only for display.
 */
export type OvKitField = 'T' | 'U'

export interface OvNativeLayer {
  field: string; visible: boolean
  /** Raw-unit colour domain; null lets Kit use the dataset's own range. */
  range: [number, number] | null
  palette?: 'coolwarm' | 'jet' | 'viridis'
  /** Surface only: the patches the panel's patch list hides (the room shell by default). */
  hidden?: string[]
}
export interface OvNativeSlice extends OvNativeLayer {
  origin: [number, number, number]; normal: [number, number, number]; radius: number
  /** Show Kit's draggable plane disc (the panel's preview ring). */
  showDisc?: boolean
}
export interface OvNativeStream {
  field: string; visible: boolean
  centre: [number, number, number]; radius: number; nSeeds: number
  /** The set's colour bar: raw-unit domain and palette. */
  range?: [number, number] | null
  palette?: 'coolwarm' | 'jet' | 'viridis'
}
export interface OvNativeIntent {
  runId: string
  timeValue: number | null
  surface: OvNativeLayer
  volume: OvNativeLayer
  /** The first visible slice set; Kit shows one plane. */
  slice: OvNativeSlice | null
  /** The first visible streamline set; Kit traces from one seed sphere. */
  stream: OvNativeStream | null
  /** Panel sets that OV cannot also show (only the first slice/stream is native). */
  extraSlices: number
  extraStreams: number
}

/** The panel's field name in Kit's dataset, or null when Kit has no such field. */
export function ovKitField(field: string): OvKitField | null {
  if (field === 'T') return 'T'
  if (field === 'U' || field === 'speed' || field === 'Umag' || field === 'magU' || field === 'mag(U)') return 'U'
  return null
}

/** A colour domain Kit accepts (finite, ascending), or null for Kit's own. */
export function kitDomain(range: [number, number] | null | undefined): [number, number] | null {
  if (!range) return null
  const [lo, hi] = range
  return Number.isFinite(lo) && Number.isFinite(hi) && lo < hi ? [lo, hi] : null
}
