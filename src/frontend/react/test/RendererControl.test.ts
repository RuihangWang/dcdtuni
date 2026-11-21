import { afterEach, expect, it, vi } from 'vitest'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { Toolbar, DEFAULT_VIEW_OPTIONS } from '../src/components/Toolbar'
import { RendererControl, LatestSceneSync, advertisedOvEndpoint, currentResultTransmission, keepActiveOvCamera, preferredOvEndpoint, waitForRenderView } from '../src/renderer/RendererControl'
import type { SceneSnapshot } from '../src/renderer/sceneSnapshot'
import type { ResultLayerSet } from '../src/renderer/resultLayers'
import * as store from '../src/projectStore'
import { cfdrunExport } from '../src/legacy/cfdrunBuild'
import { clearResultLayerSet, clearStaleResultLayerSet, currentResultLayerSet,
  publishResultLayerSet, setLiveAirResultSelection } from '../src/renderer/resultLayerRevision'
import * as meshPreviewSession from '../src/run/meshPreviewSession'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function packet(uid: string, time: number): SceneSnapshot {
  return { schema: 'dcdtuni.render-scene/v1', project: { uid, name: uid },
    coordinateSystem: { upAxis: 'Y', metersPerUnit: 1, handedness: 'right' },
    primitives: [], textures: {}, warnings: [], view: { selectedUids: [`${uid}-rack`], timeSeconds: time } }
}
afterEach(() => {
  vi.useRealTimers()
  clearResultLayerSet()
  setLiveAirResultSelection(null)
  vi.restoreAllMocks()
})

it('rechecks the selected mesh snapshot key before sending a preview result', () => {
  store.loadProject({ uid: 'project-a', name: 'Mesh' }, null)
  const projectHash = cfdrunExport().projectContentHashSync(store.getProject()!)
  const key = 'e'.repeat(64)
  const source = { projectUid: 'project-a', projectContentSha256: projectHash,
    cfdrunSha256: 'b'.repeat(64), meshReceiptSha256: 'c'.repeat(64),
    meshQuality: 'diagnostic' as const, run: null }
  const body: ResultLayerSet = { schema: 'dcdtuni.render-result-layers/v1', revision: 2,
    source, timeSeconds: null,
    coordinateSystem: { upAxis: 'Y', metersPerUnit: 1, handedness: 'right' },
    layers: [{ kind: 'meshEdges', layerId: 'wall', visible: true, meshPatchName: 'wall',
      sourceFaceUids: ['source-wall'], positions: [0, 0, 0, 1, 0, 0],
      indices: [0, 1], opacity: .5 }] }
  publishResultLayerSet(body, source, null, undefined, 'mesh-preview', {
    owner: 'mesh-preview', meshSnapshotKey: key,
  })
  const original = meshPreviewSession.getMeshPreviewSession()
  const selected = vi.spyOn(meshPreviewSession, 'getMeshPreviewSession')
  selected.mockReturnValue({ ...original, built: {} as NonNullable<typeof original.built>,
    visible: true, meshSnapshotKey: key })
  expect(currentResultTransmission('project-a', projectHash).packet).toEqual(body)
  selected.mockReturnValue({ ...original, built: {} as NonNullable<typeof original.built>,
    visible: true, meshSnapshotKey: 'f'.repeat(64) })
  expect(currentResultTransmission('project-a', projectHash).packet).toBeNull()
})

