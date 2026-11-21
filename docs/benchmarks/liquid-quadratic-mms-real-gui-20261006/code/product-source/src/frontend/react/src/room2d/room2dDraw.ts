/**
 * Painting the floor plan.
 *
 * A direct port of `_render`, `_drawGrid`, `_drawAxes`, `_drawModelProjections`
 * and `_drawPaperAnnotations` (`room_2d_editor.js:369-1050`). The colours, the
 * fonts and the layout are the legacy's to the pixel on purpose: this canvas IS
 * the topview the 3-agent Builder/Evaluator SOP screenshots, and a plan that
 * merely looks similar makes every comparison between the two apps a judgement
 * call.
 *
 * One function, called from a rAF, rather than a React tree of shapes. The plan
 * redraws on every pointer move during a drag and there can be 240 racks on it;
 * reconciling that many elements sixty times a second is the mistake this port
 * exists not to repeat.
 */

import { computeAnnotations } from './room2dAnnotations'
import {
  bottomCorners,
  drawableModels,
  modelExtentPoints,
  planFootprint,
  rotateRing,
  type PlanModel,
} from './room2dModels'
import {
  scaleBarMetres,
  worldToCanvas,
  type Point,
  type Vertex2,
  type View,
} from './room2dView'

export const DEFAULT_BG = '#1a1e26'

const FILL: Record<string, string> = {
  rack: 'rgba(90,167,255,0.35)',
  acu: 'rgba(152,195,121,0.35)',
  box: 'rgba(229,192,123,0.25)',
  baffle: 'rgba(176,128,64,0.25)',
  wall: 'rgba(100,100,120,0.15)',
  floor: 'rgba(100,140,100,0.10)',
}
const BORDER: Record<string, string> = {
  rack: '#5aa7ff',
  acu: '#98c379',
  box: '#e5c07b',
  baffle: '#d19a66',
  wall: 'rgba(100,100,120,0.5)',
  floor: 'rgba(100,140,100,0.3)',
}
const MONO = 'Share Tech Mono, monospace'

export interface PlanScene {
  width: number
  height: number
  view: View
  rotation: number
  bgColor: string
  showAxes: boolean
  showAnnotations: boolean
  gridSpacing: number
  vertices: Vertex2[]
  closed: boolean
  tool: 'draw' | 'select'
  selectedVertex: number
  selectedEdge: number
  boxSelectedVertices: readonly number[]
  boxSelectedEdges: readonly number[]
  /** The live rubber band, in canvas pixels. */
  band: { a: Point; b: Point } | null
  models: PlanModel[]
  selectedUids: ReadonlySet<string>
  rotatingUid: string | null
  rotateHoverUid: string | null
  /** The angle readout shown while a ring is being dragged, in radians. */
  rotateAngle: number | null
  /** The snapped cursor position in draw mode. */
  hover: Vertex2 | null
  shiftHeld: boolean
  unitLabel: string
  formatLength: (metres: number) => string
}

type Ctx = CanvasRenderingContext2D

export function drawPlan(ctx: Ctx, s: PlanScene): void {
  const toCanvas = (x: number, y: number) =>
    worldToCanvas(s.view, s.rotation, s.width, s.height, x, y)

  ctx.clearRect(0, 0, s.width, s.height)
  ctx.fillStyle = s.bgColor || DEFAULT_BG
  ctx.fillRect(0, 0, s.width, s.height)

  drawGrid(ctx, s)
  if (s.showAxes) drawAxes(ctx, s, toCanvas)
  drawModels(ctx, s, toCanvas)
  drawOutline(ctx, s, toCanvas)
  drawBand(ctx, s)
  drawBoxSelection(ctx, s, toCanvas)
  if (s.tool === 'draw' && !s.closed) drawCursor(ctx, s, toCanvas)
  if (s.showAnnotations) drawAnnotations(ctx, s, toCanvas)
  drawDimensions(ctx, s, toCanvas)
}

/**
 * The background grid.
 *
 * Two guards, both the legacy's and both about not hanging the tab: lines
 * closer than 3 px are not drawn at all, and more than 2000 in either direction
 * abandons the grid rather than queueing a path that takes seconds to stroke.
 */
