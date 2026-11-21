import { describe, expect, it } from 'vitest'
import { emptyManifest } from '../src/schema/cfdrun'
import type { CfdProject } from '../src/schema/cfdproj'
import {
  buildRunBody,
  cfdrunFilename,
  encodeStlFiles,
  runRequestBody,
  type RunBuildDeps,
} from '../src/run/runPayload'

/**
 * What the server does with this body is destructive, which is why it is worth
 * testing as data rather than as a screenshot: `handle_run_mesh` writes
 * `cfdrunJson` over `<case>/model/<name>.cfdrun` and, whenever `stlFiles` is
 * non-empty, DELETES every .stl and .eMesh in `<case>/Mesh/constant/triSurface/`
 * before writing the new set. A body with the wrong filename leaves two
 * manifests in `model/` and the runner then picks one alphabetically; a solve
 * that carried an STL map would wipe and rewrite the geometry of a mesh it is
 * not rebuilding.
 */

function project(meta: Record<string, unknown> = {}): CfdProject {
  return { meta, constructions: {} } as unknown as CfdProject
}

function deps(over: Partial<RunBuildDeps> = {}): RunBuildDeps {
  return {
    project: project({ projectName: 'Demo Room' }),
    projectPath: null,
    modelUids: () => ['m1', 'm2'],
    duplicateSolids: () => [],
    buildManifest: async () => ({ ...emptyManifest(), formatVersion: 2, runId: 'r1' }),
    buildStl: () => ({ 'merged_rack.stl': 'solid rack\nendsolid rack\n' }),
    ...over,
  }
}

describe('cfdrunFilename', () => {
  it('takes the loaded project basename', () => {
    expect(cfdrunFilename(project(), '/abs/case/Base604/Base604.cfdproj')).toBe('Base604.cfdrun')
  })

  it('is case-insensitive about the extension and keeps the rest of the stem', () => {
    expect(cfdrunFilename(project(), '/a/b/My.Room.v2.CFDPROJ')).toBe('My.Room.v2.cfdrun')
  })

  it('falls back to the sanitised project name when nothing is loaded from disk', () => {
    // The name reaches a filesystem path and an OpenFOAM case name, so the
    // space cannot survive.
    expect(cfdrunFilename(project({ projectName: 'Demo Room' }), null)).toBe('Demo_Room.cfdrun')
  })

  it('prefers projectName over exportFileName, then falls back to "project"', () => {
    expect(cfdrunFilename(project({ projectName: 'a', exportFileName: 'b' }), null)).toBe(
      'a.cfdrun',
    )
    expect(cfdrunFilename(project({ exportFileName: 'b' }), null)).toBe('b.cfdrun')
    expect(cfdrunFilename(project(), null)).toBe('project.cfdrun')
  })
})

describe('encodeStlFiles', () => {
  it('base64-encodes each file', () => {
    expect(encodeStlFiles({ 'a.stl': 'solid a' })).toEqual({ 'a.stl': btoa('solid a') })
  })

  it('survives a non-ASCII solid name instead of throwing', () => {
    // `btoa` alone throws on anything above 0xFF, and a model can be named in
    // Chinese — the whole export would die on one rack.
    const out = encodeStlFiles({ 'a.stl': 'solid 机柜' })
    expect(atob(out['a.stl'] ?? '')).toBe(unescape(encodeURIComponent('solid 机柜')))
  })
})

describe('runRequestBody', () => {
  it('sends the STL set for a mesh run', () => {
    const body = runRequestBody('mesh', '{}', 'x.cfdrun', { 'merged_rack.stl': 'AAA' })
    expect(body).toEqual({
      cfdrunJson: '{}',
      cfdrunFilename: 'x.cfdrun',
      stlFiles: { 'merged_rack.stl': 'AAA' },
      meshOnly: true,
      solveOnly: false,
    })
  })

  it('drops the STL set for a solve run', () => {
    const body = runRequestBody('solve', '{}', 'x.cfdrun', { 'merged_rack.stl': 'AAA' })
    expect(body.stlFiles).toEqual({})
    expect(body.meshOnly).toBe(false)
    expect(body.solveOnly).toBe(true)
  })
})

