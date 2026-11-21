/** liquidCooling.cpCuPairs is the one list of a server's cold plates.
 *
 * The v3→v4 migration also wrote `constructions.coldPlates` (keyed by plate
 * uid) and nothing has written it since; the editor, the parameter registry,
 * the timeline and the legacy HTML editor all write the pair array. So the
 * compiled graph must take its MEMBERSHIP from the pairs, and a pair's identity
 * from a `uid` — taken from the plate it was migrated into, so every compiled
 * edge id is what it was before. The compiler derives it for any document; the
 * inspector writes it into the project at the first pair edit, never on open.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { compileSavedExperiment } from '../src/liquid/liquidExperiment'
import { backfillPairUids } from '../src/liquid/cpCuPairIdentity'
import {
  addCpCuPair,
  addPair,
  readRawPairs,
  removeCpCuPair,
  updateCpCuPair,
  type RawPair,
} from '../src/liquid/liquidColdPlates'
import * as store from '../src/projectStore'

type Bag = Record<string, any>

function liquidCase(dir = 'Liquid', file = dir): CfdProject {
  return JSON.parse(readFileSync(resolve(process.cwd(), `../../../case/${dir}/${file}.cfdproj`), 'utf8'))
}

function coldPlateEdges(): string[] {
  const run = compileSavedExperiment(store.getProject()!).liquidrun as unknown as {edges: {id: string; type: string}[]}
  return run.edges.filter(e => e.type === 'cold_plate').map(e => e.id)
}

function liveServer(rack: string, server: string): Bag {
  return (store.getProject()!.constructions!.racks as Bag)[rack].constructions.servers[server]
}

describe('a migrated project (non-empty constructions.coldPlates)', () => {
  it('drops the compiled cold-plate edge of a pair deleted in the editor', () => {
    store.loadProject(liquidCase(), null)
    const before = coldPlateEdges()
    expect(before).toContain('m_1_e_1_cp_CP-CU_1')
    removeCpCuPair('m_1', 'e_1', 0)
    expect(coldPlateEdges()).toEqual(before.filter(id => id !== 'm_1_e_1_cp_CP-CU_1'))
  })

  it('compiles a pair added in the editor into a new cold-plate edge', () => {
    store.loadProject(liquidCase(), null)
    const before = coldPlateEdges()
    addCpCuPair('m_1', 'e_1')
    const after = coldPlateEdges()
    expect(after.filter(id => !before.includes(id))).toEqual(['m_1_e_1_cp_CP-CU_5'])
    expect(after).toHaveLength(before.length + 1)
  })

  it('opens unchanged, and stamps a server\'s pairs with their plate uids at its first pair edit', () => {
    // Opening must not change the project's content hash: air results and mesh
    // previews are bound to it (renderer/windResultProducer.ts).
    store.loadProject(liquidCase(), null)
    expect(readRawPairs(liveServer('m_1', 'e_1')).map(p => p.uid)).toEqual([undefined, undefined, undefined, undefined])
    updateCpCuPair('m_1', 'e_1', 1, 'Q_W', 400)
    expect(readRawPairs(liveServer('m_1', 'e_1')).map(p => p.uid))
      .toEqual(['CP-CU_1', 'CP-CU_2', 'CP-CU_3', 'CP-CU_4'])
    expect(readRawPairs(liveServer('m_3', 'e_2')).map(p => p.uid)).toEqual([undefined, undefined, undefined, undefined])
  })

  it('writes no uid with a keyframe edit off t = 0, where static fields are frozen', () => {
    store.loadProject(liquidCase('Shinaide_Liquid', 'Shinaide_liquid'), null)
    store.edit('setTransient', '', d => { (d as Bag).transient.mode = 'transient'; (d as Bag).transient.durationS = 600 })
    store.setPlayhead(120)
    updateCpCuPair('m_1', 'e_1', 0, 'Q_W', 180)
    expect(store.lastEditRejection()).toBeNull()
    expect(readRawPairs(liveServer('m_1', 'e_1'))[0]).toMatchObject({Q_W: 180})
    expect(readRawPairs(liveServer('m_1', 'e_1'))[0]?.uid).toBeUndefined()
  })

  it('keeps a plate edge when its pair is renamed', () => {
    store.loadProject(liquidCase(), null)
    const before = coldPlateEdges()
    updateCpCuPair('m_1', 'e_1', 0, 'name', 'GPU-0')
    expect(coldPlateEdges()).toEqual(before)
  })
})

describe('a server created in the editor (empty constructions.coldPlates)', () => {
  function editorCreated(): CfdProject {
    const p = liquidCase() as Bag
    p.constructions.racks.m_1.constructions.servers.e_1.constructions.coldPlates = {}
    return p as CfdProject
  }

  it('keeps the other plates\' edges when an earlier pair is deleted', () => {
    store.loadProject(editorCreated(), null)
    const before = coldPlateEdges().filter(id => id.startsWith('m_1_e_1_'))
    expect(before).toHaveLength(4)
    removeCpCuPair('m_1', 'e_1', 0)
    expect(coldPlateEdges().filter(id => id.startsWith('m_1_e_1_'))).toEqual(before.slice(1))
  })
})

describe('pair uids', () => {
  it('a new pair gets a uid no other pair on the server uses', () => {
    expect(addPair([])[0]?.uid).toBe('CP-CU_1')
    // Pair 1 was deleted: the positional name CP-CU_2 is still in use.
    expect(addPair([{uid: 'CP-CU_2', name: 'CP-CU_2'}])[1]?.uid).toBe('CP-CU_3')
  })

  it('backfills a distinct uid for each pair, matching a plate at most once', () => {
    // The legacy editor deleted CP-CU_1 and then added a pair, which it names by
    // position: two pairs called CP-CU_2, and CP-CU_1 left behind in coldPlates.
    const plate = (uid: string) => ({uid, name: uid, Q_W: 350})
    const server: Bag = {
      liquidCooling: {cpCuPairs: [{name: 'CP-CU_2', Q_W: 350}, {name: 'CP-CU_2', Q_W: 700}] as RawPair[]},
      constructions: {coldPlates: {'CP-CU_1': plate('CP-CU_1'), 'CP-CU_2': plate('CP-CU_2')}},
    }
    backfillPairUids({constructions: {servers: {e_1: server}}})
    const uids = readRawPairs(server).map(p => p.uid)
    expect(uids[0]).toBe('CP-CU_2')
    expect(uids[1]).not.toBe('CP-CU_2')
    expect(new Set(uids).size).toBe(2)
    expect(readRawPairs(server).map(p => p.Q_W)).toEqual([350, 700])
  })

  it('leaves a pair that already has a uid alone', () => {
    const server: Bag = {
      liquidCooling: {cpCuPairs: [{uid: 'nv_heat_01', name: 'MOCK equivalent 01'}]},
      constructions: {coldPlates: {nv_heat_01: {uid: 'nv_heat_01', name: 'MOCK equivalent 01'}}},
    }
    backfillPairUids({constructions: {servers: {e_1: server}}})
    expect(readRawPairs(server)).toEqual([{uid: 'nv_heat_01', name: 'MOCK equivalent 01'}])
  })
})
