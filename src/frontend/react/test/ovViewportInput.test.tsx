import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

// The streaming library enables input forwarding only from a focusin on the
// video's PARENT (onFocusStream -> captureUserInput(true)). A video that cannot
// take focus never raises focusin, so clicks and drags in the OV viewport
// (seed sphere, slice preview disc) were never forwarded to Kit. The reference
// demo's video carries tabIndex=-1 for exactly this.
vi.mock('@nvidia/omniverse-webrtc-streaming-library', () => ({
  AppStreamer: { connect: vi.fn(() => new Promise(() => {})), sendMessage: vi.fn(), terminate: vi.fn(async () => {}) },
}))

let host: HTMLDivElement, root: Root
beforeEach(() => {
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove() })

it('the stream video is focusable so a click raises focusin on its parent', async () => {
  const { OvViewport } = await import('../src/renderer/OvViewport')
  await act(async () => root.render(<OvViewport server="localhost" snapshot={() => ({}) as never} projectHash={() => ''} />))
  const video = host.querySelector('video')!
  expect(video.getAttribute('tabindex')).toBe('-1')
  const seen = vi.fn()
  video.parentElement!.addEventListener('focusin', seen)
  video.focus()
  expect(seen).toHaveBeenCalled()
})