it('never resends an old result after a same-UID scene edit, and clears it at the new scene epoch', () => {
  store.loadProject({ uid: 'project-a', name: 'Before' }, null)
  const before = cfdrunExport().projectContentHashSync(store.getProject()!)
  const source = { projectUid: 'project-a', projectContentSha256: before,
    cfdrunSha256: 'b'.repeat(64), meshReceiptSha256: 'c'.repeat(64),
    meshQuality: 'accepted' as const, run: { uid: 'physical-run', receiptSha256: 'd'.repeat(64) } }
  const session = { runId: 'selected-path-run', timeValue: 20, revision: 'pp-revision-1' }
  const selection = { owner: 'air-postprocess' as const, session,
    surfaceField: 'T', meshReceiptSha256: source.meshReceiptSha256 }
  const body: ResultLayerSet = { schema: 'dcdtuni.render-result-layers/v1', revision: 1, source,
    coordinateSystem: { upAxis: 'Y', metersPerUnit: 1, handedness: 'right' }, timeSeconds: 20,
    layers: [{ kind: 'surfaceField', layerId: 'T/wall', visible: true, meshPatchName: 'wall',
      sourceFaceUids: ['wall-face'], positions: [0, 0, 0, 1, 0, 0, 0, 1, 0],
      indices: [0, 1, 2], values: [290, 295, 300], field: { name: 'T', unit: 'K' },
      lut: { palette: 'coolwarm', range: [290, 310], opacity: 1 } }] }
  const publish = () => {
    setLiveAirResultSelection(selection)
    publishResultLayerSet(body, source, 20, undefined, 'air-postprocess', selection)
  }
  publish()
  expect(currentResultTransmission('project-a', before).packet).toEqual(body)
  expect(currentResultTransmission('project-a', 'e'.repeat(64)).packet).toBeNull()
  // The UID is unchanged; only the actual editable project content moves.
  store.loadProject({ uid: 'project-a', name: 'After' }, null)
  const after = cfdrunExport().projectContentHashSync(store.getProject()!)
  expect(after).not.toBe(before)
  expect(currentResultTransmission('project-a', before).packet).toBeNull()
  expect(currentResultTransmission('project-a', after).packet).toBeNull()
  clearStaleResultLayerSet('project-a', after)
  expect(currentResultLayerSet()).toBeNull()
  // Independently selected run, time, field and mesh each invalidate the old frame.
  store.loadProject({ uid: 'project-a', name: 'Before' }, null)
  publish()
  setLiveAirResultSelection({ ...selection, session: { ...session, runId: 'new-run' } })
  expect(currentResultLayerSet()).toBeNull()
  publish()
  setLiveAirResultSelection({ ...selection, session: { ...session, timeValue: 21 } })
  expect(currentResultLayerSet()).toBeNull()
  publish()
  setLiveAirResultSelection({ ...selection, surfaceField: 'U' })
  expect(currentResultLayerSet()).toBeNull()
  publish()
  setLiveAirResultSelection({ ...selection, meshReceiptSha256: 'f'.repeat(64) })
  expect(currentResultLayerSet()).toBeNull()
})

it('uses the backend-advertised OV endpoint when React is opened through a loopback tunnel', () => {
  expect(preferredOvEndpoint({ ovSignalingHost: '40901.dynip.ntu.edu.sg', ovSignalingPort: 8301 }, '127.0.0.1'))
    .toEqual({ host: '40901.dynip.ntu.edu.sg', port: 8301 })
  expect(preferredOvEndpoint(null, '127.0.0.1')).toEqual({ host: '127.0.0.1', port: 8301 })
})

it('a launcher --ov-port reaches the client even when no signalling host is configured', () => {
  // start_server.sh --ov exports only DCDTUNI_OV_SIGNALING_PORT; the browser
  // kept dialling 8301 because the port was applied only alongside a host.
  expect(advertisedOvEndpoint({ ovSignalingHost: null, ovSignalingPort: 8303 }, 'localhost'))
    .toEqual({ host: 'localhost', port: 8303 })
  expect(advertisedOvEndpoint(null, 'localhost')).toBeNull()
})

it('keeps the live OV camera during same-project edits without reverting new time or selection', () => {
  const next = packet('project-a', 600)
  next.view.selectedUids = ['new-rack']
  const camera = { position: [4, 5, 6] as [number, number, number], target: [0, 1, 0] as [number, number, number],
    up: [0, 1, 0] as [number, number, number], fov: 45, aspect: 1.5, near: 0.1, far: 500 }
  const identity = { projectUid: 'project-a', projectHash: 'a'.repeat(64), sceneRevision: 4 }
  const ack = { requestId: 'live', ok: true, projectHash: identity.projectHash, sceneRevision: identity.sceneRevision,
    view: { camera, selectedUids: ['old-rack'], timeSeconds: 0 } }
  expect(keepActiveOvCamera(next, ack, 'project-a', identity).view).toEqual({
    camera, selectedUids: ['new-rack'], timeSeconds: 600,
  })
  expect(keepActiveOvCamera(packet('project-b', 0), ack, 'project-a', identity).view.camera).toBeUndefined()
})

