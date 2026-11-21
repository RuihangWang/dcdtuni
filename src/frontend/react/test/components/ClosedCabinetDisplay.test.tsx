import {createRoot} from 'react-dom/client'
import {flushSync} from 'react-dom'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {Toolbar,DEFAULT_VIEW_OPTIONS} from '../../src/components/Toolbar'
import type {CfdProject} from '../../src/schema/cfdproj'
import {setLiquidModeActive} from '../../src/liquid/liquidModeState'
import {setShowCabinetPipes,showCabinetPipes,registerCabinetDisplayRefresh,setCabinetDisplayDiagnostics} from '../../src/liquid/closedCabinetDisplay'
import * as store from '../../src/projectStore'
vi.mock('../../src/components/ViewCube',()=>({ViewCube:()=>null}))
let host:HTMLDivElement
let root:ReturnType<typeof createRoot>
beforeEach(()=>{registerCabinetDisplayRefresh(null);setShowCabinetPipes(false);setCabinetDisplayDiagnostics([]);host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);setLiquidModeActive(true)})
afterEach(()=>{flushSync(()=>root.unmount());host.remove();setLiquidModeActive(false)})
it('offers a real display-only inspect toggle in View tools, closed by default',()=>{
 const project={constructions:{}} as CfdProject
 const before=JSON.stringify(project)
 flushSync(()=>root.render(<Toolbar project={project} view={DEFAULT_VIEW_OPTIONS} onChange={()=>{}}
   hasSelection={false}/>))
 const input=host.querySelector<HTMLInputElement>('#show-cabinet-pipes')
 expect(input,'visible checkbox in the actual toolbar').not.toBeNull()
 expect(input?.closest('#toolbar-view-tools')).not.toBeNull()
 expect(input?.type).toBe('checkbox')
 expect(input?.checked).toBe(false)
 expect(JSON.stringify(project)).toBe(before)
})

it('updates the controlled checkbox and no project-edit API when inspected',()=>{
 const edit=vi.spyOn(store,'notifyLiveEdit'),checkpoint=vi.spyOn(store,'pushUndoCheckpoint')
 flushSync(()=>root.render(<Toolbar project={{constructions:{}} as CfdProject} view={DEFAULT_VIEW_OPTIONS}
  onChange={()=>{}} hasSelection={false}/>))
 const input=host.querySelector<HTMLInputElement>('#show-cabinet-pipes')!
 flushSync(()=>input.click())
 expect(input.checked).toBe(true);expect(showCabinetPipes()).toBe(true)
 expect(edit).not.toHaveBeenCalled();expect(checkpoint).not.toHaveBeenCalled()
 edit.mockRestore();checkpoint.mockRestore()
})
it('reacts to the liquid mode and shows a visible fail-open warning',()=>{
 flushSync(()=>root.render(<Toolbar project={{constructions:{}} as CfdProject} view={DEFAULT_VIEW_OPTIONS}
  onChange={()=>{}} hasSelection={false}/>))
 flushSync(()=>setCabinetDisplayDiagnostics(['pipe: clip-budget']))
 expect(host.querySelector('[role=note]')?.textContent).toMatch(/完整路线|fully visible/)
 flushSync(()=>setLiquidModeActive(false))
 expect(host.querySelector('#show-cabinet-pipes')).toBeNull()
})
