import { describe, expect, it } from 'vitest'
import { buildZip, crc32, dosDateTime, readZip } from '../src/io/zipWriter'

/**
 * The archive is checked THROUGH its central directory (`readZip`), not against
 * the offsets the writer just used: a writer verified against its own arithmetic
 * agrees with itself while `unzip` refuses the file.
 *
 * The CRC values below are the published IEEE 802.3 check values, so the
 * checksum is pinned to the standard rather than to this implementation.
 */

const ascii = (s: string) => new TextEncoder().encode(s)

describe('crc32', () => {
  it('matches the published check value for "123456789"', () => {
    expect(crc32(ascii('123456789')).toString(16)).toBe('cbf43926')
  })

  it('is 0 for empty input, which is what a folder entry stores', () => {
    expect(crc32(new Uint8Array(0))).toBe(0)
  })

  it('matches the check value for "The quick brown fox jumps over the lazy dog"', () => {
    expect(crc32(ascii('The quick brown fox jumps over the lazy dog')).toString(16)).toBe('414fa339')
  })
})

describe('dosDateTime', () => {
  it('encodes a date into the 1980-epoch fields', () => {
    const { time, date } = dosDateTime(new Date(2026, 7, 30, 13, 45, 20))
    expect((date >> 9) + 1980).toBe(2026)
    expect((date >> 5) & 0xf).toBe(8)
    expect(date & 0x1f).toBe(30)
    expect(time >> 11).toBe(13)
    expect((time >> 5) & 0x3f).toBe(45)
    // Two-second resolution: 20 seconds is stored as 10.
    expect((time & 0x1f) * 2).toBe(20)
  })

  it('clamps a pre-1980 date rather than writing a negative year field', () => {
    const { date } = dosDateTime(new Date(1970, 0, 1))
    expect((date >> 9) + 1980).toBe(1980)
  })
})

describe('buildZip', () => {
  it('round-trips names and content', () => {
    const bytes = buildZip([
      { name: 'proj_cfd/', text: '' },
      { name: 'proj_cfd/merged_rack.stl', text: 'solid rack\nendsolid rack\n' },
      { name: 'proj_cfd/proj.cfdrun', text: '{"a":1}' },
    ])
    const listing = readZip(bytes)
    expect(listing.map((e) => e.name)).toEqual([
      'proj_cfd/',
      'proj_cfd/merged_rack.stl',
      'proj_cfd/proj.cfdrun',
    ])
    expect(listing[1]?.text).toBe('solid rack\nendsolid rack\n')
    expect(listing[2]?.text).toBe('{"a":1}')
  })

  it('stores UTF-8 bytes, not UTF-16 code units', () => {
    // A Chinese project name in the manifest is one entry of 3-byte characters;
    // sizing the entry by `text.length` would truncate the JSON by two thirds.
    const text = '{"projectName":"机房A"}'
    const listing = readZip(buildZip([{ name: 'a.json', text }]))
    expect(listing[0]?.size).toBe(new TextEncoder().encode(text).length)
    expect(listing[0]?.text).toBe(text)
  })

  it('records a CRC a reader will accept', () => {
    const text = 'solid x\nendsolid x\n'
    const listing = readZip(buildZip([{ name: 'x.stl', text }]))
    expect(listing[0]?.crc).toBe(crc32(ascii(text)))
  })

  it('keeps the two size fields in step across a mixed archive', () => {
    // Local header and central directory each carry the size; a reader that
    // trusts the other one reads the next entry's header as file data.
    const entries = [
      { name: 'd/', text: '' },
      { name: 'd/small.txt', text: 'x' },
      { name: 'd/big.txt', text: 'y'.repeat(70000) },
      { name: 'd/empty.txt', text: '' },
    ]
    const listing = readZip(buildZip(entries))
    expect(listing.map((e) => e.size)).toEqual([0, 1, 70000, 0])
    expect(listing[2]?.text.length).toBe(70000)
  })

  it('writes an end-of-central-directory record with the entry count', () => {
    const bytes = buildZip([{ name: 'a', text: '1' }, { name: 'b', text: '2' }])
    const view = new DataView(bytes.buffer)
    const eocd = bytes.length - 22
    expect(view.getUint32(eocd, true)).toBe(0x06054b50)
    expect(view.getUint16(eocd + 8, true)).toBe(2)
  })

  it('produces an empty archive rather than throwing on no entries', () => {
    expect(readZip(buildZip([]))).toEqual([])
  })
})
