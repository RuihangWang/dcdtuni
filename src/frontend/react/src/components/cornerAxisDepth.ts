/** Per-pixel camera-space depth for the existing Canvas HUD silhouettes.
 * Shafts interpolate the real origin-to-end depth; caps and their labels use
 * the real endpoint depth. The hub is at the origin, not a new 3D geometry.
 */
export type CornerAxis = { label: string; color: string; sign: number; sx: number; sy: number; depth: number }
export const CORNER_AXIS_RADIUS = 35
export const CORNER_AXIS_LABEL_HALF = 11
export type CornerAxisRaster = {
  width: number; pixels: Uint8ClampedArray; owners: Int8Array; depth: Float64Array
}
function rgb(hex: string): [number, number, number] {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)]
}
/** Larger camera-space Z is nearer, as in the shared Three camera. */
export function rasterCornerAxes(axes: readonly CornerAxis[], cssSize: number, pixelRatio = 1): CornerAxisRaster {
  const width = Math.max(1, Math.round(cssSize * pixelRatio))
  const pixels = new Uint8ClampedArray(width * width * 4)
  const owners = new Int8Array(width * width); owners.fill(-1)
  const depth = new Float64Array(width * width); depth.fill(-Infinity)
  const centre = cssSize / 2
  const put = (x: number, y: number, z: number, owner: number, color: readonly number[]) => {
    const index = y * width + x
    if (!Number.isFinite(z) || z < depth[index]!) return
    depth[index] = z; owners[index] = owner
    const at = index * 4
    pixels[at] = color[0]!; pixels[at + 1] = color[1]!; pixels[at + 2] = color[2]!; pixels[at + 3] = 255
  }
  const each = (loX: number, loY: number, hiX: number, hiY: number,
    paint: (x: number, y: number, px: number, py: number) => void) => {
    const x0 = Math.max(0, Math.floor((centre + loX) * pixelRatio))
    const y0 = Math.max(0, Math.floor((centre + loY) * pixelRatio))
    const x1 = Math.min(width - 1, Math.ceil((centre + hiX) * pixelRatio))
    const y1 = Math.min(width - 1, Math.ceil((centre + hiY) * pixelRatio))
    for (let y = y0; y <= y1; ++y) for (let x = x0; x <= x1; ++x)
      paint(x, y, (x + .5) / pixelRatio - centre, (y + .5) / pixelRatio - centre)
  }
  const marker = (cx: number, cy: number, cz: number, radius: number, owner: number,
    color: readonly number[], border: readonly number[], borderWidth: number) => {
    each(cx - radius, cy - radius, cx + radius, cy + radius, (x, y, px, py) => {
      const distance2 = (px - cx) ** 2 + (py - cy) ** 2
      if (distance2 > radius * radius) return
      put(x, y, cz, owner, distance2 >= (radius - borderWidth) ** 2 ? border : color)
    })
  }
  axes.forEach((axis, ordinal) => {
    const dx = axis.sx, dy = axis.sy, dz = axis.depth
    if (![dx, dy, dz].every(Number.isFinite)) return
    const endX = dx * CORNER_AXIS_RADIUS, endY = dy * CORNER_AXIS_RADIUS
    const radius = axis.depth >= 0 ? 3.25 : 2.5
    const inkRadius = axis.depth >= 0 ? 1.4 : .9
    const base = rgb(axis.color), dark = rgb('#182331')
    const ink = axis.depth >= 0 ? base : base.map((v, k) => Math.round(v * (0x88 / 255) + [16, 21, 29][k]! * (1 - 0x88 / 255)))
    const markerInk = axis.depth >= 0 ? base : base.map((v, k) => Math.round(v * (0x88 / 255) + dark[k]! * (1 - 0x88 / 255)))
    // Use the existing round shaft footprint. Unlike whole-ray painter order,
    // its depth varies at each projected point from the origin to the true tip.
    each(Math.min(0, endX) - radius, Math.min(0, endY) - radius,
      Math.max(0, endX) + radius, Math.max(0, endY) + radius, (x, y, px, py) => {
        const projected = endX * endX + endY * endY
        const fraction = projected === 0 ? 0 : Math.max(0, Math.min(1, (px * endX + py * endY) / projected))
        const offset2 = (px - fraction * endX) ** 2 + (py - fraction * endY) ** 2
        if (offset2 > radius * radius) return
        const dash = axis.depth >= 0 || (fraction * Math.sqrt(projected)) % 6 <= 3
        put(x, y, dz * CORNER_AXIS_RADIUS * fraction, axes.length + ordinal,
          dash && offset2 <= inkRadius * inkRadius ? ink : [16, 21, 29])
      })
    const front = axis.depth >= 0
    marker(endX, endY, dz * CORNER_AXIS_RADIUS, axis.sign === 1 ? 9 : 8, ordinal,
      front && axis.sign === 1 ? base : dark, markerInk, front ? 1.8 : 1.2)
  })
  marker(0, 0, 0, 6.3, axes.length * 2, rgb('#253347'), rgb('#9aabba'), .5)
  return { width, pixels, owners, depth }
}
/** Alpha-composite glyphs only onto the marker surface that is actually visible. */
export function compositeCornerAxisLabel(raster: CornerAxisRaster, owner: number,
  glyph: Uint8ClampedArray, glyphWidth: number, left: number, top: number): void {
  for (let y = 0; y < glyphWidth; ++y) for (let x = 0; x < glyphWidth; ++x) {
    const gx = left + x, gy = top + y
    if (gx < 0 || gy < 0 || gx >= raster.width || gy >= raster.width) continue
    const index = gy * raster.width + gx
    if (raster.owners[index] !== owner) continue
    const source = (y * glyphWidth + x) * 4, target = index * 4
    const alpha = glyph[source + 3]! / 255
    for (let k = 0; k < 3; ++k)
      raster.pixels[target + k] = glyph[source + k]! * alpha + raster.pixels[target + k]! * (1 - alpha)
  }
}