function drawGrid(ctx: Ctx, s: PlanScene): void {
  const spacing = s.gridSpacing
  if (!(spacing > 0)) return
  if (spacing * s.view.scale < 3) return

  const halfW = s.width / 2 / s.view.scale
  const halfH = s.height / 2 / s.view.scale
  const minX = Math.floor((s.view.offsetX - halfW) / spacing) * spacing
  const maxX = Math.ceil((s.view.offsetX + halfW) / spacing) * spacing
  const minY = Math.floor((s.view.offsetY - halfH) / spacing) * spacing
  const maxY = Math.ceil((s.view.offsetY + halfH) / spacing) * spacing
  if ((maxX - minX) / spacing > 2000 || (maxY - minY) / spacing > 2000) return

  ctx.strokeStyle = '#2a2e38'
  ctx.lineWidth = 0.5
  ctx.beginPath()
  for (let x = minX; x <= maxX; x += spacing) {
    const px = (x - s.view.offsetX) * s.view.scale + s.width / 2
    ctx.moveTo(px, 0)
    ctx.lineTo(px, s.height)
  }
  for (let y = minY; y <= maxY; y += spacing) {
    const py = s.height / 2 - (y - s.view.offsetY) * s.view.scale
    ctx.moveTo(0, py)
    ctx.lineTo(s.width, py)
  }
  ctx.stroke()
}

function drawAxes(ctx: Ctx, s: PlanScene, toCanvas: (x: number, y: number) => Point): void {
  const origin = toCanvas(0, 0)
  ctx.strokeStyle = '#e06c75'
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.moveTo(origin.x, origin.y)
  ctx.lineTo(s.width, origin.y)
  ctx.stroke()
  ctx.fillStyle = '#e06c75'
  ctx.font = `12px ${MONO}`
  ctx.fillText('X', s.width - 15, origin.y - 5)

  ctx.strokeStyle = '#98c379'
  ctx.beginPath()
  ctx.moveTo(origin.x, origin.y)
  ctx.lineTo(origin.x, 0)
  ctx.stroke()
  ctx.fillStyle = '#98c379'
  ctx.fillText('Y', origin.x + 5, 15)
}

function tracePolygon(ctx: Ctx, points: Point[]): void {
  ctx.beginPath()
  points.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)))
  ctx.closePath()
}

function drawModels(ctx: Ctx, s: PlanScene, toCanvas: (x: number, y: number) => Point): void {
  for (const m of drawableModels(s.models)) {
    const corners = planFootprint(m).map(([x, y]) => toCanvas(x, y))
    tracePolygon(ctx, corners)
    ctx.fillStyle = FILL[m.kind] ?? 'rgba(128,128,128,0.1)'
    ctx.fill()
    ctx.strokeStyle = BORDER[m.kind] ?? 'rgba(128,128,128,0.3)'
    ctx.lineWidth = 1
    ctx.stroke()

    const selected = s.selectedUids.has(m.uid)
    if (selected) {
      ctx.strokeStyle = '#ffcc00'
      ctx.lineWidth = 2.5
      ctx.setLineDash([6, 3])
      tracePolygon(ctx, corners)
      ctx.stroke()
      ctx.setLineDash([])
    }

    // Walls and baffles are thin sheets: a name centred on one is unreadable
    // and clutters the row of racks behind it.
    if (m.kind !== 'wall' && m.kind !== 'baffle') {
      const xs = corners.map((c) => c.x)
      const ys = corners.map((c) => c.y)
      const cx = (Math.min(...xs) + Math.max(...xs)) / 2
      const cy = (Math.min(...ys) + Math.max(...ys)) / 2
      ctx.font = `10px ${MONO}`
      ctx.fillStyle = selected ? '#ffcc00' : (BORDER[m.kind] ?? 'rgba(128,128,128,0.3)')
      ctx.textAlign = 'center'
      const maxWidth = Math.max(20, Math.max(...xs) - Math.min(...xs) - 4)
      let label = m.name
      while (label.length > 3 && ctx.measureText(label).width > maxWidth) {
        label = `${label.slice(0, -2)}…`
      }
      ctx.fillText(label, cx, cy + 4)
      ctx.textAlign = 'start'
    }

    if (m.kind === 'rack') drawAirflowArrow(ctx, m, toCanvas)

    const ring = rotateRing(m, toCanvas)
    if (ring) {
      const dragging = s.rotatingUid === m.uid
      const hover = !dragging && s.rotateHoverUid === m.uid
      ctx.beginPath()
      ctx.arc(ring.centre.x, ring.centre.y, ring.radius, 0, Math.PI * 2)
      ctx.strokeStyle = dragging ? '#ffcc00' : hover ? '#00d4ff' : 'rgba(0,212,255,0.35)'
      ctx.lineWidth = dragging ? 3 : hover ? 2.5 : 1.5
      ctx.setLineDash(dragging ? [] : [4, 4])
      ctx.stroke()
      ctx.setLineDash([])

      ctx.beginPath()
      ctx.arc(ring.hx, ring.hy, hover || dragging ? 8 : 6, 0, Math.PI * 2)
      ctx.fillStyle = dragging ? '#ffcc00' : hover ? '#00e5ff' : '#00d4ff'
      ctx.fill()
      ctx.strokeStyle = '#1a1e26'
      ctx.lineWidth = 1.5
      ctx.stroke()

      ctx.beginPath()
      ctx.moveTo(ring.centre.x, ring.centre.y)
      ctx.lineTo(ring.hx, ring.hy)
      ctx.strokeStyle = dragging
        ? 'rgba(255,204,0,0.5)'
        : hover
          ? 'rgba(0,212,255,0.5)'
          : 'rgba(0,212,255,0.25)'
      ctx.lineWidth = 1
      ctx.stroke()

      if (dragging && s.rotateAngle !== null) {
        const deg = (((s.rotateAngle * 180) / Math.PI) % 360).toFixed(1)
        ctx.font = `13px ${MONO}`
        ctx.fillStyle = '#ffcc00'
        ctx.textAlign = 'center'
        ctx.fillText(`${deg}°`, ring.centre.x, ring.centre.y - ring.radius - 10)
        ctx.textAlign = 'start'
      }
    }
  }
}

