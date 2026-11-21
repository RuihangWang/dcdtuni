import { describe, expect, it } from 'vitest'
import { schema, stateV4 } from '../src/legacy/cfdprojModules'
import { checkProjectDocument, readProjectFileText, serializeProjectState } from '../src/io/projectFiles'
import type { CfdProject } from '../src/schema/cfdproj'
import { bindStlFileHashes, buildManifestV2, cfdrunExport } from '../src/legacy/cfdrunBuild'
import { buildRunBody } from '../src/run/runPayload'
import { buildCfdPackage } from '../src/io/cfdPackage'
import { emptyManifest } from '../src/schema/cfdrun'

type Topology = Record<string, unknown>

function project(): CfdProject {
  const p = stateV4().getInitialStateV4() as unknown as CfdProject
  p.uid = 'proj_geom'
  p.name = 'Geometry topology draft'
  return p
}

function topology(): Topology {
  return {
    version: 1,
    mode: 'strict',
    coverage: 'airInternalSheets',
    wallPairs: {
      wall_1: {
        sides: ['proj_geom/m_1/skin_a/source-sheet', 'proj_geom/m_1/skin_b/source-sheet'],
        thickness_m: 0.02,
        meshReferenceFaceUid: 'proj_geom/m_1/skin_a/source-sheet',
        meshTreatment: 'collapsedAirBarrier',
      },
    },
    independentPairs: {
      gap_1: {
        faces: ['proj_geom/m_2/source-sheet', 'proj_geom/m_3/source-sheet'],
        relation: 'fluidGap',
        minGap_m: 0.04,
      },
    },
    edgeContacts: {
      seam_1: {
        kind: 'edgeFace',
        edge: {
          sourceFaceUid: 'proj_geom/m_4/source-sheet',
          sourceEdgeUid: 'proj_geom/m_4/source-sheet/edge/bottom',
        },
        face: { sourceFaceUid: 'proj_geom/m_5/source-sheet' },
      },
      seam_2: {
        kind: 'edgeEdge',
        a: {
          sourceFaceUid: 'proj_geom/m_6/source-sheet',
          sourceEdgeUid: 'proj_geom/m_6/source-sheet/edge/right',
        },
        b: {
          sourceFaceUid: 'proj_geom/m_7/source-sheet',
          sourceEdgeUid: 'proj_geom/m_7/source-sheet/edge/left',
        },
      },
    },
  }
}

function withTopology(value: unknown): CfdProject {
  return Object.assign(project(), { geometryTopology: value })
}

function relation(t: Topology, mapName: string, id: string): Record<string, unknown> {
  return ((t[mapName] as Record<string, unknown>)[id] as Record<string, unknown>)
}

function errors(value: unknown): string[] {
  return schema().validateDocument(withTopology(value)).map(({ path, msg }) => `${path}: ${msg}`)
}

