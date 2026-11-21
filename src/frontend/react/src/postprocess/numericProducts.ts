/** Actual common response receipts are carried with decoded objects, never
 * persisted to projects or invented from display arrays. Backend owns bytes. */
import {assertSessionIdentity,type PostprocessSession} from './ppRevision'
export type NumericKind='mesh'|'field'|'slice'|'streamline'|'volume'
export interface NumericProductReceipt {schema:'dcdtuni.numeric-product-receipt/v1';token:string;kind:NumericKind;postprocess:PostprocessSession;payloadSha256:string;nativeInputSha256:string}
const receipts=new WeakMap<object,NumericProductReceipt>()
const hash=(value:unknown)=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value)
export function bindNumericProduct<T extends object>(value:T,response:Response,kind:NumericKind,session:PostprocessSession):T {
 const raw=response.headers.get('X-PP-Product')
 if(!raw)return value // Diagnostic Three inspection can lack trusted provenance.
 const receipt=JSON.parse(raw)as NumericProductReceipt
 if(receipt.schema!=='dcdtuni.numeric-product-receipt/v1'||receipt.kind!==kind||!hash(receipt.token)||!hash(receipt.payloadSha256)||!hash(receipt.nativeInputSha256))throw new Error('Invalid actual numeric product receipt')
 assertSessionIdentity(receipt.postprocess,session);receipts.set(value,structuredClone(receipt));return value
}
export function requireNumericProduct(value:object,kind:NumericKind,session:PostprocessSession,nativeInputSha256:string):string {
 const receipt=receipts.get(value)
 if(!receipt||receipt.kind!==kind||receipt.nativeInputSha256!==nativeInputSha256)throw new Error('Common numeric product has no matching owned source receipt')
 assertSessionIdentity(receipt.postprocess,session);return receipt.token
}
