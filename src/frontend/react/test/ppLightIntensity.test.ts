import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * 渲染场光照强度 reaches the volume too.
 *
 * Every post-processing layer is drawn with a ShaderMaterial that does its own
 * lighting, so the slider works by walking the group and writing a uniform. The
 * surface, the slices and the streamline tubes all call theirs
 * `uLightIntensity` — and the VOLUME does not have one. Its ray-marcher scales
 * the marched colour by `uBrightness` (`ppVolume.ts`), so the traversal walked
 * straight past it and the slider dimmed everything except the one layer that
 * is pure colour.
 *
 * ## Why this is a source assertion and not a render
 *
 * The write lives inside a closure in `Viewport`'s scene-setup effect, reached
 * only through a WebGL context that jsdom cannot give. Rendering the viewport
 * here would test the mock, not the code. So this pins the two facts that make
 * the bug impossible to reintroduce silently: the volume declares
 * `uBrightness`, and the light applier writes it. The browser is what confirmed
 * the behaviour.
 */

const SRC = join(__dirname, '..', 'src')

function read(rel: string): string {
  return readFileSync(join(SRC, rel), 'utf8')
}

describe('the volume shader', () => {
  const volume = read('postprocess/ppVolume.ts')

  it('lights itself with uBrightness, not uLightIntensity', () => {
    expect(volume).toMatch(/uniform float uBrightness/)
    expect(volume).toMatch(/color\.rgb \*= uBrightness/)
    // If this ever stops being true the applier below should follow, and the
    // assertion that fails first should be this one.
    expect(volume).not.toMatch(/uniform float uLightIntensity/)
  })
})

describe('applyPpLightIntensity', () => {
  const viewport = read('components/Viewport.tsx')
  const applier = viewport.slice(
    viewport.indexOf('const applyPpLightIntensity'),
    viewport.indexOf('ppLightRef.current = applyPpLightIntensity'),
  )

  it('exists as one traversal, so no layer can be forgotten in a second copy', () => {
    expect(applier).toContain('ppGroupRef.current?.traverse')
    expect(viewport.split('const applyPpLightIntensity').length - 1).toBe(1)
  })

  it('writes uLightIntensity — the surface, slices and tubes', () => {
    expect(applier).toContain('uLightIntensity')
  })

  it('writes uBrightness — the volume', () => {
    expect(applier).toContain('uBrightness')
  })

  it('writes the SAME value to both, so the layers cannot disagree', () => {
    // One `value`, computed once from the toggle and the slider.
    expect(applier).toMatch(/const value = lightsEnabledRef\.current \? lightScaleRef\.current : 0/)
    const writes = [...applier.matchAll(/\.value = value/g)]
    expect(writes.length).toBe(2)
  })
})
