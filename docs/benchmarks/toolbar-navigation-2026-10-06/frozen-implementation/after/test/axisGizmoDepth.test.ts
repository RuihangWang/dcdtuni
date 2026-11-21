import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { drawCornerAxesGizmo, projectCornerAxes } from '../src/components/AxisGizmo'

function cameraAt(x: number, y: number, z: number) {
  const camera = new THREE.PerspectiveCamera()
  camera.position.set(x, y, z)
  camera.lookAt(0, 0, 0)
  camera.updateMatrixWorld(true)
  return camera
}

describe('corner axis camera-space depth', () => {
  it.each([[0, 0, 5], [-4, 6, 3], [0, 0, -5]])('matches the actual camera view matrix at %s/%s/%s', (x, y, z) => {
    const camera = cameraAt(x, y, z)
    const axes = projectCornerAxes(camera.quaternion)
    expect(new Set(axes.map(a => a.label)).size).toBe(6)
    for (const axis of axes) {
      const letter = axis.label.at(-1)
      const world = new THREE.Vector3(letter === 'X' ? axis.sign : 0,
        letter === 'Y' ? axis.sign : 0, letter === 'Z' ? axis.sign : 0)
      const view = world.transformDirection(camera.matrixWorldInverse)
      expect(axis.sx).toBeCloseTo(view.x, 12)
      expect(axis.sy).toBeCloseTo(-view.y, 12)
      expect(axis.depth).toBeCloseTo(view.z, 12)
    }
    expect(axes.map(a => a.depth)).toEqual(axes.map(a => a.depth).sort((a, b) => a - b))
  })

  it('reverses which Z direction is nearer when looking from the other side', () => {
    const front = projectCornerAxes(cameraAt(0, 0, 5).quaternion)
    const back = projectCornerAxes(cameraAt(0, 0, -5).quaternion)
    expect(front.find(a => a.label === 'Z')!.depth).toBeCloseTo(1)
    expect(back.find(a => a.label === 'Z')!.depth).toBeCloseTo(-1)
    expect(back.find(a => a.label === '−Z')!.depth).toBeCloseTo(1)
  })

  it('puts the opaque hub between rear and front labels and uses opaque front silhouettes', () => {
    const events: { kind: string; label?: string; width?: number; color?: string }[] = []
    const ctx = {
      fillStyle: '', strokeStyle: '', lineWidth: 0,
      clearRect() {}, save() {}, restore() {}, setLineDash() {}, beginPath() {},
      moveTo() {}, lineTo() {}, arc() {},
      fill() { if (this.fillStyle === '#253347') events.push({ kind: 'hub' }) },
      stroke() { events.push({ kind: 'stroke', width: this.lineWidth, color: this.strokeStyle }) },
      fillText(label: string) { events.push({ kind: 'label', label }) },
    }
    const camera = cameraAt(-4, 6, 3)
    const axes = projectCornerAxes(camera.quaternion)
    drawCornerAxesGizmo(ctx as unknown as CanvasRenderingContext2D, camera.quaternion)
    const hub = events.findIndex(e => e.kind === 'hub')
    expect(hub).toBeGreaterThan(0)
    for (const axis of axes) {
      const label = events.findIndex(e => e.kind === 'label' && e.label === axis.label)
      expect(label >= 0).toBe(true)
      expect(axis.depth < 0 ? label < hub : label > hub).toBe(true)
    }
    const silhouettes = events.slice(hub + 1).filter(e => e.kind === 'stroke' && e.width === 6.5)
    expect(silhouettes.length).toBe(3)
    expect(silhouettes.every(e => e.color === '#10151d')).toBe(true)
  })
})
