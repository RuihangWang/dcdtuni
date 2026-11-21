import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

const html = readFileSync(resolve(__dirname, '../../../web/STL_generate.html'), 'utf8')

it('shows the independent H5 time-provenance notice in the legacy editor', () => {
  const page = new DOMParser().parseFromString(html, 'text/html')
  const notice = page.querySelector('[data-i18n="pp.ai_independent_h5_notice"]')
  expect(notice).not.toBeNull()
})

function legacyFunction(name: string): string {
  const start = html.indexOf(`window.${name} = `)
  if (start < 0) throw new Error(`Missing legacy function ${name}`)
  const opening = html.indexOf('{', start)
  let depth = 0
  for (let index = opening; index < html.length; index++) {
    if (html[index] === '{') depth++
    if (html[index] === '}' && --depth === 0) return html.slice(start, index + 1) + ';'
  }
  throw new Error(`Unclosed legacy function ${name}`)
}

it('invalidates an in-flight HTML postprocess load when the selected run or time changes', () => {
  const state = { initialized: false, initPending: true, ppSession: null, initGeneration: 1 }
  const calls: string[] = []
  const legacyWindow = {
    _ppState: state,
    ppClearAll: () => {
      calls.push('clear')
      state.initGeneration++
      state.initPending = false
    },
    onLogRunChange: () => {},
    ppOnTimeChange: () => {},
  }
  const run = document.createElement('select')
  run.id = 'pp-log-run'
  run.innerHTML = '<option value="log/other">other</option>'
  document.body.append(run)
  const time = document.createElement('select')
  time.id = 'pp-timestep'
  document.body.append(time)
  const uiState = { session: { logRuns: [
    { path: 'log/other', timesteps: [1, 2] },
  ] } }
  const install = new Function('window', 'document', 'uiState', 't',
    legacyFunction('onLogRunChange') + legacyFunction('ppOnTimeChange'))
  try {
    install(legacyWindow, document, uiState, (key: string) => key)
    legacyWindow.onLogRunChange()
    expect(calls).toEqual(['clear'])
    expect(state.initGeneration).toBe(2)
    expect(run.value).toBe('log/other')
    expect(time.value).toBe('2')

    state.initPending = true
    legacyWindow.ppOnTimeChange()
    expect(calls).toEqual(['clear', 'clear'])
    expect(state.initGeneration).toBe(3)
    expect(time.value).toBe('2')
  } finally {
    run.remove()
    time.remove()
  }
})

it('rejects an old HTML result after another local selection becomes active', async () => {
  const oldSession = { runId: 'run-A', timeValue: 1, revision: 'revision-A' }
  const state = { ppSession: oldSession }
  let release: () => void = () => {}
  const gate = new Promise<void>((resolve) => { release = resolve })
  const legacyWindow = { _ppState: state, _ppFetchForSession: async (_url: string) => null,
    ppClearAll: () => { state.ppSession = null as never } }
  let sentRevision = ''
  const fetch = async (_url: string, request: RequestInit) => {
    sentRevision = new Headers(request.headers).get('X-PP-Revision') ?? ''
    await gate
    return { ok: true, status: 200, statusText: 'OK',
      headers: { get: () => 'revision-A' }, arrayBuffer: async () => new ArrayBuffer(0) }
  }
  new Function('window', 'fetch', 'Headers', legacyFunction('_ppFetchForSession'))(
    legacyWindow, fetch, Headers)
  const request = legacyWindow._ppFetchForSession('/api/pp/field/T')
  state.ppSession = { runId: 'run-B', timeValue: 2, revision: 'revision-B' }
  release()
  await expect(request).rejects.toThrow(/selection changed/i)
  expect(sentRevision).toBe('revision-A')
  expect(state.ppSession.revision).toBe('revision-B')
})

it('drops an HTML init response when the user changes time before it returns', async () => {
  const state = { initialized: false, initPending: false, ppSession: null,
    initGeneration: 0 }
  let release: () => void = () => {}
  const gate = new Promise<void>((resolve) => { release = resolve })
  let completed = false
  const legacyWindow = {
    _ppState: state,
    ppInitPostProcess: async () => {},
    ppOnTimeChange: () => {},
    ppClearAll: () => {
      state.ppSession = null
      state.initPending = false
      state.initGeneration++
    },
  }
  const run = document.createElement('select')
  run.id = 'pp-log-run'
  run.innerHTML = '<option value="log/run">run</option>'
  document.body.append(run)
  const time = document.createElement('select')
  time.id = 'pp-timestep'
  time.innerHTML = '<option value="1">1</option><option value="2">2</option>'
  document.body.append(time)
  const task = { log: () => {}, isCancelled: () => false,
    fail: () => {}, done: () => { completed = true } }
  const install = new Function(
    'window', 'document', 'ppSelectionModel', 'showBlockingTaskDialog',
    'logOperation', 't', 'fetch', 'AbortController',
    legacyFunction('ppInitPostProcess') + legacyFunction('ppOnTimeChange'),
  )
  const fetch = async () => {
    await gate
    return { ok: true, json: async () => ({ success: true, runId: 'run',
      revision: 'rev', timeValue: 1 }) }
  }
  try {
    install(legacyWindow, document, null, () => task, () => {}, (key: string) => key,
      fetch, AbortController)
    const pending = legacyWindow.ppInitPostProcess()
    expect(state.initPending).toBe(true)
    time.value = '2'
    legacyWindow.ppOnTimeChange()
    release()
    await pending
    expect(state.initPending).toBe(false)
    expect(state.ppSession).toBeNull()
    expect(completed).toBe(false)
  } finally {
    release()
    run.remove()
    time.remove()
  }
})