it('keeps one latest pending state while the previous scene awaits its acknowledgement', async () => {
  vi.useFakeTimers()
  const sent: SceneSnapshot[] = []
  let resolve!: () => void
  const sync = new LatestSceneSync(async p => { sent.push(p); await new Promise<void>(r => { resolve = r }) }, () => {})
  sync.offer('a:0', () => packet('a', 0))
  await vi.advanceTimersByTimeAsync(350)
  expect(sent.map(p => p.view.timeSeconds)).toEqual([0])
  sync.offer('a:1', () => packet('a', 1))
  await vi.advanceTimersByTimeAsync(350)
  sync.offer('b:2', () => packet('b', 2))
  await vi.advanceTimersByTimeAsync(350)
  expect(sent).toHaveLength(1)
  resolve()
  await vi.advanceTimersByTimeAsync(0)
  expect(sent.map(p => [p.project.uid, p.view.selectedUids, p.view.timeSeconds])).toEqual([
    ['a', ['a-rack'], 0], ['b', ['b-rack'], 2],
  ])
  expect(sync.isCurrent('b:2')).toBe(false)
  resolve()
  await vi.advanceTimersByTimeAsync(0)
  expect(sync.isCurrent('b:2')).toBe(true)
  sync.close()
})

it('reports idle only once no scene transfer is queued or in flight (and on close)', async () => {
  vi.useFakeTimers()
  let resolve!: () => void
  const sync = new LatestSceneSync(async () => { await new Promise<void>(r => { resolve = r }) }, () => {})
  let idle = 0
  await sync.whenIdle().then(() => { idle++ })
  expect(idle).toBe(1)                                   // nothing queued: immediate
  sync.offer('a:0', () => packet('a', 0))
  void sync.whenIdle().then(() => { idle++ })
  await vi.advanceTimersByTimeAsync(350)                 // debounce elapsed, transfer in flight
  sync.offer('a:1', () => packet('a', 1))                // a newer state queued behind it
  resolve(); await vi.advanceTimersByTimeAsync(350)
  expect(idle).toBe(1)                                   // the queued one is now in flight
  resolve(); await vi.advanceTimersByTimeAsync(0)
  expect(idle).toBe(2)
  sync.offer('a:2', () => packet('a', 2))
  void sync.whenIdle().then(() => { idle++ })
  sync.close(); await vi.advanceTimersByTimeAsync(0)
  expect(idle).toBe(3)                                   // a closed sync never strands a waiter
  vi.useRealTimers()
})

it('cancels pending snapshots on return and ignores an old-session failure', async () => {
  vi.useFakeTimers()
  let reject!: (error: Error) => void
  const failed = vi.fn()
  const sent: string[] = []
  const sync = new LatestSceneSync(async p => { sent.push(p.project.uid); await new Promise((_, r) => { reject = r }) }, failed)
  sync.offer('a', () => packet('a', 0))
  await vi.advanceTimersByTimeAsync(350)
  sync.offer('b', () => packet('b', 5))
  sync.close()
  reject(new Error('Old stream disconnected'))
  await vi.advanceTimersByTimeAsync(1000)
  expect(sent).toEqual(['a'])
  expect(failed).not.toHaveBeenCalled()
})

it('does not read a previous scene view while a newer project or frame is waiting', () => {
  vi.useFakeTimers()
  const sync = new LatestSceneSync(async () => {}, () => {}, 'project-a:time-0')
  expect(sync.isCurrent('project-a:time-0')).toBe(true)
  sync.offer('project-b:time-20', () => packet('project-b', 20))
  expect(sync.isCurrent('project-a:time-0')).toBe(false)
  expect(sync.isCurrent('project-b:time-20')).toBe(false)
  sync.close()
})

it('stops waiting after a short view timeout; a late reply cannot be applied by the caller', async () => {
  vi.useFakeTimers()
  let resolve!: (value: { requestId: string; ok: boolean }) => void
  const source = new Promise<{ requestId: string; ok: boolean }>(r => { resolve = r })
  const applied = vi.fn()
  const result = waitForRenderView(source).then(applied, error => error.message)
  await vi.advanceTimersByTimeAsync(1200)
  expect(await result).toMatch(/timed out/)
  resolve({ requestId: 'late', ok: true })
  await vi.advanceTimersByTimeAsync(0)
  expect(applied).not.toHaveBeenCalled()
})

it('ovSession publishes renderer ownership for the post-process panel', async () => {
  const { ovSession, setOvSession, subscribeOvSession } = await import('../src/renderer/ovSession')
  const seen: string[] = []
  const off = subscribeOvSession(() => seen.push(`${ovSession().mode}:${ovSession().ready}:${ovSession().degraded}`))
  setOvSession({ mode: 'ov', ready: true, degraded: false })
  setOvSession({ degraded: true })
  setOvSession({ mode: 'three', ready: false, degraded: false, client: null })
  off()
  expect(seen).toEqual(['ov:true:false', 'ov:true:true', 'three:false:false'])
  expect(ovSession().client).toBeNull()
})

