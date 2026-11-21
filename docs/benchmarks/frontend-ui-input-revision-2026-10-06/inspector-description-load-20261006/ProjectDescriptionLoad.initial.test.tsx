import { useSyncExternalStore } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import * as store from '../../src/projectStore'
import { Inspector } from '../../src/components/Inspector'

let host: HTMLDivElement
let root: Root

function documentWith(description: string): CfdProject {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  // Normal exported copies can reuse their original project UID.
  project.uid = 'canonical-reused-project'
  project.meta = { ...project.meta, description }
  return project
}

function CurrentInspector() {
  const project = useSyncExternalStore(store.subscribe, store.getProject)
  return <Inspector project={project} selectedUid={null} />
}

function load(description: string) {
  flushSync(() => store.loadProject(documentWith(description), null))
}

function textarea(): HTMLTextAreaElement {
  const value = host.querySelector<HTMLTextAreaElement>('#project-description')
  expect(value).not.toBeNull()
  return value!
}

function type(value: string) {
  const input = textarea()
  flushSync(() => {
    input.focus()
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(() => {
  store.loadProject(documentWith(''), null)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  flushSync(() => root.render(<CurrentInspector />))
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('project description belongs to the loaded document', () => {
  it('clears a previous committed description on loading an empty copy with the same UID', () => {
    const previous = 'previous-proof-payload '.repeat(100)
    load(previous)
    expect(textarea().value).toBe(previous)
    const uid = store.getProject()!.uid
    load('')
    expect(store.getProject()!.uid).toBe(uid)
    expect(textarea().value).toBe('')
    flushSync(() => { textarea().focus(); textarea().blur() })
    expect(store.getProject()!.meta?.description).toBe('')
    expect(store.isDirty()).toBe(false)
    expect(store.undoDepth()).toBe(0)
  })

  it('drops an uncommitted old-document draft even when the next UID and committed text are identical', () => {
    type('not part of the next document')
    expect(textarea().value).toBe('not part of the next document')
    expect(store.getProject()!.meta?.description).toBe('')
    load('')
    expect(textarea().value).toBe('')
    expect(store.isDirty()).toBe(false)
    expect(store.undoDepth()).toBe(0)
  })

  it('keeps normal blur editing and restores the visible committed text on undo', () => {
    load('committed description')
    type('edited description')
    const depth = store.undoDepth()
    flushSync(() => textarea().blur())
    expect(store.getProject()!.meta?.description).toBe('edited description')
    expect(store.undoDepth()).toBe(depth + 1)
    expect(textarea().value).toBe('edited description')
    flushSync(() => store.undo())
    expect(store.getProject()!.meta?.description).toBe('committed description')
    expect(textarea().value).toBe('committed description')
  })

  it('preserves a draft during unrelated same-document edits until blur commits it', () => {
    type('draft kept until blur')
    flushSync(() => store.edit('unrelated name', '', (project) => {
      project.meta = { ...project.meta, projectName: 'new name' }
    }))
    expect(textarea().value).toBe('draft kept until blur')
    expect(store.getProject()!.meta?.description).toBe('')
    flushSync(() => textarea().blur())
    expect(store.getProject()!.meta?.description).toBe('draft kept until blur')
  })
})
