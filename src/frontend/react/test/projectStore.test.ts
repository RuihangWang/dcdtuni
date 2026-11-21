import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'

function project(): CfdProject {
  return {
    name: 'T',
    constructions: {
      racks: {
        r1: { uid: 'r1', meta: { name: 'Rack_1', enabled: true }, geometry: { location: { x: 0, y: 0, z: 0 }, size: { x: 0.6, y: 2, z: 1 } } },
        r2: { uid: 'r2', meta: { name: 'Rack_2', enabled: true } },
      },
    },
  }
}

beforeEach(() => store.loadProject(project(), '/tmp/T.cfdproj'))

describe('edit / undo / redo', () => {
  it('checkpoints before mutating, so undo restores the previous value', () => {
    store.renameModel('r1', 'Renamed')
    expect(store.getProject()?.constructions?.racks?.r1?.meta.name).toBe('Renamed')
    store.undo()
    expect(store.getProject()?.constructions?.racks?.r1?.meta.name).toBe('Rack_1')
  })

  it('does not let a mutation touch the snapshot it was checkpointed from', () => {
    // The whole reason edit() hands out a deep copy: if the draft shared
    // structure with the snapshot, undo would restore the edited object.
    const before = store.getProject()
    store.setEnabled('r1', false)
    expect(before?.constructions?.racks?.r1?.meta.enabled).toBe(true)
  })

  it('redoes what was undone', () => {
    store.removeModel('r2')
    expect(store.getProject()?.constructions?.racks?.r2).toBeUndefined()
    store.undo()
    expect(store.getProject()?.constructions?.racks?.r2).toBeDefined()
    store.redo()
    expect(store.getProject()?.constructions?.racks?.r2).toBeUndefined()
  })

  it('drops the redo stack once a new edit lands', () => {
    // Otherwise redo would replay a branch the user has already diverged from.
    store.renameModel('r1', 'A')
    store.undo()
    expect(store.canRedo()).toBe(true)
    store.renameModel('r1', 'B')
    expect(store.canRedo()).toBe(false)
  })

  it('clears history on load, because there is nothing to undo to', () => {
    store.renameModel('r1', 'A')
    expect(store.canUndo()).toBe(true)
    store.loadProject(project(), '/tmp/T.cfdproj')
    expect(store.canUndo()).toBe(false)
  })

  it('ignores a rename to blank rather than making a model unnameable', () => {
    store.renameModel('r1', '   ')
    expect(store.getProject()?.constructions?.racks?.r1?.meta.name).toBe('Rack_1')
    expect(store.canUndo()).toBe(false)
  })
})

describe('duplicateModel', () => {
  it('gives the copy a fresh uid and a non-colliding name', () => {
    const uid = store.duplicateModel('r1')
    expect(uid).toBeTruthy()
    const copy = store.getProject()?.constructions?.racks?.[uid!]
    expect(copy?.uid).toBe(uid)
    expect(copy?.uid).not.toBe('r1')
    expect(copy?.meta.name).toBe('Rack_1_copy')
  })

  it('offsets the copy so it is not hidden inside the original', () => {
    const uid = store.duplicateModel('r1')
    const copy = store.getProject()?.constructions?.racks?.[uid!]
    expect(copy?.geometry?.location?.x).toBeGreaterThan(0)
  })

  it('renumbers the servers and patches of the copy, so the export can still join on them', () => {
    // The export refused such a project: "duplicate server uid ... in racks".
    store.loadProject(
      {
        name: 'T',
        constructions: {
          racks: {
            r1: {
              uid: 'r1',
              meta: { name: 'Rack_1', enabled: true },
              geometry: {
                location: { x: 0, y: 0, z: 0 },
                size: { x: 0.6, y: 2, z: 1 },
                faces: [{ patches: [{ id: 'p_4' }] }] as never,
              },
              constructions: {
                servers: {
                  e_7: {
                    uid: 'e_7',
                    id: 'e_7',
                    geometry: { slotPosition: 0, slotOccupation: 2 },
                    faces: { inlet: { patches: [{ id: 'p_9' }] } },
                  },
                  srv_a: { uid: 'srv_a', geometry: { slotPosition: 4, slotOccupation: 2 } },
                },
              },
            },
          },
          acus: {
            a1: {
              uid: 'a1',
              meta: { name: 'ACU_1', enabled: true },
              geometry: { faces: { front: { patches: [{ id: 'p_2' }] } } as never },
            },
          },
        },
      },
      '/tmp/T.cfdproj',
    )
    const copyUid = store.duplicateModel('r1')!
    const racks = store.getProject()!.constructions!.racks!
    const copy = racks[copyUid]!
    const servers = (copy.constructions as { servers: Record<string, { uid: string; id?: string }> }).servers
    expect(Object.keys(servers)).toEqual(['e_8', 'e_9'])
    expect(servers.e_8).toMatchObject({ uid: 'e_8', id: 'e_8' })
    expect(JSON.stringify(copy)).not.toMatch(/"p_(2|4|9)"/)
    const acuUid = store.duplicateModel('a1')!
    const acu = store.getProject()!.constructions!.acus![acuUid]!
    expect(JSON.stringify(acu.geometry)).not.toContain('"p_2"')
    // The original is untouched.
    expect(Object.keys((racks.r1!.constructions as { servers: object }).servers)).toEqual(['e_7', 'srv_a'])
  })

  it('keeps counting up when the _copy name is already taken', () => {
    const first = store.duplicateModel('r1')
    expect(store.getProject()?.constructions?.racks?.[first!]?.meta.name).toBe('Rack_1_copy')
    const second = store.duplicateModel('r1')
    expect(store.getProject()?.constructions?.racks?.[second!]?.meta.name).toBe('Rack_1_copy2')
  })
})