/**
 * The rack's inlet -> outlet arrow.
 *
 * Spans 25% to 75% of the depth through the width centre, then turns with the
 * rack. `frontIsInlet` flips it: a rack whose faces have been swapped blows the
 * other way, and an arrow that ignored that would contradict the aisle labels
 * drawn from the same quaternion.
 */
function drawAirflowArrow(
  ctx: Ctx,
  m: PlanModel,
  toCanvas: (x: number, y: number) => Point,
): void {
  const quad = bottomCorners(m)
  // The quad's own edges give the local axes without repeating the quaternion
  // maths: corner0->corner1 is local +X, corner0->corner3 is local +Z.
  const [c0, c1, , c3] = quad as [Vertex2, Vertex2, Vertex2, Vertex2]
  const alongX = (t: number): Vertex2 => [c0[0] + (c1[0] - c0[0]) * t, c0[1] + (c1[1] - c0[1]) * t]
  const depth = (p: Vertex2, t: number): Vertex2 => [
    p[0] + (c3[0] - c0[0]) * t,
    p[1] + (c3[1] - c0[1]) * t,
  ]
  const mid = alongX(0.5)
  const from = m.frontIsInlet ? 0.25 : 0.75
  const to = m.frontIsInlet ? 0.75 : 0.25
  const pS = toCanvas(...depth(mid, from))
  const pE = toCanvas(...depth(mid, to))
  const dx = pE.x - pS.x
  const dy = pE.y - pS.y
  const len = Math.hypot(dx, dy)
  if (len <= 3) return
  const ux = dx / len
  const uy = dy / len
  const headLen = Math.min(9, len * 0.35)
  const headHalf = headLen * 0.55
  ctx.strokeStyle = 'rgba(0,212,255,0.9)'
  ctx.fillStyle = 'rgba(0,212,255,0.9)'
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(pS.x, pS.y)
  ctx.lineTo(pE.x - ux * headLen, pE.y - uy * headLen)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(pE.x, pE.y)
  ctx.lineTo(pE.x - ux * headLen - uy * headHalf, pE.y - uy * headLen + ux * headHalf)
  ctx.lineTo(pE.x - ux * headLen + uy * headHalf, pE.y - uy * headLen - ux * headHalf)
  ctx.closePath()
  ctx.fill()
}

