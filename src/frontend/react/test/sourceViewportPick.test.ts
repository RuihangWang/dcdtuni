// Portable controlled event / SDK consumer regression; no native browser gesture proof.
import {afterEach,expect,it,vi} from 'vitest'
import {installSourcePickPointer,videoContentPoint,validateSourcePickReply,type SourcePickContext} from '../src/renderer/sourceViewportPick'
const cleanup:(()=>void)[]=[]
afterEach(()=>{cleanup.splice(0).forEach(fn=>fn());document.body.replaceChildren();vi.restoreAllMocks()})
let ownerSequence=0
function fixture(){
 const id=`controlled-video-${++ownerSequence}`
 const parent=document.createElement('div');parent.id=`${id}-input-parent`;document.body.append(parent)
 const video=document.createElement('video');video.id=id;video.style.cssText='object-fit:contain;padding:0;border:0';parent.append(video)
 let paused=false,ended=false,w=1192,h=796,width=1191.2,height=796,clock=100
 vi.spyOn(performance,'now').mockImplementation(()=>clock)
 Object.defineProperties(video,{videoWidth:{get:()=>w},videoHeight:{get:()=>h},readyState:{get:()=>4},paused:{get:()=>paused},ended:{get:()=>ended},clientWidth:{get:()=>1191},clientHeight:{get:()=>796}})
 video.getBoundingClientRect=()=>({left:0,top:0,width,height}as DOMRect)
 let c:SourcePickContext|null={binding:{projectUid:'project',projectContentSha256:'a'.repeat(64),sceneRevision:1,viewRevision:4},
  output:{owner:'video-owner',revision:2,decoded:[1192,796],css:[1191.2,796],layoutCss:[1191,796],objectFit:'contain'},signalPort:8302}
 const vEvents=new Map<string,EventListener[]>(),wEvents=new Map<string,EventListener[]>()
 const bind=(target:HTMLElement|Window,m:Map<string,EventListener[]>)=>{const original=target.addEventListener.bind(target)
  vi.spyOn(target,'addEventListener').mockImplementation(((name:string,cb:EventListener,options?:boolean)=>{m.set(name,[...(m.get(name)??[]),cb]);original(name,cb,options)})as typeof target.addEventListener)}
 bind(video,vEvents);bind(window,wEvents)
 const frames=new Map<number,VideoFrameRequestCallback>();let key=0
 Object.defineProperties(video,{requestVideoFrameCallback:{value:(fn:VideoFrameRequestCallback)=>{frames.set(++key,fn);return key}},cancelVideoFrameCallback:{value:(id:number)=>frames.delete(id)}})
 const send=vi.fn(),controller=installSourcePickPointer(video,video.id,()=>c,send);cleanup.push(controller.dispose)
 const present=(count=1)=>{const [id,fn]=Array.from(frames)[0]!;frames.delete(id);fn(clock,{presentedFrames:count}as VideoFrameCallbackMetadata)}
 const event=(name:string,patch:Record<string,unknown>={})=>{const e={target:video,isTrusted:true,pointerType:'mouse',button:0,pointerId:1,shiftKey:true,ctrlKey:false,metaKey:false,altKey:false,clientX:595.6,clientY:398,timeStamp:clock,preventDefault:vi.fn(),stopImmediatePropagation:vi.fn(),...patch}
  for(const fn of (name==='pointerup'||name==='pointermove'?wEvents:vEvents).get(name)??[])fn(e as unknown as Event)
  return e}
 const click=(patch:Record<string,unknown>={})=>{event('pointerdown',patch);clock+=10;event('pointerup',patch);clock+=1;event('click',patch)}
 return{video,parent,present,event,click,send,controller,frames,setClock:(v:number)=>{clock=v},setContext:(v:SourcePickContext|null)=>{c=v},context:()=>c,
  pause:()=>{paused=true;video.dispatchEvent(new Event('pause'))},resize:()=>{width=900},dimensions:()=>{w=1280}}
}
it('forwards one controlled real-event protocol Shift click without focus or keyboard synthesis',()=>{
 const f=fixture();f.present();const focus=vi.spyOn(f.video,'focus');f.click()
 expect(f.send).toHaveBeenCalledTimes(1);expect(f.send.mock.calls[0]![0]).toMatchObject({gestureId:1,point:[.5,.5],owner:'video-owner',decoded:[1192,796]})
 expect(focus).not.toHaveBeenCalled();f.click();expect(f.send).toHaveBeenCalledTimes(2);expect(f.send.mock.calls[1]![0].gestureId).toBe(2)
})
it('does not capture ordinary SDK clicks, Ctrl/Meta/Alt, touch, overlays or synthetic DOM events',()=>{
 const f=fixture();f.present()
 for(const patch of [{shiftKey:false},{ctrlKey:true},{metaKey:true},{altKey:true},{pointerType:'touch'},{target:f.parent},{isTrusted:false}]){
  expect(f.event('pointerdown',patch).preventDefault).not.toHaveBeenCalled();f.click(patch)
 }
 f.video.dispatchEvent(new MouseEvent('click',{bubbles:true,shiftKey:true}));expect(f.send).not.toHaveBeenCalled()
})
it('slop, drag, canceled, unmatched pointer or released modifier do not query',()=>{
 const f=fixture();f.present();f.event('pointerdown');f.event('pointermove',{clientX:610});f.event('pointerup');f.event('click')
 f.event('pointerdown');f.event('pointerup',{pointerId:2});f.event('click')
 f.event('pointerdown');f.event('pointerup',{shiftKey:false});f.event('click')
 f.event('pointerdown');window.dispatchEvent(new Event('pointercancel'));f.event('pointerup');f.event('click')
 expect(f.send).not.toHaveBeenCalled()
})
it('closing or owner/raster/view changes during a pointer-pair fail closed',()=>{
 const f=fixture();f.present();f.event('pointerdown');f.resize();f.event('pointerup');f.event('click');expect(f.send).not.toHaveBeenCalled()
 const g=fixture();g.present();g.event('pointerdown');g.setContext({...g.context()!,binding:{...g.context()!.binding,viewRevision:5}});g.event('pointerup');g.event('click');expect(g.send).not.toHaveBeenCalled()
 const h=fixture();h.present();h.controller.dispose();h.click();expect(h.send).not.toHaveBeenCalled();expect(h.frames.size).toBe(0)
})
it('paused, stale, duplicate-only, detached or replaced videos cannot authorize a query',()=>{
 const f=fixture();f.click();expect(f.send).not.toHaveBeenCalled();f.present();f.setClock(1201);f.present(1);f.click();expect(f.send).not.toHaveBeenCalled()
 const g=fixture();g.present();g.pause();g.click();expect(g.send).not.toHaveBeenCalled()
 const h=fixture();h.present();h.video.id='old';h.click();expect(h.send).not.toHaveBeenCalled()
})
it('maps measured DOM contain letterboxing, while borders, rotation, dimensions and outside positions refuse',()=>{
 const f=fixture();expect(videoContentPoint(f.video,595.6,398)).toEqual([.5,.5])
 expect(videoContentPoint(f.video,-1,398)).toBeNull();f.parent.style.transform='rotate(10deg)';expect(videoContentPoint(f.video,595.6,398)).toBeNull()
 f.parent.style.transform='none';f.video.style.borderLeftWidth='1px';expect(videoContentPoint(f.video,595.6,398)).toBeNull()
})
it('accepts binding fields independent of Carb/JSON key ordering but rejects mismatched/extra/invalid replies',()=>{
 const f=fixture();f.present();f.click();const req=f.send.mock.calls[0]![0]
 const reply={schema:'dcdtuni.source-pick/v1',gestureId:1,binding:{projectHash:req.binding.projectHash,projectUid:'project',sceneRevision:1,viewRevision:4},owner:'video-owner',rasterRevision:2,status:'miss',reason:'source-pick-empty'}
 expect(validateSourcePickReply(JSON.stringify(reply),req).status).toBe('miss')
 for(const patch of [{gestureId:2},{owner:'old'},{event:{}},{status:'hit'},{extra:1},{binding:{...reply.binding,viewRevision:3}}])
  expect(()=>validateSourcePickReply(JSON.stringify({...reply,...patch}),req)).toThrow()
})

it('frame timestamps/counts that go backwards or are invalid cannot refresh a gesture',()=>{
 for(const count of [0,-1,Number.NaN]){const f=fixture();f.present(count);f.click();expect(f.send).not.toHaveBeenCalled()}
 const f=fixture();f.present();f.setClock(99);f.present(2);f.click();expect(f.send).not.toHaveBeenCalled()
 const g=fixture();g.present();g.setClock(-1);g.present(2);g.click();expect(g.send).not.toHaveBeenCalled()
})

it('unique controlled owners all have live adapters, and a changed owner cancels a pending click',()=>{
 const a=fixture(),b=fixture();a.present();b.present();a.click();b.click()
 expect(a.send).toHaveBeenCalledTimes(1);expect(b.send).toHaveBeenCalledTimes(1)
 const c=fixture();c.present();c.event('pointerdown');c.setContext({...c.context()!,output:{...c.context()!.output,owner:'new-owner'}});c.event('pointerup');c.event('click');expect(c.send).not.toHaveBeenCalled()
})
