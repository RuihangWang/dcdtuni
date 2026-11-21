import { afterEach, expect, it, vi } from 'vitest'
import { PostprocessDisplayDriver, type DisplayInput, type DisplaySnapshot, type DisplayApplyContext } from '../src/postprocess/displaySession'
import type {WindResultFrame} from '../src/renderer/windResultProducer'
import { clearResultLayerSet } from '../src/renderer/resultLayerRevision'
import { decodeSlice, decodeStreamlines } from '../src/postprocess'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const sha = (v: string) => v.repeat(64)
const session = { runId: sha('9'), revision: 'accepted-pp-r1', timeValue: 0.015 }
const source = { projectUid: 'project-a', projectContentSha256: sha('a'), cfdrunSha256: sha('b'),
  meshReceiptSha256: sha('c'), meshQuality: 'accepted' as const,
  run: { uid: 'execution-a', receiptSha256: sha('d') } }
const descriptor = { schema: 'dcdtuni.verified-air-result-source/v1' as const, postprocess: session,
  source, sourceFacesByPatch: { wall: ['project-a/wall/face-7'] }, fieldUnits: { T: 'K', speed: 'm/s' } }
const mesh = { positions: new Float32Array([0,0,0,1,0,0,0,1,0]), indices: new Uint32Array([0,1,2]),
  edges: new Uint32Array([0,1,1,2,2,0]), patches: [{name:'wall',nPoints:3,nTriangles:1,nEdges:3,defaultVisible:true}],
  totals:{points:3,triangles:1,edges:3} }
function readPayload(name: string) { const v=readFileSync(join(__dirname,'__fixtures__',name));return v.buffer.slice(v.byteOffset,v.byteOffset+v.byteLength) }
const cut = decodeSlice(readPayload('pp_slice_small.bin'))
const stream = decodeStreamlines(readPayload('pp_streamlines_small.bin'))
function input(time=0.015): DisplayInput & {windFrame: WindResultFrame} {
  const accepted={...session,timeValue:time}
  return { layers: {mesh,surface:{visible:true,colorMap:'coolwarm' as const,climMin:299,climMax:303,autoScale:false},
    slices:[{id:17,payload:cut,visible:true,colorMap:'viridis' as const,clim:[288,295] as [number,number],opacity:0.4},
      {id:42,payload:cut,visible:true,colorMap:'coolwarm' as const,clim:[290,298] as [number,number],opacity:0.9}],
    streamlines:[{id:9,set:stream,visible:true,colorMap:'jet' as const,clim:[0,1] as [number,number],opacity:0.7,tubeRadius:0.03}],
    ring:{show:false,origin:[0,1,0] as [number,number,number],normal:[0,1,0] as [number,number,number],radius:2},
    seed:{show:false,centre:[0,1,0] as [number,number,number],radius:1}},
    windFrame:{descriptor:{...descriptor,postprocess:accepted},session:accepted,currentProject:{uid:'project-a',contentSha256:sha('a')},mesh,
      activeSurfaceField:'T',surface:{field:'T',values:new Float32Array([300,301,302]),visible:true,colorMap:'coolwarm' as const,
        range:[299,303] as [number,number],opacity:0.6,hidden:new Set<string>()},
      slices:[{id:17,payload:cut,origin:[0,1,0] as [number,number,number],normal:[0,1,0] as [number,number,number],colorMap:'viridis' as const,range:[288,295] as [number,number],opacity:0.4,visible:true},
        {id:42,payload:cut,origin:[0,2,0] as [number,number,number],normal:[1,0,0] as [number,number,number],colorMap:'coolwarm' as const,range:[290,298] as [number,number],opacity:0.9,visible:true}],
      streamlines:[{id:9,set:stream,colorMap:'jet' as const,range:[0,1] as [number,number],opacity:0.7,tubeRadius:0.03,visible:true}]},
    volume:{field:'T',unit:'K',visible:false,payload:null,range:[299,303] as [number,number],palette:'coolwarm' as const,opacity:0.5,resolution:128},
    internalVisible:true,handles:{ringSetId:'draft' as const,seedSetId:'draft' as const},isCurrent:()=>true }
}
const adapter = (kind: 'three'|'ov') => ({kind,render:vi.fn(async(_frame:DisplaySnapshot,_context:DisplayApplyContext)=>({status:'displayed' as const})),clear:vi.fn(async()=>{}),dispose:vi.fn()})
afterEach(()=>clearResultLayerSet())

