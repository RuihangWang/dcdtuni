// Actual mounted Toolbar/PanelResizers, controlled DOM size observations.
// jsdom has no layout engine: these are lifecycle/budget checks, not pixels.
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, expect, it } from 'vitest'
import { Toolbar, DEFAULT_VIEW_OPTIONS } from '../src/components/Toolbar'
import { PanelResizers, refitToolbar } from '../src/components/PanelResizers'
;(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true
class SizeObserver {
 static all: SizeObserver[]=[]
 targets=new Set<Element>();closed=false
 constructor(readonly callback:ResizeObserverCallback){SizeObserver.all.push(this)}
 observe(e:Element){this.targets.add(e)}
 unobserve(e:Element){this.targets.delete(e)}
 disconnect(){this.closed=true;this.targets.clear()}
 emit(){this.callback([],this as unknown as ResizeObserver)}
}
let host:HTMLDivElement
let root:ReturnType<typeof createRoot>
let width=700,headerHeight=80,footerHeight=120
let originalHeight:number
beforeEach(()=>{
 originalHeight=window.innerHeight;Object.defineProperty(window,'innerHeight',{configurable:true,value:1000})
 SizeObserver.all=[];globalThis.ResizeObserver=SizeObserver as unknown as typeof ResizeObserver
 width=700;headerHeight=80;footerHeight=120
 host=document.createElement('div');document.body.append(host);root=createRoot(host)
 act(()=>root.render(<Toolbar view={DEFAULT_VIEW_OPTIONS} onChange={()=>{}} hasSelection={false} project={null}/>))
})
afterEach(()=>{
 act(()=>root.unmount());host.remove()
 expect(SizeObserver.all.every(o=>o.closed)).toBe(true)
 Object.defineProperty(window,'innerHeight',{configurable:true,value:originalHeight})
})
function click(id:string){act(()=>document.getElementById(id)!.dispatchEvent(new MouseEvent('click',{bubbles:true})))}
function regionFixture(){
 click('toolbar-view-tools-toggle')
 const toolbar=document.getElementById('toolbar')!,content=document.getElementById('toolbar-content')!
 // The before budget consumer is also exercised with the proposed public DOM
 // regions supplied as controlled input, separately from its structural test.
 let header=document.getElementById('toolbar-header')
 if(!header){header=document.createElement('div');header.id='toolbar-header';content.prepend(header)}
 const body=document.getElementById('toolbar-body')!
 const footer=document.createElement('div');footer.id='timeline-strip';content.append(footer)
 Object.defineProperty(toolbar,'offsetWidth',{configurable:true,get:()=>width})
 Object.defineProperty(toolbar,'scrollHeight',{configurable:true,get:()=>500})
 toolbar.getBoundingClientRect=()=>({width,height:parseFloat(toolbar.style.height)||500,bottom:parseFloat(toolbar.style.height)||500}) as DOMRect
 Object.defineProperty(header,'scrollHeight',{configurable:true,get:()=>headerHeight})
 Object.defineProperty(header,'offsetWidth',{configurable:true,get:()=>width})
 Object.defineProperty(body,'scrollHeight',{configurable:true,get:()=>300})
 Object.defineProperty(footer,'scrollHeight',{configurable:true,get:()=>footerHeight})
 const slot=document.createElement('div');host.append(slot)
 const resizers=createRoot(slot);act(()=>resizers.render(<PanelResizers/>))
 return {toolbar,content,header,body,footer,resizers}
}
function close(resizers:ReturnType<typeof createRoot>){act(()=>resizers.unmount())}
it('scrolls only expanded tools while preserving both primary and section controls',()=>{
 const body=document.getElementById('toolbar-body')!
 const primary=document.querySelector('.toolbar-primary')!,tabs=document.querySelector('.toolbar-group-tabs')!
 expect(body.contains(primary),'primary controls must not belong to tools scroll region').toBe(false)
 expect(body.contains(tabs),'section navigation must not belong to tools scroll region').toBe(false)
 const nodes=Array.from(tabs.children)
 for(const group of ['model-tools','view-tools','preferences']){
  click('toolbar-'+group+'-toggle');body.scrollTop=800
  expect(body.contains(document.getElementById('toolbar-'+group))).toBe(true)
  expect(Array.from(tabs.children)).toEqual(nodes)
 }
 expect(document.getElementById('toolbar-header')!.tabIndex).toBe(0)
 expect(body.tabIndex).toBe(0)
})
it('reserves the measured header AND timeline on deliberate keyboard shrinking',()=>{
 const f=regionFixture()
 try{
  const handle=document.getElementById('toolbar-resizer')!
  act(()=>{for(let i=0;i<4;i++)handle.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',shiftKey:true,bubbles:true}))})
  expect(parseFloat(f.toolbar.style.height)).toBe(232)
  expect(handle.getAttribute('aria-valuemin')).toBe('232')
  expect(parseFloat(f.toolbar.style.getPropertyValue('--toolbar-header-available-height'))).toBe(80)
  expect(parseFloat(f.toolbar.style.getPropertyValue('--timeline-available-height'))).toBe(120)
 }finally{close(f.resizers)}
})
it('remeasures a same-width header rewrap through its actual ResizeObserver',()=>{
 const f=regionFixture()
 try{
  const observe=SizeObserver.all.find(o=>o.targets.has(f.header))
  expect(observe).toBeDefined()
  headerHeight=140;act(()=>observe!.emit())
  expect(document.getElementById('toolbar-resizer')!.getAttribute('aria-valuemin')).toBe('292')
  expect(parseFloat(f.toolbar.style.getPropertyValue('--toolbar-header-available-height'))).toBeLessThanOrEqual(140)
 }finally{close(f.resizers)}
})
it('keeps manual preference across header expansion and narrower/wider layouts',()=>{
 const f=regionFixture()
 try{
  const handle=document.getElementById('toolbar-resizer')!
  act(()=>{for(let i=0;i<4;i++)handle.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',shiftKey:true,bubbles:true}))})
  const observe=SizeObserver.all.find(o=>o.targets.has(f.header))!
  headerHeight=120;act(()=>observe.emit())
  expect(parseFloat(f.toolbar.style.height)).toBe(272)
  width=800;headerHeight=80;act(()=>observe.emit())
  expect(f.toolbar.dataset.layout).toBe('wide')
  expect(parseFloat(f.toolbar.style.height)).toBe(232)
  width=600;act(()=>observe.emit())
  expect(f.toolbar.dataset.layout).toBe('compact')
  expect(parseFloat(f.toolbar.style.height)).toBe(232)
 }finally{close(f.resizers)}
})
it('remeasures timeline visibility without moving the fixed controls into scrolling tools',async()=>{
 const f=regionFixture()
 try{
  await act(async()=>{f.footer.hidden=true;await Promise.resolve()})
  expect(document.getElementById('toolbar-resizer')!.getAttribute('aria-valuemin')).toBe('112')
  await act(async()=>{f.footer.hidden=false;footerHeight=160;await Promise.resolve()})
  expect(document.getElementById('toolbar-resizer')!.getAttribute('aria-valuemin')).toBe('272')
  click('toolbar-collapse-btn');expect(f.toolbar.classList.contains('collapsed')).toBe(true)
  click('toolbar-collapse-btn');expect(f.toolbar.classList.contains('collapsed')).toBe(false)
  expect(f.content.contains(f.header)).toBe(true)
 }finally{close(f.resizers)}
})
it('keeps positive bounded keyboard regions at an extremely short viewport height',()=>{
 Object.defineProperty(window,'innerHeight',{configurable:true,value:120})
 const f=regionFixture()
 try{
  act(()=>refitToolbar())
  const header=parseFloat(f.toolbar.style.getPropertyValue('--toolbar-header-available-height'))
  const body=parseFloat(f.toolbar.style.getPropertyValue('--toolbar-body-floor'))
  const footer=parseFloat(f.toolbar.style.getPropertyValue('--timeline-available-height'))
  expect(header).toBeGreaterThan(0);expect(body).toBeGreaterThan(0);expect(footer).toBeGreaterThan(0)
  expect(header+body+footer).toBeCloseTo(parseFloat(f.toolbar.style.height))
  expect(f.toolbar.dataset.headerScrollable).toBe('true');expect(f.toolbar.dataset.footerScrollable).toBe('true')
 }finally{close(f.resizers)}
})

it('removes the empty tools budget when all sections close and restores it on reopening',async()=>{
 const f=regionFixture()
 try{
  expect(f.body.hidden).toBe(false)
  await act(async()=>{click('toolbar-view-tools-toggle');await Promise.resolve()})
  expect(f.body.hidden).toBe(true)
  expect(document.getElementById('toolbar-resizer')!.getAttribute('aria-valuemin')).toBe('200')
  expect(parseFloat(f.toolbar.style.getPropertyValue('--toolbar-body-floor'))).toBe(0)
  await act(async()=>{click('toolbar-model-tools-toggle');await Promise.resolve()})
  expect(f.body.hidden).toBe(false)
  expect(document.getElementById('toolbar-resizer')!.getAttribute('aria-valuemin')).toBe('232')
  expect(parseFloat(f.toolbar.style.getPropertyValue('--toolbar-body-floor'))).toBe(32)
 }finally{close(f.resizers)}
})
