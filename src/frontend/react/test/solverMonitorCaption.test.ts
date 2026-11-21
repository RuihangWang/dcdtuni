import { describe, expect, it } from 'vitest'
import { captionOf } from '../src/components/SolverMonitorPanel'

describe('captionOf', () => {
  it('drops the rack name a server patch carries twice', () => {
    expect(captionOf('Rack_A_01_Rack_A_01_server_1_p0')).toBe('Rack_A_01_server_1_p0')
    expect(captionOf('DataHall604_Rack_A01_DataHall604_Rack_A01_Slot6_p0')).toBe('DataHall604_Rack_A01_Slot6_p0')
  })
  it('leaves a name without a repeat alone', () => {
    expect(captionOf('ACU_01')).toBe('ACU_01')
    expect(captionOf('Rack_1_server_1')).toBe('Rack_1_server_1')
  })
})
