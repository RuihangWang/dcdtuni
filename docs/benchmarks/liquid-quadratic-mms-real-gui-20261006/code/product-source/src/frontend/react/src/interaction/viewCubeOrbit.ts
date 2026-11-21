/**
 * Orbiting the main camera from a widget that is not the viewport.
 *
 * A port of the legacy `orbitMainCamera` (STL_generate.html:17836), which the
 * view cube drives while its cube is being dragged. OrbitControls cannot be
 * asked to rotate by a pixel delta from outside — its own rotation state is
 * private to its pointer handlers — so the camera is moved the way that
 * function does it: convert the offset from the target into spherical
 * coordinates, turn the two angles, convert back.
 *
 * The angle conversion is split out and pure because it is the part with a
 * convention in it: the same pixel delta must turn the camera by the same
 * amount as an equivalent drag in the viewport, which is what makes the cube
 * feel attached to the scene rather than to a second, differently-geared,
 * control. Getting the sign or the denominator wrong is invisible in a
 * screenshot and obvious in a test.
 */

import * as THREE from 'three'
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

/** Y-up, as the whole editor is (`WORLD_UP`, STL_generate.html:1600). */
export const WORLD_UP = Object.freeze({ x: 0, y: 1, z: 0 })

export interface OrbitLimits {
  minAzimuthAngle: number
  maxAzimuthAngle: number
  minPolarAngle: number
  maxPolarAngle: number
  /** OrbitControls' own gearing, so the cube and the canvas turn alike. */
  rotateSpeed: number
}

export const DEFAULT_LIMITS: OrbitLimits = {
  minAzimuthAngle: -Infinity,
  maxAzimuthAngle: Infinity,
  minPolarAngle: 0,
  maxPolarAngle: Math.PI,
  rotateSpeed: 1,
}

/**
 * Where the two spherical angles land after a drag of `dx`, `dy` pixels.
 *
 * Both deltas are divided by the viewport HEIGHT, not by its own axis: that is
 * OrbitControls' rule and the legacy copies it, so a horizontal drag across a
 * wide window turns the camera by the same angle as the same drag across a
 * narrow one.
 */
export function orbitAngles(
  theta: number,
  phi: number,
  dx: number,
  dy: number,
  height: number,
  limits: OrbitLimits = DEFAULT_LIMITS,
): { theta: number; phi: number } {
  const h = Math.max(1, height)
  const speed = Number.isFinite(limits.rotateSpeed) ? limits.rotateSpeed : 1
  const nextTheta = theta - (2 * Math.PI * dx) / h * speed
  const nextPhi = phi - (2 * Math.PI * dy) / h * speed
  // `makeSafe`'s epsilon: a polar angle of exactly 0 or PI puts the camera on
  // its own up vector and the view collapses to nothing.
  const EPS = 0.000001
  return {
    theta: Math.max(limits.minAzimuthAngle, Math.min(limits.maxAzimuthAngle, nextTheta)),
    phi: Math.max(
      EPS,
      Math.min(
        Math.PI - EPS,
        Math.max(limits.minPolarAngle, Math.min(limits.maxPolarAngle, nextPhi)),
      ),
    ),
  }
}

/** Read the limits off a live OrbitControls, defaults where it has none. */
export function limitsOf(controls: OrbitControls): OrbitLimits {
  return {
    minAzimuthAngle: controls.minAzimuthAngle ?? DEFAULT_LIMITS.minAzimuthAngle,
    maxAzimuthAngle: controls.maxAzimuthAngle ?? DEFAULT_LIMITS.maxAzimuthAngle,
    minPolarAngle: controls.minPolarAngle ?? DEFAULT_LIMITS.minPolarAngle,
    maxPolarAngle: controls.maxPolarAngle ?? DEFAULT_LIMITS.maxPolarAngle,
    rotateSpeed: controls.rotateSpeed ?? DEFAULT_LIMITS.rotateSpeed,
  }
}

/**
 * Turn the camera about the orbit target by a pixel delta.
 *
 * The up vector is put back to world up first, as the legacy's
 * `normalizeMainCameraUp` does: the top and bottom presets leave it pointing
 * along ±Z, and orbiting from there with that up still in place rolls the
 * horizon over as the camera moves.
 */
export function orbitCameraByPixels(
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
  dx: number,
  dy: number,
  height: number,
): void {
  const target = controls.target
  const offset = camera.position.clone().sub(target)
  camera.up.set(WORLD_UP.x, WORLD_UP.y, WORLD_UP.z)

  const spherical = new THREE.Spherical().setFromVector3(offset)
  const next = orbitAngles(spherical.theta, spherical.phi, dx, dy, height, limitsOf(controls))
  spherical.theta = next.theta
  spherical.phi = next.phi
  offset.setFromSpherical(spherical)

  camera.position.copy(target).add(offset)
  camera.lookAt(target)
  controls.update()
}
