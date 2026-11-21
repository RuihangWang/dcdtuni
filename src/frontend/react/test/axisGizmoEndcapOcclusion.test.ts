import * as THREE from 'three'
import {describe, expect, it} from 'vitest'
import {projectCornerAxes} from '../src/components/AxisGizmo'
import {compositeCornerAxisLabel, rasterCornerAxes} from '../src/components/cornerAxisDepth'

function cameraAt(x: number, y: number, z: number) {
  const camera = new THREE.PerspectiveCamera()
  camera.position.set(x, y, z); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true)
  return camera
}

describe('axis endcap and label per-pixel occlusion', () => {
  it.each([[5, 0, 0], [-5, 0, 0], [0, 5, 0], [0, -5, 0], [0, 0, 5], [0, 0, -5]])
    ('the near cap hides the origin and rear opposite axis at %s/%s/%s', (x, y, z) => {
      const axes = projectCornerAxes(cameraAt(x, y, z).quaternion)
      const near = axes.findIndex(a => a.depth > .999)
      const rear = axes.findIndex(a => a.depth < -.999)
      const raster = rasterCornerAxes(axes, 92, 2)
      const pixel = 92 * raster.width + 92
      expect(raster.owners[pixel]).toBe(near)
      expect(raster.depth[pixel]).toBeCloseTo(axes[near]!.depth * 35, 12)
      const original = raster.pixels.slice(pixel * 4, pixel * 4 + 4)
      // A glyph on the rear marker cannot be drawn through the opaque near cap.
      const glyph = new Uint8ClampedArray([255, 255, 255, 255])
      compositeCornerAxisLabel(raster, rear, glyph, 1, 92, 92)
      expect(raster.pixels.slice(pixel * 4, pixel * 4 + 4)).toEqual(original)
      compositeCornerAxisLabel(raster, near, glyph, 1, 92, 92)
      expect(raster.pixels.slice(pixel * 4, pixel * 4 + 4)).toEqual(glyph)
    })

  it('occludes a rear label behind a different near axis in an oblique view', () => {
    const axes = projectCornerAxes(cameraAt(5, 0, 5).quaternion)
    const near = axes.findIndex(a => a.label === 'X')
    const rear = axes.findIndex(a => a.label === '−Z')
    expect(axes[near]!.sx).toBeCloseTo(axes[rear]!.sx, 12)
    expect(axes[near]!.sy).toBeCloseTo(axes[rear]!.sy, 12)
    const raster = rasterCornerAxes(axes, 92)
    const x = Math.floor(46 + axes[near]!.sx * 35)
    const y = Math.floor(46 + axes[near]!.sy * 35)
    expect(raster.owners[y * raster.width + x]).toBe(near)
    expect(raster.depth[y * raster.width + x]).toBeGreaterThan(axes[rear]!.depth * 35)
  })
})
