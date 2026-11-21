import {expect,it} from 'vitest'
import reply from './fixtures/actual-bounded-native-reply.json'
import {validateDiagnosticReply} from '../src/renderer/ovDisplayDiagnostics'
it('strict browser whitelist accepts the actual literal-API + CPUUSD route reply without reshaping',()=>{
 const context={expectedSceneRevision:reply.sceneRevision,expectedProjectHash:reply.projectHash,roofModelUid:reply.diagnostics.roof.modelUid}
 const validated=validateDiagnosticReply(reply,context);expect(validated).toEqual(reply);expect(validated.diagnostics.pixelVerified).toBe(false);expect((validated.diagnostics.viewport as {layoutProof:unknown}).layoutProof).toBe('unresolved')
})
