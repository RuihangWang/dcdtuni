/** Research entry: saved .cfdproj V4 -> the same strict DAE V3 exporter used by React. */
import { stateV4, schema, projectExperimentErrors,
  projectHybridHeatBudgetErrors, projectGeometryTopologyErrors } from '../src/frontend/react/src/legacy/cfdprojModules'
import { cfdrunExport } from '../src/frontend/react/src/legacy/cfdrunBuild'
import { compileSavedExperiment } from '../src/frontend/react/src/liquid/liquidExperiment'
import { assertLiquidrunDAEV3, type LiquidrunDAEV3 } from '../src/frontend/react/src/schema/liquidrunDAE'
import type { CfdProject } from '../src/frontend/react/src/schema/cfdproj'

/** Requires an explicit DAE experiment already persisted in the source file.
 * No surrogate graph, mock adoption or runtime defaults are introduced here.
 */
export function compileProjectDocument(raw: unknown): LiquidrunDAEV3 {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) ||
      ![4, 5].includes(Number((raw as { schemaVersion?: unknown }).schemaVersion))) {
    throw new Error('Research DAE export requires a saved .cfdproj V4 or v5 document')
  }
  if (!(raw as { constructions?: unknown }).constructions ||
      typeof (raw as { constructions?: unknown }).constructions !== 'object') {
    throw new Error('Source .cfdproj has no V4 constructions')
  }
  const before = JSON.stringify(raw)
  const project = stateV4().normalizeStateV4(raw) as unknown as CfdProject
  const errors = [
    ...schema().validateDocument(project),
    ...projectExperimentErrors(project),
    ...projectHybridHeatBudgetErrors(project),
    ...projectGeometryTopologyErrors(project),
  ]
  if (errors.length) throw new Error(`Invalid source .cfdproj: ${errors.map(e => `${e.path}: ${e.msg}`).join('; ')}`)
  const saved = (project as unknown as {runDefaults?:{liquid?:{experiment?:{dae?:unknown}}}})
    .runDefaults?.liquid?.experiment
  if (!saved?.dae) throw new Error('Source .cfdproj has no saved DAE V3 experiment; explicit adoption is required')
  const result = compileSavedExperiment(project).liquidrun
  if (result.schemaVersion !== 3) throw new Error('Frontend compiler returned a legacy liquidrun instead of strict DAE V3')
  assertLiquidrunDAEV3(result)
  if (JSON.stringify(raw) !== before) throw new Error('Frontend compiler mutated the source .cfdproj')
  const contentHash = cfdrunExport().projectContentHashSync(project)
  if (result.source_project.sha256 !== contentHash) {
    throw new Error('Compiled liquidrun does not bind the normalized source project content hash')
  }
  return result
}
