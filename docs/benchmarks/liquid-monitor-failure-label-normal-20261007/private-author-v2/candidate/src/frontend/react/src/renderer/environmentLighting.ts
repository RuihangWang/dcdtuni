/**
 * Image-based light for the viewport's physically based surfaces.
 *
 * The liquid equipment (pipes, fittings, manifolds, CDU, cold plates) is
 * MeshStandardMaterial, and a metal lit only by the camera LightKit's five
 * directional lights has almost nothing to reflect — brushed stainless and
 * copper read as flat dark grey. A pre-filtered `RoomEnvironment` gives them
 * something to reflect.
 *
 * Scope: `scene.environment` feeds MeshStandardMaterial/MeshPhysicalMaterial
 * only. The Phong preset kinds (racks, walls, floor) and the post-processing
 * ShaderMaterials ignore it, so their look — and the LightKit/exposure they
 * were tuned under — is unchanged. The intensity is deliberately low and
 * follows the viewport's light slider.
 */

import * as THREE from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'

export const ENVIRONMENT_INTENSITY = 0.5

/**
 * Install the environment; returns its disposer. Never throws: without a
 * working WebGL context (jsdom, a lost context) the scene simply keeps no
 * environment, which is how it rendered before.
 */
export function installRoomEnvironment(
  renderer: THREE.WebGLRenderer,
  scene: THREE.Scene,
  intensity: number = ENVIRONMENT_INTENSITY,
): () => void {
  let pmrem: THREE.PMREMGenerator | null = null
  let room: RoomEnvironment | null = null
  try {
    pmrem = new THREE.PMREMGenerator(renderer)
    room = new RoomEnvironment()
    const target = pmrem.fromScene(room, 0.04)
    scene.environment = target.texture
    scene.environmentIntensity = intensity
    return () => {
      if (scene.environment === target.texture) scene.environment = null
      target.dispose()
    }
  } catch (err) {
    console.warn('[viewport] environment lighting unavailable; standard materials use direct light only', err)
    return () => {}
  } finally {
    room?.dispose()
    pmrem?.dispose()
  }
}
