import * as THREE from 'three'
import { beforeEach, describe, expect, it } from 'vitest'
import type { Construction } from '../src/schema/cfdproj'
import {
  buildModelPreviewStl,
  buildPreviewEntries,
  type PreviewExportApi,
  previewFlags,
  readFlagsFromStorage,
  resetPreviewForTest,
  writeFlagsToStorage,
} from '../src/io/stlPreview'

/**
 * The preview is only worth anything if it is built from the EXPORTER. These
 * pin the two places that could quietly stop being true: the rack branch that
 * appends the leakage solids, and the custom branch that goes down a different
 * exporter entirely.
 */

const FACET = `facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
`

function solid(name: string, facets = 1): string {
  return `solid ${name}\n${FACET.repeat(facets)}endsolid ${name}\n`
}

function model(uid: string, kind: string, name = uid): Construction {
  return { uid, kind, meta: { name, enabled: true } } as unknown as Construction
}

function fakeApi(overrides: Partial<PreviewExportApi> = {}): PreviewExportApi & {
  calls: string[]
} {
  const calls: string[] = []
  return {
    calls,
    exportModelToStl(m) {
      calls.push(`box:${m.uid}`)
      return solid(`${m.meta?.name}_body`)
    },
    exportCustomModel(m, parts) {
      calls.push(`custom:${m.uid}`)
      parts.push(solid('imported_region'))
    },
    buildThroughZoneStl(m) {
      calls.push(`leak:${m.uid}`)
      return { stl: solid('rack_u1_u3_through_zone') }
    },
    applyModelSolidOrientationOverrides(_m, text) {
      calls.push('overrides')
      return text
    },
    withTemporaryRenderedModels(models, cb) {
      calls.push(`temp:${models.map((m) => m.uid).join(',')}`)
      cb([])
    },
    ...overrides,
  }
}

const wrapper = () => new THREE.Group()

beforeEach(() => {
  resetPreviewForTest()
  localStorage.clear()
})

describe('buildModelPreviewStl', () => {
  it('renders the model temporarily and exports it', () => {
    const api = fakeApi()
    const text = buildModelPreviewStl(api, wrapper(), model('m_1', 'box', 'Box_A'))
    expect(text).toContain('solid Box_A_body')
    expect(api.calls).toContain('temp:m_1')
  })

  it('appends a rack leakage through-zone, which is exported separately', () => {
    // Missing here would mean the preview shows a rack the file does not, and
    // the leakage BC binds to a solid nobody could see.
    const api = fakeApi()
    const text = buildModelPreviewStl(api, wrapper(), model('m_2', 'rack'))
    expect(text).toContain('through_zone')
    expect(api.calls).toContain('leak:m_2')
  })

  it('takes the custom-mesh branch without a temporary render', () => {
    const api = fakeApi()
    const text = buildModelPreviewStl(api, wrapper(), model('m_3', 'custom'))
    expect(text).toContain('imported_region')
    expect(api.calls).toContain('custom:m_3')
    expect(api.calls.some((c) => c.startsWith('temp:'))).toBe(false)
  })

  it('always runs the solid-orientation overrides, which apply the reverse flags', () => {
    const api = fakeApi()
    buildModelPreviewStl(api, wrapper(), model('m_1', 'box'))
    expect(api.calls).toContain('overrides')
  })

  it('asks the WRAPPER whether the scene is mirrored', () => {
    // The exporter flips the winding for a mirrored scene. Asking the wrong
    // object gives a preview whose normals disagree with the file's.
    const seen: boolean[] = []
    const api = fakeApi({
      exportModelToStl(_m, isMirrored) {
        seen.push(isMirrored)
        return solid('a')
      },
    })
    const mirrored = new THREE.Group()
    mirrored.scale.x = -1
    buildModelPreviewStl(api, mirrored, model('m_1', 'box'))
    buildModelPreviewStl(api, wrapper(), model('m_1', 'box'))
    expect(seen).toEqual([true, false])
  })
})

describe('buildPreviewEntries', () => {
  it('splits each model into its solids, with counts and colours', () => {
    const api = fakeApi({
      exportModelToStl: () => solid('wall_front', 2) + solid('wall_patch_1', 4),
    })
    const entries = buildPreviewEntries(api, wrapper(), [model('m_1', 'wall', 'Wall_N')])
    const entry = entries.get('m_1')!
    expect(entry.regions.map((r) => r.name)).toEqual(['wall_front', 'wall_patch_1'])
    expect(entry.regions.map((r) => r.triangleCount)).toEqual([2, 4])
    expect(entry.triangleCount).toBe(6)
    expect(entry.regions[0]!.color).not.toBe(entry.regions[1]!.color)
  })

  it('keeps an entry for a model whose export is empty', () => {
    // A model that exports nothing is exactly what the user needs to see; an
    // absent entry would look like the tree simply had not been rebuilt.
    const api = fakeApi({ exportModelToStl: () => '' })
    const entries = buildPreviewEntries(api, wrapper(), [model('m_1', 'box')])
    expect(entries.get('m_1')!.regions).toEqual([])
    expect(entries.get('m_1')!.triangleCount).toBe(0)
  })
})

describe('flag persistence', () => {
  it('round-trips through the shared UI-prefs blob', () => {
    const flags = {
      visibility: { m_1: false },
      regionVisibility: { m_1: { a: false } },
      regionReverse: { m_1: { a: true } },
    }
    writeFlagsToStorage('p_1', flags)
    expect(readFlagsFromStorage('p_1')).toEqual(flags)
  })

  it('uses the legacy key, so both editors read the same flags', () => {
    writeFlagsToStorage('p_1', previewFlags())
    expect(localStorage.getItem('cfdproj.ui.p_1')).not.toBeNull()
  })

  it('preserves the other keys in the blob', () => {
    // The same blob holds the model groups and the tree collapse state; a
    // wholesale write would wipe whichever app saved last.
    localStorage.setItem('cfdproj.ui.p_1', JSON.stringify({ groups: [{ id: 'g1' }] }))
    writeFlagsToStorage('p_1', previewFlags())
    const blob = JSON.parse(localStorage.getItem('cfdproj.ui.p_1')!) as Record<string, unknown>
    expect(blob.groups).toEqual([{ id: 'g1' }])
  })

  it('survives a blob written by something else', () => {
    localStorage.setItem('cfdproj.ui.p_1', 'not json')
    expect(readFlagsFromStorage('p_1')).toEqual({
      visibility: {},
      regionVisibility: {},
      regionReverse: {},
    })
  })

  it('drops non-boolean values rather than trusting the blob', () => {
    localStorage.setItem(
      'cfdproj.ui.p_1',
      JSON.stringify({ previewVisibility: { m_1: 'yes', m_2: true } }),
    )
    expect(readFlagsFromStorage('p_1').visibility).toEqual({ m_2: true })
  })
})
