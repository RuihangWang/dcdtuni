import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import {
  checkProjectDocument,
  clearCurrentRemoteProjectFile,
  ensureProjectFileName,
  getDefaultRemoteSavePath,
  getPathBasename,
  getPathDirname,
  getProjectDialogStartPath,
  getSuggestedProjectFileName,
  joinFsPath,
  normalizeRemoteSaveTarget,
  projectForSave,
  readProjectFileText,
  rememberRemoteProjectPath,
  serializeProjectState,
} from '../src/io/projectFiles'

/** A minimal but REAL V4 document: the marker the validation looks for is `constructions`. */
function project(over: Partial<CfdProject> = {}): CfdProject {
  return {
    schemaVersion: 4,
    uid: 'proj_T',
    name: 'T',
    meta: { projectName: 'T', exportFileName: 'default' },
    geometry: { height: 3, plane: [], tileSize: 0.6 },
    constructions: { racks: {} },
    ...over,
  } as CfdProject
}

beforeEach(() => localStorage.clear())

describe('path arithmetic', () => {
  it('splits a POSIX path', () => {
    expect(getPathBasename('/a/b/c.cfdproj')).toBe('c.cfdproj')
    expect(getPathDirname('/a/b/c.cfdproj')).toBe('/a/b')
  })

  it('gives a root-level file "/" for a parent, not ""', () => {
    // '' would send the dialog to the server's home directory instead of up one
    // level, so the up button would jump somewhere unrelated.
    expect(getPathDirname('/c.cfdproj')).toBe('/')
  })

  it('ignores a trailing separator', () => {
    expect(getPathBasename('/a/b/')).toBe('b')
    expect(getPathDirname('/a/b/')).toBe('/a')
  })

  it('handles a Windows path the server may have reported', () => {
    expect(getPathBasename('C:\\cases\\x.cfdproj')).toBe('x.cfdproj')
    expect(joinFsPath('C:\\cases', 'x.cfdproj')).toBe('C:\\cases\\x.cfdproj')
  })

  it('joins without doubling separators', () => {
    expect(joinFsPath('/a/b/', '/c.cfdproj')).toBe('/a/b/c.cfdproj')
    expect(joinFsPath('', 'c.cfdproj')).toBe('c.cfdproj')
    expect(joinFsPath('/a/b', '')).toBe('/a/b')
  })
})

describe('ensureProjectFileName', () => {
  it('adds the extension only when it is missing', () => {
    expect(ensureProjectFileName('run')).toBe('run.cfdproj')
    expect(ensureProjectFileName('run.cfdproj')).toBe('run.cfdproj')
    expect(ensureProjectFileName('run.CFDPROJ')).toBe('run.CFDPROJ')
  })

  it('leaves an empty name empty, so a caller can fall back', () => {
    expect(ensureProjectFileName('   ')).toBe('')
  })
})

describe('suggested names and default paths', () => {
  it('offers the file the project was opened from, not its display name', () => {
    rememberRemoteProjectPath('/cases/Base_leakage/Base_leakage.cfdproj')
    expect(getSuggestedProjectFileName(project())).toBe('Base_leakage.cfdproj')
  })

  it('falls back to the project name, sanitised', () => {
    clearCurrentRemoteProjectFile()
    expect(getSuggestedProjectFileName(project({ meta: { projectName: 'Case 07' } }))).toBe(
      'Case_07.cfdproj',
    )
  })

  it('opens the dialog in the remembered directory before the case dir', () => {
    rememberRemoteProjectPath('/cases/A/A.cfdproj')
    expect(getProjectDialogStartPath(project(), '/srv/case')).toBe('/cases/A')
  })

  it('falls back to the case dir once the remembered file is cleared', () => {
    rememberRemoteProjectPath('/cases/A/A.cfdproj')
    clearCurrentRemoteProjectFile()
    // The directory is still remembered separately, which is the point: the
    // next save opens where the last one went.
    expect(getProjectDialogStartPath(project(), '/srv/case')).toBe('/cases/A')
  })

  it('defaults a save to the open file, so Save is Save and not Save As', () => {
    rememberRemoteProjectPath('/cases/A/A.cfdproj')
    expect(getDefaultRemoteSavePath(project())).toBe('/cases/A/A.cfdproj')
  })

  it('defaults to the browsed directory plus a suggested name when nothing is open', () => {
    expect(getDefaultRemoteSavePath(project(), '/srv/case')).toBe('/srv/case/T.cfdproj')
  })
})

