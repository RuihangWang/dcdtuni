import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { createResizeHandles } from '../src/ui/resizeHandles'
import { geometryPure } from '../src/legacy/geometryBuilder'

/** A 1 x 1 quad in the XY plane at z = 0, counter-clockwise from bottom-left. */
function quad(z = 0): THREE.Vector3[] {
  return [
    new THREE.Vector3(0, 0, z),
    new THREE.Vector3(1, 0, z),
    new THREE.Vector3(1, 1, z),
    new THREE.Vector3(0, 1, z),
  ]
}

const CAMERA = new THREE.Vector3(0.5, 0.5, 20)

function setup() {
  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000)
  camera.position.copy(CAMERA)
  camera.updateMatrixWorld()
  const handles = createResizeHandles(scene, camera)
  return { scene, camera, handles }
}

/** The rule the cube follows: 1.8% of ITS OWN distance to the camera. */
const sideFor = (camera: THREE.Camera, mesh: THREE.Object3D) =>
  geometryPure().handleCubeSize(camera.position.distanceTo(mesh.position))

const cubesOf = (handles: ReturnType<typeof createResizeHandles>) =>
  handles.pickables() as THREE.Mesh[]

const at = (handles: ReturnType<typeof createResizeHandles>, key: string) =>
  handles.pickables().find((m) => m.userData.edgeKey === key || m.userData.cornerIndex === 0 && key === 'corner')!

