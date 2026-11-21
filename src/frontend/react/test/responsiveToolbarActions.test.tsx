import {act} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {afterEach,beforeEach,expect,it} from 'vitest'
import {Toolbar,DEFAULT_VIEW_OPTIONS} from '../src/components/Toolbar'
import {RendererControl} from '../src/renderer/RendererControl'
import {setLocale,t} from '../src/i18n'
import type {CfdProject} from '../src/schema/cfdproj'
;(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true
const project={uid:'layout-contract',constructions:{}} as CfdProject
let host:HTMLDivElement,root:Root
beforeEach(()=>{host=document.createElement('div');document.body.append(host);root=createRoot(host)})
afterEach(()=>{act(()=>root.unmount());host.remove()})
it.each(['zh-CN','en-US'] as const)('keeps renderer select/export/OV connection as shared visible actions in %s',async locale=>{
 act(()=>setLocale(locale));const before=JSON.stringify(project)
 await act(async()=>{root.render(<><Toolbar view={DEFAULT_VIEW_OPTIONS} onChange={()=>{}} hasSelection={false} project={project}/><RendererControl project={project} selectedUids={[]} onSelection={()=>{}} activeTab="geometry" airMode={true}/></>);await Promise.resolve()})
 const slot=host.querySelector('#renderer-switch-slot')!
 expect(slot.querySelector('select')?.getAttribute('aria-label')).toBe(t('renderer.label'))
 const button=slot.querySelector<HTMLButtonElement>(`button[aria-label="${t('renderer.export')}"]`)!
 expect(button.textContent).toBe(t('export.confirm'));expect(button.getAttribute('title')).toBe(t('renderer.export'))
 const connection=slot.querySelector<HTMLButtonElement>('.renderer-settings-btn')!
 expect(connection.textContent).toBe(t('renderer.settings'));expect(connection.getAttribute('aria-label')).toBe(t('renderer.connection'))
 expect(slot.querySelector('.ov-status')?.textContent).toBeTruthy()
 const tabs=[...host.querySelectorAll<HTMLButtonElement>('.toolbar-group-toggle')]
 expect(tabs).toHaveLength(3)
 for(const tab of tabs){act(()=>tab.click());const panel=host.querySelector<HTMLElement>('#'+tab.getAttribute('aria-controls'))!;expect(panel.hidden).toBe(false);expect(tab.title).toBe(tab.querySelector('span')!.textContent)}
 const collapse=host.querySelector<HTMLButtonElement>('#toolbar-collapse-btn')!
 act(()=>collapse.click());expect(host.querySelector('#toolbar')!.classList.contains('collapsed')).toBe(true)
 expect(slot.isConnected).toBe(true);expect(slot.querySelector('select')!.value).toBe('three');expect(JSON.stringify(project)).toBe(before)
})
