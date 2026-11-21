/** Colour ramps Kit actually renders with, mirrored for the browser legend.
 *
 * The OV display paints slices from `slice_render._RAMP` and the IndeX volume
 * from `scene._apply_thermal_colormap`; a legend drawn from any other table
 * would disagree with the picture. `test/ovColormaps.test.ts` parses both
 * Python sources and fails if these copies drift.
 */
export type Rgb = [number, number, number]
export type Ramp = readonly (readonly [number, Rgb])[]

export const OV_SLICE_RAMP: Ramp = [
  [0.0, [0.10, 0.25, 0.85]],
  [0.25, [0.10, 0.75, 0.85]],
  [0.5, [0.20, 0.80, 0.30]],
  [0.75, [0.95, 0.85, 0.15]],
  [1.0, [0.90, 0.15, 0.10]],
]

/** RGB of the IndeX volume transfer function (its alpha is not drawn: the
 * legend shows hue against value; opacity is a rendering choice). */
export const OV_VOLUME_RAMP: Ramp = [
  [0.0, [0.10, 0.22, 1.00]],
  [0.20, [0.00, 0.80, 1.00]],
  [0.45, [0.20, 0.90, 0.30]],
  [0.65, [1.00, 0.75, 0.05]],
  [0.82, [1.00, 0.35, 0.02]],
  [1.0, [0.85, 0.00, 0.08]],
]

/** ParaView coolwarm used for liquid result colours on both renderers
 * (ppColormaps.colormapData('coolwarm') / liquid_colour.coolwarm_lut). */
export const OV_LIQUID_RAMP: Ramp = [
  [0.0, [59 / 255, 76 / 255, 192 / 255]],
  [0.5, [1, 1, 1]],
  [1.0, [180 / 255, 4 / 255, 38 / 255]],
]

/** Piecewise-linear sample of a ramp at t in [0,1] (clamped; NaN -> grey,
 * matching `slice_render.scalar_to_rgb`). */
export function sampleRamp(ramp: Ramp, t: number): Rgb {
  if (!Number.isFinite(t)) return [0.5, 0.5, 0.5]
  const x = Math.min(1, Math.max(0, t))
  for (let i = 1; i < ramp.length; i++) {
    const [t1, c1] = ramp[i - 1]!
    const [t2, c2] = ramp[i]!
    if (x <= t2) {
      const f = t2 === t1 ? 0 : (x - t1) / (t2 - t1)
      return [0, 1, 2].map(k => c1[k]! + (c2[k]! - c1[k]!) * f) as Rgb
    }
  }
  return [...ramp[ramp.length - 1]![1]] as Rgb
}
