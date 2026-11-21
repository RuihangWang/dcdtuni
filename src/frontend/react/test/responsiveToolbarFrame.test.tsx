import {act} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {PanelResizers} from '../src/components/PanelResizers'

;(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true
let width=961,leftRequested=330,rightRequested=340,host:HTMLDivElement,root:Root,originalStyle:string|null
const panels:Record<string,HTMLDivElement>={}
const observers:MeasuredObserver[]=[]
class MeasuredObserver {
 observed=new Set<Element>()
 constructor(public callback:ResizeObserverCallback){observers.push(this)}
 observe(el:Element){this.observed.add(el)}
 disconnect(){this.observed.clear()}
}
function side(requested:number,edge:'left'|'right'){const css=document.documentElement.style;const ask=parseFloat(css.getPropertyValue(`--${edge}-panel-width`))||requested;const fit=Math.max(0,(width-260)/2);const share=css.getPropertyValue(`--${edge}-panel-share`)?fit:width*.23;return Math.min(Math.max(220,Math.min(ask,share)),fit)}
function notify(){act(()=>{for(const observer of observers)observer.callback([],observer as unknown as ResizeObserver)})}
beforeEach(()=>{
 width=961;leftRequested=330;rightRequested=340;observers.length=0;originalStyle=document.documentElement.getAttribute('style');document.documentElement.style.cssText='--panel-floor:220px;--min-viewport-width:260px'
 vi.stubGlobal('ResizeObserver',MeasuredObserver);vi.spyOn(window,'innerWidth','get').mockImplementation(()=>width);vi.spyOn(window,'innerHeight','get').mockReturnValue(719)
 for(const edge of ['left','right','toolbar','log']){
  const el=document.createElement('div');el.id=edge==='toolbar'?'toolbar':`${edge}-panel`;panels[edge]=el
  const drawn=()=>edge==='left'?side(leftRequested,'left'):edge==='right'?side(rightRequested,'right'):width-side(leftRequested,'left')-side(rightRequested,'right')-16
  Object.defineProperty(el,'offsetWidth',{get:drawn});Object.defineProperty(el,'getBoundingClientRect',{value:()=>({width:drawn(),height:120,bottom:128,top:8,left:0,right:drawn()})})
  if(edge==='toolbar'){Object.defineProperty(el,'scrollHeight',{get:()=>120});const body=document.createElement('div');body.id='toolbar-content';el.append(body)}
  document.body.append(el)
 }
 host=document.createElement('div');document.body.append(host);root=createRoot(host);act(()=>root.render(<PanelResizers/>))
})
afterEach(()=>{act(()=>root.unmount());host.remove();for(const p of Object.values(panels))p.remove();if(originalStyle===null)document.documentElement.removeAttribute('style');else document.documentElement.setAttribute('style',originalStyle);vi.restoreAllMocks();vi.unstubAllGlobals()})
it.each([900,960,1200,1440])('adapts to measured toolbar and panels at %spx without changing requested viewport widths',w=>{
 width=w;notify()
 const toolbarWidth=width-side(leftRequested,'left')-side(rightRequested,'right')-16
 expect(panels.toolbar!.dataset.layout).toBe(toolbarWidth>=720?'wide':'compact')
 expect(panels.left!.dataset.compact).toBe(String(side(leftRequested,'left')<280))
 expect(panels.right!.dataset.compact).toBe(String(side(rightRequested,'right')<280))
 expect(panels.toolbar!.style.transform).toBe('')
 expect(leftRequested).toBe(330);expect(rightRequested).toBe(340)
})
it('responds to a sidebar resize at a fixed wide window and restores the wide row when room returns',()=>{
 width=1440;notify();expect(panels.toolbar!.dataset.layout).toBe('wide')
 act(()=>{for(let i=0;i<5;i++)document.getElementById('left-resizer')!.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',shiftKey:true,bubbles:true}))});notify();expect(panels.toolbar!.dataset.layout).toBe('compact')
 act(()=>{for(let i=0;i<12;i++)document.getElementById('left-resizer')!.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',shiftKey:true,bubbles:true}))});notify();expect(panels.toolbar!.dataset.layout).toBe('wide');expect(panels.left!.dataset.compact).toBe('true')
})
