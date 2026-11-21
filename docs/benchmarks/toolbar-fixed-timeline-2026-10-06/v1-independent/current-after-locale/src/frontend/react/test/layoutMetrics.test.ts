import { describe, expect, it } from 'vitest'
import {
  COLLAPSED_LOG_HEIGHT,
  MIN_SIZE,
  MIN_VIEWPORT_WIDTH,
  clampResize,
  toggleLogDock,
  panelFitWidth,
  toolbarScaleStyle,
  toolbarTargetHeight,
} from '../src/ui/layoutMetrics'

describe('panelFitWidth', () => {
  it('leaves the desktop widths alone', () => {
    expect(panelFitWidth(330, 1920)).toBe(330)
    expect(panelFitWidth(340, 1920)).toBe(340)
    expect(panelFitWidth(330, 1280)).toBe(330)
  })

  it('never lets two panels cover the window', () => {
    for (const w of [1000, 800, 640, 480, 320]) {
      const left = panelFitWidth(330, w)
      const right = panelFitWidth(340, w)
      expect(left + right).toBeLessThanOrEqual(w - MIN_VIEWPORT_WIDTH + 0.001)
    }
  })

  it('does not go negative on a window narrower than the gap', () => {
    expect(panelFitWidth(330, 60)).toBe(0)
  })
})

describe('clampResize', () => {
  it('tracks the pointer between the bounds', () => {
    expect(clampResize('left', 400, 0, 1920, 1080)).toBe(400)
    expect(clampResize('right', 1520, 0, 1920, 1080)).toBe(400)
    expect(clampResize('log', 0, 880, 1920, 1080)).toBe(200)
    expect(clampResize('toolbar', 0, 200, 1920, 1080)).toBe(200)
  })

  it('holds each edge at its minimum', () => {
    expect(clampResize('left', 10, 0, 1920, 1080)).toBe(MIN_SIZE.left)
    expect(clampResize('right', 1910, 0, 1920, 1080)).toBe(MIN_SIZE.right)
    expect(clampResize('log', 0, 1079, 1920, 1080)).toBe(MIN_SIZE.log)
    expect(clampResize('toolbar', 0, 2, 1920, 1080)).toBe(MIN_SIZE.toolbar)
  })

  it('keeps a viewport between two fully dragged side panels', () => {
    const w = 1920
    const left = clampResize('left', w, 0, w, 1080)
    const right = clampResize('right', 0, 0, w, 1080)
    expect(left + right).toBe(w - MIN_VIEWPORT_WIDTH)
  })

  it('caps the log and the toolbar at half the window', () => {
    expect(clampResize('log', 0, 0, 1920, 1080)).toBe(540)
    expect(clampResize('toolbar', 0, 1080, 1920, 1080)).toBe(540)
  })

  it('lets the minimum win on a window too small for the fraction', () => {
    expect(clampResize('log', 0, 0, 400, 40)).toBe(MIN_SIZE.log)
  })
})

describe('toggleLogDock', () => {
  it('collapses to the header and remembers where it was', () => {
    expect(toggleLogDock(160, 160)).toEqual({
      heightPx: COLLAPSED_LOG_HEIGHT,
      rememberPx: 160,
    })
  })

  it('remembers a dragged height, not the default', () => {
    expect(toggleLogDock(300, 160).rememberPx).toBe(300)
  })

  it('restores what it remembered', () => {
    expect(toggleLogDock(COLLAPSED_LOG_HEIGHT, 300)).toEqual({
      heightPx: 300,
      rememberPx: 300,
    })
  })

  it('never remembers the collapsed height as the one to restore', () => {
    const collapsed = toggleLogDock(220, 160)
    expect(toggleLogDock(collapsed.heightPx, collapsed.rememberPx).heightPx).toBe(220)
  })

  it('folds a dock that was dragged open from collapsed', () => {
    // No boolean to disagree with: the height in front of the user decides.
    expect(toggleLogDock(200, 160).heightPx).toBe(COLLAPSED_LOG_HEIGHT)
  })
})

describe('toolbarTargetHeight', () => {
  it('leaves a toolbar that fits at its natural height', () => {
    expect(toolbarTargetHeight(129, null, 1080)).toBe(129)
    // 281 of a 700px window is 40% — over the auto cap, so it is scaled now.
    // It used to be left alone, which is how a wrapped toolbar came to own a
    // third of the screen at 1100x700 while the viewport got 230x140.
    expect(toolbarTargetHeight(190, null, 700)).toBe(190)
  })

  it('scales a WRAPPED toolbar past 28% of the window, not half', () => {
    // Nobody asked for a toolbar this tall — it only wrapped. A drag is
    // different and keeps the generous cap; see the manual case below.
    expect(toolbarTargetHeight(403, null, 640)).toBeCloseTo(640 * 0.28, 6)
    expect(toolbarTargetHeight(1245, null, 480)).toBeCloseTo(480 * 0.28, 6)
    expect(toolbarTargetHeight(281, null, 700)).toBeCloseTo(700 * 0.28, 6)
  })

  it('honours a manual drag but never past the cap or the natural height', () => {
    expect(toolbarTargetHeight(400, 100, 1080)).toBe(100)
    expect(toolbarTargetHeight(400, 900, 1080)).toBe(400)
    expect(toolbarTargetHeight(400, 380, 600)).toBe(300)
  })
})

describe('toolbar footer reservation', () => {
  it('keeps the wrapped footer and an operable body after a small manual drag', () => {
    expect(toolbarTargetHeight(340, 28, 720, 116)).toBe(116)
    expect(toolbarTargetHeight(340, null, 720, 116)).toBeCloseTo(201.6)
  })
  it('increases the minimum when the footer wraps, without growing past either cap', () => {
    expect(toolbarTargetHeight(340, 80, 720, 174)).toBe(174)
    expect(toolbarTargetHeight(340, null, 720, 260)).toBeCloseTo(201.6)
    expect(toolbarTargetHeight(500, 28, 180, 260)).toBe(90)
  })
})

describe('toolbarScaleStyle', () => {
  it('writes no transform when the toolbar already fits', () => {
    expect(toolbarScaleStyle(129, 129)).toEqual({ transform: '', height: '' })
    expect(toolbarScaleStyle(200, 129)).toEqual({ transform: '', height: '' })
  })

  it('scales by the ratio and pins the natural height', () => {
    expect(toolbarScaleStyle(320, 640)).toEqual({ transform: 'scale(0.5)', height: '640px' })
  })

  it('survives a toolbar that has not been measured yet', () => {
    expect(toolbarScaleStyle(100, 0)).toEqual({ transform: '', height: '' })
  })
})
