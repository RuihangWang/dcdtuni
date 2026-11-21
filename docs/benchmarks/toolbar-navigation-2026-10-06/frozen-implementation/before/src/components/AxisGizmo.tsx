import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { subscribeViewport } from '../viewportBus'

/**
 * The bottom-left orientation triad — the legacy corner gizmo, stroke for
 * stroke (`_drawCornerAxesGizmo`, STL_generate.html:29374-29438).
 *
 * A 2D canvas, not a Three.js scene: the legacy projects the three world axes
 * through the inverse camera quaternion and paints them back to front with
 * depth-based dimming, a flat arrow head, a dark label chip outlined in the
 * axis colour and a white origin dot. A WebGL triad with lit 3D cones read as
 * a different widget, which is why this is a transcription rather than a
 * re-interpretation.
 *
 * Read-only, unlike the view cube: it answers "which way is X" while the user
 * is orbiting, and making it clickable would put two competing controls for
 * the same thing on screen.
 */

/** CSS size of the canvas, as the legacy's `_CORNER_AXES_CSS`. */
export const CORNER_AXES_CSS = 92

const AXES: { world: THREE.Vector3; color: string; label: string }[] = [
  { world: new THREE.Vector3(1, 0, 0), color: '#ff5566', label: 'X' },
  { world: new THREE.Vector3(0, 1, 0), color: '#4ade80', label: 'Y' },
  { world: new THREE.Vector3(0, 0, 1), color: '#60a5fa', label: 'Z' },
]

/** One frame of the legacy drawing, given the main camera's quaternion. */
export function drawCornerAxesGizmo(ctx: CanvasRenderingContext2D, cameraQuaternion: THREE.Quaternion) {
  const W = CORNER_AXES_CSS
  const H = CORNER_AXES_CSS
  const CX = W / 2
  const CY = H / 2
  const R = 28
  ctx.clearRect(0, 0, W, H)

  // Rotate world axes into view space (screen: x-right, y-down)
  const qInv = cameraQuaternion.clone().invert()
  const axes = AXES.map((a) => {
    const v = a.world.clone().applyQuaternion(qInv)
    return { color: a.color, label: a.label, sx: v.x, sy: -v.y, depth: -v.z }
  }).sort((a, b) => a.depth - b.depth) // back to front

  for (const a of axes) {
    const endX = CX + a.sx * R
    const endY = CY + a.sy * R
    // Depth-based dimming (front axes brighter)
    const dim = (a.depth + 1) * 0.5 // 0..1
    const alpha = 0.55 + 0.45 * dim
    const col = a.color + Math.round(alpha * 255).toString(16).padStart(2, '0')
    // Line
    ctx.strokeStyle = col
    ctx.lineWidth = 2.2
    ctx.lineCap = 'round'
    ctx.beginPath()
    ctx.moveTo(CX, CY)
    ctx.lineTo(endX, endY)
    ctx.stroke()
    // Arrow head
    const ang = Math.atan2(endY - CY, endX - CX)
    const ah = 6
    ctx.fillStyle = col
    ctx.beginPath()
    ctx.moveTo(endX, endY)
    ctx.lineTo(endX - ah * Math.cos(ang - 0.45), endY - ah * Math.sin(ang - 0.45))
    ctx.lineTo(endX - ah * Math.cos(ang + 0.45), endY - ah * Math.sin(ang + 0.45))
    ctx.closePath()
    ctx.fill()
    // Label chip
    const lbX = CX + a.sx * (R + 11)
    const lbY = CY + a.sy * (R + 11)
    ctx.fillStyle = 'rgba(0,0,0,0.55)'
    ctx.beginPath()
    ctx.arc(lbX, lbY, 8, 0, Math.PI * 2)
    ctx.fill()
    ctx.strokeStyle = col
    ctx.lineWidth = 1.2
    ctx.stroke()
    ctx.fillStyle = col
    ctx.font = 'bold 11px "Segoe UI", "Share Tech Mono", monospace'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(a.label, lbX, lbY + 0.5)
  }
  // Origin marker
  ctx.fillStyle = 'rgba(255,255,255,0.9)'
  ctx.beginPath()
  ctx.arc(CX, CY, 2.5, 0, Math.PI * 2)
  ctx.fill()
}

export function AxisGizmo() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width = CORNER_AXES_CSS * dpr
    canvas.height = CORNER_AXES_CSS * dpr
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.scale(dpr, dpr)

    let mainCamera: THREE.PerspectiveCamera | null = null
    const unsubscribe = subscribeViewport((h) => {
      mainCamera = h?.camera ?? null
    })

    // The legacy repaints inside its on-demand `_doRender`; this viewport
    // renders continuously, so the paint is skipped while the camera holds
    // still rather than redrawn every frame.
    const last = new THREE.Quaternion(NaN, NaN, NaN, NaN)
    let raf = 0
    const tick = () => {
      raf = requestAnimationFrame(tick)
      if (!mainCamera) return
      if (mainCamera.quaternion.equals(last)) return
      last.copy(mainCamera.quaternion)
      drawCornerAxesGizmo(ctx, mainCamera.quaternion)
    }
    tick()

    return () => {
      cancelAnimationFrame(raf)
      unsubscribe()
    }
  }, [])

  // `corner-axes-gizmo` is the legacy id; kept so one selector finds it in
  // both apps.
  return <canvas id="corner-axes-gizmo" ref={canvasRef} />
}