describe('optional geometryTopology V1 project section', () => {
  it('keeps an old project byte-identical when the optional section is absent', () => {
    const original = serializeProjectState(project())
    expect(original).not.toContain('geometryTopology')
    const read = readProjectFileText(original)
    expect(read.ok).toBe(true)
    if (read.ok) expect(serializeProjectState(read.project)).toBe(original)
  })

  it('validates and preserves the complete strict draft on save and reopen', () => {
    const p = withTopology(topology())
    expect(schema().validateDocument(p)).toEqual([])
    const saved = serializeProjectState(p)
    const reopened = readProjectFileText(saved)
    expect(reopened.ok).toBe(true)
    if (reopened.ok) {
      expect(reopened.project.geometryTopology).toEqual(topology())
      expect(serializeProjectState(reopened.project)).toBe(saved)
    }
  })

  it('allows a wall side to have a separate fluid-gap relation with a third face', () => {
    const t = topology()
    relation(t,'independentPairs','gap_1').faces = [
      'proj_geom/m_1/skin_a/source-sheet', 'proj_geom/m_3/source-sheet',
    ]
    expect(errors(t)).toEqual([])
    const reopened = readProjectFileText(serializeProjectState(withTopology(t)))
    expect(reopened.ok).toBe(true)
  })

  it('allows different fluid-gap relations to share one face', () => {
    const t = topology()
    ;(t.independentPairs as Record<string, unknown>).gap_2 = {
      faces: ['proj_geom/m_2/source-sheet', 'proj_geom/m_8/source-sheet'],
      relation: 'fluidGap', minGap_m: 0.06,
    }
    expect(errors(t)).toEqual([])
  })

  it.each([
    ['unknown root field', () => ({ ...topology(), guessedPair: {} }), /guessedPair.*unknown/],
    ['unknown pair field', () => { const t = topology(); relation(t,'wallPairs','wall_1').guess = true; return t }, /wall_1\.guess.*unknown/],
    ['unsupported version', () => ({ ...topology(), version: 2 }), /version.*1/],
    ['bad logical UID', () => ({ ...topology(), wallPairs: { 'wall 1': (topology().wallPairs as Record<string, unknown>).wall_1 } }), /wall 1.*UID/],
    ['one side', () => { const t = topology(); relation(t,'wallPairs','wall_1').sides = ['proj_geom/m_1/a']; return t }, /sides.*exactly two/],
    ['identical sides', () => { const t = topology(); relation(t,'wallPairs','wall_1').sides = ['proj_geom/m_1/a', 'proj_geom/m_1/a']; return t }, /sides.*distinct/],
    ['zero wall thickness', () => { const t = topology(); relation(t,'wallPairs','wall_1').thickness_m = 0; return t }, /thickness_m.*positive/],
    ['infinite wall thickness', () => { const t = topology(); relation(t,'wallPairs','wall_1').thickness_m = Infinity; return t }, /thickness_m.*finite/],
    ['reference outside sides', () => { const t = topology(); relation(t,'wallPairs','wall_1').meshReferenceFaceUid = 'proj_geom/m_other/source-sheet'; return t }, /meshReferenceFaceUid.*one of sides/],
    ['zero gap', () => { const t = topology(); relation(t,'independentPairs','gap_1').minGap_m = 0; return t }, /minGap_m.*positive/],
    ['one face in two wall pairs', () => { const t = topology(); (t.wallPairs as Record<string, unknown>).wall_2 = { ...relation(t,'wallPairs','wall_1'), sides: ['proj_geom/m_1/skin_a/source-sheet', 'proj_geom/m_8/source-sheet'] }; return t }, /source face.*multiple wallPairs/],
    ['same face pair declared as wall and gap', () => { const t = topology(); relation(t,'independentPairs','gap_1').faces = ['proj_geom/m_1/skin_b/source-sheet', 'proj_geom/m_1/skin_a/source-sheet']; return t }, /same face pair.*wallPairs/],
    ['same gap pair declared twice', () => { const t = topology(); (t.independentPairs as Record<string, unknown>).gap_2 = { ...relation(t,'independentPairs','gap_1'), faces: ['proj_geom/m_3/source-sheet', 'proj_geom/m_2/source-sheet'] }; return t }, /duplicate independentPairs.*face pair/],
    ['duplicate relation UID across maps', () => { const t = topology(); (t.independentPairs as Record<string, unknown>).wall_1 = (t.independentPairs as Record<string, unknown>).gap_1; return t }, /wall_1.*relation UID.*multiple/],
    ['bad edge reference shape', () => { const t = topology(); (relation(t,'edgeContacts','seam_1').edge as Record<string, unknown>).sourceEdgeUid = ''; return t }, /sourceEdgeUid.*UID/],
    ['edge contact with same face twice', () => { const t = topology(); relation(t,'edgeContacts','seam_1').face = {sourceFaceUid: 'proj_geom/m_4/source-sheet'}; return t }, /sourceFaceUid.*distinct/],
    ['edge-edge contact with same face twice', () => { const t = topology(); relation(t,'edgeContacts','seam_2').b = {sourceFaceUid: 'proj_geom/m_6/source-sheet', sourceEdgeUid: 'proj_geom/m_6/source-sheet/edge/left'}; return t }, /sourceFaceUid.*distinct/],
    ['edge contact with unknown field', () => { const t = topology(); relation(t,'edgeContacts','seam_1').offset = 1; return t }, /seam_1\.offset.*unknown/],
  ])('rejects %s on validation, save and reopen', (_label, mutate, expected) => {
    const invalid = mutate()
    expect(errors(invalid).join('; ')).toMatch(expected)
    expect(() => serializeProjectState(withTopology(invalid))).toThrow(expected)
    const read = checkProjectDocument(withTopology(invalid))
    expect(read.ok).toBe(false)
    if (!read.ok) expect(read.error).toMatch(expected)
  })
})

describe('strict geometry topology is a saved draft, not an executable air topology', () => {
  const emptyTopology = () => ({ version: 1, mode: 'strict', coverage: 'airInternalSheets', wallPairs: {}, independentPairs: {}, edgeContacts: {} })

  it('blocks the shared manifest compiler even when all relation maps are empty', () => {
    expect(() => cfdrunExport().buildManifest(withTopology(emptyTopology()), {}, '2026-09-27T00:00:00Z'))
      .toThrow(/geometryTopology.*not yet compiled/)
  })

  it('blocks the React manifest compiler before it builds STL or mesh geometry', async () => {
    const api = {
      buildComprehensiveStlMap: () => { throw new Error('STL work must not start') },
      buildCfdrunJson: () => { throw new Error('mesh geometry work must not start') },
    }
    await expect(buildManifestV2(api, withTopology(emptyTopology()), '2026-09-27T00:00:00Z'))
      .rejects.toThrow(/geometryTopology.*not yet compiled/)
  })

  it('blocks online mesh and solve before buildManifest is invoked', async () => {
    for (const kind of ['mesh', 'solve'] as const) {
      await expect(buildRunBody(kind, {
        project: withTopology(emptyTopology()), projectPath: null,
        modelUids: () => ['m_1'], duplicateSolids: () => [],
        buildManifest: async () => { throw new Error('build must not start') },
        buildStl: () => { throw new Error('STL work must not start') },
      })).rejects.toThrow(/geometryTopology.*not yet compiled/)
    }
  })

  it('blocks direct CFD package assembly and does not produce a zip', () => {
    expect(() => buildCfdPackage('draft', emptyManifest(), {'merged_baffle.stl': 'solid a\nendsolid a'}, new Date(), withTopology(emptyTopology())))
      .toThrow(/geometryTopology.*not yet compiled/)
  })

  it('blocks direct STL binding with a strict project as well', () => {
    expect(() => bindStlFileHashes(emptyManifest(), {'merged_baffle.stl': 'solid a\nendsolid a'}, withTopology(emptyTopology())))
      .toThrow(/geometryTopology.*not yet compiled/)
  })
})