function drawOutline(ctx: Ctx, s: PlanScene, toCanvas: (x: number, y: number) => Point): void {
  if (s.vertices.length === 0) return
  const pts = s.vertices.map(([x, y]) => toCanvas(x, y))

  ctx.strokeStyle = '#00d4ff'
  ctx.lineWidth = 2
  ctx.beginPath()
  pts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)))
  if (s.closed) ctx.closePath()
  ctx.stroke()

  if (!s.closed && s.tool === 'draw' && s.hover) {
    const last = pts[pts.length - 1]!
    const mouse = toCanvas(s.hover[0], s.hover[1])
    ctx.setLineDash([5, 5])
    ctx.strokeStyle = s.shiftHeld ? '#ffcc00' : '#00d4ff'
    ctx.beginPath()
    ctx.moveTo(last.x, last.y)
    ctx.lineTo(mouse.x, mouse.y)
    ctx.stroke()
    ctx.setLineDash([])
    ctx.beginPath()
    ctx.arc(mouse.x, mouse.y, 4, 0, Math.PI * 2)
    ctx.fillStyle = s.shiftHeld ? '#ffcc00' : '#00d4ff'
    ctx.fill()
    const lastVertex = s.vertices[s.vertices.length - 1]!
    const len = Math.hypot(s.hover[0] - lastVertex[0], s.hover[1] - lastVertex[1])
    if (len > 1e-6) {
      ctx.font = `14px ${MONO}`
      ctx.fillStyle = s.shiftHeld ? '#ffcc00' : '#00d4ff'
      ctx.fillText(
        `${s.formatLength(len)} ${s.unitLabel}`,
        (last.x + mouse.x) / 2 + 8,
        (last.y + mouse.y) / 2 - 10,
      )
    }
  }

  pts.forEach((p, i) => {
    ctx.beginPath()
    ctx.arc(p.x, p.y, 6, 0, Math.PI * 2)
    ctx.fillStyle = i === s.selectedVertex ? '#ffffff' : '#00d4ff'
    ctx.fill()
    ctx.strokeStyle = '#00d4ff'
    ctx.lineWidth = 2
    ctx.stroke()
  })

  if (s.selectedEdge >= 0 && s.selectedEdge < pts.length) {
    const a = pts[s.selectedEdge]!
    const b = pts[(s.selectedEdge + 1) % pts.length]!
    ctx.strokeStyle = '#ffcc00'
    ctx.lineWidth = 3
    ctx.beginPath()
    ctx.moveTo(a.x, a.y)
    ctx.lineTo(b.x, b.y)
    ctx.stroke()
  }

  // The close indicator: a ring round the first vertex once the cursor is
  // within 15 px of it, so it is clear that the next click closes the room
  // rather than adding a vertex on top of it.
  if (!s.closed && s.tool === 'draw' && s.vertices.length >= 3 && s.hover) {
    const first = pts[0]!
    const mouse = toCanvas(s.hover[0], s.hover[1])
    if (Math.hypot(first.x - mouse.x, first.y - mouse.y) < CLOSE_PX) {
      ctx.beginPath()
      ctx.arc(first.x, first.y, 12, 0, Math.PI * 2)
      ctx.strokeStyle = '#00ff88'
      ctx.lineWidth = 2
      ctx.stroke()
    }
  }
}

/** How close to the first vertex a click closes the ring. Legacy `:1364`. */
export const CLOSE_PX = 15

function drawBand(ctx: Ctx, s: PlanScene): void {
  if (!s.band) return
  ctx.strokeStyle = 'rgba(0,212,255,0.6)'
  ctx.fillStyle = 'rgba(0,212,255,0.08)'
  ctx.lineWidth = 1
  ctx.setLineDash([4, 4])
  const x = Math.min(s.band.a.x, s.band.b.x)
  const y = Math.min(s.band.a.y, s.band.b.y)
  const w = Math.abs(s.band.b.x - s.band.a.x)
  const h = Math.abs(s.band.b.y - s.band.a.y)
  ctx.fillRect(x, y, w, h)
  ctx.strokeRect(x, y, w, h)
  ctx.setLineDash([])
}