describe('buildRunBody', () => {
  it('carries a freshly built manifest under the project filename', async () => {
    const body = await buildRunBody('mesh', deps({ projectPath: '/case/STDCT/STDCT.cfdproj' }))
    expect(body.cfdrunFilename).toBe('STDCT.cfdrun')
    // The manifest travels verbatim; assert the marker fields rather than
    // deep-equalling a whole V2 document.
    expect(JSON.parse(body.cfdrunJson)).toMatchObject({ formatVersion: 2, runId: 'r1', schemaVersion: 2 })
  })

  it('uploads a non-empty STL map for mesh and an empty one for solve', async () => {
    const mesh = await buildRunBody('mesh', deps())
    expect(Object.keys(mesh.stlFiles)).toEqual(['merged_rack.stl'])

    const solve = await buildRunBody('solve', deps())
    expect(solve.stlFiles).toEqual({})
  })

  it('binds the current cached grouped STL source metadata for solve without uploading geometry', async () => {
    let built = 0
    const body = await buildRunBody('solve', deps({
      buildStl: () => { built += 1; return { 'merged_rack.stl': 'solid rack\nendsolid rack\n' } },
    }))
    expect(built).toBe(1)
    expect(body.stlFiles).toEqual({})
    const geometry = JSON.parse(body.cfdrunJson).geometryExport
    expect(Object.values(geometry.stlFiles)).toEqual([
      expect.objectContaining({ path: 'merged_rack.stl', contentHash: expect.stringMatching(/^[0-9a-f]{64}$/) }),
    ])
  })

  it('still rebuilds the manifest for a solve', async () => {
    // The point of G66: endTime, gravity and the schemes only reach the run
    // through this manifest.
    let built = 0
    const body = await buildRunBody(
      'solve',
      deps({
        buildManifest: async () => {
          built += 1
          return { ...emptyManifest(), simulationConfig: { endTime: 1234 } }
        },
      }),
    )
    expect(built).toBe(1)
    expect(JSON.parse(body.cfdrunJson)).toMatchObject({ simulationConfig: { endTime: 1234 } })
  })

  it('refuses a mesh run whose models claim the same solid name', async () => {
    await expect(
      buildRunBody(
        'mesh',
        deps({ duplicateSolids: () => [{ name: 'rack_inlet', sources: ['R1', 'R2'] }] }),
      ),
    ).rejects.toThrow(/rack_inlet/)
  })

  it('lets a solve through on duplicates, as the legacy solve path does', async () => {
    const body = await buildRunBody(
      'solve',
      deps({ duplicateSolids: () => [{ name: 'rack_inlet', sources: ['R1', 'R2'] }] }),
    )
    expect(body.solveOnly).toBe(true)
  })

  it('refuses when there is no geometry to send', async () => {
    // An empty upload is not a no-op: the server clears triSurface/ whenever
    // the map is non-empty and the manifest is written regardless, so a run
    // fired before a project was loaded would overwrite the case.
    await expect(buildRunBody('mesh', deps({ modelUids: () => [] }))).rejects.toThrow()
    await expect(buildRunBody('mesh', deps({ project: null }))).rejects.toThrow()
  })

  it('builds the manifest before the STL, so the shared grouped map is reusable', async () => {
    const order: string[] = []
    await buildRunBody(
      'mesh',
      deps({
        buildManifest: async () => {
          order.push('manifest')
          return emptyManifest()
        },
        buildStl: () => {
          order.push('stl')
          return { 'merged_rack.stl': 'solid rack\nendsolid rack\n' }
        },
      }),
    )
    expect(order).toEqual(['manifest', 'stl'])
  })
})
