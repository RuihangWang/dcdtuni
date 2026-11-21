import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {expect,it} from 'vitest'
import * as store from '../src/projectStore'
import {readProjectFileText,serializeProjectState} from '../src/io/projectFiles'
import {cfdrunExport} from '../src/legacy/cfdrunBuild'
import {updateCpCuPair} from '../src/liquid/liquidColdPlates'
import {transientOf} from '../src/transient/model'
import type {CfdProject} from '../src/schema/cfdproj'
const CASE=resolve(process.cwd(),'../../../case/Shinaide_Liquid/Shinaide_liquid.cfdproj')
const raw=()=>JSON.parse(readFileSync(CASE,'utf8')) as CfdProject
const hash=()=>cfdrunExport().projectContentHashSync(store.getProject())
const server=()=> (store.getProject() as any).constructions.racks.m_1.constructions.servers.e_1
it('keeps newly backfilled UID through actual nonzero thermal playhead/edit/undo/redo/save/reopen, while source hash tracks definitions',()=>{
 const disk=raw();disk.uid='';store.loadProject(disk,null)
 const uid=store.getProject()!.uid;expect(uid).toMatch(/^proj_[0-9a-f-]{36}$/);expect(disk.uid).toBe('')
 expect(store.edit('setTransient','',d=>{(d as any).transient.mode='transient';(d as any).transient.durationS=600})).toBe(true)
 const initialHeat=server().liquidCooling.cpCuPairs[0].Q_W
 store.setPlayhead(120);expect(store.playheadTime()).toBe(120)
 expect(store.edit('setPower','e_1',d=>{(d as any).constructions.racks.m_1.constructions.servers.e_1.cooling.power=1500})).toBe(true)
 const beforeCp=hash();updateCpCuPair('m_1','e_1',0,'Q_W',initialHeat+90)
 expect(store.lastEditRejection()).toBeNull();expect(server().cooling.power).toBe(1500);expect(server().liquidCooling.cpCuPairs[0].Q_W).toBe(initialHeat+90)
 expect(transientOf(store.getProject()).tracks.find(t=>t.param==='cp.heat'&&t.target==='e_1/0')!.keyframes.at(-1)).toMatchObject({t:120,value:initialHeat+90})
 const trackedHash=hash(),savedAt120=serializeProjectState(store.getProject()!);expect(trackedHash).not.toBe(beforeCp)
 store.setPlayhead(0);expect(store.playheadTime()).toBe(0);expect(server().cooling.power).toBe(1000);expect(server().liquidCooling.cpCuPairs[0].Q_W).toBe(initialHeat)
 expect(store.getProject()!.uid).toBe(uid);expect(hash()).toBe(trackedHash);expect(serializeProjectState(store.getProject()!)).toBe(savedAt120)
 store.setPlayhead(300);expect(server().cooling.power).toBe(1500);expect(server().liquidCooling.cpCuPairs[0].Q_W).toBe(initialHeat+90);expect(hash()).toBe(trackedHash)
 store.undo();expect(server().liquidCooling.cpCuPairs[0].Q_W).toBe(initialHeat);expect(hash()).toBe(beforeCp);expect(store.getProject()!.uid).toBe(uid)
 store.redo();expect(server().liquidCooling.cpCuPairs[0].Q_W).toBe(initialHeat+90);expect(hash()).toBe(trackedHash);expect(store.getProject()!.uid).toBe(uid)
 store.markSaved('/controlled/thermal-identity.cfdproj');expect(hash()).toBe(trackedHash);expect(serializeProjectState(store.getProject()!)).toBe(savedAt120)
 const parsed=readProjectFileText(savedAt120);expect(parsed.ok).toBe(true);if(!parsed.ok)throw new Error(parsed.error)
 store.loadProject(parsed.project,'/controlled/thermal-identity.cfdproj');expect(store.playheadTime()).toBe(0);expect(store.getProject()!.uid).toBe(uid);expect(hash()).toBe(trackedHash)
 store.setPlayhead(120);expect(server().cooling.power).toBe(1500);expect(server().liquidCooling.cpCuPairs[0].Q_W).toBe(initialHeat+90);expect(hash()).toBe(trackedHash);expect(store.getProject()!.uid).toBe(uid)
})
it('preserves legacy v3 UID migration and existing whitespace policy while refusing current-format nonstrings',()=>{
 for(const uid of ['',undefined,null,false,0]){store.loadProject({schemaVersion:3,uid,meta:{projectName:'Legacy LAN'},construction:[]} as unknown as CfdProject,null);expect(store.getProject()!.uid).toBe('proj_Legacy_LAN')}
 const doc=raw();doc.uid=' \t ';store.loadProject(doc,null);expect(store.getProject()!.uid).toBe(' \t ')
 for(const uid of [null,false,0,7,{},[]]){expect(()=>store.loadProject({schemaVersion:5,uid} as unknown as CfdProject,null)).toThrow('Project uid must be a string');expect(store.getProject()!.uid).toBe(' \t ')}
})
