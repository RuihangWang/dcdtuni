import {expect,it,vi} from 'vitest'
import {stateV4} from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import type {CfdProject} from '../src/schema/cfdproj'
it('uses actual secure random values on LAN without randomUUID',()=>{const original=globalThis.crypto;const getRandomValues=original.getRandomValues.bind(original);vi.stubGlobal('crypto',{getRandomValues});try{const a=stateV4().newDocumentV4(),b=stateV4().newDocumentV4();expect(a.uid).toMatch(/^proj_[0-9a-f-]{36}$/);expect(a.uid).not.toBe(b.uid)}finally{vi.unstubAllGlobals()}})
it('refuses missing secure randomness rather than minting a weak identity',()=>{for(const crypto of [undefined,{}]){vi.stubGlobal('crypto',crypto);try{expect(()=>stateV4().newDocumentV4()).toThrow('Secure random source unavailable for project identity')}finally{vi.unstubAllGlobals()}}})
it('preserves an existing identity without RNG and rejects malformed UID types',()=>{vi.stubGlobal('crypto',undefined);try{const doc={uid:'proj_saved',schemaVersion:5,constructions:{}} as CfdProject;store.loadProject(doc,null);expect(store.getProject()!.uid).toBe('proj_saved');for(const uid of [null,0,7,false,{},[]])expect(()=>store.loadProject({uid,schemaVersion:5} as unknown as CfdProject,null)).toThrow('Project uid must be a string');expect(store.getProject()!.uid).toBe('proj_saved')}finally{vi.unstubAllGlobals()}})
