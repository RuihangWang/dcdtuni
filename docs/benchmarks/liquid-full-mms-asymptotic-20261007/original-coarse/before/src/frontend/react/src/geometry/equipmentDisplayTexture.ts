import * as THREE from 'three'

/** A display texture belongs to one equipment mesh, never the shared PBR cache.
 * Disposal can reach it through both its geometry and the scene material walk.
 */
class OwnedDisplayTexture extends THREE.CanvasTexture {
  private released = false

  override dispose() {
    if (this.released) return
    this.released = true
    super.dispose()
  }
}

/** Static equipment schematic only: no simulated values or live-state claims.
 * A server/test without a working 2D canvas gets no texture, rather than a
 * fabricated image which would pretend the screen was rendered.
 */
export function createCduDisplayTexture(): THREE.CanvasTexture | null {
  if (typeof document === 'undefined' || typeof CanvasRenderingContext2D === 'undefined') return null
  const canvas = document.createElement('canvas')
  canvas.width = 512
  canvas.height = 288
  let ctx: CanvasRenderingContext2D | null
  try {
    ctx = canvas.getContext('2d')
  } catch {
    return null
  }
  if (!ctx) return null

  ctx.fillStyle = '#10252d'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.strokeStyle = '#355660'
  ctx.lineWidth = 3
  ctx.strokeRect(10, 10, 492, 268)
  ctx.fillStyle = '#e4edef'
  ctx.font = '600 48px sans-serif'
  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  ctx.fillText('CDU', 28, 47)

  ctx.font = '600 23px sans-serif'
  ctx.fillStyle = '#acc4cc'
  ctx.textAlign = 'center'
  ctx.fillText('PRIMARY', 139, 104)
  ctx.fillText('SECONDARY', 374, 104)

  // Two circuit loops and a central heat-exchanger symbol. Their colours label
  // the diagram; they do not indicate a measured temperature or operating state.
  ctx.lineWidth = 8
  for (const [left, right, colour] of [[36, 220, '#78b7cb'], [292, 476, '#bb9b88']] as const) {
    ctx.strokeStyle = colour
    ctx.beginPath()
    ctx.moveTo(left + 18, 136)
    ctx.lineTo(right - 18, 136)
    ctx.lineTo(right - 18, 208)
    ctx.lineTo(left + 18, 208)
    ctx.closePath()
    ctx.stroke()
  }
  ctx.strokeStyle = '#d0dadd'
  ctx.lineWidth = 4
  for (const x of [244, 256, 268]) {
    ctx.beginPath()
    ctx.moveTo(x, 130)
    ctx.lineTo(x, 214)
    ctx.stroke()
  }
  ctx.fillStyle = '#acc4cc'
  ctx.font = '600 24px sans-serif'
  ctx.textAlign = 'left'
  ctx.fillText('SERVICE', 28, 252)

  const texture = new OwnedDisplayTexture(canvas)
  texture.name = 'cdu-static-service-schematic'
  texture.colorSpace = THREE.SRGBColorSpace
  texture.minFilter = THREE.LinearFilter
  texture.magFilter = THREE.LinearFilter
  texture.generateMipmaps = false
  return texture
}
