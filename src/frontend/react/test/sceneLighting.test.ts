import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { sceneLighting } from '../src/renderer/environmentLighting'

/**
 * js/scene_lighting.js — the one lighting set-up both editors use, from the
 * manifest's `lighting` section (textures/materials.json): tone mapping and
 * exposure, the camera-parented light kit, the ambient term and the
 * pre-filtered room environment the PBR surfaces reflect.
 */

const REPO = resolve(process.cwd(), '../../..')
const lighting = JSON.parse(readFileSync(resolve(REPO, 'src/frontend/shared/textures/materials.json'), 'utf8')).lighting

function stubPmrem() {
  const target = { texture: new THREE.Texture(), dispose: vi.fn() }
  vi.spyOn(THREE.PMREMGenerator.prototype, 'fromScene').mockReturnValue(target as unknown as THREE.WebGLRenderTarget)
  vi.spyOn(THREE.PMREMGenerator.prototype, 'dispose').mockImplementation(() => {})
  return target
}

afterEach(() => vi.restoreAllMocks())

describe('sceneLighting.install', () => {
  it('configures the renderer and lights the scene exactly as the manifest says', () => {
    const target = stubPmrem()
    const renderer = {} as THREE.WebGLRenderer
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera()
    scene.add(camera)
    const kit = sceneLighting.install(THREE, renderer, scene, camera)
    expect(renderer.toneMapping).toBe(THREE.ACESFilmicToneMapping)
    expect(renderer.toneMappingExposure).toBe(lighting.exposure)
    expect(renderer.outputColorSpace).toBe(THREE.SRGBColorSpace)
    const directional = camera.children.filter((c) => (c as THREE.DirectionalLight).isDirectionalLight) as THREE.DirectionalLight[]
    expect(directional).toHaveLength(lighting.cameraLights.length)
    lighting.cameraLights.forEach((l: { color: string; intensity: number; direction: number[] }, i: number) => {
      expect(directional[i]!.intensity).toBeCloseTo(l.intensity * Math.PI, 10)
      expect(directional[i]!.position.toArray()).toEqual(l.direction)
      expect('#' + directional[i]!.color.getHexString()).toBe(l.color)
    })
    const ambient = scene.children.find((c) => (c as THREE.AmbientLight).isAmbientLight) as THREE.AmbientLight
    expect(ambient.intensity).toBeCloseTo(lighting.ambient.intensity * Math.PI, 10)
    expect(scene.environment).toBe(target.texture)
    expect(scene.environmentIntensity).toBe(lighting.environment.intensity)
    expect(kit.lights).toHaveLength(lighting.cameraLights.length + 1)

    kit.setScale(0.5)
    expect(directional[0]!.intensity).toBeCloseTo(lighting.cameraLights[0].intensity * Math.PI * 0.5, 10)
    expect(scene.environmentIntensity).toBeCloseTo(lighting.environment.intensity * 0.5, 10)

    kit.dispose()
    expect(camera.children.some((c) => (c as THREE.Light).isLight)).toBe(false)
    expect(scene.children.some((c) => (c as THREE.Light).isLight)).toBe(false)
    expect(scene.environment).toBeNull()
    expect(target.dispose).toHaveBeenCalledTimes(1)
  })

  it('without WebGL the lights still go in and the environment is skipped, not thrown', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(THREE.PMREMGenerator.prototype, 'fromScene').mockImplementation(() => { throw new Error('no WebGL context') })
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera()
    const kit = sceneLighting.install(THREE, {} as THREE.WebGLRenderer, scene, camera)
    expect(scene.environment).toBeNull()
    expect(kit.lights.length).toBeGreaterThan(0)
    expect(() => kit.dispose()).not.toThrow()
  })

  it('builds its room environment without three/examples (the legacy page has no module loader)', () => {
    const room = sceneLighting.roomEnvironmentScene(THREE)
    let meshes = 0
    room.traverse((o) => { if ((o as THREE.Mesh).isMesh) meshes++ })
    expect(meshes).toBeGreaterThan(5)
    sceneLighting.disposeScene(room)
  })
})

describe('both editors use it', () => {
  it('the legacy page loads js/scene_lighting.js and installs it', () => {
    const page = readFileSync(resolve(REPO, 'src/frontend/web/STL_generate.html'), 'utf8')
    expect(page).toContain('<script src="js/material_library.js"></script>')
    expect(page).toContain('<script src="js/scene_lighting.js"></script>')
    expect(page).toMatch(/sceneLighting\.install\(THREE, renderer, scene, camera\)/)
  })

  it('the React viewport installs it', () => {
    const viewport = readFileSync(resolve(REPO, 'src/frontend/react/src/components/Viewport.tsx'), 'utf8')
    expect(viewport).toMatch(/sceneLighting\.install\(THREE, renderer, scene, camera\)/)
  })
})

