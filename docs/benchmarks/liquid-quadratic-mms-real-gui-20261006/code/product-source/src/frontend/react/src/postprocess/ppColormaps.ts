/**
 * The post-processing colour maps, as 256-entry lookup tables.
 *
 * A LUT rather than a stop list, because the colour is looked up PER FRAGMENT
 * in a shader: the geometry carries the raw scalar and the fragment shader
 * samples this texture. Interpolating baked RGB across a triangle instead
 * blends through hues that are in neither end colour — a coolwarm triangle
 * spanning blue to red passes through grey-purple, which reads as banding the
 * data does not have.
 *
 * Ported from `_ppColormapPresets` (STL_generate.html:19828). The three presets
 * are the three the legacy editor offers; the arithmetic is copied so a value
 * gets the same byte triple in both apps.
 */

import * as THREE from 'three'

export type ColorMap = 'coolwarm' | 'jet' | 'viridis'

/** The presets, in the order `pp-cmap-preset` lists them. */
export const COLOR_MAPS: { id: ColorMap; label: string }[] = [
  { id: 'coolwarm', label: 'Cool to Warm' },
  { id: 'jet', label: 'Jet' },
  { id: 'viridis', label: 'Viridis' },
]

/** 256 RGBA bytes for a preset. */
export function colormapData(preset: ColorMap): Uint8Array {
  const data = new Uint8Array(256 * 4)
  if (preset === 'jet') {
    for (let i = 0; i < 256; i++) {
      const t = i / 255
      const r = Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 3)))
      const g = Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 2)))
      const b = Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 1)))
      data[i * 4] = Math.round(r * 255)
      data[i * 4 + 1] = Math.round(g * 255)
      data[i * 4 + 2] = Math.round(b * 255)
      data[i * 4 + 3] = 255
    }
    return data
  }
  if (preset === 'viridis') {
    const stops: [number, number, number, number][] = [
      [0.0, 68, 1, 84],
      [0.25, 59, 82, 139],
      [0.5, 33, 145, 140],
      [0.75, 94, 201, 98],
      [1.0, 253, 231, 37],
    ]
    for (let i = 0; i < 256; i++) {
      const t = i / 255
      let s0 = stops[0] as [number, number, number, number]
      let s1 = stops[1] as [number, number, number, number]
      for (let j = 1; j < stops.length; j++) {
        if (t <= (stops[j] as [number, number, number, number])[0]) {
          s0 = stops[j - 1] as [number, number, number, number]
          s1 = stops[j] as [number, number, number, number]
          break
        }
      }
      const f = (t - s0[0]) / (s1[0] - s0[0] || 1)
      data[i * 4] = Math.round(s0[1] + f * (s1[1] - s0[1]))
      data[i * 4 + 1] = Math.round(s0[2] + f * (s1[2] - s0[2]))
      data[i * 4 + 2] = Math.round(s0[3] + f * (s1[3] - s0[3]))
      data[i * 4 + 3] = 255
    }
    return data
  }
  // coolwarm — two linear ramps meeting at white, the ParaView default.
  for (let i = 0; i < 256; i++) {
    const t = i / 255
    let r: number, g: number, b: number
    if (t < 0.5) {
      const s = t * 2
      r = Math.round(59 + s * (255 - 59))
      g = Math.round(76 + s * (255 - 76))
      b = Math.round(192 + s * (255 - 192))
    } else {
      const s = (t - 0.5) * 2
      r = Math.round(255 - s * (255 - 180))
      g = Math.round(255 - s * (255 - 4))
      b = Math.round(255 - s * (255 - 38))
    }
    data[i * 4] = r
    data[i * 4 + 1] = g
    data[i * 4 + 2] = b
    data[i * 4 + 3] = 255
  }
  return data
}

/**
 * A 256x1 texture for the shaders.
 *
 * Linear filtering and clamped wrapping: a value at the very top of the range
 * samples u=1.0, and with the default repeat wrapping that lands back on the
 * FIRST entry — the hottest cell in the room would render as the coldest
 * colour.
 */
export function createColormapTexture(preset: ColorMap): THREE.DataTexture {
  const tex = new THREE.DataTexture(colormapData(preset), 256, 1, THREE.RGBAFormat)
  tex.minFilter = THREE.LinearFilter
  tex.magFilter = THREE.LinearFilter
  tex.wrapS = THREE.ClampToEdgeWrapping
  tex.needsUpdate = true
  return tex
}

/**
 * One colour from a preset, as a CSS string.
 *
 * For the parts of the UI that are not the 3D scene — the colorbar gradient and
 * the panel swatches. It reads the SAME table the shader samples, so the legend
 * cannot drift from the picture.
 */
export function colormapCss(preset: ColorMap, t: number): string {
  const data = colormapData(preset)
  const i = Math.min(255, Math.max(0, Math.round((Number.isFinite(t) ? t : 0) * 255)))
  return `rgb(${data[i * 4]}, ${data[i * 4 + 1]}, ${data[i * 4 + 2]})`
}

/** A CSS `linear-gradient` across the whole map, for a colorbar. */
export function colormapGradient(preset: ColorMap, stops = 16): string {
  const parts: string[] = []
  for (let i = 0; i < stops; i++) {
    const t = i / (stops - 1)
    parts.push(`${colormapCss(preset, t)} ${Math.round(t * 100)}%`)
  }
  return `linear-gradient(to top, ${parts.join(', ')})`
}
