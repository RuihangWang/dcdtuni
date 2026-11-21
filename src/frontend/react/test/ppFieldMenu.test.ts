import { describe, expect, it } from 'vitest'
import {
  buildFieldGroups,
  fieldValues,
  magnitudeName,
  metaFromNames,
  streamlineFieldGroups,
  type FieldMetaEntry,
} from '../src/postprocess/ppFieldMenu'

/**
 * The metadata below is what `/api/pp/mesh` returns for a solved
 * buoyantBoussinesqSimpleFoam case: the scalars it wrote plus `U` with the
 * component names the server itself derives (postprocess.py:1985).
 */
const RUN_FIELDS: FieldMetaEntry[] = [
  { name: 'T', type: 'scalar', label: 'T' },
  { name: 'p', type: 'scalar', label: 'p' },
  { name: 'p_rgh', type: 'scalar', label: 'p_rgh' },
  { name: 'U', type: 'vector', label: 'U', components: ['Ux', 'Uy', 'Uz'] },
]

describe('buildFieldGroups', () => {
  it('splits scalars and vectors into the two optgroups', () => {
    expect(buildFieldGroups(RUN_FIELDS).map((g) => g.kind)).toEqual(['scalar', 'vector'])
  })

  it('offers the magnitude and each component for a vector', () => {
    const vector = buildFieldGroups(RUN_FIELDS).find((g) => g.kind === 'vector')
    expect(vector?.options).toEqual([
      { value: 'Umag', label: 'U (Magnitude)' },
      { value: 'Ux', label: 'Ux' },
      { value: 'Uy', label: 'Uy' },
      { value: 'Uz', label: 'Uz' },
    ])
  })

  it('never offers the bare vector name', () => {
    // `/api/pp/field/U` answers with the magnitude, so an option called `U`
    // renders |U| under a name that promises the vector.
    expect(fieldValues(buildFieldGroups(RUN_FIELDS))).not.toContain('U')
  })

  it('lists every value in menu order', () => {
    expect(fieldValues(buildFieldGroups(RUN_FIELDS))).toEqual([
      'T',
      'p',
      'p_rgh',
      'Umag',
      'Ux',
      'Uy',
      'Uz',
    ])
  })

  it('appends a label only when it differs from the name', () => {
    const groups = buildFieldGroups([
      { name: 'T', type: 'scalar', label: 'T' },
      { name: 'alphat', type: 'scalar', label: 'Turbulent Diffusivity' },
    ])
    expect(groups[0]?.options).toEqual([
      { value: 'T', label: 'T' },
      { value: 'alphat', label: 'alphat (Turbulent Diffusivity)' },
    ])
  })

  it('drops an empty group instead of rendering a heading with nothing under it', () => {
    const groups = buildFieldGroups([{ name: 'T', type: 'scalar' }])
    expect(groups).toHaveLength(1)
    expect(groups[0]?.kind).toBe('scalar')
  })

  it('offers the magnitude alone when a vector reports no components', () => {
    const vector = buildFieldGroups([{ name: 'U', type: 'vector' }]).find((g) => g.kind === 'vector')
    expect(vector?.options).toEqual([{ value: 'Umag', label: 'U (Magnitude)' }])
  })

  it('expands a second vector under the same group', () => {
    const groups = buildFieldGroups([
      { name: 'U', type: 'vector', components: ['Ux', 'Uy', 'Uz'] },
      { name: 'gradT', type: 'vector', components: ['gradTx', 'gradTy', 'gradTz'] },
    ])
    expect(fieldValues(groups)).toEqual([
      'Umag',
      'Ux',
      'Uy',
      'Uz',
      'gradTmag',
      'gradTx',
      'gradTy',
      'gradTz',
    ])
  })

  it('is empty for an empty run rather than inventing fields', () => {
    expect(buildFieldGroups([])).toEqual([])
  })
})

describe('magnitudeName', () => {
  it('is the name the server resolves to <name>_magnitude', () => {
    expect(magnitudeName('U')).toBe('Umag')
  })
})

describe('streamlineFieldGroups', () => {
  it('puts speed ahead of the groups, outside them', () => {
    const menu = streamlineFieldGroups(buildFieldGroups(RUN_FIELDS))
    expect(menu.loose).toEqual([{ value: 'speed', label: 'Speed (|U|)' }])
    expect(menu.groups.map((g) => g.kind)).toEqual(['scalar', 'vector'])
  })
})

describe('metaFromNames', () => {
  it('treats bare names as scalars, so a pre-init list still renders', () => {
    expect(fieldValues(buildFieldGroups(metaFromNames(['T', 'U'])))).toEqual(['T', 'U'])
  })
})
