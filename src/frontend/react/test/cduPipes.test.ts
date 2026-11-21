import { describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { cduConnectedPipes } from '../src/liquid/cduPipes'

function pipe(uid: string, name: string, start: string | null, end: string | null) {
  return {
    uid,
    meta: { name, enabled: true },
    geometry: {},
    cooling: { connection: { startPortId: start, endPortId: end } },
  }
}

function project(): CfdProject {
  return {
    name: 'T',
    constructions: {
      cdus: { m_1: { uid: 'm_1', meta: { name: 'CDU_1' } } },
      pipeSegments: {
        p1: pipe('p1', 'Pipe_1', 'm_1.supply', 'm_5.inlet'),
        p2: pipe('p2', 'Pipe_2', 'm_5.outlet', 'm_1.return'),
        p3: pipe('p3', 'Pipe_3', null, null),
        // A different CDU whose uid has `m_1` as a prefix — the reason the
        // match is on `<uid>.` and not on the bare uid.
        p4: pipe('p4', 'Pipe_4', 'm_10.supply', null),
      },
    },
  } as unknown as CfdProject
}

describe('cduConnectedPipes', () => {
  it('splits the attached pipes into supply and return', () => {
    const groups = cduConnectedPipes(project(), 'm_1')
    expect(groups.supply.map((p) => p.uid)).toEqual(['p1'])
    expect(groups.returns.map((p) => p.uid)).toEqual(['p2'])
    expect(groups.total).toBe(2)
  })

  it('does not match a CDU whose uid is a prefix of another', () => {
    // `m_1` must not claim `m_10.supply`, or a two-CDU loop reads as wired to
    // the wrong source.
    expect(cduConnectedPipes(project(), 'm_10').supply.map((p) => p.uid)).toEqual(['p4'])
    expect(cduConnectedPipes(project(), 'm_1').supply.map((p) => p.uid)).not.toContain('p4')
  })

  it('matches on either end of the pipe', () => {
    const p = project()
    p.constructions!.pipeSegments!.p2!.cooling = {
      connection: { startPortId: 'm_1.return', endPortId: null },
    }
    expect(cduConnectedPipes(p, 'm_1').returns.map((x) => x.uid)).toEqual(['p2'])
  })

  it('is empty for an unwired CDU and for no project at all', () => {
    expect(cduConnectedPipes(project(), 'm_99').total).toBe(0)
    expect(cduConnectedPipes(null, 'm_1').total).toBe(0)
  })
})
