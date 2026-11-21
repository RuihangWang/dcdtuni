import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  isRotationSnapOn,
  ROTATION_SNAP_BUTTON_ID,
  setRotationSnap,
  toggleRotationSnap,
} from '../src/room2d/rotationSnapBridge'

/** The toolbar button App renders, with App's own toggle behind it. */
function mountToolbarButton(active: boolean): HTMLButtonElement {
  const btn = document.createElement('button')
  btn.id = ROTATION_SNAP_BUTTON_ID
  btn.className = active ? 'tool-btn active' : 'tool-btn'
  btn.addEventListener('click', () => btn.classList.toggle('active'))
  document.body.appendChild(btn)
  return btn
}

beforeEach(() => setRotationSnap(false))
afterEach(() => {
  document.getElementById(ROTATION_SNAP_BUTTON_ID)?.remove()
})

describe('isRotationSnapOn', () => {
  it('reads the 3D toolbar button, which IS the shared flag', () => {
    mountToolbarButton(true)
    expect(isRotationSnapOn()).toBe(true)
  })

  it('falls back to its own copy when no toolbar is mounted', () => {
    setRotationSnap(true)
    expect(isRotationSnapOn()).toBe(true)
  })
})

describe('setRotationSnap', () => {
  it('clicks the button rather than setting the class', () => {
    // The class is a RENDER of App state. Setting it directly would be
    // overwritten on the next render, and the 3D gizmo would keep the old
    // behaviour while the 2D toolbar claimed otherwise.
    const btn = mountToolbarButton(false)
    setRotationSnap(true)
    expect(btn.classList.contains('active')).toBe(true)
    expect(isRotationSnapOn()).toBe(true)
  })

  it('does not click when the button already agrees', () => {
    const btn = mountToolbarButton(true)
    setRotationSnap(true)
    expect(btn.classList.contains('active')).toBe(true)
  })

  it('turns it back off through the same click', () => {
    const btn = mountToolbarButton(true)
    setRotationSnap(false)
    expect(btn.classList.contains('active')).toBe(false)
  })
})

describe('toggleRotationSnap', () => {
  it('flips the flag and reports the new value', () => {
    const btn = mountToolbarButton(false)
    expect(toggleRotationSnap()).toBe(true)
    expect(btn.classList.contains('active')).toBe(true)
    expect(toggleRotationSnap()).toBe(false)
    expect(btn.classList.contains('active')).toBe(false)
  })
})
