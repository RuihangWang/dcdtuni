import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { expect, it } from 'vitest'
import '../src/legacy/geometryBuilder'
import { createModel } from '../src/legacy/factories'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createCfdrunBuild, buildManifestV2, bindStlFileHashes } from '../src/legacy/cfdrunBuild'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import type { CfdProject, Construction } from '../src/schema/cfdproj'

function fixture(mode: 'temperature' | 'power', cut = false) {
  const project = stateV4().getInitialStateV4() as unknown as CfdProject
  project.geometry = { height: 3, plane: [[0, 0], [4, 0], [4, 4], [0, 4]] }
  const { model } = createModel(project, 'box')
  model.meta.name = 'Thermal_block'
  model.geometry!.size = { x: 1, y: 1, z: 1 }
  model.cooling = { role: 'heatSource', heatMode: mode, fixedTemperature: 32, totalPower: 120 } as any
  if (cut) {
    const faces = model.geometry!.faces as any
    // A full-height stripe leaves TWO body pieces; the complete back face
    // leaves NONE. The grilles themselves remain outside the body heat load.
    faces.front.patches = [{ id: 'stripe', type: 'rect', x: .4, y: 0, w: .2, h: 1 }]
    faces.back.patches = [{ id: 'whole', type: 'rect', x: 0, y: 0, w: 1, h: 1 }]
  }
  ;(stateV4() as any).addConstruction(project, model)
  return project
}

function areas(files: Record<string, string>) {
  const out = new Map<string, number>()
  for (const text of Object.values(files)) {
    for (const solid of text.matchAll(/^solid\s+(\S+)\s*\n([\s\S]*?)^endsolid\b/gm)) {
      const vertices = [...solid[2]!.matchAll(/vertex\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)/g)]
        .map(m => new THREE.Vector3(Number(m[1]), Number(m[2]), Number(m[3])))
      let area = 0
      for (let i = 0; i < vertices.length; i += 3) {
        const a = vertices[i]!, b = vertices[i + 1]!, c = vertices[i + 2]!
        area += b.clone().sub(a).cross(c.clone().sub(a)).length() / 2
      }
      out.set(solid[1]!, area)
    }
  }
  return out
}

async function exportActual(project: CfdProject, missing = false) {
  const globalGroup = new THREE.Group(), wrapperGroup = new THREE.Group(), root = new THREE.Group()
  root.add(wrapperGroup); wrapperGroup.add(globalGroup)
  const host: SceneHost = { globalGroup, wrapperGroup, registries: emptyRegistries(), selectedUid: () => null,
    project: () => project, webglAvailable: false, previewMode: () => false }
  const globals = window as unknown as { allModels?: () => Construction[] }
  const old = globals.allModels
  globals.allModels = () => stateV4().allModels(project) as Construction[]
  const builder = createSceneBuilder(host)
  try {
    builder.rebuild(); wrapperGroup.updateMatrixWorld(true)
    const api = createCfdrunBuild({ state: project, globalGroup, LENGTH_UNITS: { m: { scale: 1 } },
      _MESH_MODES: ['bodyFitted', 'cartesianLocalized', 'structured'],
      buildGroupedStlMap: (ids, leakage) => missing ? {} : builder.api.buildGroupedStlMap(ids, leakage) })
    const manifest = await buildManifestV2(api, project, '2026-10-05T00:00:00.000Z')
    const files = builder.api.buildGroupedStlMap(globals.allModels().map(m => m.uid), true) as Record<string, string>
    bindStlFileHashes(manifest, files, project)
    // Opt-in fixture capture for the Python reader/generator integration gate.
    // Normal test runs never rewrite committed primary export fixtures.
    if (process.env.DCDTUNI_THERMAL_TEST_OUTPUT) {
      const box = Object.values(project.constructions!.boxes!)[0]! as any
      const mode = box.geometry.scale.x === 2 ? 'scaled' : box.cooling.heatMode
      const dir = resolve(process.env.DCDTUNI_THERMAL_TEST_OUTPUT, mode)
      mkdirSync(dir, { recursive: true })
      writeFileSync(resolve(dir, 'thermal.cfdrun'), JSON.stringify(manifest, null, 2))
      for (const [name, text] of Object.entries(files)) writeFileSync(resolve(dir, name), text)
    }
    return { manifest, area: areas(files) }
  } finally { builder.clear(); if (old) globals.allModels = old; else delete globals.allModels }
}

it('binds fixed temperature to all six actual STL body faces', async () => {
  const { manifest, area } = await exportActual(fixture('temperature'))
  const thermal = Object.values(manifest.boundaryBindings).filter(b => b.bcType === 'fixedTemperature')
  expect(thermal).toHaveLength(6)
  for (const b of thermal) {
    expect(area.has(b.solidName!)).toBe(true)
    expect(b.patchName).toBe(b.solidName)
    expect(b.parameters!.temperatureC).toBe(32)
    expect(area.get(b.solidName!)).toBeCloseTo(1)
  }
  expect((manifest as any).meshGeometry.heatSources.Thermal_block.wallSolidNames.sort())
    .toEqual(thermal.map(b => b.solidName).sort())
})

it('covers every remaining body piece and preserves total power after grille subtraction', async () => {
  const { manifest, area } = await exportActual(fixture('power', true))
  const thermal = Object.values(manifest.boundaryBindings).filter(b => b.bcType === 'heat_source_flux')
  const expected = [...area.keys()].filter(name => !name.includes('_patch'))
  expect(thermal.map(b => b.solidName).sort()).toEqual(expected.sort())
  expect(thermal.some(b => b.solidName!.includes('Back'))).toBe(false)
  expect(thermal.filter(b => b.solidName!.includes('Front'))).toHaveLength(2)
  const integral = thermal.reduce((sum, b) => sum + Number(b.parameters!.heatFluxWm2) * area.get(b.solidName!)!, 0)
  expect(integral).toBeCloseTo(120, 6)
  for (const b of thermal) expect(b.parameters!.surfaceAreaM2).toBeCloseTo(4.8)
})

it('uses actual world surface area when a box is scaled', async () => {
  const p = fixture('power')
  const box = Object.values(p.constructions!.boxes!)[0]!
  box.geometry!.scale = { x: 2, y: 1, z: 3 }
  const { manifest, area } = await exportActual(p)
  expect([...area.values()].reduce((a, b) => a + b, 0)).toBeCloseTo(22)
  const thermal = Object.values(manifest.boundaryBindings).filter(b => b.bcType === 'heat_source_flux')
  expect(thermal).toHaveLength(6)
  const integral = thermal.reduce((sum, b) => sum + Number(b.parameters!.heatFluxWm2) * area.get(b.solidName!)!, 0)
  expect(integral).toBeCloseTo(120, 6)
})

it('refuses a thermal declaration when the actual grouped STL lacks its body', async () => {
  await expect(exportActual(fixture('power'), true)).rejects.toThrow(/thermal source.*missing.*STL/)
})
