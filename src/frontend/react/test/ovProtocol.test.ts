import { afterEach, describe, expect, it, vi } from 'vitest'
import { OvCommandClient, ovAckTimeoutDegraded, sceneAckBudgetMs } from '../src/renderer/ovProtocol'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
import * as THREE from 'three'
import type { RenderOverlayPacket } from '../src/renderer/meshPreviewOverlay'
import type { ResultLayerSet } from '../src/renderer/resultLayers'

it('waits for the matching Kit acknowledgement, not just transport delivery', async () => {
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) })
  const promise = client.updateView({ selectedUids: ['rack'], timeSeconds: 1.5 })
  let settled = false
  void promise.then(() => { settled = true })
  await Promise.resolve()
  expect(settled).toBe(false)
  const id = JSON.parse(sent[0]!.message).requestId
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: 'unrelated', ok: true } })
  await Promise.resolve()
  expect(settled).toBe(false)
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: id, ok: true, view: { selectedUids: ['rack'], timeSeconds: 1.5 } } })
  await expect(promise).resolves.toMatchObject({ ok: true })
})

it('sends bounded ordered chunks and surfaces a rejected scene without treating it as ready', async () => {
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000, 64)
  const scene = buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'p', name: '房间' } })
  const promise = client.loadScene(scene)
  await new Promise(resolve => setTimeout(resolve, 0))
  const chunks = sent.filter(p => p.command_name === 'renderSceneChunk').map(p => JSON.parse(p.message))
  expect(chunks.length).toBeGreaterThan(1)
  expect(chunks.map(c => c.index)).toEqual(chunks.map((_, i) => i))
  expect(chunks.every(c => c.data.length <= 64)).toBe(true)
  expect(JSON.parse(chunks.map(c => c.data).join(''))).toEqual(scene)
  const id = JSON.parse(sent.at(-1)!.message).requestId
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: id, ok: false, error: 'Bad geometry' } })
  await expect(promise).rejects.toThrow('Bad geometry')
})

it('sends a large scene deflated and announces the encoding in the header', async () => {
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000, 64)
  client.compressFromChars = 0
  const scene = buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'p', name: '房间' } })
  const promise = client.loadScene(scene)
  await vi.waitFor(() => expect(sent.some(p => p.command_name === 'loadRenderScene')).toBe(true))
  const begin = JSON.parse(sent.find(p => p.command_name === 'beginRenderScene')!.message)
  const chunks = sent.filter(p => p.command_name === 'renderSceneChunk').map(p => JSON.parse(p.message))
  expect(begin.encoding).toBe('deflate-base64')
  expect(begin.chunks).toBe(chunks.length)
  const bytes = Uint8Array.from(atob(chunks.map(c => c.data).join('')), c => c.charCodeAt(0))
  const text = await new Response(new Response(bytes).body!.pipeThrough(new DecompressionStream('deflate'))).text()
  expect(JSON.parse(text)).toEqual(scene)
  const id = JSON.parse(sent.at(-1)!.message).requestId
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: id, ok: false, error: 'stop' } })
  await expect(promise).rejects.toThrow('stop')
  // Below the threshold the packet goes as plain text, header unchanged.
  sent.length = 0
  client.compressFromChars = Infinity
  const plain = client.loadScene(scene)
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(Object.keys(JSON.parse(sent.find(p => p.command_name === 'beginRenderScene')!.message)).sort()).toEqual(['chunks', 'requestId'])
  client.dispose()
  await expect(plain).rejects.toThrow()
})

it('rejects outstanding work when disconnected', async () => {
  const client = new OvCommandClient(async () => {})
  const p = client.updateView({ selectedUids: [], timeSeconds: null })
  client.dispose()
  await expect(p).rejects.toThrow(/disconnect/i)
})

it('delivers a Kit source-pick event without requiring a pending render ACK', () => {
  const client = new OvCommandClient(async () => {})
  const picked = vi.fn()
  client.onEditEvent(picked)
  const event = { schema: 'dcdtuni.ov-edit/v1', sceneRevision: 3, primId: 'rack-prim' }
  client.receive({ event_type: 'dcdtuni:ovEditEvent', payload: event })
  expect(picked).toHaveBeenCalledWith(event)
  client.receive({ event_type: 'dcdtuni:ovEditEvent', payload: { text: 'x'.repeat(70_000) } })
  expect(picked).toHaveBeenCalledTimes(1)
  client.dispose()
  client.receive({ event_type: 'dcdtuni:ovEditEvent', payload: event })
  expect(picked).toHaveBeenCalledTimes(1)
})

it('serializes whole-scene transfers so a newer edit cannot overwrite a partial packet', async () => {
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) })
  const scene = buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'p' } })
  const first = client.loadScene(scene)
  const second = client.loadScene(scene)
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(sent.filter(p => p.command_name === 'beginRenderScene')).toHaveLength(1)
  const firstId = JSON.parse(sent.at(-1)!.message).requestId
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: firstId, ok: true } })
  await first
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(sent.filter(p => p.command_name === 'beginRenderScene')).toHaveLength(2)
  const secondId = JSON.parse(sent.at(-1)!.message).requestId
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: secondId, ok: true } })
  await second
})