describe('lighting presets and shadows (the top bar\'s lighting group)', () => {
  const setup = () => {
    stubPmrem()
    const renderer = { shadowMap: { enabled: false, type: 0, autoUpdate: true, needsUpdate: false } } as unknown as THREE.WebGLRenderer
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera()
    scene.add(camera)
    const kit = sceneLighting.install(THREE, renderer, scene, camera)
    const byRole = (role: string) => camera.children.find((c) => c.name === 'lightkit-' + role) as THREE.DirectionalLight
    return { renderer, scene, kit, byRole }
  }

  it('scales each camera light by its role, the ambient, the environment and the exposure', () => {
    const { renderer, scene, kit, byRole } = setup()
    const p = lighting.presets.soft
    const base = (role: string) => lighting.cameraLights.find((l: { role: string }) => l.role === role).intensity * Math.PI
    kit.setPreset('soft')
    expect(byRole('key').intensity).toBeCloseTo(base('key') * p.key, 10)
    expect(byRole('fill').intensity).toBeCloseTo(base('fill') * p.fill, 10)
    expect(byRole('head').intensity).toBeCloseTo(base('head') * p.fill, 10)
    expect(byRole('back-left').intensity).toBeCloseTo(base('back-left') * p.rim, 10)
    expect(scene.environmentIntensity).toBeCloseTo(lighting.environment.intensity * p.environment, 10)
    expect(renderer.toneMappingExposure).toBeCloseTo(lighting.exposure * p.exposure, 10)
    const ambient = scene.children.find((c) => (c as THREE.AmbientLight).isAmbientLight) as THREE.AmbientLight
    expect(ambient.intensity).toBeCloseTo(lighting.ambient.intensity * Math.PI * p.ambient, 10)
    kit.setPreset('no-such-look')
    expect(byRole('key').intensity).toBeCloseTo(base('key'), 10)
    expect(kit.presets[0]).toBe('standard')
    expect(kit.presets).toEqual(expect.arrayContaining(Object.keys(lighting.presets)))
  })

  it('casts shadows from a world-fixed light that takes over part of the key, onto lit meshes only', () => {
    const { renderer, scene, kit, byRole } = setup()
    const root = new THREE.Group()
    const box = new THREE.Mesh(new THREE.BoxGeometry(4, 2, 6), new THREE.MeshStandardMaterial())
    const glass = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial({ transparent: true, opacity: 0.3 }))
    const helper = new THREE.Mesh(new THREE.SphereGeometry(0.1), new THREE.MeshBasicMaterial())
    root.add(box, glass, helper)
    scene.add(root)
    const keyBase = byRole('key').intensity
    kit.setShadows(true, root)
    expect(renderer.shadowMap.enabled).toBe(true)
    const caster = scene.children.find((c) => c.name === 'lightkit-shadow') as THREE.DirectionalLight
    expect(caster.castShadow).toBe(true)
    expect(caster.shadow.mapSize.x).toBe(lighting.shadow.mapSize)
    // It takes `share` of the key, is lit `gain` times that, and the
    // environment dims meanwhile, so the shadow is not lost in it.
    expect(byRole('key').intensity).toBeCloseTo(keyBase * (1 - lighting.shadow.share), 10)
    expect(caster.intensity).toBeCloseTo(keyBase * lighting.shadow.share * lighting.shadow.gain, 10)
    expect(scene.environmentIntensity).toBeCloseTo(lighting.environment.intensity * lighting.shadow.environment, 10)
    expect([box.castShadow, box.receiveShadow]).toEqual([true, true])
    expect(glass.castShadow).toBe(false)
    expect([helper.castShadow, helper.receiveShadow]).toEqual([false, false])
    // Aimed at the bounds' centre from the shadow light, the one Kit casts
    // from too: [-49, -43, 0] -> [-0.4474, 0.7547, 0.4798] (pxr), the upper
    // left of the default view, so the shadows fall where they can be seen.
    expect(caster.target.position.toArray()).toEqual([0, 0, 0])
    expect(lighting.shadow.rotateXYZ).toEqual([-49, -43, 0])
    const d = new THREE.Vector3(...sceneLighting.kitLightDirection(lighting.shadow.rotateXYZ))
    expect(d.toArray().map((v) => +v.toFixed(4))).toEqual([-0.4474, 0.7547, 0.4798])
    expect(sceneLighting.kitLightDirection(lighting.kit.keyRotateXYZ).map((v) => +v.toFixed(4))).toEqual([0.4474, 0.7547, 0.4798])
    expect(caster.position.clone().normalize().dot(d)).toBeCloseTo(1, 6)
    // Cheap: the PCF filter, and the map drawn on demand, not every frame.
    expect(renderer.shadowMap.type).toBe(THREE.PCFShadowMap)
    expect(renderer.shadowMap.autoUpdate).toBe(false)
    expect(renderer.shadowMap.needsUpdate).toBe(true)
    renderer.shadowMap.needsUpdate = false
    kit.refreshShadows!()
    expect(renderer.shadowMap.needsUpdate).toBe(true)

    // The room shell receives but does not cast (the caller's filter).
    const ceiling = new THREE.Mesh(new THREE.PlaneGeometry(4, 6), new THREE.MeshStandardMaterial())
    ceiling.userData.modelId = 'room-ceiling'
    root.add(ceiling)
    kit.setShadowCasterFilter!((mesh) => mesh.userData.modelId !== 'room-ceiling')
    kit.fitShadows(root)
    expect([ceiling.castShadow, ceiling.receiveShadow]).toEqual([false, true])
    expect(box.castShadow).toBe(true)

    kit.setShadows(false)
    expect(renderer.shadowMap.enabled).toBe(false)
    expect(byRole('key').intensity).toBeCloseTo(keyBase, 10)
    expect(scene.environmentIntensity).toBeCloseTo(lighting.environment.intensity, 10)
    expect(box.castShadow).toBe(false)
    kit.dispose()
    expect(scene.children.some((c) => c.name === 'lightkit-shadow')).toBe(false)
  })
})
