import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'

const sdk = vi.hoisted(() => {
  let resolve!: () => void
  const init = new Promise<void>(r => { resolve = r })
  return { init, resolve, status: 0, config: null as null | {onStart:(event:unknown)=>void} }
})
vi.mock('@nvidia/omniverse-webrtc-streaming-library', () => ({
  StreamStatus: {none:0},
  AppStreamer: {
    get streamStatus() { return sdk.status },
    connect: vi.fn((options:{streamConfig:typeof sdk.config}) => {
      sdk.config = options.streamConfig
      return sdk.init.then(() => { sdk.status = 1; return {status:'inProgress'} })
    }),
    terminate: vi.fn(async () => {
      if(sdk.status === 1) {sdk.status = 3; return {status:'inProgress'}}
      sdk.status = 0; return {status:'success'}
    }),
  },
}))
vi.mock('../src/useTranslation', () => ({useTranslation:() => ({t:(key:string)=>key})}))
vi.mock('../src/renderer/ovProtocol', () => ({
  OvCommandClient:class {dispose=vi.fn()}, ovAckTimeoutDegraded:() => false,
}))
vi.mock('../src/renderer/ovStreamLease', async importOriginal => {
  const original = await importOriginal<typeof import('../src/renderer/ovStreamLease')>()
  return {...original, ovStreamLeases:new original.OvStreamLeases(), terminateOvViewer:vi.fn(original.terminateOvViewer)}
})
import { OvViewport } from '../src/renderer/OvViewport'
import { terminateOvViewer } from '../src/renderer/ovStreamLease'

it('records terminal SDK start after disposal without starting scene synchronization', async () => {
  vi.useFakeTimers()
  const mount = document.createElement('div'); document.body.append(mount)
  const root = createRoot(mount), snapshot = vi.fn(() => {throw new Error('disposed scene must not serialize')})
  const onReady = vi.fn()
  const previousAct = (globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT
  ;(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT = true
  try {
    await act(async () => {root.render(<OvViewport server="not-contacted.invalid" snapshot={snapshot} projectHash={()=>'hash'} onReady={onReady}/> )})
    expect(sdk.config).not.toBeNull()
    await act(async () => {root.unmount()})
    const calls = vi.mocked(terminateOvViewer).mock.calls
    expect(calls).toHaveLength(1)
    const startEvent = calls[0][5]
    expect(startEvent).toBeDefined()
    sdk.config!.onStart({status:'warning'})
    expect(startEvent!.settled).toBe(false)
    sdk.config!.onStart({status:'success'})
    expect(startEvent!.settled).toBe(true)
    sdk.resolve()
    await vi.advanceTimersByTimeAsync(50)
    await expect(vi.mocked(terminateOvViewer).mock.results[0].value).resolves.toBeUndefined()
    expect(sdk.status).toBe(0)
    expect(snapshot).not.toHaveBeenCalled(); expect(onReady).not.toHaveBeenCalled()
  } finally {
    mount.remove(); vi.useRealTimers()
    ;(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT = previousAct
  }
})
