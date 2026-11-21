/**
 * The model library: pin a configured rack or ACU, then stamp copies of it.
 *
 * A room is not built one rack at a time. Twenty racks in a row are the SAME
 * rack — same servers, same flow, same blanking panels — and duplicating one
 * then re-editing it is how the twentieth ends up with a different boundary
 * condition from the first. A template is the fix: configure once, instantiate
 * many, and every instance carries `fromModel` so they can be told apart from
 * hand-made ones later.
 *
 * Templates live in `models.geometryModels.racks` / `.acus`, NOT in
 * `constructions` — they are not geometry in the room, they are a recipe, and
 * the .cfdproj schema already has that shelf (`js/cfdproj_schema.js:54`). The
 * exporter never walks it, so a pinned template adds no STL solid and no
 * OpenFOAM patch.
 *
 * ## Why this does not go through `createModelFromRaw`
 *
 * The legacy pair `pinModelToLibrary` (STL_generate.html:13946) and
 * `addInstanceFromTemplate` (:13965) still runs the V3 path: it hands the
 * template to `createModelFromRaw`, which reads `rawModel.kind`, `.name`,
 * `.anchor`, `.base`, `.faces` — every one of them a V3 field. Two things then
 * go wrong on a V4 project. `kind` is defined NON-enumerable
 * (`js/cfdproj_state_v4.js:211`) so `JSON.stringify` drops it and the raw comes
 * back as `kind: undefined`, which `createModelFromRaw` defaults to `'box'`;
 * and the V4 body lives under `meta`/`geometry`, which that function does not
 * read at all. The legacy therefore instantiates a DEFAULT 0.6x0.4x1.8 BOX
 * wearing the template's name — the same stale-reference class as the other V4
 * regressions in this port.
 *
 * So the extract/instantiate pair from the shared `js/model_library.js` is
 * still used — it is the definition of what a template is and what it drops —
 * and the V3 fields it stamps on (`id`, `name`, `anchor`) are translated to
 * their V4 homes here instead of being fed to a V4-blind constructor.
 */

import '../../../shared/js/model_library.js'

import type { CfdProject, Construction } from '../schema/cfdproj'
import { reviveKinds } from '../legacy/cfdprojModules'
import { nextModelUid } from '../legacy/factories'
import * as store from '../projectStore'

/** The two kinds the legacy toolbar and inspector offer. Nothing else is pinnable. */
export type LibraryKind = 'rack' | 'acu'

const BUCKET_OF: Record<LibraryKind, 'racks' | 'acus'> = { rack: 'racks', acu: 'acus' }

export interface LibraryTemplate {
  /** The `t_xxxxxxx` key it is stored under. */
  id: string
  kind: LibraryKind
  label: string
}

type Bag = Record<string, unknown>

interface ModelLibraryModule {
  extractTemplateFromModel(model: unknown, label: string): Bag
  instantiateTemplate(template: Bag, templateId: string, nextId: () => string): Bag
}

function library(): ModelLibraryModule {
  const mod = window as unknown as Partial<ModelLibraryModule>
  if (!mod.extractTemplateFromModel || !mod.instantiateTemplate) {
    throw new Error(
      'window.extractTemplateFromModel is missing — js/model_library.js did not load. It attaches to window on import.',
    )
  }
  return mod as ModelLibraryModule
}

/** The `geometryModels` shelf, created on demand. */
function shelf(project: CfdProject, kind: LibraryKind): Record<string, Bag> {
  const holder = project as unknown as Bag
  const models = (holder.models ?? (holder.models = {})) as Bag
  const geometryModels = (models.geometryModels ?? (models.geometryModels = {})) as Bag
  const bucket = BUCKET_OF[kind]
  const items = geometryModels[bucket]
  // A freshly initialised document writes `[]` for the empty shelves
  // (`case/*/*.cfdproj` all carry arrays), so an array has to be treated as
  // empty rather than indexed into — pushing a `t_` key onto an array would
  // save a template nothing can read back.
  if (!items || typeof items !== 'object' || Array.isArray(items)) {
    const fresh: Record<string, Bag> = {}
    geometryModels[bucket] = fresh
    return fresh
  }
  return items as Record<string, Bag>
}

/** Read the shelf without creating it — for rendering. */
function readShelf(project: CfdProject | null, kind: LibraryKind): Record<string, Bag> {
  const geometryModels = ((project as unknown as Bag | null)?.models as Bag | undefined)
    ?.geometryModels as Bag | undefined
  const items = geometryModels?.[BUCKET_OF[kind]]
  if (!items || typeof items !== 'object' || Array.isArray(items)) return {}
  return items as Record<string, Bag>
}

/** Every pinned template, racks before ACUs — the order the legacy toolbar lists. */
export function libraryTemplates(project: CfdProject | null): LibraryTemplate[] {
  const out: LibraryTemplate[] = []
  for (const kind of ['rack', 'acu'] as LibraryKind[]) {
    for (const [id, template] of Object.entries(readShelf(project, kind))) {
      out.push({ id, kind, label: String(template.label ?? kind) })
    }
  }
  return out
}

/**
 * A fresh template id.
 *
 * Random rather than sequential, matching the legacy: an index would be reused
 * after an unpin, and the next template would inherit the buttons — and the
 * `fromModel` links — of the one that was removed.
 */
