/**
 * Two post-processing choices the legacy offers and the port hard-wired.
 *
 * ## Streamline colouring
 *
 * `_ppPopulateFieldDropdown` fills every field select from the run's own field
 * list, then puts ONE extra entry at the front of the streamline one
 * (STL_generate.html:21292):
 *
 *     slFieldSel.innerHTML = '<option value="speed">Speed (|U|)</option>' + sel.innerHTML
 *
 * `speed` is not a field in the result. It is a name `/api/pp/streamlines`
 * understands, and it is the FIRST option, so it is what a streamline is
 * coloured by unless the user picks otherwise. That matters: the other options
 * are `U`, `Ux`, `Uy`, `Uz`, `T` — colouring a flow field by one Cartesian
 * component draws stripes across the room where the flow turns, and colouring
 * it by the vector `U` asks the shader to map three numbers onto one ramp.
 *
 * ## Volume resolution
 *
 * The radios at STL_generate.html:686-688, read back by `ppApplyVolume`
 * (`:20265`). 64 is the interactive setting on a room-scale grid, 256 the one
 * for a figure; 128 is `checked` in the markup and is the default here too.
 * The value reaches `/api/pp/volume` as the grid size per axis and then sets
 * the ray-marcher's step budget, so it is a cost the user has to be able to
 * choose — 256³ is eight times the memory of 128³.
 */

/** The synthetic streamline field: velocity magnitude, computed server-side. */
export const SPEED_FIELD = 'speed'
export const SPEED_LABEL = 'Speed (|U|)'

export interface FieldOption {
  value: string
  label: string
}

/**
 * The streamline colour dropdown: `speed` first, then the run's own fields.
 *
 * A field the run happens to call `speed` is not listed twice — the synthetic
 * entry wins, because the two would be indistinguishable in the list and the
 * duplicate `value` would make the select's own state ambiguous.
 */
export function streamlineFieldOptions(fields: readonly string[]): FieldOption[] {
  const out: FieldOption[] = [{ value: SPEED_FIELD, label: SPEED_LABEL }]
  for (const f of fields) {
    if (f === SPEED_FIELD) continue
    out.push({ value: f, label: f })
  }
  return out
}

/** The three grid sizes the legacy offers, in the order it renders them. */
export const VOLUME_RESOLUTIONS = [64, 128, 256] as const
export const DEFAULT_VOLUME_RESOLUTION = 128

/**
 * Snap a stored resolution onto one of the three radios.
 *
 * A project or a stale state can carry any number; a radio group that matches
 * none of its values renders with nothing selected, which reads as "no
 * resolution" rather than as the value actually in use.
 */
export function normaliseVolumeResolution(value: number | undefined): number {
  if (!Number.isFinite(value)) return DEFAULT_VOLUME_RESOLUTION
  let best: number = DEFAULT_VOLUME_RESOLUTION
  let bestGap = Infinity
  for (const r of VOLUME_RESOLUTIONS) {
    const gap = Math.abs(r - (value as number))
    if (gap < bestGap) {
      bestGap = gap
      best = r
    }
  }
  return best
}