describe('normalizeRemoteSaveTarget', () => {
  it('treats a trailing slash as "into this directory"', () => {
    // /api/write-file refuses a directory target outright, so without this the
    // save fails instead of writing the suggested filename.
    expect(normalizeRemoteSaveTarget('/cases/A/', project())).toBe('/cases/A/T.cfdproj')
  })

  it('adds the extension to a typed stem', () => {
    expect(normalizeRemoteSaveTarget('/cases/A/variant', project())).toBe('/cases/A/variant.cfdproj')
  })

  it('leaves an empty entry empty so the caller can complain', () => {
    expect(normalizeRemoteSaveTarget('   ', project())).toBe('')
  })
})

describe('serialisation', () => {
  it('writes only the canonical keys (V4 order plus v5 transient)', () => {
    const doc = project() as unknown as Record<string, unknown>
    doc.__runtimeOnly = { threeJsHandle: 1 }
    expect(Object.keys(projectForSave(doc as unknown as CfdProject))).toEqual([
      'schemaVersion',
      'uid',
      'name',
      'meta',
      'geometry',
      'constructions',
      'transient',
    ])
  })

  it('is two-space JSON, the bytes the legacy editor writes', () => {
    expect(serializeProjectState(project()).split('\n')[1]).toBe('  "schemaVersion": 5,')
  })
})

describe('checkProjectDocument', () => {
  // The migrator is the reason this check has to be strict: it fills in every
  // default, so it turns ANY object into a valid empty room.
  const migrate = (raw: unknown) => raw

  it('accepts a V4 document', () => {
    const result = checkProjectDocument(project(), migrate)
    expect(result.ok).toBe(true)
  })

  it('accepts a pre-V4 document, which is what the migrator is for', () => {
    const v3 = { schemaVersion: 3, models: [], cfdRoom: { height: 3 } }
    expect(checkProjectDocument(v3, migrate).ok).toBe(true)
  })

  it('rejects arbitrary JSON that the migrator would happily turn into an empty room', () => {
    const result = checkProjectDocument({ name: 'dcdtuni-editor', version: '0.1.0' }, migrate)
    expect(result.ok).toBe(false)
    expect(result.ok === false && result.error).toMatch(/no constructions or models/)
  })

  it('rejects a non-object', () => {
    expect(checkProjectDocument([1, 2], migrate).ok).toBe(false)
    expect(checkProjectDocument('x', migrate).ok).toBe(false)
    expect(checkProjectDocument(null, migrate).ok).toBe(false)
  })

  it('refuses a schemaVersion from the future rather than half-reading it', () => {
    const result = checkProjectDocument(project({ schemaVersion: 6 }), migrate)
    expect(result.ok).toBe(false)
    expect(result.ok === false && result.error).toBe('Unsupported schemaVersion 6')
  })

  it('really does run the shared migrator by default — a v3 file comes back as v5', () => {
    const v3 = { schemaVersion: 3, models: [], cfdRoom: { height: 3.2 } }
    const result = checkProjectDocument(v3)
    expect(result.ok).toBe(true)
    expect(result.ok && result.project.schemaVersion).toBe(5)
    expect(result.ok && typeof result.project.constructions).toBe('object')
  })
})

describe('readProjectFileText', () => {
  it('reports a parse error as a message rather than throwing', () => {
    const result = readProjectFileText('{ not json')
    expect(result.ok).toBe(false)
    expect(result.ok === false && result.error.length).toBeGreaterThan(0)
  })

  it('accepts the text of a real .cfdproj', () => {
    expect(readProjectFileText(serializeProjectState(project())).ok).toBe(true)
  })

  it('rejects an unrelated .json the user picked by mistake', () => {
    expect(readProjectFileText('{"compilerOptions":{"strict":true}}').ok).toBe(false)
  })
})
