import { expect, it, vi } from 'vitest'
import { inflateSync } from 'node:zlib'
import { handOffScene, kitMayHoldScene, syncLatestOvScene } from '../src/renderer/ovSceneTransfer'
import type { SceneSnapshot } from '../src/renderer/sceneSnapshot'
import type { RenderAck } from '../src/renderer/ovProtocol'
it('finishes the project loaded while the older scene is still transferring', async () => {
  let active = 'old', release!: (ack: RenderAck) => void
  const sent: string[] = []
  const task = syncLatestOvScene(() => ({packet: {project: {uid: active}} as SceneSnapshot, hash: active}),
    async (packet, hash) => {
      expect(packet.project.uid).toBe(hash); sent.push(hash)
      return hash === 'old' ? new Promise<RenderAck>(resolve => {release = resolve}) : {requestId: hash, ok: true, projectHash: hash}
    }, () => active, () => false)
  active = 'new'; release({requestId:'old', ok:true, projectHash:'old'})
  expect((await task)?.packet.project.uid).toBe('new')
  expect(sent).toEqual(['old','new'])
})
it('ignores an acknowledgement after the viewer has been disposed', async () => {
  let disposed = false, release!: (ack: RenderAck) => void
  const task = syncLatestOvScene(() => ({packet: {} as SceneSnapshot, hash:'project'}),
    () => new Promise<RenderAck>(resolve => {release = resolve}), () => 'project', () => disposed)
  disposed = true; release({requestId:'old', ok:true})
  expect(await task).toBeNull()
})
it('hands a key captured with the packet to the load, and leaves a later capture to compute its own', async () => {
  let active = 'old', release!: (ack: RenderAck) => void
  const keys: (string | undefined)[] = []
  let first = true
  const task = syncLatestOvScene(() => {
    const taken = first ? {key: 'k-old'} : {}
    first = false
    return {packet: {project: {uid: active}} as SceneSnapshot, hash: active, ...taken}
  }, async (_packet, hash, key) => {
    keys.push(key)
    return hash === 'old' ? new Promise<RenderAck>(resolve => {release = resolve}) : {requestId: hash, ok: true}
  }, () => active, () => false)
  active = 'new'; release({requestId: 'old', ok: true})
  await task
  expect(keys).toEqual(['k-old', undefined])
})
it('tries to reuse only the project Kit says it holds', () => {
  expect(kitMayHoldScene({requestId: 'p', ok: true, projectHash: 'a'}, 'a')).toBe(true)
  expect(kitMayHoldScene({requestId: 'p', ok: true, projectHash: 'b'}, 'a')).toBe(false)
  expect(kitMayHoldScene({requestId: 'p', ok: false, error: 'No editor render scene loaded'}, 'a')).toBe(false)
  expect(kitMayHoldScene({requestId: 'p', ok: true}, 'a')).toBe(false)
})
it('hands the scene to the server deflated, with its project hash and scene key', async () => {
  const fetch = vi.fn(async (_url: string, _init: RequestInit) => new Response('{"ok":true}', { status: 200 }))
  vi.stubGlobal('fetch', fetch)
  try {
    const text = JSON.stringify({ schema: 'dcdtuni.render-scene/v1', primitives: [] })
    expect(await handOffScene(text, 'a'.repeat(64), 'b'.repeat(64))).toBe(true)
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/ov/scene-handoff')
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({ 'X-Project-Hash': 'a'.repeat(64), 'X-Scene-Key': 'b'.repeat(64) })
    expect(inflateSync(Buffer.from(init.body as ArrayBuffer)).toString()).toBe(text)
    fetch.mockResolvedValueOnce(new Response('{}', { status: 503 }))
    expect(await handOffScene(text, 'a'.repeat(64), 'b'.repeat(64))).toBe(false)
  } finally {
    vi.unstubAllGlobals()
  }
})
