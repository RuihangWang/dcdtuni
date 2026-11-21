/**
 * Which pipes are plugged into a CDU, split by loop side.
 *
 * Ports `_buildCduConnectedPipesHtml` (STL_generate.html:8457). The list is not
 * decoration: an unwired CDU looks exactly like a correct one in the viewport,
 * and the only place the wiring is visible is here.
 *
 * A pipe's ends are `cooling.connection.startPortId` / `endPortId`, and a CDU
 * port id is `<cdu uid>.supply` or `<cdu uid>.return` (js/liquid_mode.js:692).
 * So the match is a PREFIX test on `<uid>.` — testing for the uid alone would
 * make `m_1` match `m_10`'s ports.
 */

import { modelsIn, type CfdProject, type Construction } from '../schema/cfdproj'

type Bag = Record<string, unknown>

export interface CduPipeGroups {
  supply: Construction[]
  returns: Construction[]
  total: number
}

/** The port id on `cdu` that this pipe is attached by, or null. */
function attachedPort(pipe: Construction, cduUid: string): string | null {
  const connection = (((pipe as Bag).cooling as Bag | undefined)?.connection ?? {}) as Bag
  const prefix = `${cduUid}.`
  const start = connection.startPortId
  if (typeof start === 'string' && start.startsWith(prefix)) return start
  const end = connection.endPortId
  if (typeof end === 'string' && end.startsWith(prefix)) return end
  return null
}

export function cduConnectedPipes(
  project: CfdProject | null,
  cduUid: string,
): CduPipeGroups {
  const supply: Construction[] = []
  const returns: Construction[] = []
  if (project) {
    for (const pipe of modelsIn(project, 'pipeSegments')) {
      const port = attachedPort(pipe, cduUid)
      if (port === null) continue
      // Anything that is not the supply port counts as return, exactly as the
      // legacy splits them: a pipe on a port the editor cannot name is still
      // wired to the CDU and must not vanish from the list.
      if (port.includes('.supply')) supply.push(pipe)
      else returns.push(pipe)
    }
  }
  return { supply, returns, total: supply.length + returns.length }
}
