import { afterEach, expect, it, vi } from 'vitest'
import { OvCommandClient } from '../src/renderer/ovProtocol'

afterEach(() => vi.unstubAllGlobals())

it('correlates concurrent ACKs on LAN HTTP where randomUUID is unavailable', async () => {
  const browserCrypto = globalThis.crypto
  vi.stubGlobal('crypto', { getRandomValues: (values: Uint8Array) => browserCrypto.getRandomValues(values) })
  const sent: { command_name: string; message: string }[] = []
  const client = new OvCommandClient(async payload => { sent.push(payload) })
  const first = client.updateView({ selectedUids: ['first'], timeSeconds: 1 })
  const second = client.updateView({ selectedUids: ['second'], timeSeconds: 2 })
  const ids = sent.map(payload => JSON.parse(payload.message).requestId as string)
  expect(ids).toHaveLength(2)
  expect(ids[0]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  expect(ids[0]).not.toBe(ids[1])
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: ids[1], ok: true, view: { selectedUids: ['second'] } } })
  await expect(second).resolves.toMatchObject({ view: { selectedUids: ['second'] } })
  client.receive({ event_type: 'dcdtuni:renderAck', payload: { requestId: ids[0], ok: true, view: { selectedUids: ['first'] } } })
  await expect(first).resolves.toMatchObject({ view: { selectedUids: ['first'] } })
  client.dispose()
})
