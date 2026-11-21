import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import { importCustomStl } from '../src/io/stlAssets'

function project(): CfdProject {
  return { name: 'T', constructions: { customMeshes: {} } } as unknown as CfdProject
}

/** One axis-aligned triangle per named solid, so the regions are distinguishable. */
function stlText(): string {
  const facet = (v: number[][]) =>
    `  facet normal 0 0 1\n    outer loop\n${v
      .map(([x, y, z]) => `      vertex ${x} ${y} ${z}`)
      .join('\n')}\n    endloop\n  endfacet\n`
  return (
    `solid lid\n${facet([
      [1, 1, 3],
      [2, 1, 3],
      [1, 2, 3],
    ])}endsolid lid\n` +
    `solid base\n${facet([
      [1, 1, 1],
      [2, 1, 1],
      [1, 2, 1],
    ])}endsolid base\n`
  )
}

const file = (name = 'shell.stl') =>
  new File([stlText()], name, { type: 'application/sla' })

beforeEach(() => store.loadProject(project(), null))

describe('importCustomStl', () => {
  it('adds the file to the project as a custom model', async () => {
    const result = await importCustomStl(file())
    expect(result.ok).toBe(true)
    const model = store.findConstruction(store.getProject()!, result.uid!)!.model
    expect(model.kind).toBe('custom')
    expect(model.meta?.name).toBe('shell')
  })

  it('keeps every named solid, in file order', async () => {
    const result = await importCustomStl(file())
    const model = store.findConstruction(store.getProject()!, result.uid!)!.model
    const mesh = (model as unknown as Record<string, unknown>).customMesh as {
      solidRegions: string[]
      offset: Record<string, number>
      stlBase64: string
    }
    expect(mesh.solidRegions).toEqual(['lid', 'base'])
    expect(mesh.stlBase64.length).toBeGreaterThan(0)
  })

  it('offsets the mesh so its corner lands on the model origin', async () => {
    // The file is authored at x=1..2, y=1..2, z=1..3; without the negated
    // minimum the model would draw a metre away from its own anchor.
    const result = await importCustomStl(file())
    const model = store.findConstruction(store.getProject()!, result.uid!)!.model
    const mesh = (model as unknown as Record<string, unknown>).customMesh as {
      offset: Record<string, number>
    }
    expect(mesh.offset).toEqual({ x: -1, y: -1, z: -1 })
    expect(model.geometry?.size).toEqual({ x: 1, y: 2, z: 1 })
  })

  it('is one undo step', async () => {
    const result = await importCustomStl(file())
    store.undo()
    expect(store.findConstruction(store.getProject()!, result.uid!)).toBeNull()
  })

  it('reports a parse failure instead of adding an empty model', async () => {
    const before = Object.keys(store.getProject()!.constructions!.customMeshes ?? {}).length
    const result = await importCustomStl(new File(['not an stl at all'], 'bad.stl'))
    expect(result.ok).toBe(false)
    expect(Object.keys(store.getProject()!.constructions!.customMeshes ?? {})).toHaveLength(before)
  })
})
