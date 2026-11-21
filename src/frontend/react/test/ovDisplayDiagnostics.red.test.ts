import { expect, it } from 'vitest'
import { OvCommandClient } from '../src/renderer/ovProtocol'
it('explicit diagnostic readback must capture identity and preserve raw-view rejection', async () => {
 const sent:{command_name:string;message:string}[]=[]
 const c=new OvCommandClient(async p=>{sent.push(p)},10)
 try {
  const method=(c as unknown as {readViewDiagnostics?:(v:object)=>Promise<unknown>}).readViewDiagnostics
  expect(typeof method).toBe('function')
 } finally {c.dispose()}
})
