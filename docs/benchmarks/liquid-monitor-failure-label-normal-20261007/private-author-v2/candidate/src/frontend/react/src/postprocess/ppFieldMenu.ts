/**
 * The post-processing field menu, built from the run's own metadata.
 *
 * `/api/pp/mesh` reports each field as scalar or vector
 * (`_discover_fields`, postprocess.py:1958), and a VECTOR is not something the
 * surface, slice or volume renderer can draw: those colour one scalar per
 * point. `_ppPopulateFieldDropdown` (STL_generate.html:21244) therefore expands
 * every vector into the four scalars the server can derive from it — `Umag`,
 * `Ux`, `Uy`, `Uz` — and files them under a 向量场 optgroup.
 *
 * ## Why the derived names matter
 *
 * The server resolves them itself: `_resolve_field_name` (postprocess.py:2050)
 * turns `Umag` into `U_magnitude` and `Ux` into component 0 of `U`. Listing the
 * bare vector name instead is not merely a missing option — `/api/pp/field/U`
 * silently returns the MAGNITUDE (`_extract_scalars_from_mesh` norms anything
 * two-dimensional), so a user asking for velocity gets |U| labelled `U` and no
 * way to see a single component at all.
 *
 * ## Groups, not a flat list
 *
 * The two optgroups are what make a ten-entry list readable, and they are also
 * the only thing separating `T` from `Ux` — both are scalars by the time they
 * reach the renderer.
 */

import { SPEED_FIELD, SPEED_LABEL, type FieldOption } from './ppFieldOptions'

/** A field as `/api/pp/mesh` reports it. */
export interface FieldMetaEntry {
  name: string
  type?: string
  label?: string
  /** `['Ux','Uy','Uz']` for a vector; absent for a scalar. */
  components?: string[]
}

export interface FieldOptionGroup {
  /** Which optgroup this is; the panel translates it. */
  kind: 'scalar' | 'vector'
  options: FieldOption[]
}

/** The i18n key for a group's optgroup label, as the legacy labels them. */
export const GROUP_LABEL_KEY = {
  scalar: 'pp.scalar_fields',
  vector: 'pp.vector_fields',
} as const

/** The magnitude entry's value for a vector called `name` — `U` -> `Umag`. */
export function magnitudeName(name: string): string {
  return `${name}mag`
}

/**
 * Build the optgroups.
 *
 * An empty group is dropped rather than rendered empty, as in the legacy
 * (`if (scalarGroup.children.length)`, :21277): a case with no vector field
 * would otherwise show an empty 向量场 heading.
 */
export function buildFieldGroups(fields: readonly FieldMetaEntry[]): FieldOptionGroup[] {
  const scalar: FieldOption[] = []
  const vector: FieldOption[] = []

  for (const f of fields) {
    if (!f?.name) continue
    if (f.type === 'vector') {
      vector.push({ value: magnitudeName(f.name), label: `${f.name} (Magnitude)` })
      for (const component of f.components ?? []) {
        vector.push({ value: component, label: component })
      }
    } else {
      // The label is only appended when it says something the name does not,
      // which is how the legacy renders it — `T (T)` otherwise.
      const suffix = f.label && f.label !== f.name ? ` (${f.label})` : ''
      scalar.push({ value: f.name, label: `${f.name}${suffix}` })
    }
  }

  const groups: FieldOptionGroup[] = []
  if (scalar.length) groups.push({ kind: 'scalar', options: scalar })
  if (vector.length) groups.push({ kind: 'vector', options: vector })
  return groups
}

/** Every selectable value, in menu order. This is what a stored choice is checked against. */
export function fieldValues(groups: readonly FieldOptionGroup[]): string[] {
  return groups.flatMap((g) => g.options.map((o) => o.value))
}

/**
 * The streamline menu: `speed` first, then the same groups.
 *
 * The legacy prepends the option to the SAME markup
 * (`slFieldSel.innerHTML = '<option value="speed">…' + sel.innerHTML`, :21292),
 * so the synthetic entry sits outside both optgroups and is the default.
 */
export function streamlineFieldGroups(groups: readonly FieldOptionGroup[]): {
  loose: FieldOption[]
  groups: FieldOptionGroup[]
} {
  return {
    loose: [{ value: SPEED_FIELD, label: SPEED_LABEL }],
    groups: [...groups],
  }
}

/**
 * Treat a bare name list as scalars.
 *
 * The panel has a field list before it has metadata — the run's names are known
 * from an earlier init, the types are not — and showing those names flat is
 * better than showing nothing. Never used once `/api/pp/mesh` has answered.
 */
export function metaFromNames(names: readonly string[]): FieldMetaEntry[] {
  return names.map((name) => ({ name, type: 'scalar' }))
}
