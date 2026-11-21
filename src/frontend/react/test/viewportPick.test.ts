import { describe, expect, it } from 'vitest'
import {
  resolveViewportSelection,
  viewportSelectionFor,
  type ViewportPickContext,
} from '../src/interaction/picking'

const ctx = (over: Partial<ViewportPickContext> = {}): ViewportPickContext => ({
  kind: 'box',
  facePatchCount: () => 0,
  ...over,
})

describe('resolveViewportSelection', () => {
  it('picks the patch a hit carries', () => {
    expect(
      resolveViewportSelection('m_1', { refIds: ['p_7'], faceId: 'front' }, ctx()),
    ).toEqual({ type: 'patch', uid: 'm_1', faceId: 'front', patchId: 'p_7' })
  })

  it('carries the region a custom patch was projected onto', () => {
    expect(
      resolveViewportSelection(
        'm_2',
        { refIds: ['p_3'], faceId: 'top', regionName: 'shell_top' },
        ctx({ kind: 'custom' }),
      ),
    ).toEqual({
      type: 'patch',
      uid: 'm_2',
      faceId: 'top',
      patchId: 'p_3',
      regionName: 'shell_top',
    })
  })

  it('treats an EMPTY refIds as no patch — a wall segment carries refIds: null', () => {
    expect(
      resolveViewportSelection('m_1', { refIds: [], faceId: 'front', isBaseWall: true }, ctx()),
    ).toEqual({ type: 'face', uid: 'm_1', faceId: 'front' })
  })

  it('selects one solid of an imported mesh', () => {
    expect(
      resolveViewportSelection(
        'm_3',
        { isImportedRegion: true, isImportedBase: true, regionName: 'lid' },
        ctx({ kind: 'custom' }),
      ),
    ).toEqual({ type: 'region', uid: 'm_3', regionName: 'lid' })
  })

  it('selects the WHOLE-model region for an imported mesh drawn as one piece', () => {
    expect(
      resolveViewportSelection('m_3', { isImportedBase: true }, ctx({ kind: 'custom' })),
    ).toEqual({ type: 'region', uid: 'm_3' })
  })

  it('gives a wall region only once patches have split the face', () => {
    const data = { isBaseWall: true, faceId: 'front', wallIndex: 1 }
    expect(
      resolveViewportSelection('m_4', data, ctx({ kind: 'box', facePatchCount: () => 1 })),
    ).toEqual({
      type: 'wallRegion',
      uid: 'm_4',
      faceId: 'front',
      index: 1,
    })
    expect(
      resolveViewportSelection('m_4', data, ctx({ kind: 'box', facePatchCount: () => 0 })),
    ).toEqual({ type: 'face', uid: 'm_4', faceId: 'front' })
  })

  it('still gives a SPLIT planar sheet its wall region', () => {
    // The kind rule below must not swallow this: a floor with vent tiles is
    // exactly the case where each remaining piece needs its own opacity.
    expect(
      resolveViewportSelection(
        'm_5',
        { isBaseWall: true, faceId: 'top', wallIndex: 0 },
        ctx({ kind: 'floor', facePatchCount: () => 2 }),
      ),
    ).toEqual({ type: 'wallRegion', uid: 'm_5', faceId: 'top', index: 0 })
  })

  it('selects the FACE of an unsplit planar body, so the inspector can name it', () => {
    expect(
      resolveViewportSelection(
        'm_6',
        { isBaseWall: true, faceId: 'front', wallIndex: 0 },
        ctx({ kind: 'baffle' }),
      ),
    ).toEqual({ type: 'face', uid: 'm_6', faceId: 'front' })
  })

  it('selects the face of a box body', () => {
    expect(
      resolveViewportSelection('m_7', { isBaseWall: true, faceId: 'left' }, ctx({ kind: 'acu' })),
    ).toEqual({ type: 'face', uid: 'm_7', faceId: 'left' })
  })

  it('falls through to the model for a mesh with no flags at all', () => {
    expect(resolveViewportSelection('m_8', { exportName: 'merged' }, ctx())).toBeNull()
  })
})

describe('resolveViewportSelection wall-region context', () => {
  it('asks about the face that was hit, not the primary one', () => {
    const asked: string[] = []
    resolveViewportSelection(
      'm_9',
      { isBaseWall: true, faceId: 'right', wallIndex: 2 },
      ctx({
        facePatchCount: (faceId) => {
          asked.push(faceId)
          return 1
        },
      }),
    )
    expect(asked).toEqual(['right'])
  })
})

describe('viewportSelectionFor', () => {
  const model = (over: Record<string, unknown>) => ({ kind: 'floor', ...over })

  it('counts the patches on the hit face', () => {
    expect(
      viewportSelectionFor(
        'm_1',
        { isBaseWall: true, faceId: 'top', wallIndex: 3 },
        model({ geometry: { faces: { top: { patches: [{ id: 'p_1' }] } } } }),
      ),
    ).toEqual({ type: 'wallRegion', uid: 'm_1', faceId: 'top', index: 3 })
  })

  it('reads a RACK, whose faces are an array, as no patches at all', () => {
    // Treating the array as a map yields undefined and a NaN count; the legacy
    // reads `faces[faceId].patches.length` off an object only.
    expect(
      viewportSelectionFor(
        'm_2',
        { isBaseWall: true, faceId: 'front', wallIndex: 0 },
        model({ kind: 'acu', geometry: { faces: [] } }),
      ),
    ).toEqual({ type: 'face', uid: 'm_2', faceId: 'front' })
  })

  it('survives a model the registry does not have', () => {
    expect(viewportSelectionFor('m_3', { isBaseWall: true, faceId: 'left' }, undefined)).toEqual({
      type: 'face',
      uid: 'm_3',
      faceId: 'left',
    })
  })
})
