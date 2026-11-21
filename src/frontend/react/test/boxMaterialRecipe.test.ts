import { describe, it, expect } from 'vitest'
import { DomainProject } from '../src/domain'
import type { CfdProject } from '../src/schema/cfdproj'
import original from './fixtures/boxMaterialRecipeBaseline.json'

const actual = original.cases.filter(sample => sample.actual && sample.label.endsWith('-library-present-unloaded'))
function entityFor(sample: typeof actual[number]) {
  const raw = { schemaVersion: 5, uid: 'material-domain-missing-method',
    constructions: { [sample.sample.bucket]: { [sample.sample.uid]: structuredClone(sample.sample.model) } } } as unknown as CfdProject
  return new DomainProject(raw).topLevel().find(entity => entity.uid === sample.sample.uid)!
}
describe('original material source records and controlled Domain missing capability', () => {
  it('uses the three original no-kind records with populated producer snapshot output', () => {
    expect(actual).toHaveLength(3)
    for (const sample of actual) {
      expect(sample.sample.synthetic).toBe(false)
      expect(sample.sample.model).not.toHaveProperty('kind')
      expect(sample.creation.materials.length).toBeGreaterThan(0)
      expect(sample.creation.snapshot.primitives.length).toBeGreaterThan(0)
      expect(entityFor(sample).kind).toBe(sample.sample.bucket === 'cdus' ? 'cdu' : 'box')
    }
  })
  for (const sample of actual) {
    it(sample.label + ' has a Domain materialRecipe method', () => {
      const entity = entityFor(sample)
      const before = JSON.stringify(entity.record)
      const method = (entity as unknown as { materialRecipe?: unknown }).materialRecipe
      expect(typeof method).toBe('function')
      expect(JSON.stringify(entity.record)).toBe(before)
    })
  }
})