describe('resize handle cubes follow the camera', () => {
  it('sizes every cube from its own distance at build time', () => {
    const { camera, handles } = setup()
    handles.showRect({ uid: 'm_1', faceId: 'front', patchId: null, corners: quad() })
    const cubes = handles.pickables() as THREE.Mesh[]
    expect(cubes.length).toBe(4)
    for (const cube of cubes) {
      expect(cube.scale.x).toBeCloseTo(sideFor(camera, cube), 9)
      expect((cube.geometry as THREE.BoxGeometry).parameters.width).toBe(1)
    }
  })

  it('re-sizes them on a zoom, without a rebuild', () => {
    const { camera, handles } = setup()
    handles.showRect({ uid: 'm_1', faceId: 'front', patchId: null, corners: quad() })
    const cube = at(handles, 'top') as THREE.Mesh
    const far = cube.scale.x
    // Roll in 10x: a selection made from across the room must not keep
    // room-sized cubes sitting on top of the model being edited.
    camera.position.set(0.5, 0.5, 2)
    handles.updateScale()
    expect(cube).toBe(at(handles, 'top'))
    expect(cube.scale.x).toBeCloseTo(sideFor(camera, cube), 9)
    expect(cube.scale.x).toBeLessThan(far / 5)
    // ...and back out again
    camera.position.copy(CAMERA)
    handles.updateScale()
    expect(cube.scale.x).toBeCloseTo(far, 9)
  })

  it('sizes each cube of a big face for where THAT cube is', () => {
    // A 9 m floor with the camera 2 m off one edge: one size for the whole
    // overlay, measured at the face centre, leaves the near cube tens of pixels
    // across and covering the floor. Each cube keeps the same size ON SCREEN
    // instead, which is what an affordance is supposed to do.
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000)
    camera.position.set(0, -2, 1)
    camera.updateMatrixWorld()
    const handles = createResizeHandles(scene, camera)
    handles.showRect({
      uid: 'm_floor',
      faceId: 'top',
      patchId: null,
      corners: [
        new THREE.Vector3(-4.5, -4, 0),
        new THREE.Vector3(4.5, -4, 0),
        new THREE.Vector3(4.5, 4, 0),
        new THREE.Vector3(-4.5, 4, 0),
      ],
    })
    const near = cubesOf(handles).find((m) => m.userData.edgeKey === 'bottom')!
    const far = cubesOf(handles).find((m) => m.userData.edgeKey === 'top')!
    expect(camera.position.distanceTo(near.position)).toBeLessThan(
      camera.position.distanceTo(far.position),
    )
    expect(near.scale.x).toBeCloseTo(sideFor(camera, near), 9)
    expect(far.scale.x).toBeCloseTo(sideFor(camera, far), 9)
    // Smaller in world metres, identical on screen.
    expect(near.scale.x).toBeLessThan(far.scale.x)
    const px = (m: THREE.Object3D) => (m.scale.x / camera.position.distanceTo(m.position)) * 1000
    expect(px(near)).toBeCloseTo(px(far), 6)
  })

  it('writes nothing while the camera stays put', () => {
    const { handles } = setup()
    handles.showRect({ uid: 'm_1', faceId: 'front', patchId: null, corners: quad() })
    const cube = at(handles, 'top')
    const scale = cube.scale.x
    handles.updateScale()
    handles.updateScale()
    expect(cube.scale.x).toBe(scale)
  })

  it('keeps the hover and drag growth multiplied into the camera size', () => {
    const { camera, handles } = setup()
    handles.showRect({ uid: 'm_1', faceId: 'front', patchId: null, corners: quad() })
    handles.setStates('top', null)
    const idle = at(handles, 'bottom') as THREE.Mesh
    const hovered = at(handles, 'top') as THREE.Mesh
    expect(idle.scale.x).toBeCloseTo(sideFor(camera, idle), 9)
    expect(hovered.scale.x).toBeCloseTo(sideFor(camera, hovered) * 1.55, 9)
    // A zoom re-measures both factors, so the lit handle stays lit and larger.
    camera.position.set(0.5, 0.5, 2)
    handles.updateScale()
    expect(hovered.scale.x).toBeCloseTo(sideFor(camera, hovered) * 1.55, 9)
    expect(idle.scale.x).toBeCloseTo(sideFor(camera, idle), 9)
  })

  it('sizes the blue corner cubes the same way', () => {
    const { camera, handles } = setup()
    handles.showRect({
      uid: 'm_1',
      faceId: 'front',
      patchId: null,
      corners: quad(),
      cornerHandles: quad(),
    })
    const corner = cubesOf(handles).find((m) => m.userData.cornerIndex === 0)!
    expect(corner.scale.x).toBeCloseTo(sideFor(camera, corner), 9)
    camera.position.set(0.5, 0.5, 4)
    handles.updateScale()
    expect(corner.scale.x).toBeCloseTo(sideFor(camera, corner), 9)
  })

  it('re-measures from where the handles are NOW, mid-drag', () => {
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000)
    camera.position.set(0.5, 0.5, 8)
    camera.updateMatrixWorld()
    const handles = createResizeHandles(scene, camera)
    handles.showRect({ uid: 'm_1', faceId: 'front', patchId: null, corners: quad() })
    const cube = at(handles, 'top') as THREE.Mesh
    const before = cube.scale.x
    // Dragged 4 m away from the camera without a rebuild: the size follows the
    // drawn position, not the one measured when the overlay was built.
    handles.moveRect(quad(-4))
    expect(cube.scale.x).toBeCloseTo(sideFor(camera, cube), 9)
    expect(cube.scale.x).toBeGreaterThan(before)
  })

  it('does nothing at all when no overlay is drawn', () => {
    const { handles } = setup()
    expect(() => handles.updateScale()).not.toThrow()
    expect(handles.pickables()).toEqual([])
  })

  it('sizes a circle patch ring dots from the camera too', () => {
    const { camera, handles } = setup()
    const centre = new THREE.Vector3(0.5, 0.5, 0)
    const ring: THREE.Vector3[] = []
    const dots: THREE.Vector3[] = []
    for (let i = 0; i <= 48; i++) {
      const t = (i / 48) * Math.PI * 2
      ring.push(new THREE.Vector3(0.5 + 0.4 * Math.cos(t), 0.5 + 0.4 * Math.sin(t), 0))
    }
    for (const f of [0, 0.25, 0.5, 0.75]) {
      const t = f * Math.PI * 2
      dots.push(new THREE.Vector3(0.5 + 0.4 * Math.cos(t), 0.5 + 0.4 * Math.sin(t), 0))
    }
    handles.showCircle({ uid: 'm_1', faceId: 'front', patchId: 'p_1', centre, ring, dots })
    const dot = cubesOf(handles)[0]!
    expect(dot.scale.x).toBeCloseTo(sideFor(camera, dot), 9)
    camera.position.set(0.5, 0.5, 3)
    handles.updateScale()
    expect(dot.scale.x).toBeCloseTo(sideFor(camera, dot), 9)
  })
})
