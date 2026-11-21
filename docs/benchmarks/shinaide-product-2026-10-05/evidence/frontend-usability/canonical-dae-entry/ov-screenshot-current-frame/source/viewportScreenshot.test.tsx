import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LogPanel } from '../src/components/LogPanel'
import * as store from '../src/projectStore'

vi.mock('../src/components/TerminalPanel', () => ({ TerminalPanel: () => null }))

type ViewportWindow = Window & { __viewportRender?: () => HTMLCanvasElement | null }
const viewportWindow = window as ViewportWindow
let host: HTMLDivElement
let root: Root
let threeCanvas: HTMLCanvasElement
let drawImage: ReturnType<typeof vi.fn>
let fetchScreenshot: ReturnType<typeof vi.fn>
let renderThree: ReturnType<typeof vi.fn>
let saveCanvas: HTMLCanvasElement | undefined

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  })
  document.body.innerHTML = '<div id="center-view"><canvas></canvas></div>'
  threeCanvas = document.querySelector<HTMLCanvasElement>('#center-view canvas')!
  drawImage = vi.fn()
  saveCanvas = undefined
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
    saveCanvas = this
    return { drawImage } as unknown as CanvasRenderingContext2D
  })
  vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(function (this: HTMLCanvasElement) {
    return this === threeCanvas ? 'data:image/png;base64,THREE' : 'data:image/png;base64,OV_CURRENT_FRAME'
  })
  fetchScreenshot = vi.fn(async () => ({ json: async () => ({ path: 'screenshots/current.png' }) }))
  vi.stubGlobal('fetch', fetchScreenshot)
  renderThree = vi.fn(() => threeCanvas)
  viewportWindow.__viewportRender = renderThree
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  act(() => root.render(<LogPanel />))
})

afterEach(() => {
  act(() => root.unmount())
  delete viewportWindow.__viewportRender
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function ovVideo(readyState = 4, width = 1600, height = 900) {
  const overlay = document.createElement('div')
  overlay.className = 'ov-render-viewport'
  const video = document.createElement('video')
  video.style.width = '320px'
  video.style.height = '180px'
  Object.defineProperties(video, {
    readyState: { value: readyState, configurable: true },
    videoWidth: { value: width, configurable: true },
    videoHeight: { value: height, configurable: true },
  })
  overlay.append(video)
  document.getElementById('center-view')!.append(overlay)
  return video
}

async function pressScreenshot() {
  const button = host.querySelector<HTMLButtonElement>('#btn-screenshot')!
  await act(async () => {
    button.click()
    await Promise.resolve()
  })
}

it('the actual screenshot button saves the decoded OV frame at native dimensions, without rendering hidden Three', async () => {
  const video = ovVideo(2, 1920, 1080)
  await pressScreenshot()
  expect(renderThree).not.toHaveBeenCalled()
  expect(drawImage).toHaveBeenCalledTimes(1)
  expect(drawImage).toHaveBeenCalledWith(video, 0, 0, 1920, 1080)
  expect([saveCanvas!.width, saveCanvas!.height]).toEqual([1920, 1080])
  expect(fetchScreenshot).toHaveBeenCalledTimes(1)
  expect(fetchScreenshot).toHaveBeenCalledWith('/api/screenshot', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ imageData: 'data:image/png;base64,OV_CURRENT_FRAME' }),
  })
})

it('an OV stream without a current decoded frame reports an error and never falls back or posts Three', async () => {
  ovVideo(1)
  await pressScreenshot()
  expect(fetchScreenshot).not.toHaveBeenCalled()
  expect(renderThree).not.toHaveBeenCalled()
  expect(drawImage).not.toHaveBeenCalled()
  expect(store.getLog()[0]).toMatchObject({ action: 'captureScreenshot' })
  expect(store.getLog()[0]!.detail).toMatch(/Omniverse.*decoded current frame/i)
})

it('an empty OV video with no native frame size cannot produce a screenshot', async () => {
  ovVideo(4, 0, 0)
  await pressScreenshot()
  expect(fetchScreenshot).not.toHaveBeenCalled()
  expect(renderThree).not.toHaveBeenCalled()
  expect(store.getLog()[0]!.detail).toMatch(/Omniverse.*decoded current frame/i)
})

it('a mounted OV viewport with no video explicitly refuses the screenshot', async () => {
  const overlay = document.createElement('div')
  overlay.className = 'ov-render-viewport'
  document.getElementById('center-view')!.append(overlay)
  await pressScreenshot()
  expect(fetchScreenshot).not.toHaveBeenCalled()
  expect(renderThree).not.toHaveBeenCalled()
  expect(store.getLog()[0]!.detail).toMatch(/Omniverse.*video/i)
})

it('Three screenshots still re-render immediately before toDataURL', async () => {
  let rendered = false
  renderThree.mockImplementation(() => {
    rendered = true
    return threeCanvas
  })
  vi.spyOn(threeCanvas, 'toDataURL').mockImplementation(() => {
    expect(rendered).toBe(true)
    return 'data:image/png;base64,FRESH_THREE'
  })
  await pressScreenshot()
  expect(renderThree).toHaveBeenCalledTimes(1)
  expect(drawImage).not.toHaveBeenCalled()
  expect(JSON.parse(fetchScreenshot.mock.calls[0]![1].body)).toEqual({
    imageData: 'data:image/png;base64,FRESH_THREE',
  })
})

it('Three can retain the existing canvas fallback when its render hook is unavailable', async () => {
  delete viewportWindow.__viewportRender
  await pressScreenshot()
  expect(drawImage).not.toHaveBeenCalled()
  expect(JSON.parse(fetchScreenshot.mock.calls[0]![1].body)).toEqual({
    imageData: 'data:image/png;base64,THREE',
  })
})

it('OV frame drawing errors are logged without a hidden-Three fallback or invalid POST', async () => {
  ovVideo()
  drawImage.mockImplementation(() => { throw new Error('decoded frame became unavailable') })
  await pressScreenshot()
  expect(renderThree).not.toHaveBeenCalled()
  expect(fetchScreenshot).not.toHaveBeenCalled()
  expect(store.getLog()[0]!.detail).toContain('decoded frame became unavailable')
})