describe('batch model operations', () => {
  it('duplicates several models as ONE undo entry', () => {
    // Eight duplicates behind eight undo presses is the failure this guards.
    const before = store.undoDepth()
    const created = store.duplicateModels(['r1', 'r2'])
    expect(created).toHaveLength(2)
    expect(store.undoDepth()).toBe(before + 1)
    store.undo()
    expect(store.findConstruction(store.getProject()!, created[0]!)).toBeNull()
  })

  it('removes several models as ONE undo entry', () => {
    const before = store.undoDepth()
    store.removeModels(['r1', 'r2'])
    expect(store.findConstruction(store.getProject()!, 'r1')).toBeNull()
    expect(store.findConstruction(store.getProject()!, 'r2')).toBeNull()
    expect(store.undoDepth()).toBe(before + 1)
    store.undo()
    expect(store.findConstruction(store.getProject()!, 'r1')).not.toBeNull()
  })

  it('ignores uids the project does not have', () => {
    expect(store.duplicateModels(['nope'])).toEqual([])
  })
})

describe('pushUndoCheckpoint and captured references', () => {
  it('detaches references taken before the checkpoint from the live project', () => {
    // The trap a drag gizmo falls into: it captures model objects at drag
    // start, mutates them live for sixty frames, then checkpoints and writes
    // the final value. The checkpoint CLONES, so that last write lands in the
    // undo snapshot and the live project keeps the drag's start — the model
    // snapped back the moment the mouse came up. The rotate gizmo re-resolves
    // by uid after the checkpoint for exactly this reason.
    const captured = store.getProject()!.constructions!.racks!.r1!
    store.pushUndoCheckpoint('drag')
    store.mutateLive(() => {
      captured.meta = { ...captured.meta, name: 'WrittenThroughStaleRef' }
    })
    expect(store.getProject()?.constructions?.racks?.r1?.meta.name).toBe('Rack_1')

    store.mutateLive((project) => {
      const model = store.findConstruction(project, 'r1')?.model
      if (model) model.meta = { ...model.meta, name: 'WrittenByUid' }
    })
    expect(store.getProject()?.constructions?.racks?.r1?.meta.name).toBe('WrittenByUid')
  })
})

describe('move and rotate tools are mutually exclusive', () => {
  it('drops every move tool when a rotate tool is enabled', () => {
    store.setMoveTool('r1', { enabled: true })
    store.setMoveTool('r2', { enabled: true })
    store.setRotateTool('r2', { enabled: true })
    const racks = store.getProject()!.constructions!.racks!
    // Every model, not just the one turned on: the legacy calls
    // disableAllModelMoveTools() so no arrow survives anywhere.
    expect((racks.r1 as { moveTool?: { enabled: boolean } }).moveTool?.enabled).toBe(false)
    expect((racks.r2 as { moveTool?: { enabled: boolean } }).moveTool?.enabled).toBe(false)
    expect((racks.r2 as { rotateTool?: { enabled: boolean } }).rotateTool?.enabled).toBe(true)
  })

  it('drops the rotate tool when the same model is put in move mode', () => {
    store.setRotateTool('r1', { enabled: true })
    store.setMoveTool('r1', { enabled: true })
    const r1 = store.getProject()!.constructions!.racks!.r1 as {
      moveTool?: { enabled: boolean }
      rotateTool?: { enabled: boolean }
    }
    expect(r1.moveTool?.enabled).toBe(true)
    expect(r1.rotateTool?.enabled).toBe(false)
  })

  it('keeps the axes it was not asked to change', () => {
    store.setRotateTool('r1', { enabled: true, axes: { pitch: true } })
    store.setRotateTool('r1', { axes: { roll: true } })
    const tool = (store.getProject()!.constructions!.racks!.r1 as {
      rotateTool?: { enabled: boolean; axes: { yaw: boolean; pitch: boolean; roll: boolean } }
    }).rotateTool
    expect(tool).toEqual({ enabled: true, axes: { yaw: true, pitch: true, roll: true } })
  })
})

describe('scene hint: the models a display-only edit changed', () => {
  it('names them for the document the edit produced, and only that one', () => {
    store.edit('opacity', 'r1', (d) => { d.constructions!.racks!.r1!.meta.name = 'x' }, { sceneUids: ['r1'] })
    const after = store.getProject()
    expect(store.sceneUidsFor(after)).toEqual(['r1'])
    // An edit that names nothing, an undo or a load is a whole-scene change.
    store.renameModel('r2', 'y')
    expect(store.sceneUidsFor(store.getProject())).toBeNull()
    expect(store.sceneUidsFor(after)).toBeNull()
    store.edit('opacity', 'r1', () => {}, { sceneUids: ['r1'] })
    store.undo()
    expect(store.sceneUidsFor(store.getProject())).toBeNull()
  })

  it('a checkpointed live edit names its models on the clone the checkpoint made', () => {
    const before = store.getProject()
    store.pushUndoCheckpoint('drag')
    const live = store.getProject()
    expect(live).not.toBe(before)
    store.notifyLiveEdit('updateModelOpacity', 'r1 40%', { sceneUids: ['r1'] })
    expect(store.sceneUidsFor(live)).toEqual(['r1'])
    store.notifyLiveEdit('liveEdit')
    expect(store.sceneUidsFor(live)).toBeNull()
  })
})
