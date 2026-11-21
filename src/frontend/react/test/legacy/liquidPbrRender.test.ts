import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MeshColorOverlay } from '../../src/liquid/meshColorOverlay'

/**
 * The shared liquid renderers (pipes, couplers, caps, valves, tees, hoses)
 * build their surfaces from js/pbr_materials.js, and a result colour still
 * wins over the texture on both colouring paths:
 *
 *   React  — MeshColorOverlay clones the material and sets `color`; the
 *            tintable colour maps are high-key neutral, so the colour reads.
 *   legacy — `_togglePipeTemperatureColoring` recolours in place; it drops the
 *            colour map while painted and restores it afterwards.
 *
 * The images never load in jsdom, so the loader's callbacks are driven by hand
 * with canvas-like fake textures.
 */

type Bag = Record<string, unknown>
type Lib = {
  _resetForTests(): void
  loadTextureSets(base: string): void
  SETS: Record<string, { tintable: boolean }>
}

const manifest = JSON.parse(
  readFileSync(resolve(process.cwd(), '../shared/textures/materials.json'), 'utf8'),
) as { sets: Record<string, { stats: { meanLuminance: number } }> }

let globalGroup: THREE.Group
let models: Bag[]

function lib(): Lib {
  return (window as unknown as { pbrMaterials: Lib }).pbrMaterials
}

/** Request every set, then land every image with a fake canvas texture. */
function landAllTextures() {
  const onLoads: Array<() => void> = []
  vi.spyOn(THREE.TextureLoader.prototype, 'load').mockImplementation(((url: string, onLoad?: (t: THREE.Texture) => void) => {
    const image = { width: 2, height: 2, toDataURL: () => `data:image/png;base64,${btoa(url)}` }
    onLoads.push(() => onLoad?.(new THREE.Texture(image as unknown as HTMLCanvasElement)))
    return new THREE.Texture()
  }) as unknown as THREE.TextureLoader['load'])
  lib().loadTextureSets('/textures')
  return () => onLoads.forEach((f) => f())
}

function pipeSegment(uid: string, loopRole: string, over: Bag = {}): Bag {
  return {
    uid,
    kind: 'pipe_segment',
    meta: { name: uid, enabled: true },
    geometry: {
      diameterM: 0.03,
      waypoints: [{ x: 0, y: 1, z: 0 }, { x: 2, y: 1, z: 0 }],
    },
    cooling: { loopRole, connection: { blindEnds: { start: true } } },
    ...over,
  }
}

function meshes(pred: (m: THREE.Mesh) => boolean): THREE.Mesh[] {
  const out: THREE.Mesh[] = []
  globalGroup.traverse((o) => { if ((o as THREE.Mesh).isMesh && pred(o as THREE.Mesh)) out.push(o as THREE.Mesh) })
  return out
}
const std = (m: THREE.Mesh) => m.material as THREE.MeshStandardMaterial

beforeEach(async () => {
  const w = window as unknown as Bag
  globalGroup = new THREE.Group()
  models = []
  w.THREE = THREE
  w.globalGroup = globalGroup
  w.state = {}
  w.allModels = () => models
  w.requestRender = () => {}
  w._modelMap = { get: (uid: string) => models.find((m) => m.uid === uid) ?? null }
  await import('../../../shared/js/pbr_materials.js')
  await import('../../../shared/js/liquid_pipe_renderer.js')
  await import('../../../shared/js/liquid_mode.js')
  lib()._resetForTests()
})

afterEach(() => {
  vi.restoreAllMocks()
  lib()._resetForTests()
  const w = window as unknown as Bag
  delete w.globalGroup
  delete w.state
  delete w.allModels
  delete w._modelMap
})

function renderPipes() {
  ;(window as unknown as { _liquidPipeRenderer: { updatePipeGeometry(): void } })._liquidPipeRenderer.updatePipeGeometry()
}
function renderValves() {
  ;(window as unknown as { _liquidMode: { renderValves(): void } })._liquidMode.renderValves()
}

describe('liquid pipes', () => {
  it('are foam-insulated MeshStandardMaterial, colour-coded, one material per pipe, identity intact', () => {
    const land = landAllTextures()
    models = [pipeSegment('p_sup', 'supply'), pipeSegment('p_ret', 'return')]
    renderPipes()
    land() // images arrive AFTER the pipes were built: the maps bind late
    const pipes = meshes((m) => !!m.userData.isPipe && typeof m.userData.pipeModelId === 'string')
    expect(pipes).toHaveLength(2)
    const sup = pipes.find((m) => m.userData.pipeModelId === 'p_sup')!
    const ret = pipes.find((m) => m.userData.pipeModelId === 'p_ret')!
    for (const p of [sup, ret]) {
      expect(p.material).toBeInstanceOf(THREE.MeshStandardMaterial)
      expect(std(p).userData.pbrRole).toBe('pipe-insulation')
      expect(std(p).map).not.toBeNull()
      expect(std(p).normalMap).not.toBeNull()
      expect(std(p).roughnessMap).not.toBeNull()
      expect(std(p).opacity).toBe(0.85)
    }
    expect(sup.userData).toMatchObject({ isPipe: true, pipeColor: 0x2196f3, pipeModelId: 'p_sup' })
    expect(std(sup).color.getHex()).toBe(0x2196f3)
    expect(std(ret).color.getHex()).toBe(0xf44336)
    expect(sup.material).not.toBe(ret.material)
    expect(std(sup).map).toBe(std(ret).map) // one texture per image
    // 2 m pipe, 0.25 m tiles along it
    const uv = sup.geometry.getAttribute('uv')
    let maxU = 0
    for (let i = 0; i < uv.count; i++) maxU = Math.max(maxU, uv.getX(i))
    expect(maxU).toBeCloseTo(8, 0)
  })

  it('blind end caps are rubber and keep their cap identity', () => {
    models = [pipeSegment('p_sup', 'supply')]
    renderPipes()
    const caps = meshes((m) => !!m.userData.isBlindEndCap)
    expect(caps).toHaveLength(1)
    expect(std(caps[0]!).userData.pbrRole).toBe('rubber')
    expect(caps[0]!.userData).toMatchObject({ isBlindEndCap: true, pipeId: 'p_sup', end: 'start' })
  })
})

