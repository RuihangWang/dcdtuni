import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { subscribeViewport } from '../viewportBus'
import { CORNER_AXIS_LABEL_HALF, CORNER_AXIS_RADIUS, compositeCornerAxisLabel, rasterCornerAxes } from './cornerAxisDepth'

/** Read-only world orientation: six rays with per-pixel camera-space depth occlusion. */
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

const labelCanvases = new WeakMap<CanvasRenderingContext2D, HTMLCanvasElement>()

/** Share one orthographic depth frame across shafts, caps, hub and glyphs. */
export function drawCornerAxesGizmo(ctx: CanvasRenderingContext2D, cameraQuaternion: THREE.Quaternion) {
  const size = CORNER_AXES_CSS
  const ratio = ctx.canvas.width > 0 ? ctx.canvas.width / size : 1
  const axes = projectCornerAxes(cameraQuaternion)
  const raster = rasterCornerAxes(axes, size, ratio)
  let labels = labelCanvases.get(ctx)
  if (!labels) {
    labels = ctx.canvas.ownerDocument.createElement('canvas')
    labelCanvases.set(ctx, labels)
  }
  const labelSize = Math.round(CORNER_AXIS_LABEL_HALF * 2 * ratio)
  if (labels.width !== labelSize || labels.height !== labelSize) {
    labels.width = labelSize; labels.height = labelSize
  }
  const labelCtx = labels.getContext('2d', { willReadFrequently: true })
  if (labelCtx) {
    labelCtx.setTransform(ratio, 0, 0, ratio, 0, 0)
    axes.forEach((axis, owner) => {
      labelCtx.clearRect(0, 0, CORNER_AXIS_LABEL_HALF * 2, CORNER_AXIS_LABEL_HALF * 2)
      const x = size / 2 + axis.sx * CORNER_AXIS_RADIUS
      const y = size / 2 + axis.sy * CORNER_AXIS_RADIUS
      const left = Math.floor((x - CORNER_AXIS_LABEL_HALF) * ratio)
      const top = Math.floor((y - CORNER_AXIS_LABEL_HALF) * ratio)
      labelCtx.fillStyle = axis.depth >= 0 && axis.sign === 1 ? '#10151d' : axis.color + (axis.depth >= 0 ? 'ff' : 'bb')
      labelCtx.font = `600 ${axis.sign === 1 ? 11 : 9}px "Segoe UI", sans-serif`
      labelCtx.textAlign = 'center'; labelCtx.textBaseline = 'middle'
      labelCtx.fillText(axis.label, x - left / ratio, y + .5 - top / ratio)
      const glyph = labelCtx.getImageData(0, 0, labelSize, labelSize)
      compositeCornerAxisLabel(raster, owner, glyph.data, labelSize, left, top)
    })
  }
  const image = ctx.createImageData(raster.width, raster.width)
  image.data.set(raster.pixels)
  // putImageData uses physical pixels, independently of the Canvas CSS transform.
  ctx.putImageData(image, 0, 0)
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