it('replays the identical accepted exact frame and every set ID across Three -> OV -> Three',async()=>{
 const three=adapter('three'),ov=adapter('ov'),d=new PostprocessDisplayDriver(three)
 d.present(input());await d.whenIdle();const frame=d.current()!
 expect(frame.packet?.timeSeconds).toBe(0.015)
 expect(frame.packet?.layers.filter(l=>l.kind==='slice').map(l=>l.sourceSliceUid)).toEqual([`${session.runId}/time-0.015/slice-17`,`${session.runId}/time-0.015/slice-42`])
 d.activate(ov);await d.whenIdle();d.activate(three);await d.whenIdle()
 expect(ov.render.mock.calls[0]![0]).toBe(frame);expect(three.render.mock.lastCall![0]).toBe(frame)
 expect(frame.packet?.source).toEqual(source);expect(frame.packet?.layers.find(l=>l.kind==='surfaceField')?.lut).toEqual({palette:'coolwarm',range:[299,303],opacity:0.6})
})

it('retains hidden patch choices, opacity, palette, volume resolution and draft/set identity',async()=>{
 const a=adapter('three'),d=new PostprocessDisplayDriver(a),v=input();v.windFrame.surface.hidden.add('wall');v.volume={...v.volume,resolution:256,palette:'viridis',opacity:0.23}
 v.handles={ringSetId:42,seedSetId:9};d.present(v);await d.whenIdle()
 expect(d.current()!.packet!.layers.filter(l=>l.kind==='surfaceField')).toHaveLength(0)
 expect(d.current()!.volume).toMatchObject({resolution:256,palette:'viridis',opacity:0.23})
 expect(d.current()!.handles).toEqual({ringSetId:42,seedSetId:9})
})

it('refuses stale descriptor/session and current field rather than changing to latest',async()=>{
 const a=adapter('ov'),d=new PostprocessDisplayDriver(a),v=input()
 v.windFrame.descriptor={...v.windFrame.descriptor,postprocess:{...session,timeValue:0.020}}
 expect(()=>d.present(v)).toThrow(/session|time/i);expect(a.render).not.toHaveBeenCalled()
 const w=input();w.windFrame.activeSurfaceField='speed';expect(()=>d.present(w)).toThrow(/stale|field/i)
 expect(d.current()).toBeNull()
})

it('keeps a receipt-free diagnostic frame available to Three with no forged accepted OV packet',async()=>{
 const a=adapter('three'),d=new PostprocessDisplayDriver(a),v={...input(),windFrame:null};d.present(v);await d.whenIdle()
 expect(d.current()!.packet).toBeNull();expect(a.render).toHaveBeenCalledOnce()
})

it('drops old pending frames and makes in-flight guards false before executing the new frame',async()=>{
 let release!:()=>void;const gate=new Promise<void>(done=>release=done),guards:(()=>boolean)[]=[]
 const a=adapter('ov');a.render.mockImplementationOnce(async(_frame,context)=>{guards.push(context.isCurrent);await gate;return {status:'displayed' as const}})
 const d=new PostprocessDisplayDriver(a);d.present(input(0.005));await Promise.resolve();d.present(input(0.010));d.present(input(0.015))
 expect(guards[0]!()).toBe(false);release();await d.whenIdle()
 expect(a.render.mock.calls.map(call=>call[0].packet?.timeSeconds)).toEqual([0.005,0.015])
 expect(d.status()).toMatchObject({status:'displayed',timeSeconds:0.015})
})

it('does not run a queued frame after its project or accepted source ceases to be current',async()=>{
 const a=adapter('ov'),d=new PostprocessDisplayDriver(a),v=input();let live=true;v.isCurrent=()=>live
 d.present(v);live=false;await d.whenIdle();expect(a.render).not.toHaveBeenCalled();expect(d.status().status).not.toBe('displayed')
})