it('keeps renderer controls in the same toolbar slot through collapse and expansion', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => {
      root.render(createElement('div', null,
        createElement(Toolbar, { view: DEFAULT_VIEW_OPTIONS, onChange: () => {},
          hasSelection: false, project: null }),
        createElement(RendererControl, { project: null, selectedUids: [],
          onSelection: () => {}, activeTab: 'mesh', airMode: false })))
    })
    const toolbar = document.getElementById('toolbar')!
    const slot = document.getElementById('renderer-switch-slot')!
    const select = slot.querySelector('select')!
    const settings = slot.querySelector<HTMLButtonElement>('.renderer-settings-btn')!
    expect(select.value).toBe('three')
    await act(async () => { settings.click() })
    // the OV settings float over the page (portalled), outside the toolbar
    const panel = document.querySelector('.renderer-connection-popover')!
    expect(panel).not.toBeNull()
    expect(document.getElementById('toolbar')!.contains(panel)).toBe(false)
    const collapse = document.getElementById('toolbar-collapse-btn')!
    await act(async () => { collapse.click() })
    expect(toolbar.classList.contains('collapsed')).toBe(true)
    expect(document.getElementById('renderer-switch-slot')).toBe(slot)
    expect(slot.querySelector('select')).toBe(select)
    expect(slot.querySelector('.renderer-settings-btn')).toBe(settings)
    expect(settings.getAttribute('aria-expanded')).toBe('true')
    expect(host.querySelector('.renderer-controls select')).toBeNull()
    expect(host.querySelectorAll('option[value="ov"]')).toHaveLength(1)
    await act(async () => { collapse.click() })
    expect(toolbar.classList.contains('collapsed')).toBe(false)
    expect(slot.querySelector('select')).toBe(select)
    expect(document.querySelector('.renderer-connection-popover')).toBe(panel)
    expect(host.querySelector('.renderer-controls select')).toBeNull()
  } finally {
    await act(async () => { root.unmount() })
    host.remove()
  }
})

it('keeps the renderer controls available when no toolbar slot is mounted', async () => {
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    await act(async () => {
      root.render(createElement(RendererControl, { project: null, selectedUids: [],
        onSelection: () => {}, activeTab: 'mesh', airMode: false }))
    })
    expect(host.querySelector<HTMLSelectElement>('.renderer-controls select')?.value).toBe('three')
    expect(host.querySelectorAll('option[value="ov"]')).toHaveLength(1)
  } finally {
    await act(async () => { root.unmount() })
    host.remove()
  }
})

it('flipping Kit liquid colouring on freezes the result part of the sync key instead of reshaping it', async () => {
  const { readFileSync } = await import('node:fs')
  const { resolve } = await import('node:path')
  const src = readFileSync(resolve(__dirname, '../src/renderer/RendererControl.tsx'), 'utf8')
  // One key shape for both states. The independent display revision remains live;
  // while active the result components are the frozen ones.
  expect(src).toMatch(/const key=displaySceneKey\(project\?\.uid\?\?'',sourceHash,\[keySample,keyTime,keyVisual\],displayRevision\)/)
  expect(src).toMatch(/else if\(!frozenResult\.current\)frozenResult\.current=\[sample,timeSeconds,visualRevision\]/)
  expect(src).not.toMatch(/liquidActive\?\[project/)
})

it('a selection change updates the Kit view instead of resending the scene', async () => {
  const { readFileSync } = await import('node:fs')
  const { resolve } = await import('node:path')
  const src = readFileSync(resolve(__dirname, '../src/renderer/RendererControl.tsx'), 'utf8')
  const key = /const key=displaySceneKey\((.*)\)/.exec(src)![1]!
  expect(key).not.toContain('selectedUids')
  expect(src).toMatch(/useEffect\(\(\)=>\{pushView\(\)\},\[selectedUids\]\)/)
  expect(src).toMatch(/useEffect\(\(\)=>subscribeCameraSettled\(pushView\),\[pushView\]\)/)
})

it('an unchanged scene key is not resent even when the snapshot callback changes', async () => {
  const sent: string[] = []
  const sync = new LatestSceneSync<string>(async p => { sent.push(p) }, () => {}, 'k1')
  sync.offer('k1', () => 'selection-only change')
  await new Promise(r => setTimeout(r, 400))
  await sync.whenIdle()
  expect(sent).toEqual([])
  sync.close()
})
