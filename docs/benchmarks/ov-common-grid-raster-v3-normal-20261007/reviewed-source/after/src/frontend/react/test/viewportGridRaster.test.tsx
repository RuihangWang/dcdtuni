/** Real React Viewport effect/ResizeObserver/Three camera and shared view capture.
 * WebGL/RAF/DOM sizing carriers are controlled; no GPU/OV/network acceptance. */
import{act}from'react'
import{createRoot}from'react-dom/client'
import{afterEach,expect,it,vi}from'vitest'
import*as THREE from'three'
import{subscribeCameraSettled,getViewportHandle,setViewportHandle}from'../src/viewportBus'
import{captureViewState}from'../src/renderer/sceneSnapshot'
const carrier=vi.hoisted(()=>({width:1320,height:560,frames:0,renderer:null as any,observers:[]as any[]}))
vi.mock('three',async(importOriginal)=>{const actual=await importOriginal<typeof import('three')>();return{...actual,WebGLRenderer:class{
 domElement=document.createElement('canvas');ratio=1;gl={ALIASED_LINE_WIDTH_RANGE:1,isContextLost:()=>false,getParameter:()=>new Float32Array([1,1])};getContext(){return this.gl}toneMapping=0;toneMappingExposure=1;outputColorSpace='';size=new actual.Vector2()
 constructor(){carrier.renderer=this;this.domElement.style.objectFit='fill';this.domElement.style.padding='0px';this.domElement.style.border='0px solid'}setPixelRatio(r:number){this.ratio=r}getPixelRatio(){return this.ratio}setClearAlpha(){}setSize(w:number,h:number){this.size.set(w,h);this.domElement.width=Math.floor(w*this.ratio);this.domElement.height=Math.floor(h*this.ratio)}getSize(v:any){return v.copy(this.size)}getDrawingBufferSize(v:any){return v.set(this.domElement.width,this.domElement.height)}render(){carrier.frames++}dispose(){}
}}})
vi.mock('../src/renderer/environmentLighting',()=>({ENVIRONMENT_INTENSITY:.5,installRoomEnvironment:()=>()=>{}}))
vi.mock('../src/components/AxisGizmo',()=>({AxisGizmo:()=>null}))
import{Viewport}from'../src/components/Viewport'
import type{ViewOptions}from'../src/components/Toolbar'
const view={showGrid:true,gridSpacingMm:600,showAnchors:false,showWaypointHandles:false,showLights:true,showOrientationHelper:false,showSeedHelper:false}as unknown as ViewOptions
let root:ReturnType<typeof createRoot>|null=null,host:HTMLElement|null=null,stop:(()=>void)|null=null
async function mount(){
 vi.useFakeTimers()
 vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(()=>({width:carrier.width,height:carrier.height}as DOMRect));
 vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockImplementation(()=>carrier.width);vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockImplementation(()=>carrier.height)
 vi.stubGlobal('requestAnimationFrame',vi.fn(()=>1));vi.stubGlobal('cancelAnimationFrame',vi.fn());vi.stubGlobal('ResizeObserver',class{callback:()=>void;dead=false;constructor(cb:()=>void){this.callback=cb;carrier.observers.push(this)}observe(){}disconnect(){this.dead=true}})
 const old=(globalThis as any).IS_REACT_ACT_ENVIRONMENT;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true
 host=document.createElement('div');document.body.append(host);root=createRoot(host)
 await act(async()=>root!.render(<Viewport project={null}selectedUid={null}view={view}onSelect={()=>{}}/>))
 // Drain initial OrbitControls' separate120ms camera event before subscribing
 // to this test's resize event. Its real bus remains active and unmodified.
 await act(async()=>{await vi.advanceTimersByTimeAsync(300)})
 return{old,observer:carrier.observers.at(-1),camera:getViewportHandle()!.camera}
}
afterEach(async()=>{if(root)await act(async()=>root!.unmount());root=null;stop?.();stop=null;host?.remove();host=null;setViewportHandle(null);carrier.observers=[];carrier.width=1320;carrier.height=560;vi.restoreAllMocks();vi.useRealTimers();vi.unstubAllGlobals();(globalThis as any).IS_REACT_ACT_ENVIRONMENT=false})
it('real component resize publishes one coherent camera after projection and drawing-buffer size update',async()=>{
 const{observer,camera}=await mount(),events:any[]=[];const originalPose=camera.position.toArray(),originalFov=camera.fov
 stop=subscribeCameraSettled(()=>{const handle=getViewportHandle()!;const packet=captureViewState({root:new THREE.Group(),project:{uid:'CONTROLLED_DISPLAY'},camera:handle.camera,target:handle.controls.target});events.push({view:packet,width:carrier.renderer.domElement.width,height:carrier.renderer.domElement.height,projection:handle.camera.projectionMatrix.clone()})})
 carrier.width=800;carrier.height=700;await act(async()=>observer.callback())
 expect(events).toHaveLength(1);expect(events[0].view.camera.aspect).toBe(800/700);expect(events[0].width).toBe(Math.floor(800*carrier.renderer.ratio));expect(events[0].height).toBe(Math.floor(700*carrier.renderer.ratio));expect(events[0].projection.equals(camera.projectionMatrix)).toBe(true);expect(camera.aspect).toBe(800/700);expect(camera.position.toArray()).toEqual(originalPose);expect(camera.fov).toBe(originalFov)
})
it('same-aspect pixel-size resize still notifies; hidden zero-size observations preserve the last coherent view',async()=>{
 const{observer,camera}=await mount(),events:number[]=[];stop=subscribeCameraSettled(()=>events.push(camera.aspect));carrier.width=660;carrier.height=280;await act(async()=>observer.callback());expect(events).toEqual([1320/560]);const width=carrier.renderer.domElement.width,height=carrier.renderer.domElement.height;carrier.width=0;await act(async()=>observer.callback());carrier.width=660;carrier.height=0;await act(async()=>observer.callback());expect(events).toEqual([1320/560]);expect(carrier.renderer.domElement.width).toBe(width);expect(carrier.renderer.domElement.height).toBe(height);expect(camera.aspect).toBe(1320/560)
})
it('a real ResizeObserver disconnects when the Viewport is unmounted',async()=>{const{observer}=await mount();await act(async()=>root!.unmount());root=null;expect(observer.dead).toBe(true);expect(getViewportHandle()).toBeNull()})

it('the actual retained component publishes a live GL/drawing-buffer observation after the real resize bus event',async()=>{
 const{observer}=await mount(),handle=getViewportHandle()!,events:any[]=[];expect(handle.readLineRaster?.()).toMatchObject({drawingBuffer:[1320,560],effectiveWidthPx:1,aliasedRange:[1,1]})
 stop=subscribeCameraSettled(()=>events.push(handle.readLineRaster?.()));carrier.width=800;carrier.height=700;await act(async()=>observer.callback());expect(events).toHaveLength(1);expect(events[0]).toMatchObject({drawingBuffer:[800,700],effectiveWidthPx:1});expect(handle.camera.aspect).toBe(800/700)
 await act(async()=>root!.unmount());root=null;expect(handle.readLineRaster?.()).toBeNull()
})
