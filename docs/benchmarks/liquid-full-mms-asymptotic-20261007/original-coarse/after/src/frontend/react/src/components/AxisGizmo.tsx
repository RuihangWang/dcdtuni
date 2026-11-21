import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { subscribeViewport } from '../viewportBus'

/** Read-only world orientation: six rays, painted in camera-space depth order. */
export const CORNER_AXES_CSS = 92
const AXES = [
  { world: new THREE.Vector3(1, 0, 0), color: '#ff667c', label: 'X' },
  { world: new THREE.Vector3(0, 1, 0), color: '#66dc99', label: 'Y' },
  { world: new THREE.Vector3(0, 0, 1), color: '#7eb8ff', label: 'Z' },
]

export function projectCornerAxes(cameraQuaternion: THREE.Quaternion) {
  const inverse = cameraQuaternion.clone().invert()
  return AXES.flatMap(axis => ([1, -1] as const).map(sign => {
    const view = axis.world.clone().multiplyScalar(sign).applyQuaternion(inverse)
    // A Three camera looks along -Z: positive view-space Z is TOWARD the user.
    return { label: sign === 1 ? axis.label : `−${axis.label}`, color: axis.color,
      sign, sx: view.x, sy: -view.y, depth: view.z }
  })).sort((a, b) => a.depth - b.depth)
}

/** Painter depth is exact for these radial rays: rear rays, depth-zero hub, front rays. */
export function drawCornerAxesGizmo(ctx: CanvasRenderingContext2D, cameraQuaternion: THREE.Quaternion) {
  const size = CORNER_AXES_CSS
  const centre = size / 2
  const radius = 35
  const axes = projectCornerAxes(cameraQuaternion)
  ctx.clearRect(0, 0, size, size)
  ctx.save()
  const drawRay = (axis: ReturnType<typeof projectCornerAxes>[number]) => {
    const front = axis.depth >= 0
    const x = centre + axis.sx * radius
    const y = centre + axis.sy * radius
    ctx.lineCap = 'round'
    ctx.setLineDash([])
    ctx.beginPath()
    ctx.moveTo(centre, centre)
    ctx.lineTo(x, y)
    // An opaque silhouette gives the nearer shaft real occlusion at crossings.
    ctx.strokeStyle = '#10151d'
    ctx.lineWidth = front ? 6.5 : 5
    ctx.stroke()
    ctx.strokeStyle = axis.color + (front ? 'ff' : '88')
    ctx.lineWidth = front ? 2.8 : 1.8
    ctx.setLineDash(front ? [] : [3, 3])
    ctx.stroke()
    ctx.setLineDash([])
    ctx.beginPath()
    ctx.arc(x, y, axis.sign === 1 ? 9 : 8, 0, Math.PI * 2)
    ctx.fillStyle = front && axis.sign === 1 ? axis.color : '#182331'
    ctx.fill()
    ctx.strokeStyle = axis.color + (front ? 'ff' : '88')
    ctx.lineWidth = front ? 1.8 : 1.2
    ctx.stroke()
    ctx.fillStyle = front && axis.sign === 1 ? '#10151d' : axis.color + (front ? 'ff' : 'bb')
    ctx.font = `600 ${axis.sign === 1 ? 11 : 9}px "Segoe UI", sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(axis.label, x, y + 0.5)
  }
  for (const axis of axes) if (axis.depth < 0) drawRay(axis)
  // The hub hides shafts and end labels behind the origin, including an axis
  // looking directly away. Front-facing rays/markers are painted OVER this hub.
  ctx.beginPath()
  ctx.arc(centre, centre, 6.3, 0, Math.PI * 2)
  ctx.fillStyle = '#253347'
  ctx.fill()
  ctx.strokeStyle = '#9aabba'
  ctx.lineWidth = 1
  ctx.stroke()
  for (const axis of axes) if (axis.depth >= 0) drawRay(axis)
  ctx.restore()
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
