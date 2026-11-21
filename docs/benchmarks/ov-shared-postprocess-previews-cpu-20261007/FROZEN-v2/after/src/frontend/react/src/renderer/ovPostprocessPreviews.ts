/** Non-numeric previews of the same shared form. They cannot request a cut/trace. */
import Ajv2020 from 'ajv/dist/2020'
import schema from '../../../shared/schema/ov-postprocess-previews.schema.json'
import type {ResultSource} from './resultLayers'
import type {DisplaySnapshot,DisplaySetId} from '../postprocess/displaySession'
import type {CanonicalRenderReceipt} from '../postprocess/commonRenderIntent'
export interface PreviewBinding {
 source:ResultSource;packetSha256:string;timeSeconds:number;sceneRevision:number
 displayGeneration:number;scopeKey:string;ownerKey:string
}
export type PreviewHandle = {kind:'ring';setId:DisplaySetId;origin:[number,number,number];normal:[number,number,number];radius:number} |
 {kind:'seed';setId:DisplaySetId;centre:[number,number,number];radius:number}
export interface PreviewPacket {schema:'dcdtuni.ov-postprocess-previews/v1';binding:PreviewBinding;handles:PreviewHandle[]}
export interface PreviewAck {schema:'dcdtuni.ov-postprocess-previews-ack/v1';packet:PreviewPacket;leaseKey:string;ready:true}
export interface PreviewEvent {schema:'dcdtuni.ov-postprocess-preview-event/v1';binding:PreviewBinding;leaseKey:string;sequence:number;kind:'ring'|'seed';setId:DisplaySetId;position:[number,number,number]}
type PreviewWire=PreviewPacket|PreviewAck|PreviewEvent
const validate=new Ajv2020({strict:true,strictNumbers:true}).compile<PreviewWire>(schema)
function semanticPacket(packet:PreviewPacket) {
 if(!packet.binding.source.run)return false
 const kinds=new Set<string>()
 for(const handle of packet.handles){
  if(kinds.has(handle.kind))return false;kinds.add(handle.kind)
  if(handle.kind==='ring'&&!(Math.hypot(...handle.normal)>1e-12))return false
 }
 return true
}
function valid(value:unknown):value is PreviewWire {
 try {
  if(new TextEncoder().encode(JSON.stringify(value)).byteLength>16384||!validate(value))return false
  if(value.schema==='dcdtuni.ov-postprocess-previews/v1')return semanticPacket(value)
  if(value.schema==='dcdtuni.ov-postprocess-previews-ack/v1')return semanticPacket(value.packet)
  return value.binding.source.run!==null
 }catch{return false}
}
export function parsePreviewEvent(value:unknown):PreviewEvent|null {
 return valid(value)&&value.schema==='dcdtuni.ov-postprocess-preview-event/v1'?value:null
}
export function parsePreviewAck(value:unknown):PreviewAck|null {
 return valid(value)&&value.schema==='dcdtuni.ov-postprocess-previews-ack/v1'?value:null
}
export function assertPreviewPacket(value:unknown):asserts value is PreviewPacket {
 if(!valid(value)||value.schema!=='dcdtuni.ov-postprocess-previews/v1')throw new Error('Invalid owned shared preview packet')
}
function canonical(value:unknown):string {
 if(Array.isArray(value))return `[${value.map(canonical).join(',')}]`
 if(value!==null&&typeof value==='object')return `{${Object.keys(value).sort().map(key=>`${JSON.stringify(key)}:${canonical((value as Record<string,unknown>)[key])}`).join(',')}}`
 return JSON.stringify(value)
}
export function samePreviewValue(a:unknown,b:unknown){return canonical(a)===canonical(b)}
export function previewOwnerKey():string {
 const bytes=crypto.getRandomValues(new Uint8Array(16));return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('')
}
export function buildPreviewPacket(frame:DisplaySnapshot,receipt:CanonicalRenderReceipt,ownerKey:string):PreviewPacket {
 const packet:PreviewPacket={schema:'dcdtuni.ov-postprocess-previews/v1',binding:{source:structuredClone(receipt.source),packetSha256:receipt.sha256,timeSeconds:receipt.timeSeconds,sceneRevision:receipt.sceneRevision,displayGeneration:frame.generation,scopeKey:frame.scopeKey,ownerKey},handles:[]}
 if(frame.layers.ring?.show)packet.handles.push({kind:'ring',setId:frame.handles.ringSetId,origin:[...frame.layers.ring.origin],normal:[...frame.layers.ring.normal],radius:frame.layers.ring.radius})
 if(frame.layers.seed?.show)packet.handles.push({kind:'seed',setId:frame.handles.seedSetId,centre:[...frame.layers.seed.centre],radius:frame.layers.seed.radius})
 assertPreviewPacket(packet);return packet
}