it('rejects invalid shared volume intent before any renderer mutation',()=>{
 const a=adapter('three'),d=new PostprocessDisplayDriver(a),v=input();v.volume.opacity=1.5
 expect(()=>d.present(v)).toThrow(/opacity/);expect(a.render).not.toHaveBeenCalled()
 const w=input();w.volume.resolution=0;expect(()=>d.present(w)).toThrow(/resolution/)
})

it('accepts interaction readback only for the displayed generation and concrete draft/set identity',async()=>{
 const a=adapter('ov'),d=new PostprocessDisplayDriver(a),commit=vi.fn(),v=input();v.handles.ringSetId=42
 d.onInteraction(commit);d.present(v);await d.whenIdle();const f=d.current()!
 expect(d.interact({generation:f.generation,scopeKey:f.scopeKey,kind:'ring',setId:42,position:[1,2,3]})).toBe(true)
 expect(commit).toHaveBeenCalledWith({kind:'ring',setId:42,position:[1,2,3]})
 expect(d.interact({generation:f.generation,scopeKey:f.scopeKey,kind:'ring',setId:17,position:[1,2,3]})).toBe(false)
 d.present(input(0.020));expect(d.interact({generation:f.generation,scopeKey:f.scopeKey,kind:'ring',setId:42,position:[1,2,3]})).toBe(false)
})

it('keeps right-panel surface opacity after a new public frame is accepted without changing its time',async()=>{
 const a=adapter('three'),d=new PostprocessDisplayDriver(a);d.present(input());await d.whenIdle();d.configureAppearance({fieldOpacity:0.25});await d.whenIdle()
 d.present(input(0.020));await d.whenIdle();const frame=d.current()!
 expect(frame.packet!.timeSeconds).toBe(0.020);expect(frame.packet!.layers.find(l=>l.kind==='surfaceField')?.lut.opacity).toBe(0.25)
 expect(frame.appearance?.fieldOpacity).toBe(0.25)
})
it('restores the same concrete internal set IDs after a shared visibility off/on round trip',async()=>{
 const a=adapter('three'),d=new PostprocessDisplayDriver(a);d.present(input());await d.whenIdle();d.setInternalVisible(false);await d.whenIdle()
 expect(d.current()!.packet!.layers.some(l=>l.kind==='slice'||l.kind==='streamline')).toBe(false)
 d.setInternalVisible(true);await d.whenIdle()
 expect(d.current()!.packet!.layers.filter(l=>l.kind==='slice').map(l=>l.sourceSliceUid)).toEqual([`${session.runId}/time-0.015/slice-17`,`${session.runId}/time-0.015/slice-42`])
})
it('clear invalidates a pending OV frame before it can appear and settles the queue',async()=>{
 const a=adapter('ov'),d=new PostprocessDisplayDriver(a);d.present(input());d.clear();await d.whenIdle()
 expect(a.render).not.toHaveBeenCalled();expect(a.clear).toHaveBeenCalledOnce();expect(d.current()).toBeNull();expect(d.status().status).toBe('idle')
})

it('returns to local rendering immediately despite an OV ACK that never settles',async()=>{
 let release!:()=>void;const gate=new Promise<void>(done=>release=done)
 const ov=adapter('ov'),three=adapter('three');ov.render.mockImplementationOnce(async()=>{await gate;return {status:'displayed' as const}})
 const d=new PostprocessDisplayDriver(ov);d.present(input(0.005));await Promise.resolve()
 d.activate(three)
 expect(three.render).toHaveBeenCalledOnce()
 d.clear();expect(three.clear).toHaveBeenCalledOnce();expect(d.status().status).toBe('idle')
 d.present(input(0.015));expect(three.render).toHaveBeenCalledTimes(2)
 await d.whenIdle();expect(d.status()).toMatchObject({renderer:'three',status:'displayed',timeSeconds:0.015})
 release();for(let i=0;i<5;i++)await Promise.resolve()
 expect(d.status()).toMatchObject({renderer:'three',status:'displayed',timeSeconds:0.015})
})
