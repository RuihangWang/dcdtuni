import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import {
  probeCasePath,
  readCasePath,
  readSavedCasePath,
  setRepoRoot,
  syncCasePathFromServer,
  validateCasePathAfterLoad,
  writeCasePath,
} from '../src/run/caseDir'

/**
 * `meta.casePath` was write-only in the port: read as a dialog start path,
 * never written and never re-applied. That is how a project gets meshed into
 * the case directory the PREVIOUS user set — this server holds exactly one.
 */

function project(casePath?: string): CfdProject {
  return {
    schemaVersion: 4,
    name: 'T',
    meta: casePath === undefined ? {} : { casePath },
    constructions: { racks: {} },
  } as CfdProject
}

const realFetch = globalThis.fetch

beforeEach(() => store.loadProject(project('/cases/T'), '/cases/T.cfdproj'))
afterEach(() => {
  globalThis.fetch = realFetch
  vi.restoreAllMocks()
})

describe('writeCasePath', () => {
  it('stores the path and leaves an undoable checkpoint', () => {
    writeCasePath('/cases/Other')
    expect(readCasePath()).toBe('/cases/Other')
    store.undo()
    expect(readCasePath()).toBe('/cases/T')
  })

  it('skips a write of the path already stored', () => {
    // Re-applying a project's own saved path on open would otherwise leave an
    // undo entry and a dirty flag for an edit that changed nothing.
    const depth = store.undoDepth()
    writeCasePath('/cases/T')
    expect(store.undoDepth()).toBe(depth)
    expect(store.isDirty()).toBe(false)
  })
})

describe('syncCasePathFromServer', () => {
  it('adopts the server’s resolved directory without a checkpoint', () => {
    const depth = store.undoDepth()
    syncCasePathFromServer('/srv/case/Base_leakage', true)
    expect(readCasePath()).toBe('/srv/case/Base_leakage')
    expect(store.undoDepth()).toBe(depth)
    expect(store.isDirty()).toBe(false)
  })

  it('does not overwrite a saved path with a different server directory', () => {
    syncCasePathFromServer('/previous/project')
    expect(readCasePath()).toBe('/cases/T')
  })

  it('ignores an empty answer rather than clearing the project’s path', () => {
    syncCasePathFromServer(undefined)
    syncCasePathFromServer('')
    expect(readCasePath()).toBe('/cases/T')
  })
})

describe('probeCasePath', () => {
  it('reads /api/browse-dir’s error field, which is how the server says "missing"', async () => {
    globalThis.fetch = vi.fn(async (url) => {
      expect(String(url)).toContain(encodeURIComponent('/cases/T'))
      return { status: 400, ok: false, json: async () => ({ error: 'Path does not exist: /cases/T' }) } as Response
    }) as unknown as typeof fetch
    expect(await probeCasePath('/cases/T')).toBe(false)
  })

  it.each([401, 403, 500])('does not call a %s failure a missing directory', async (status) => {
    globalThis.fetch = vi.fn(async () => ({ status, ok: false, json: async () => ({ error: 'unavailable' }) }) as Response) as unknown as typeof fetch
    await expect(probeCasePath('/cases/T')).rejects.toThrow()
    expect(readCasePath()).toBe('/cases/T')
  })
})

describe('validateCasePathAfterLoad', () => {
  it('re-applies a path that exists on this server', async () => {
    const applied: string[] = []
    await validateCasePathAfterLoad(async (dir, options) => {
      applied.push(dir)
      expect(options).toMatchObject({ automatic: true, createIfMissing: false })
      return true
    })
    expect(applied).toEqual(['/cases/T'])
  })

  it('retains the saved path if applying it fails without a confirmed missing response', async () => {
    await validateCasePathAfterLoad(async () => false)
    expect(readCasePath()).toBe('/cases/T')
    await validateCasePathAfterLoad(async () => { throw new Error('offline') })
    expect(readCasePath()).toBe('/cases/T')
  })

  it('does nothing at all for a project that carries no path', async () => {
    store.loadProject(project(), null)
    const fetchSpy = vi.fn()
    globalThis.fetch = fetchSpy as unknown as typeof fetch
    await validateCasePathAfterLoad(async () => true)
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})

describe('repository-relative case paths', () => {
  beforeEach(() => setRepoRoot('/clone'))
  afterEach(() => setRepoRoot(null))

  it('reads a relative saved path as a directory below the repository root', () => {
    store.loadProject(project('case/T'), '/clone/case/T/T.cfdproj')
    expect(readCasePath()).toBe('/clone/case/T')
    expect(readSavedCasePath()).toBe('case/T')
  })

  it('stores a directory inside the repository in its relative form', () => {
    writeCasePath('/clone/case/Other')
    expect(readSavedCasePath()).toBe('case/Other')
    expect(readCasePath()).toBe('/clone/case/Other')
  })

  it('stores a directory outside the repository as given', () => {
    writeCasePath('/scratch/case')
    expect(readSavedCasePath()).toBe('/scratch/case')
  })

  it('does not rewrite a relative path the server confirms', () => {
    store.loadProject(project('case/T'), null)
    syncCasePathFromServer('/clone/case/T', true)
    expect(readSavedCasePath()).toBe('case/T')
    expect(store.isDirty()).toBe(false)
  })

  it('leaves a relative path relative while the root is unknown', () => {
    setRepoRoot(null)
    store.loadProject(project('case/T'), null)
    expect(readCasePath()).toBe('case/T')
  })
})