describe('a result colour still wins', () => {
  it('React overlay: the painted clone shows the result colour over a high-key tintable map', () => {
    const land = landAllTextures()
    models = [pipeSegment('p_sup', 'supply')]
    renderPipes()
    land()
    const pipe = meshes((m) => m.userData.pipeModelId === 'p_sup')[0]!
    const original = std(pipe)
    const set = original.userData.pbrSet as string
    expect(lib().SETS[set]!.tintable).toBe(true)
    expect(manifest.sets[set]!.stats.meanLuminance).toBeGreaterThanOrEqual(0.85)
    const overlay = new MeshColorOverlay()
    overlay.paint(pipe, 0x00ff00)
    expect(std(pipe).color.getHex()).toBe(0x00ff00)
    expect(original.color.getHex()).toBe(0x2196f3)
    overlay.restore()
    expect(pipe.material).toBe(original)
  })

  it('legacy overlay: the colour map comes off while painted and goes back on restore', () => {
    const land = landAllTextures()
    models = [pipeSegment('p_sup', 'supply'), pipeSegment('p_ret', 'return')]
    renderPipes()
    land()
    ;(window as unknown as Bag).state = { liquidResults: { cell_temp_K: [[300], [320]] } }
    // A flat green colormap, so the painted colour can never coincide with the
    // supply blue (the built-in fallback ramp starts exactly at it).
    const green = new Uint8Array(256 * 4).map((_, i) => (i % 4 === 1 || i % 4 === 3 ? 255 : 0))
    ;(window as unknown as Bag)._ppColormapPresets = { coolwarm: () => green }
    const pipe = meshes((m) => m.userData.pipeModelId === 'p_sup')[0]!
    const map = std(pipe).map
    expect(map).not.toBeNull()
    const toggle = (window as unknown as { _togglePipeTemperatureColoring(on: boolean): void })._togglePipeTemperatureColoring
    toggle(true)
    expect(std(pipe).map).toBeNull()
    expect(std(pipe).normalMap).not.toBeNull() // relief stays
    expect(std(pipe).color.getHex()).toBe(0x00ff00)
    toggle(false)
    delete (window as unknown as Bag)._ppColormapPresets
    expect(std(pipe).map).toBe(map)
    expect(std(pipe).color.getHex()).toBe(0x2196f3)
    expect(std(pipe).userData.pbrMapSuppressed).toBeUndefined()
  })
})

describe('valves and tees', () => {
  function withFittings() {
    models = [pipeSegment('p_sup', 'supply', {
      cooling: {
        loopRole: 'supply',
        connection: {},
        valves: [
          { id: 'v_gate', type: 'gate_valve', position: { x: 0.5, y: 1, z: 0 } },
          { id: 'v_tee', type: 'tee_junction', position: { x: 1.5, y: 1, z: 0 } },
        ],
      },
    })]
  }

  it('gate valve: brass body, loop-coloured flanges, stainless stem, rubber handwheel', () => {
    const land = landAllTextures()
    withFittings()
    renderValves()
    land()
    const gate = meshes(() => true).filter((m) => m.parent?.userData.valveId === 'v_gate')
    expect(gate[0]!.parent!.userData).toMatchObject({ isValve: true, isTeeJunction: false, valveId: 'v_gate', pipeId: 'p_sup' })
    const roles = gate.map((m) => std(m).userData.pbrRole)
    expect(roles[0]).toBe('brass')
    expect(roles.filter((r) => r === 'painted-steel')).toHaveLength(2)
    expect(roles).toContain('stainless')
    expect(roles).toContain('rubber')
    const body = std(gate[0]!)
    expect(body.map).not.toBeNull()
    expect(body.color.getHex()).toBe(0xffffff) // brass hue is in the map once bound
    const flanges = gate.filter((m) => std(m).userData.pbrRole === 'painted-steel')
    expect(flanges[0]!.material).not.toBe(flanges[1]!.material)
    for (const f of flanges) {
      expect(std(f).color.getHex()).toBe(0x2196f3)
      expect(std(f).map).not.toBeNull() // the bottom flange is not a pre-load clone
    }
  })

  it('tee: stainless body tinted with the loop colour', () => {
    withFittings()
    renderValves()
    const tee = meshes(() => true).filter((m) => m.parent?.userData.valveId === 'v_tee')
    expect(tee[0]!.parent!.userData).toMatchObject({ isValve: true, isTeeJunction: true, valveId: 'v_tee' })
    expect(tee.every((m) => std(m).userData.pbrRole === 'stainless')).toBe(true)
    expect(std(tee[0]!).color.getHex()).toBe(0x26c6da)
    expect(std(tee[0]!).map).toBeNull() // nothing requested in this test: no maps
  })
})
