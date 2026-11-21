import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { projectCornerAxes } from '../src/components/AxisGizmo'
import { rasterCornerAxes } from '../src/components/cornerAxisDepth'

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

  it('uses the real interpolated shaft depth instead of its endpoint depth', () => {
    const axis = {label: 'X', color: '#ff667c', sign: 1, sx: .6, sy: 0, depth: .8}
    const raster = rasterCornerAxes([axis], 92)
    // Pixel centre is (10.5, .5) from the origin: before the endpoint marker.
    const at = 46 * raster.width + 56
    expect(raster.owners[at]).toBe(1) // shaft, not the endpoint marker or hub
    expect(raster.depth[at]).toBeCloseTo(.8 * 35 * (10.5 / (.6 * 35)), 12)
    expect(raster.depth[at]).toBeLessThan(axis.depth * 35)
  })
})