it('allows a large scene to wait for Kit processing beyond the short view ACK timeout', async () => {
  vi.useFakeTimers()
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000, 16000, 5000)
  clients.push(client)
  const scene = buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'p' } })
  const result: { ack?: boolean; error?: string } = {}
  const promise = client.loadScene(scene).then(
    ack => { result.ack = ack.ok },
    error => { result.error = error.message },
  )
  await vi.advanceTimersByTimeAsync(0)
  const id = JSON.parse(sent[0]!.message).requestId
  await vi.advanceTimersByTimeAsync(1500)
  expect(result).toEqual({})
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: id, ok: true } })
  await promise
  expect(result).toEqual({ ack: true })
})

it('starts the scene ACK clock after transport submission, not before it', async () => {
  vi.useFakeTimers()
  let releaseBegin: (() => void) | undefined
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(payload => {
    sent.push(payload)
    return payload.command_name === 'beginRenderScene'
      ? new Promise<void>(resolve => { releaseBegin = resolve }) : Promise.resolve()
  }, 1000, 16000, 5000)
  clients.push(client)
  const scene = buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'p' } })
  const result: { ack?: boolean; error?: string } = {}
  const promise = client.loadScene(scene).then(
    ack => { result.ack = ack.ok },
    error => { result.error = error.message },
  )
  await vi.advanceTimersByTimeAsync(0)
  const id = JSON.parse(sent[0]!.message).requestId
  await vi.advanceTimersByTimeAsync(1500)
  expect(result).toEqual({})
  releaseBegin?.()
  await vi.advanceTimersByTimeAsync(0)
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: id, ok: true } })
  await promise
  expect(result).toEqual({ ack: true })
})

it('paces large scene chunks behind Kit progress acknowledgements', async () => {
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000, 64, 5000, 2)
  clients.push(client)
  const scene = buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'p' } })
  const promise = client.loadScene(scene)
  await vi.waitFor(() => expect(sent.some(p => p.command_name === 'renderSceneProgress')).toBe(true))
  expect(sent.filter(p => p.command_name === 'renderSceneChunk')).toHaveLength(2)
  expect(sent.some(p => p.command_name === 'loadRenderScene')).toBe(false)
  const first = JSON.parse(sent.find(p => p.command_name === 'renderSceneProgress')!.message)
  expect(first.received).toBe(2)
  expect(first.sceneRequestId).toBe(JSON.parse(sent[0]!.message).requestId)
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: first.requestId, ok: true, received: 2 } })
  await vi.waitFor(() => expect(sent.filter(p => p.command_name === 'renderSceneChunk')).toHaveLength(4))
  expect(sent.filter(p => p.command_name === 'renderSceneProgress')).toHaveLength(2)
  client.dispose()
  await expect(promise).rejects.toThrow(/disconnect/i)
})

it('rejects a scene transfer if Kit reports the wrong received chunk count', async () => {
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000, 64, 5000, 2)
  clients.push(client)
  const scene = buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'p' } })
  const promise = client.loadScene(scene)
  await vi.waitFor(() => expect(sent.some(p => p.command_name === 'renderSceneProgress')).toBe(true))
  const progress = JSON.parse(sent.find(p => p.command_name === 'renderSceneProgress')!.message)
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: progress.requestId, ok: true, received: 1 } })
  await expect(promise).rejects.toThrow(/chunk count/i)
  expect(sent.filter(p => p.command_name === 'renderSceneChunk')).toHaveLength(2)
  expect(sent.some(p => p.command_name === 'loadRenderScene')).toBe(false)
})

it('sends a mesh overlay only after its engineering scene ACK and waits for its own ACK', async () => {
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000, 16000)
  clients.push(client)
  const scene = buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'project-a' } })
  const overlay: RenderOverlayPacket = { schema: 'dcdtuni.render-overlay/v1', projectUid: 'project-a',
    source: { kind: 'meshPreview', meshSnapshotKey: null }, primitives: [] }
  const scenePromise = client.loadScene(scene)
  const overlayPromise = client.loadOverlay(overlay)
  await vi.waitFor(() => expect(sent.some(p => p.command_name === 'loadRenderScene')).toBe(true))
  expect(sent.some(p => p.command_name === 'beginRenderOverlay')).toBe(false)
  const sceneId = JSON.parse(sent.find(p => p.command_name === 'beginRenderScene')!.message).requestId
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: sceneId, ok: true } })
  await scenePromise
  await vi.waitFor(() => expect(sent.some(p => p.command_name === 'loadRenderOverlay')).toBe(true))
  const overlayId = JSON.parse(sent.find(p => p.command_name === 'beginRenderOverlay')!.message).requestId
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: overlayId, ok: true } })
  await expect(overlayPromise).resolves.toMatchObject({ ok: true })
})

