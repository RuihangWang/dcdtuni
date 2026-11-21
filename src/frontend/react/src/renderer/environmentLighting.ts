/**
 * The viewport's lighting, from the shared js/scene_lighting.js.
 *
 * Both editors light the scene from ONE definition: the `lighting` section of
 * shared/textures/materials.json (tone mapping, exposure, the camera light
 * kit, ambient, the pre-filtered room environment). The Omniverse side reads
 * the same section. This file only gives the React side typed access to it.
 *
 * Every scene surface is MeshStandardMaterial, so the environment matters to
 * all of them: it is what metal and satin paint reflect.
 */

import type * as THREE from 'three'
import '../legacy/materialManifest'
import '../../../shared/js/scene_lighting.js'
import { materialLibrary } from '../legacy/materialManifest'

export interface LightKit {
  lights: THREE.Light[]
  baseIntensities: number[]
  environmentIntensity: number
  /** The manifest's preset ids, 'standard' first. */
  presets: string[]
  setScale(value: number): void
  setEnabled(on: boolean): void
  setPreset(id: string): void
  /** A world-fixed shadow light casting onto the meshes under `root`. */
  setShadows(on: boolean, root?: THREE.Object3D | null): void
  /** Re-aim the shadow light after the scene under `root` was rebuilt. */
  fitShadows(root?: THREE.Object3D | null): void
  /** Draw the shadow map again on the next frame; it is not redrawn every frame. */
  refreshShadows?(): void
  /** `filter(mesh) === false` keeps a mesh from casting; applied at the next fit. */
  setShadowCasterFilter?(filter: ((mesh: THREE.Object3D) => boolean) | null): void
  dispose(): void
}

export interface SceneLightingApi {
  /** Unit vector toward a Kit DistantLight with this xformOp:rotateXYZ (degrees). */
  kitLightDirection(degrees: readonly number[]): [number, number, number]
  configureRenderer(three: typeof THREE, renderer: THREE.WebGLRenderer): void
  roomEnvironmentScene(three: typeof THREE): THREE.Scene
  disposeScene(scene: THREE.Object3D): void
  installEnvironment(three: typeof THREE, renderer: THREE.WebGLRenderer, scene: THREE.Scene): () => void
  install(three: typeof THREE, renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.Camera): LightKit
}

const api = (globalThis as unknown as { sceneLighting?: SceneLightingApi }).sceneLighting
if (!api) throw new Error('js/scene_lighting.js did not attach to globalThis')
export const sceneLighting: SceneLightingApi = api

/** The environment's intensity at a light scale of 1 (the manifest's). */
export const ENVIRONMENT_INTENSITY: number = materialLibrary.lighting().environment.intensity

/**
 * Install only the environment; returns its disposer. Never throws: without a
 * working WebGL context (jsdom, a lost context) the scene simply keeps no
 * environment.
 */
export function installRoomEnvironment(three: typeof THREE, renderer: THREE.WebGLRenderer, scene: THREE.Scene): () => void {
  return sceneLighting.installEnvironment(three, renderer, scene)
}
