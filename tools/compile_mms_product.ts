import { buildMmsProductProject } from './mms_product_project'
import { compileSavedExperiment } from '../src/frontend/react/src/liquid/liquidExperiment'
import { stateV4, schema } from '../src/frontend/react/src/legacy/cfdprojModules'
import type { CfdProject } from '../src/frontend/react/src/schema/cfdproj'

export { buildMmsProductProject, MMS_TARGETS } from './mms_product_project'
export function compileProjectDocument(raw: unknown) {
  const project = stateV4().normalizeStateV4(raw) as unknown as CfdProject
  const api = schema() as unknown as {validateDocument(p:unknown):{path:string;msg:string}[]}
  const errors=api.validateDocument(project)
  if(errors.length)throw new Error(errors.map(e=>`${e.path}: ${e.msg}`).join('; '))
  return compileSavedExperiment(project).liquidrun
}