function nextTemplateId(): string {
  return `t_${Math.random().toString(36).slice(2, 9)}`
}

/**
 * The name for a new instance.
 *
 * The legacy hands every instance the template's label verbatim, which puts two
 * models with the same name in one project. That is not cosmetic here: the STL
 * solid name is derived from the model name and IS the OpenFOAM patch name, so
 * two `Rack_A`s export solids that collide and the second one's boundary
 * condition lands on the first one's surface.
 */
export function instanceName(project: CfdProject, label: string): string {
  const taken = new Set<string>()
  for (const items of Object.values(project.constructions ?? {})) {
    if (!items) continue
    for (const model of Object.values(items)) if (model?.meta?.name) taken.add(model.meta.name)
  }
  if (!taken.has(label)) return label
  let n = 2
  while (taken.has(`${label}_${n}`)) n += 1
  return `${label}_${n}`
}

/**
 * Turn a stored template into a construction, in place on a draft.
 *
 * Exported for the tests: this is the whole V3->V4 translation, and it is the
 * part the legacy gets wrong.
 */
export function buildInstance(
  draft: CfdProject,
  template: Bag,
  templateId: string,
  kind: LibraryKind,
): Construction {
  const raw = library().instantiateTemplate(template, templateId, () => nextModelUid(draft))
  const label = String(template.label ?? kind)

  // `id`, `name` and `anchor` are where `instantiateTemplate` writes the V3
  // identity. Move each to its V4 home and drop the V3 key, or the saved file
  // carries a second, stale copy of the name that nothing reads.
  const uid = String(raw.id ?? nextModelUid(draft))
  delete raw.id
  delete raw.name
  delete raw.anchor
  delete raw.enabled

  const model = raw as unknown as Construction
  model.uid = uid
  model.meta = { ...(model.meta ?? { name: label }), name: instanceName(draft, label), enabled: true }
  model.fromModel = templateId
  // The instance stands at the room origin, which is what the shared module's
  // `anchor = {0,0,0}` means in V4 terms. Landing it on top of the model it was
  // pinned from would look like nothing happened.
  const geometry = model.geometry ?? (model.geometry = {})
  geometry.location = { x: 0, y: 0, z: 0 }
  // `extractTemplateFromModel` strips both tool blocks — they are per-instance
  // UI state, not part of the recipe — so a model built from a template would
  // otherwise be the only one in the project without them.
  const bag = model as unknown as Bag
  bag.moveTool ??= { enabled: false, axes: { x: true, y: true, z: true } }
  bag.rotateTool ??= { enabled: false, axes: { yaw: true, pitch: false, roll: false } }
  return model
}

/**
 * Save a rack or ACU as a reusable template. Returns the template id.
 *
 * The label is taken, not prompted for: the caller owns the prompt so that
 * cancelling it costs no undo step, which is the reason the legacy prompts
 * BEFORE its checkpoint (STL_generate.html:13949).
 */
export function pinModelToLibrary(uid: string, label: string): string | null {
  const project = store.getProject()
  if (!project) return null
  const hit = store.findConstruction(project, uid)
  const kind = hit?.model.kind
  if (kind !== 'rack' && kind !== 'acu') return null
  const trimmed = label.trim()
  if (!trimmed) return null

  const templateId = nextTemplateId()
  store.edit('pinModelToLibrary', `${kind} template: ${trimmed}`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    if (!model) return
    shelf(draft, kind)[templateId] = library().extractTemplateFromModel(model, trimmed)
  })
  return templateId
}

/** Forget a template. The instances already placed are untouched. */
export function unpinTemplate(kind: LibraryKind, templateId: string) {
  store.edit('unpinTemplate', `${kind}/${templateId}`, (draft) => {
    delete shelf(draft, kind)[templateId]
  })
}

/**
 * Place a new instance of a template. Returns the new model's uid.
 *
 * `reviveKinds` runs afterwards because the template came out of JSON and JSON
 * cannot carry the non-enumerable `kind` discriminator. Without it the new
 * model has no `kind`, and every consumer that switches on it — the geometry
 * builder, `syncRackModelState`, the STL namer — silently treats a rack as
 * whatever its bucket happens to be called.
 */
export function addInstanceFromTemplate(kind: LibraryKind, templateId: string): string | null {
  const project = store.getProject()
  if (!project) return null
  if (!readShelf(project, kind)[templateId]) return null

  let created: string | null = null
  store.edit('addFromLibrary', `${kind} from ${templateId}`, (draft) => {
    const template = shelf(draft, kind)[templateId]
    if (!template) return
    const model = buildInstance(draft, template, templateId, kind)
    const constructions = draft.constructions ?? (draft.constructions = {})
    const bucket = BUCKET_OF[kind]
    const items = constructions[bucket] ?? (constructions[bucket] = {})
    items[model.uid] = model
    reviveKinds(draft)
    created = model.uid
  })
  return created
}

// A dev-only handle, the same idea as `window.__dcdtuni` in main.tsx. The pin
// button lives in the inspector and the add buttons in the toolbar, so checking
// the round trip any other way means driving two panels blind.
if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as { __modelLibrary: unknown }).__modelLibrary = {
    libraryTemplates,
    pinModelToLibrary,
    addInstanceFromTemplate,
    unpinTemplate,
  }
}