it('transfers verified result layers separately from the editor scene and clears after ACK', async () => {
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) })
  clients.push(client)
  const source = { projectUid: 'project-a', projectContentSha256: 'a'.repeat(64),
    cfdrunSha256: 'b'.repeat(64), meshReceiptSha256: 'c'.repeat(64),
    meshQuality: 'accepted' as const, run: { uid: 'run-a', receiptSha256: 'd'.repeat(64) } }
  const packet: ResultLayerSet = {
    schema: 'dcdtuni.render-result-layers/v1', revision: 4, source, timeSeconds: 20,
    coordinateSystem: { upAxis: 'Y', metersPerUnit: 1, handedness: 'right' },
    layers: [
      { kind: 'meshEdges', layerId: 'edges', visible: true, meshPatchName: 'wall',
        sourceFaceUids: ['source-wall'], positions: [0, 0, 0, 1, 0, 0, 0, 1, 0],
        indices: [0, 1, 1, 2, 2, 0], opacity: 0.5 },
      { kind: 'surfaceField', layerId: 'temperature', visible: true, meshPatchName: 'wall',
        sourceFaceUids: ['source-wall'], positions: [0, 0, 0, 1, 0, 0, 0, 1, 0],
        indices: [0, 1, 2], values: [290, 295, 300], field: { name: 'T', unit: 'K' },
        lut: { palette: 'coolwarm', range: [290, 310], opacity: 1 } },
      { kind: 'slice', layerId: 'cut', visible: true, sourceSliceUid: 'cut-1',
        originMeters: [0, 0.5, 0], normal: [0, 1, 0],
        positions: [0, 0.5, 0, 1, 0.5, 0, 0, 0.5, 1], indices: [0, 1, 2],
        values: [291, 295, 299], field: { name: 'T', unit: 'K' },
        lut: { palette: 'coolwarm', range: [290, 310], opacity: 1 } },
      { kind: 'streamline', layerId: 'flow', visible: true, sourceSeedUid: 'seed-1',
        lines: [{ positions: [0, 0, 0, 1, 0, 0], values: [0, 1] }],
        field: { name: 'U', unit: 'm/s' }, tubeRadiusMeters: 0.01,
        lut: { palette: 'viridis', range: [0, 1], opacity: 1 } },
    ],
  }
  expect(() => client.loadResultLayers(packet, { ...source, meshReceiptSha256: 'e'.repeat(64) }, 20)).toThrow(/meshReceiptSha256/)
  const result = client.loadResultLayers(packet, source, 20)
  const cleared = client.clearResultLayers()
  await vi.waitFor(() => expect(sent.some(item => item.command_name === 'loadRenderResultLayers')).toBe(true))
  expect(sent.some(item => item.command_name === 'clearRenderResultLayers')).toBe(false)
  const body = sent.filter(item => item.command_name === 'renderResultLayersChunk')
    .map(item => JSON.parse(item.message).data).join('')
  expect(JSON.parse(body)).toEqual({ packet, expectedSource: source, expectedTimeSeconds: 20 })
  const id = JSON.parse(sent.find(item => item.command_name === 'beginRenderResultLayers')!.message).requestId
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: id, ok: true, revision: 4, layers: 4 } })
  await expect(result).resolves.toMatchObject({ revision: 4, layers: 4 })
  await vi.waitFor(() => expect(sent.some(item => item.command_name === 'clearRenderResultLayers')).toBe(true))
  const clearId = JSON.parse(sent.find(item => item.command_name === 'clearRenderResultLayers')!.message).requestId
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: clearId, ok: true, layers: 0 } })
  await expect(cleared).resolves.toMatchObject({ layers: 0 })
})

it('uses overlay-specific progress barriers before loading a multi-chunk preview', async () => {
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000, 32, 5000, 2)
  clients.push(client)
  const overlay: RenderOverlayPacket = { schema: 'dcdtuni.render-overlay/v1', projectUid: 'project-a',
    source: { kind: 'meshPreview', meshSnapshotKey: null }, primitives: [] }
  const count = Math.ceil(JSON.stringify(overlay).length / 32)
  expect(count).toBeGreaterThan(2)
  const promise = client.loadOverlay(overlay)
  for (let received = 2; received < count + 2; received += 2) {
    const expected = Math.min(received, count)
    const ordinal = Math.ceil(expected / 2)
    await vi.waitFor(() => expect(sent.filter(p => p.command_name === 'renderOverlayProgress')).toHaveLength(ordinal))
    expect(sent.filter(p => p.command_name === 'renderOverlayChunk')).toHaveLength(expected)
    expect(sent.some(p => p.command_name === 'loadRenderOverlay')).toBe(false)
    const barrier = JSON.parse(sent.filter(p => p.command_name === 'renderOverlayProgress')[ordinal - 1]!.message)
    expect(barrier.overlayRequestId).toBe(JSON.parse(sent[0]!.message).requestId)
    expect(barrier.received).toBe(expected)
    client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: barrier.requestId, ok: true, received: expected } })
  }
  await vi.waitFor(() => expect(sent.some(p => p.command_name === 'loadRenderOverlay')).toBe(true))
  const id = JSON.parse(sent[0]!.message).requestId
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: id, ok: true } })
  await expect(promise).resolves.toMatchObject({ ok: true })
})