function drawBoxSelection(
  ctx: Ctx,
  s: PlanScene,
  toCanvas: (x: number, y: number) => Point,
): void {
  for (const i of s.boxSelectedVertices) {
    const v = s.vertices[i]
    if (!v) continue
    const p = toCanvas(v[0], v[1])
    ctx.beginPath()
    ctx.arc(p.x, p.y, 10, 0, Math.PI * 2)
    ctx.strokeStyle = '#ffcc00'
    ctx.lineWidth = 2
    ctx.stroke()
  }
  for (const i of s.boxSelectedEdges) {
    const a = s.vertices[i]
    const b = s.vertices[(i + 1) % s.vertices.length]
    if (!a || !b) continue
    const pa = toCanvas(a[0], a[1])
    const pb = toCanvas(b[0], b[1])
    ctx.strokeStyle = '#ffcc00'
    ctx.lineWidth = 3
    ctx.beginPath()
    ctx.moveTo(pa.x, pa.y)
    ctx.lineTo(pb.x, pb.y)
    ctx.stroke()
  }
}

function drawCursor(ctx: Ctx, s: PlanScene, toCanvas: (x: number, y: number) => Point): void {
  if (!s.hover) return
  const p = toCanvas(s.hover[0], s.hover[1])
  ctx.beginPath()
  ctx.arc(p.x, p.y, 12, 0, Math.PI * 2)
  ctx.strokeStyle = 'rgba(0,212,255,0.5)'
  ctx.lineWidth = 1.5
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(p.x - 6, p.y)
  ctx.lineTo(p.x + 6, p.y)
  ctx.moveTo(p.x, p.y - 6)
  ctx.lineTo(p.x, p.y + 6)
  ctx.strokeStyle = 'rgba(0,212,255,0.3)'
  ctx.lineWidth = 1
  ctx.stroke()
}

