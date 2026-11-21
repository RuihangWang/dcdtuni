import {expect,it} from 'vitest'
import {ovPillState} from '../src/renderer/RendererControl'

const base={mode:'three' as const,ready:false,waitingKit:false,failed:false}
it('says what a click on the OV state will do', () => {
  expect(ovPillState({...base,status:null})).toBe('unknown')
  expect(ovPillState({...base,status:{bridge:false,kitListening:false}})).toBe('off')
  // launcher started Kit, signalling port not open yet: queue, do not fail
  expect(ovPillState({...base,status:{bridge:true,kitListening:false}})).toBe('starting')
  expect(ovPillState({...base,waitingKit:true,status:{bridge:true,kitListening:false}})).toBe('waiting')
  expect(ovPillState({...base,status:{bridge:true,kitListening:true}})).toBe('ready')
  expect(ovPillState({...base,failed:true,status:{bridge:true,kitListening:true}})).toBe('retry')
  expect(ovPillState({...base,mode:'ov',status:{bridge:true,kitListening:true}})).toBe('connecting')
  expect(ovPillState({...base,mode:'ov',ready:true,status:{bridge:true,kitListening:true}})).toBe('connected')
  // a server without --ov stays "off" even after a failed attempt
  expect(ovPillState({...base,failed:true,status:{bridge:false,kitListening:false}})).toBe('off')
})