/** Official messaging 1.3.3 app envelopes; independent transport fixtures, no Kit. */
function ackParts(payload: object, id = 'transport-a', partSize = 16128) {
  const text = JSON.stringify(payload)
  const parts = []
  for (let at = 0; at < text.length; at += partSize) {
    parts.push({ event_type: 'dcdtuni:renderAck', id, part: parts.length + 1,
      numParts: Math.ceil(text.length / partSize), payload: text.slice(at, at + partSize) })
  }
  return parts
}
const clients: OvCommandClient[] = []
function receivingClient() {
  vi.useFakeTimers()
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async value => { sent.push(value) }, 1000)
  clients.push(client)
  function request() {
    const result: { ack?: unknown; error?: string } = {}
    const promise = client.readView().then(ack => { result.ack = ack }, error => { result.error = error.message })
    const id = JSON.parse(sent.at(-1)!.message).requestId as string
    return { id, result, promise }
  }
  return { client, request }
}
afterEach(() => {
  clients.splice(0).forEach(client => client.dispose())
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('official multipart render acknowledgements', () => {
  it('correlates a small ACK by payload requestId, never by the extra transport id', async () => {
    const { client, request } = receivingClient()
    const first = request(), second = request()
    client.receive({ event_type: 'dcdtuni:renderAck', id: second.id, payload: { requestId: first.id, ok: true } })
    await first.promise
    expect(first.result.ack).toEqual({ requestId: first.id, ok: true })
    expect(second.result).toEqual({})
    client.receive({ message: JSON.stringify({ event_type: 'dcdtuni:renderAck', id: 'unrelated-wire-id', payload: { requestId: second.id, ok: true } }) })
    await second.promise
    expect(second.result.ack).toEqual({ requestId: second.id, ok: true })
  })

  it('reassembles a real large view in any order and ignores identical duplicate fragments', async () => {
    const { client, request } = receivingClient()
    const pending = request()
    const view = { selectedUids: Array.from({ length: 1500 }, (_, i) => `rack-${i}-stable-asset-identity`), timeSeconds: 600,
      camera: { position: [1, 2, 3], target: [0, 0, 0], up: [0, 1, 0], fov: 50, aspect: 1.5, near: .1, far: 500 } }
    const payload = { requestId: pending.id, ok: true, view }
    const parts = ackParts(payload)
    expect(JSON.stringify(payload).length).toBeGreaterThan(16128 * 2)
    client.receive(parts.at(-1))
    client.receive(parts.at(-1))
    for (let i = parts.length - 2; i > 0; i--) client.receive(JSON.stringify(parts[i]))
    await Promise.resolve()
    expect(pending.result).toEqual({})
    client.receive(parts[0])
    await vi.advanceTimersByTimeAsync(0)
    expect(pending.result.ack).toEqual(payload)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('propagates the exact large error only after complete ACK reconstruction', async () => {
    const { client, request } = receivingClient()
    const pending = request()
    const message = 'rack source mapping rejected; '.repeat(1500)
    expect(message.length).toBeGreaterThan(16128)
    ackParts({ requestId: pending.id, ok: false, error: message }).forEach(part => client.receive(part))
    await vi.advanceTimersByTimeAsync(0)
    expect(pending.result.error).toBe(message)
  })

  it('keeps interleaved transport groups separate even when their fragment indices match', async () => {
    const { client, request } = receivingClient()
    const a = request(), b = request()
    const ap = ackParts({ requestId: a.id, ok: true }, 'wire-a', 40)
    const bp = ackParts({ requestId: b.id, ok: false, error: 'B failed' }, 'wire-b', 40)
    client.receive(ap[0])
    bp.slice(1).forEach(part => client.receive(part))
    await Promise.resolve()
    expect(a.result).toEqual({})
    expect(b.result).toEqual({})
    ap.slice(1).forEach(part => client.receive(part))
    client.receive(bp[0])
    await vi.advanceTimersByTimeAsync(0)
    expect(a.result.ack).toEqual({ requestId: a.id, ok: true })
    expect(b.result.error).toBe('B failed')
  })

  it.each([
    { part: 0 }, { part: -1 }, { part: 1.5 }, { part: true }, { part: 3 },
    { numParts: 0 }, { numParts: -1 }, { numParts: '2' }, { numParts: 2.5 }, { numParts: 129 },
    { part: undefined }, { numParts: undefined },
    { id: '' }, { id: 9 }, { id: 'x'.repeat(129) }, { payload: {} },
  ])('rejects malformed fragment metadata %j without completing a request', async bad => {
    const { client, request } = receivingClient()
    const pending = request()
    client.receive({ event_type: 'dcdtuni:renderAck', id: 'wire', part: 1, numParts: 2,
      payload: JSON.stringify({ requestId: pending.id, ok: true }), ...bad })
    await Promise.resolve()
    expect(pending.result).toEqual({})
    // Malformed transport metadata must not reserve an assembly slot/timer.
    expect(vi.getTimerCount()).toBe(1)
    ackParts({ requestId: pending.id, ok: true }, 'fresh', 40).forEach(p => client.receive(p))
    await vi.advanceTimersByTimeAsync(0)
    expect(pending.result.ack).toEqual({ requestId: pending.id, ok: true })
  })

  it.each(['payload', 'count'])('quarantines a group after a conflicting %s, preserving a later valid group', async conflict => {
    const { client, request } = receivingClient()
    const pending = request()
    const parts = ackParts({ requestId: pending.id, ok: true, view: { selectedUids: ['rack'], timeSeconds: 600 } }, 'conflict', 40)
    client.receive(parts[0])
    client.receive(conflict === 'payload' ? { ...parts[0], payload: 'different fragment' } : { ...parts[0], numParts: parts.length + 1 })
    parts.forEach(part => client.receive(part))
    await Promise.resolve()
    expect(pending.result).toEqual({})
    ackParts({ requestId: pending.id, ok: true }, 'safe', 40).forEach(part => client.receive(part))
    await vi.advanceTimersByTimeAsync(0)
    expect(pending.result.ack).toEqual({ requestId: pending.id, ok: true })
    expect(vi.getTimerCount()).toBe(0)
  })

  it('caps individual fragment bytes rather than just Unicode character count', async () => {
    const { client, request } = receivingClient()
    const pending = request()
    const payload = { requestId: pending.id, ok: false, error: '机'.repeat(22000) }
    const parts = ackParts(payload, 'oversized-fragment', 23000)
    expect(parts[0]!.payload.length).toBeLessThan(65536)
    expect(new TextEncoder().encode(parts[0]!.payload).length).toBeGreaterThan(65536)
    parts.forEach(part => client.receive(part))
    await Promise.resolve()
    expect(pending.result).toEqual({})
    await vi.advanceTimersByTimeAsync(1000)
    expect(pending.result.error).toMatch(/timed out/)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('caps each complete ACK at one MiB without letting later fragments resurrect its group', async () => {
    const { client, request } = receivingClient()
    const pending = request()
    const parts = ackParts({ requestId: pending.id, ok: false, error: 'x'.repeat(1024 * 1024) }, 'oversized-ack')
    expect(parts.length).toBeLessThan(128)
    parts.forEach(part => client.receive(part))
    await Promise.resolve()
    expect(pending.result).toEqual({})
    await vi.advanceTimersByTimeAsync(1000)
    expect(pending.result.error).toMatch(/timed out/)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('bounds simultaneous groups and releases their capacity on the owning request timeout', async () => {
    const { client, request } = receivingClient()
    const old = request()
    for (let i = 0; i < 8; i++) client.receive({ event_type: 'dcdtuni:renderAck', id: `group-${i}`, part: 1, numParts: 2, payload: '{' })
    ackParts({ requestId: old.id, ok: true }, 'ninth-group', 40).forEach(p => client.receive(p))
    await Promise.resolve()
    expect(old.result).toEqual({})
    await vi.advanceTimersByTimeAsync(1000)
    expect(old.result.error).toMatch(/timed out/)
    expect(vi.getTimerCount()).toBe(0)
    const next = request()
    ackParts({ requestId: next.id, ok: true }, 'ninth-group', 40).forEach(p => client.receive(p))
    await vi.advanceTimersByTimeAsync(0)
    expect(next.result.ack).toEqual({ requestId: next.id, ok: true })
  })

  it('bounds aggregate fragment memory across individually valid groups', async () => {
    const { client, request } = receivingClient()
    const pending = request()
    for (let group = 0; group < 3; group++) {
      const prefix = ackParts({ requestId: pending.id, ok: false, error: 'x'.repeat(700000) }, `held-${group}`)
      prefix.slice(0, -1).forEach(p => client.receive(p))
    }
    ackParts({ requestId: pending.id, ok: false, error: 'y'.repeat(300000) }, 'over-total').forEach(p => client.receive(p))
    await Promise.resolve()
    expect(pending.result).toEqual({})
    client.dispose()
    await pending.promise
    expect(pending.result.error).toMatch(/disconnected/)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('drops malformed assembled JSON and cannot associate a preexisting group with a later request', async () => {
    const { client, request } = receivingClient()
    const first = request()
    client.receive({ event_type: 'dcdtuni:renderAck', id: 'bad-json', part: 2, numParts: 2, payload: 'invalid' })
    client.receive({ event_type: 'dcdtuni:renderAck', id: 'bad-json', part: 1, numParts: 2, payload: '{' })
    client.receive({ event_type: 'dcdtuni:renderAck', id: 'too-early', part: 1, numParts: 2, payload: '{"ok":true,' })
    const next = request()
    client.receive({ event_type: 'dcdtuni:renderAck', id: 'too-early', part: 2, numParts: 2, payload: `"requestId":"${next.id}"}` })
    await Promise.resolve()
    expect(first.result).toEqual({})
    expect(next.result).toEqual({})
    ackParts({ requestId: next.id, ok: true }, 'legitimate', 40).forEach(p => client.receive(p))
    await vi.advanceTimersByTimeAsync(0)
    expect(next.result.ack).toEqual({ requestId: next.id, ok: true })
    await vi.advanceTimersByTimeAsync(1000)
    expect(first.result.error).toMatch(/timed out/)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('drops timed-out and disposed groups so late pieces cannot complete a new request', async () => {
    const { client, request } = receivingClient()
    const old = request()
    const stale = ackParts({ requestId: old.id, ok: true }, 'reused-transport', 40)
    client.receive(stale[0])
    await vi.advanceTimersByTimeAsync(1000)
    expect(old.result.error).toMatch(/timed out/)
    expect(vi.getTimerCount()).toBe(0)
    const next = request()
    stale.slice(1).forEach(p => client.receive(p))
    client.receive(stale[0])
    await Promise.resolve()
    expect(next.result).toEqual({})
    client.dispose()
    await next.promise
    stale.forEach(p => client.receive(p))
    expect(next.result.error).toMatch(/disconnected/)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not allocate storage for unsolicited ACK fragments or cyclic message wrappers', () => {
    const { client, request } = receivingClient()
    client.receive({ event_type: 'dcdtuni:renderAck', id: 'unsolicited', part: 1, numParts: 2, payload: '{' })
    expect(vi.getTimerCount()).toBe(0)
    request()
    const wrapper: { message?: unknown } = {}
    wrapper.message = wrapper
    expect(() => client.receive(wrapper)).not.toThrow()
    expect(vi.getTimerCount()).toBe(1)
  })

  it('identical retransmissions do not extend the original request deadline', async () => {
    const { client, request } = receivingClient()
    const pending = request()
    const first = ackParts({ requestId: pending.id, ok: true }, 'retries', 40)[0]!
    client.receive(first)
    await vi.advanceTimersByTimeAsync(500)
    client.receive(first)
    await vi.advanceTimersByTimeAsync(500)
    expect(pending.result.error).toMatch(/timed out/)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('releases partial ACK storage when the associated send fails', async () => {
    vi.useFakeTimers()
    let requestId = ''
    const client = new OvCommandClient(async command => {
      requestId = JSON.parse(command.message).requestId
      throw new Error('Transport disconnected during send')
    }, 1000)
    clients.push(client)
    const result = client.readView().catch(error => error.message)
    client.receive(ackParts({ requestId, ok: true }, 'partial-before-send-failure', 40)[0])
    expect(await result).toBe('Transport disconnected during send')
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('ovAckTimeoutDegraded (display-only fallback policy)', () => {
  it('degrades only for ack-channel timeouts with provably live video', () => {
    expect(ovAckTimeoutDegraded('Omniverse acknowledgement timed out', 120)).toBe(true)
    expect(ovAckTimeoutDegraded('Omniverse command submission timed out', 45)).toBe(true)
  })

  it('stays a hard failure without live video', () => {
    expect(ovAckTimeoutDegraded('Omniverse acknowledgement timed out', 0)).toBe(false)
    expect(ovAckTimeoutDegraded('Omniverse acknowledgement timed out', 30)).toBe(false)
  })

  it('stays a hard failure for transport and stream errors', () => {
    expect(ovAckTimeoutDegraded('Omniverse stream stopped', 5000)).toBe(false)
    expect(ovAckTimeoutDegraded('Omniverse disconnected', 5000)).toBe(false)
    expect(ovAckTimeoutDegraded('Omniverse connection timed out', 0)).toBe(false)
    expect(ovAckTimeoutDegraded('Scene snapshot has non-finite matrix', 900)).toBe(false)
  })
})

describe('slice commands', () => {
  it('setSlice sends exactly the bridge-validated key set', async () => {
    const sent: { command_name: string; message: string }[] = []
    const client = new OvCommandClient(async payload => { sent.push(payload) })
    clients.push(client)
    const promise = client.setSlice({ visible: true, origin: [0, 1, 0], normal: [0, 1, 0], radius: 2, field: 'T', domain: null })
    await Promise.resolve()
    expect(sent[0]!.command_name).toBe('setSlice')
    const m = JSON.parse(sent[0]!.message)
    expect(Object.keys(m).sort()).toEqual(['domain', 'field', 'normal', 'origin', 'radius', 'requestId', 'visible'])
    client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: m.requestId, ok: true, slice: { origin: [0, 1, 0], radius: 2 } } })
    const ack = await promise
    expect(ack.slice).toEqual({ origin: [0, 1, 0], radius: 2 })
  })

  it('surfaces a rejected plane as an error ack, not a silent default', async () => {
    const sent: { command_name: string; message: string }[] = []
    const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000)
    clients.push(client)
    const promise = client.setSlice({ visible: true, origin: [0, 0, 0], normal: [0, 0, 0], radius: 1, field: 'T', domain: null })
    await Promise.resolve()
    const id = JSON.parse(sent[0]!.message).requestId
    client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: id, ok: false, error: 'slice normal must be non-zero' } })
    await expect(promise).rejects.toThrow(/non-zero/)
  })

  it('getSliceState round-trips the dragged origin back to the panel', async () => {
    const sent: { command_name: string; message: string }[] = []
    const client = new OvCommandClient(async payload => { sent.push(payload) })
    clients.push(client)
    const promise = client.getSliceState()
    await Promise.resolve()
    expect(sent[0]!.command_name).toBe('getSliceState')
    const id = JSON.parse(sent[0]!.message).requestId
    client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: id, ok: true, slice: { visible: true, origin: [3, 1, 3], normal: [0, 1, 0], radius: 2, field: 'Umag', domain: null } } })
    const ack = await promise
    expect(ack.slice?.origin).toEqual([3, 1, 3])
  })
})

describe('dataset and volume display commands', () => {
  it('loadResultDataset relays the grant verbatim with nothing path-like beside it', async () => {
    const sent: { command_name: string; message: string }[] = []
    const client = new OvCommandClient(async payload => { sent.push(payload) })
    clients.push(client)
    const grant = { payload: { kind: 'dataset', path: '/srv/a.case' }, signature: 'e'.repeat(64) }
    const promise = client.loadResultDataset(grant)
    await Promise.resolve()
    expect(sent[0]!.command_name).toBe('loadResultDataset')
    const m = JSON.parse(sent[0]!.message)
    expect(Object.keys(m).sort()).toEqual(['grant', 'requestId'])
    expect(m.grant).toEqual(grant)
    client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: m.requestId, ok: true,
      dataset: { run: 'r', runId: 'x', time: '2', fields: {}, ranges: {}, bounds: [0, 1, 0, 0.5, 0, 1], hasT: true, hasU: true } } })
    expect((await promise).dataset?.bounds).toEqual([0, 1, 0, 0.5, 0, 1])
  })

  it('setVolumeDisplay sends exactly {requestId, field, domain} and waits past the view timeout', async () => {
    vi.useFakeTimers()
    try {
      const sent: { command_name: string; message: string }[] = []
      const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000)
      clients.push(client)
      const promise = client.setVolumeDisplay('U', null)
      await vi.advanceTimersByTimeAsync(0)
      const m = JSON.parse(sent[0]!.message)
      expect(sent[0]!.command_name).toBe('setVolumeDisplay')
      expect(Object.keys(m).sort()).toEqual(['domain', 'field', 'requestId'])
      await vi.advanceTimersByTimeAsync(5000)  // a volume rebuild outlives the 1 s default
      client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: m.requestId, ok: true,
        volume: { field: 'U', visible: true, domain: [0.05, 0.15], degenerate: true, source: 'dataset p1..p99' } } })
      expect((await promise).volume?.domain).toEqual([0.05, 0.15])
    } finally { vi.useRealTimers() }
  })
})

describe('outbound readiness handshake', () => {
  // NVST drops Kit->browser app messages for a while after a session starts
  // (measured 2026-09-30: ~2 s through the SSH tunnel), so the first acked
  // request of a session can vanish although Kit sent it. The client probes
  // with idempotent getViewState until one ack proves the path is live.
  it('retries getViewState on a short timeout until an ack arrives', async () => {
    vi.useFakeTimers()
    try {
      const sent: { command_name: string; message: string }[] = []
      const client = new OvCommandClient(async payload => { sent.push(payload) })
      clients.push(client)
      const ready = client.awaitOutboundReady(10, 500)
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(1100)   // first two probes lost
      const probes = sent.filter(p => p.command_name === 'getViewState').map(p => JSON.parse(p.message).requestId)
      expect(probes.length).toBeGreaterThanOrEqual(3)
      expect(new Set(probes).size).toBe(probes.length)
      client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: probes.at(-1), ok: true, view: { selectedUids: [], timeSeconds: null } } })
      await expect(ready).resolves.toMatchObject({ ok: true })
      // a late ack for an abandoned probe is ignored, not an error
      client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: probes[0], ok: true } })
    } finally { vi.useRealTimers() }
  })

  it('treats an error ack as proof the path is live', async () => {
    const sent: { command_name: string; message: string }[] = []
    const client = new OvCommandClient(async payload => { sent.push(payload) })
    clients.push(client)
    const ready = client.awaitOutboundReady(5, 1000)
    await Promise.resolve(); await Promise.resolve()
    const id = JSON.parse(sent[0]!.message).requestId
    client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: id, ok: false, error: 'no render camera yet' } })
    await expect(ready).resolves.toMatchObject({ ok: false })
  })

  it('gives up with a timeout the degraded-display policy recognises', async () => {
    vi.useFakeTimers()
    try {
      const client = new OvCommandClient(async () => {})
      clients.push(client)
      const ready = client.awaitOutboundReady(3, 200)
      const assertion = expect(ready).rejects.toThrow(/acknowledgement timed out/)
      await vi.advanceTimersByTimeAsync(1000)
      await assertion
      const err = await ready.then(() => null, (e: Error) => e)
      expect(ovAckTimeoutDegraded(err!.message, 100)).toBe(true)
    } finally { vi.useRealTimers() }
  })
})

