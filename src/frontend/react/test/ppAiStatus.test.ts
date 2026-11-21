import { describe, expect, it } from 'vitest'
import {
  aiBoxIsEmpty,
  aiDevice,
  aiInferStats,
  aiReadyMessage,
  aiWarmup,
} from '../src/postprocess/ppAiStatus'

/**
 * The AI status line's readers.
 *
 * The figures live in parts of the worker's reply that the fetch helpers do not
 * declare, so each one is read defensively: a worker that stops sending
 * `inferSeconds` must leave a zero in the line, not throw inside the click
 * handler that was about to report success.
 */

// The shape `/api/pp/ai/start` forwards from pp_ai_worker.py.
const START = {
  success: true,
  status: { ready: true, device: 'cuda:0', bounds: { min: [0, 0, 0], max: [1, 1, 1] } },
  result: { warmup: { selectedCount: 4096, inferSeconds: 0.412, warmup: true } },
}

const INFER = {
  success: true,
  fields: [{ name: 'AI_T' }],
  result: {
    selectedCount: 41200,
    inferSeconds: 1.83,
    ranges: { AI_T: [295.1, 312.4], AI_p: [-1, 1], AI_Umag: [0, 3.2] },
  },
}

describe('aiDevice', () => {
  it('reads the device the worker loaded on', () => {
    expect(aiDevice(START)).toBe('cuda:0')
  })

  it('is empty when the worker did not report one', () => {
    expect(aiDevice({ success: true, status: {} })).toBe('')
    expect(aiDevice(null)).toBe('')
  })
})

describe('aiWarmup', () => {
  it('reads the warm-up cell count and time', () => {
    expect(aiWarmup(START)).toEqual({ count: 4096, seconds: 0.412 })
  })

  it('is null when the start carried no warm-up', () => {
    expect(aiWarmup({ result: {} })).toBeNull()
  })
})

describe('aiInferStats', () => {
  it('reports the cells the run inferred, not the fields it returned', () => {
    // The panel used to print Object.keys(ranges).length here — three — for a
    // run that covered 41200 cells.
    expect(aiInferStats(INFER)).toEqual({ cells: 41200, seconds: 1.83 })
  })

  it('falls back to zeros rather than throwing on an empty result', () => {
    expect(aiInferStats({ success: true })).toEqual({ cells: 0, seconds: 0 })
  })
})

describe('aiReadyMessage', () => {
  it('appends the device in brackets and then the warm-up phrase', () => {
    expect(aiReadyMessage('AI worker ready', 'cuda:0', ', warm up: 4096 cells / 0.412s')).toBe(
      'AI worker ready [cuda:0], warm up: 4096 cells / 0.412s',
    )
  })

  it('leaves out what the worker did not send', () => {
    expect(aiReadyMessage('AI worker ready', '', null)).toBe('AI worker ready')
  })
})

describe('aiBoxIsEmpty', () => {
  it('rejects a box with no extent on one axis', () => {
    expect(aiBoxIsEmpty({ min: [0, 1, 0], max: [2, 1, 3] })).toBe(true)
  })

  it('accepts a real region', () => {
    expect(aiBoxIsEmpty({ min: [0, 0, 0], max: [2, 1.5, 3] })).toBe(false)
  })
})
