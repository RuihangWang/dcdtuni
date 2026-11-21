import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ENVIRONMENT_INTENSITY, installRoomEnvironment } from '../src/renderer/environmentLighting'

/**
 * The viewport's image-based light for MeshStandardMaterial surfaces, at the
 * manifest's intensity (textures/materials.json `lighting.environment`). jsdom
 * has no WebGL, so the PMREM bake is stubbed for the success path and left to
 * fail for the guard.
 */

const lighting = JSON.parse(readFileSync(resolve(process.cwd(), '../shared/textures/materials.json'), 'utf8')).lighting

afterEach(() => vi.restoreAllMocks())

describe('installRoomEnvironment', () => {
  it('sets a dimmed scene.environment and releases everything it made', () => {
    const target = { texture: new THREE.Texture(), dispose: vi.fn() }
    const fromScene = vi.spyOn(THREE.PMREMGenerator.prototype, 'fromScene')
      .mockReturnValue(target as unknown as THREE.WebGLRenderTarget)
    const pmremDispose = vi.spyOn(THREE.PMREMGenerator.prototype, 'dispose').mockImplementation(() => {})
    const scene = new THREE.Scene()
    const dispose = installRoomEnvironment(THREE, {} as THREE.WebGLRenderer, scene)
    expect(fromScene).toHaveBeenCalledTimes(1)
    expect(scene.environment).toBe(target.texture)
    expect(scene.environmentIntensity).toBe(ENVIRONMENT_INTENSITY)
    expect(ENVIRONMENT_INTENSITY).toBe(lighting.environment.intensity)
    expect(fromScene.mock.calls[0]![1]).toBe(lighting.environment.blurSigma)
    expect(pmremDispose).toHaveBeenCalledTimes(1) // the generator is not kept
    expect(scene.background).toBeNull() // lighting only: the CSS backdrop stays visible
    dispose()
    expect(scene.environment).toBeNull()
    expect(target.dispose).toHaveBeenCalledTimes(1)
  })

  it('without WebGL it leaves the scene as it was and does not throw', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(THREE.PMREMGenerator.prototype, 'fromScene').mockImplementation(() => {
      throw new Error('no WebGL context')
    })
    const scene = new THREE.Scene()
    const dispose = installRoomEnvironment(THREE, {} as THREE.WebGLRenderer, scene)
    expect(scene.environment).toBeNull()
    expect(() => dispose()).not.toThrow()
  })
})