describe('lost Kit acknowledgements (livestream NVST_R_BUSY drops)', () => {
  const ackOf = (sent: { command_name: string; message: string }[], name: string, n = 0) =>
    JSON.parse(sent.filter(p => p.command_name === name)[n]!.message)

  it('re-asks a scene progress query whose ack was dropped, and still paces the transfer', async () => {
    vi.useFakeTimers()
    try {
      const sent: { command_name: string; message: string }[] = []
      const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000, 64, 60000, 2, 4, 60000, 60000)
      clients.push(client)
      const scene = buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'p' } })
      const promise = client.loadScene(scene)
      await vi.advanceTimersByTimeAsync(0)
      const first = ackOf(sent, 'renderSceneProgress')
      await vi.advanceTimersByTimeAsync(1000)            // its ack never comes
      const again = ackOf(sent, 'renderSceneProgress', 1)
      expect(again.requestId).not.toBe(first.requestId)
      expect([again.sceneRequestId, again.received]).toEqual([first.sceneRequestId, first.received])
      expect(sent.filter(p => p.command_name === 'renderSceneChunk')).toHaveLength(2)   // still paced
      client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: again.requestId, ok: true, received: again.received } })
      await vi.advanceTimersByTimeAsync(0)
      expect(sent.filter(p => p.command_name === 'renderSceneChunk').length).toBeGreaterThan(2)
      client.dispose()
      await expect(promise).rejects.toThrow(/disconnect/i)
    } finally { vi.useRealTimers() }
  })

  it('gives up after the bounded number of progress attempts', async () => {
    vi.useFakeTimers()
    try {
      const sent: { command_name: string; message: string }[] = []
      const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000, 64, 60000, 2, 3, 60000, 60000)
      clients.push(client)
      const promise = client.loadScene(buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'p' } }))
      const assertion = expect(promise).rejects.toThrow(/acknowledgement timed out/)
      await vi.advanceTimersByTimeAsync(3500)
      await assertion
      expect(sent.filter(p => p.command_name === 'renderSceneProgress')).toHaveLength(3)
      expect(sent.some(p => p.command_name === 'loadRenderScene')).toBe(false)
    } finally { vi.useRealTimers() }
  })

  it('recovers a dropped scene ack from the scene id Kit reports, never from another scene', async () => {
    vi.useFakeTimers()
    try {
      const sent: { command_name: string; message: string }[] = []
      const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000, 16000, 60000, 64, 4, 100, 50)
      clients.push(client)
      const promise = client.loadScene(buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'p' } }))
      let settled: unknown = null
      void promise.then(v => { settled = v })
      await vi.advanceTimersByTimeAsync(0)
      const sceneId = ackOf(sent, 'loadRenderScene').requestId
      await vi.advanceTimersByTimeAsync(100)
      const probe1 = ackOf(sent, 'getViewState')
      // Kit still reports the previous scene (authoring not finished): no settle.
      client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: probe1.requestId, ok: true,
        sceneRequestId: 'older-scene', projectHash: 'a'.repeat(64), sceneRevision: 3, primitives: 5 } })
      await vi.advanceTimersByTimeAsync(60)
      expect(settled).toBeNull()
      const probe2 = ackOf(sent, 'getViewState', 1)
      client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: probe2.requestId, ok: true,
        sceneRequestId: sceneId, projectHash: 'b'.repeat(64), sceneRevision: 4, primitives: 7, view: { selectedUids: [], timeSeconds: null } } })
      await expect(promise).resolves.toMatchObject({ requestId: sceneId, ok: true, recovered: true,
        projectHash: 'b'.repeat(64), sceneRevision: 4, primitives: 7 })
      // A late real ack for the settled scene is ignored; probing stopped.
      const probes = sent.filter(p => p.command_name === 'getViewState').length
      client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: sceneId, ok: true } })
      await vi.advanceTimersByTimeAsync(500)
      expect(sent.filter(p => p.command_name === 'getViewState').length).toBe(probes)
    } finally { vi.useRealTimers() }
  })

  it('does not probe when the real scene ack arrives', async () => {
    vi.useFakeTimers()
    try {
      const sent: { command_name: string; message: string }[] = []
      const client = new OvCommandClient(async payload => { sent.push(payload) }, 1000, 16000, 60000, 64, 4, 100, 50)
      clients.push(client)
      const promise = client.loadScene(buildSceneSnapshot({ root: new THREE.Group(), project: { uid: 'p' } }))
      await vi.advanceTimersByTimeAsync(0)
      const sceneId = ackOf(sent, 'loadRenderScene').requestId
      client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: sceneId, ok: true, sceneRevision: 1 } })
      await expect(promise).resolves.toMatchObject({ ok: true })
      await vi.advanceTimersByTimeAsync(500)
      expect(sent.some(p => p.command_name === 'getViewState')).toBe(false)
    } finally { vi.useRealTimers() }
  })
})

it('normalises a view that crossed the carb payload (empty list -> {}, None dropped)', async () => {
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) })
  clients.push(client)
  const promise = client.readView()
  await Promise.resolve(); await Promise.resolve()
  const id = JSON.parse(sent[0]!.message).requestId
  const camera = { position: [1, 2, 3], target: [0, 0, 0], up: [0, 1, 0], fov: 45, aspect: 1.6, near: 0.1, far: 100 }
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: id, ok: true, view: { camera, selectedUids: {} } } })
  const ack = await promise
  expect(ack.view!.selectedUids).toEqual([])
  expect(ack.view!.timeSeconds).toBeNull()
  expect(ack.view!.camera).toEqual(camera)
})


it('the scene ack budget grows with the primitive count and never drops below the base', () => {
  expect(sceneAckBudgetMs(240000, 2000)).toBe(240000)
  expect(sceneAckBudgetMs(240000, 10276)).toBe(60000 + 30 * 10276)
  expect(sceneAckBudgetMs(240000, 10276)).toBeGreaterThan(300000)
})