function drawDimensions(
  ctx: Ctx,
  s: PlanScene,
  toCanvas: (x: number, y: number) => Point,
): void {
  if (s.vertices.length < 2) return
  ctx.font = `13px ${MONO}`
  ctx.fillStyle = '#8be9fd'
  const count = s.closed ? s.vertices.length : s.vertices.length - 1
  for (let i = 0; i < count; i += 1) {
    const a = s.vertices[i]!
    const b = s.vertices[(i + 1) % s.vertices.length]!
    const mid = toCanvas((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    const len = Math.hypot(b[0] - a[0], b[1] - a[1])
    ctx.fillText(`${s.formatLength(len)} ${s.unitLabel}`, mid.x + 5, mid.y - 8)
  }
}

/**
 * The publication overlay: aisle labels, column letters, one rack dimension, a
 * legend, corner axes and a scale bar.
 *
 * Everything here is figure furniture — it exists so the exported PNG can be
 * read without the app around it.
 */
function drawAnnotations(
  ctx: Ctx,
  s: PlanScene,
  toCanvas: (x: number, y: number) => Point,
): void {
  const a = computeAnnotations(s.models)
  const fontSize = Math.max(12, Math.round(14 * (s.width / 1920)))
  const smallFont = Math.max(10, Math.round(11 * (s.width / 1920)))

  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.font = `bold ${fontSize}px ${MONO}`
  for (const aisle of a.aisles) {
    const p = toCanvas(aisle.worldX, aisle.worldZ)
    ctx.save()
    ctx.translate(p.x, p.y)
    if (aisle.vertical) ctx.rotate(-Math.PI / 2)
    ctx.fillStyle = aisle.type === 'HOT' ? 'rgba(224,108,117,0.9)' : 'rgba(97,175,239,0.9)'
    ctx.fillText(`${aisle.type} AISLE`, 0, 0)
    ctx.restore()
  }

  ctx.font = `${smallFont}px ${MONO}`
  ctx.fillStyle = 'rgba(200,200,200,0.7)'
  for (const col of a.columns) {
    const p = toCanvas(col.worldX, col.worldZ)
    ctx.fillText(col.text, p.x, p.y)
  }

  if (a.dimension) {
    const quad = bottomCorners(a.dimension.model).map(([x, y]) => toCanvas(x, y))
    ctx.strokeStyle = 'rgba(255,255,100,0.6)'
    ctx.lineWidth = 1
    ctx.setLineDash([4, 3])
    ctx.beginPath()
    ctx.moveTo(quad[0]!.x, quad[0]!.y)
    ctx.lineTo(quad[1]!.x, quad[1]!.y)
    ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(quad[1]!.x, quad[1]!.y)
    ctx.lineTo(quad[2]!.x, quad[2]!.y)
    ctx.stroke()
    ctx.setLineDash([])
    ctx.font = `${smallFont}px ${MONO}`
    ctx.fillStyle = 'rgba(255,255,100,0.8)'
    const wMid = { x: (quad[0]!.x + quad[1]!.x) / 2, y: (quad[0]!.y + quad[1]!.y) / 2 }
    const dMid = { x: (quad[1]!.x + quad[2]!.x) / 2, y: (quad[1]!.y + quad[2]!.y) / 2 }
    ctx.fillText(`${a.dimension.width.toFixed(1)}m`, wMid.x, wMid.y - 6)
    ctx.fillText(`${a.dimension.depth.toFixed(1)}m`, dMid.x + 12, dMid.y)
  }

  const legX = s.width - 200
  const legY = 30
  ctx.fillStyle = 'rgba(30,34,42,0.8)'
  ctx.fillRect(legX, legY, 180, 50)
  ctx.strokeStyle = 'rgba(90,167,255,0.5)'
  ctx.lineWidth = 1
  ctx.strokeRect(legX, legY, 180, 50)
  ctx.font = `${smallFont}px ${MONO}`
  ctx.fillStyle = '#5aa7ff'
  ctx.textAlign = 'left'
  ctx.fillText('Airflow Direction', legX + 10, legY + 18)
  ctx.strokeStyle = '#00d4ff'
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(legX + 10, legY + 38)
  ctx.lineTo(legX + 60, legY + 38)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(legX + 55, legY + 33)
  ctx.lineTo(legX + 60, legY + 38)
  ctx.lineTo(legX + 55, legY + 43)
  ctx.stroke()
  ctx.fillStyle = 'rgba(200,200,200,0.7)'
  ctx.fillText('inlet → outlet', legX + 70, legY + 42)

  const axLen = 50
  const axX = 40
  const axY = s.height - 40
  ctx.strokeStyle = '#e06c75'
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(axX, axY)
  ctx.lineTo(axX + axLen, axY)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(axX + axLen - 6, axY - 5)
  ctx.lineTo(axX + axLen, axY)
  ctx.lineTo(axX + axLen - 6, axY + 5)
  ctx.stroke()
  ctx.fillStyle = '#e06c75'
  ctx.font = `bold ${fontSize}px ${MONO}`
  ctx.textAlign = 'center'
  ctx.fillText('X', axX + axLen + 12, axY + 4)
  ctx.strokeStyle = '#98c379'
  ctx.beginPath()
  ctx.moveTo(axX, axY)
  ctx.lineTo(axX, axY - axLen)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(axX - 5, axY - axLen + 6)
  ctx.lineTo(axX, axY - axLen)
  ctx.lineTo(axX + 5, axY - axLen + 6)
  ctx.stroke()
  ctx.fillStyle = '#98c379'
  ctx.fillText('Z', axX, axY - axLen - 10)

  const metres = scaleBarMetres(s.view.scale)
  const px = metres * s.view.scale
  const sbX = s.width - px - 40
  const sbY = s.height - 30
  ctx.strokeStyle = '#00d4ff'
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(sbX, sbY)
  ctx.lineTo(sbX + px, sbY)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(sbX, sbY - 5)
  ctx.lineTo(sbX, sbY + 5)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(sbX + px, sbY - 5)
  ctx.lineTo(sbX + px, sbY + 5)
  ctx.stroke()
  ctx.fillStyle = '#00d4ff'
  ctx.font = `${fontSize}px ${MONO}`
  ctx.textAlign = 'center'
  ctx.fillText(`${metres} m`, sbX + px / 2, sbY - 10)

  ctx.textAlign = 'start'
  ctx.textBaseline = 'alphabetic'
}

/**
 * Everything a zoom-to-fit should frame.
 *
 * The footprint when there is one, and otherwise the models — a project loaded
 * with racks but no room outline would otherwise fit to nothing and leave the
 * view at the origin, which reads as "the 2D editor is empty".
 */
export function extentPoints(vertices: Vertex2[], models: PlanModel[]): Vertex2[] {
  return vertices.length >= 3 ? vertices : modelExtentPoints(models)
}
