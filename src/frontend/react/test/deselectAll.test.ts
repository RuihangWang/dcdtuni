import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { clearTransformTools } from '../src/interaction/deselectAll'
import * as store from '../src/projectStore'

function project(tools: { move: boolean; rotate: boolean }): CfdProject {
  const model = (uid: string) => ({
    uid,
    meta: { name: uid },
    geometry: { location: { x: 0, y: 0, z: 0 } },
    moveTool: { enabled: tools.move, axes: { x: true, y: true, z: true } },
    rotateTool: { enabled: tools.rotate, axes: { yaw: true, pitch: false, roll: false } },
  })
  return {
    uid: 'p_deselect',
    name: 'T',
    constructions: {
      racks: { r1: model('r1'), r2: model('r2') },
      floors: { f1: model('f1') },
    },
  } as unknown as CfdProject
}

function enabled(kind: 'moveTool' | 'rotateTool'): number {
  const p = store.getProject()!
  let n = 0
  for (const bucket of Object.values(p.constructions ?? {})) {
    for (const model of Object.values(bucket ?? {})) {
      if ((model as unknown as Record<string, { enabled?: boolean }>)[kind]?.enabled) n++
    }
  }
  return n
}

beforeEach(() => {
  store.loadProject(project({ move: true, rotate: false }), null)
})

describe('clearTransformTools', () => {
  it('turns off every model move tool, not only the selected ones', () => {
    // The flags outlive the selection: leaving them on puts the drag arrows
    // back the moment any one of those models is clicked again.
    expect(enabled('moveTool')).toBe(3)
    expect(clearTransformTools()).toBe(true)
    expect(enabled('moveTool')).toBe(0)
  })

  it('turns off the rotate rings too, as the legacy deselect does', () => {
    store.loadProject(project({ move: false, rotate: true }), null)
    clearTransformTools()
    expect(enabled('rotateTool')).toBe(0)
  })

  it('reports no change when nothing was on, so a click on empty space does not dirty the project', () => {
    store.loadProject(project({ move: false, rotate: false }), null)
    expect(clearTransformTools()).toBe(false)
    expect(store.isDirty()).toBe(false)
  })

  it('leaves the models themselves alone — this is a selection change, not an edit', () => {
    const before = store.undoDepth()
    clearTransformTools()
    const rack = store.findConstruction(store.getProject()!, 'r1')!.model
    expect((rack.geometry as { location: { x: number } }).location).toEqual({ x: 0, y: 0, z: 0 })
    // Live, with no undo entry: the legacy does not checkpoint a deselect.
    expect(store.undoDepth()).toBe(before)
  })
})
