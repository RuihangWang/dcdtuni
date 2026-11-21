/**
 * A ZIP writer, because the CFD package is one file or it is nothing.
 *
 * The legacy exporter packs the manifest and the merged STL set with JSZip
 * (`vendor/jszip.min.js`, STL_generate.html:34) and hands the user
 * `<project>_cfd.zip`. The React bundle carries no zip library — `fflate` is
 * present in node_modules only as a transitive dev dependency of the test
 * runner, and depending on a package nothing declares is how a build breaks
 * later — so the format is written here instead.
 *
 * ## Why writing it by hand is reasonable
 *
 * JSZip's default compression IS `STORE` (no deflate), which is the whole of
 * what `generateAsync({type: 'blob'})` produces for the legacy package. A
 * STORE-only zip is a header, the bytes, a central directory and an end
 * record — no compressor, no bit-stream. The only real work is CRC-32, which
 * every entry needs and which a reader checks, so a wrong one is caught by
 * `unzip -t` rather than by a corrupted STL nobody notices.
 *
 * ## What a reader needs to agree with
 *
 * Sizes and offsets appear TWICE, in the local header and again in the central
 * directory. If those two disagree the archive still opens in some readers and
 * fails in others, which is the failure mode this module is written to avoid:
 * every number is computed once and written to both places.
 */

/** One file in the archive. Directories are entries whose name ends in `/`. */
export interface ZipEntry {
  name: string
  /** UTF-8 text. The package holds JSON and ASCII STL, both text. */
  text: string
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

/** CRC-32 (IEEE), the checksum every zip entry carries. */
export function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff
  for (let i = 0; i < bytes.length; i++) {
    c = (CRC_TABLE[(c ^ (bytes[i] as number)) & 0xff] as number) ^ (c >>> 8)
  }
  return (c ^ 0xffffffff) >>> 0
}

/**
 * MS-DOS date and time, the only timestamp the base format has.
 *
 * Two-second resolution and a 1980 epoch. A date before 1980 cannot be
 * represented at all, and encoding one produces a year field that reads as
 * garbage, so it is clamped.
 */
export function dosDateTime(date: Date): { time: number; date: number } {
  const year = Math.max(1980, date.getFullYear())
  return {
    time: (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1),
    date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
  }
}

interface Prepared {
  nameBytes: Uint8Array
  data: Uint8Array
  crc: number
  isDir: boolean
}

/**
 * Pack entries into a STORE-method zip.
 *
 * Entry order is preserved: the central directory lists them in the same order
 * the local headers appear, which is what a listing shows.
 */
export function buildZip(entries: ZipEntry[], now: Date = new Date()): Uint8Array {
  const encoder = new TextEncoder()
  const { time, date } = dosDateTime(now)

  const prepared: Prepared[] = entries.map((entry) => {
    const isDir = entry.name.endsWith('/')
    const data = isDir ? new Uint8Array(0) : encoder.encode(entry.text)
    return {
      nameBytes: encoder.encode(entry.name),
      data,
      crc: crc32(data),
      isDir,
    }
  })

  let localSize = 0
  let centralSize = 0
  for (const p of prepared) {
    localSize += 30 + p.nameBytes.length + p.data.length
    centralSize += 46 + p.nameBytes.length
  }

  const out = new Uint8Array(localSize + centralSize + 22)
  const view = new DataView(out.buffer)
  let offset = 0
  const offsets: number[] = []

  for (const p of prepared) {
    offsets.push(offset)
    view.setUint32(offset, 0x04034b50, true)
    view.setUint16(offset + 4, 20, true) // version needed: 2.0
    // Bit 11 is the UTF-8 name flag. Set unconditionally rather than only for
    // non-ASCII names: the alternative is a name field whose encoding a reader
    // has to guess from the locale it happens to run under.
    view.setUint16(offset + 6, 0x0800, true)
    view.setUint16(offset + 8, 0, true) // method 0 = STORE
    view.setUint16(offset + 10, time, true)
    view.setUint16(offset + 12, date, true)
    view.setUint32(offset + 14, p.crc, true)
    view.setUint32(offset + 18, p.data.length, true)
    view.setUint32(offset + 22, p.data.length, true)
    view.setUint16(offset + 26, p.nameBytes.length, true)
    view.setUint16(offset + 28, 0, true) // no extra field
    out.set(p.nameBytes, offset + 30)
    out.set(p.data, offset + 30 + p.nameBytes.length)
    offset += 30 + p.nameBytes.length + p.data.length
  }

  const centralStart = offset
  for (let i = 0; i < prepared.length; i++) {
    const p = prepared[i] as Prepared
    view.setUint32(offset, 0x02014b50, true)
    view.setUint16(offset + 4, 20, true) // version made by
    view.setUint16(offset + 6, 20, true) // version needed
    view.setUint16(offset + 8, 0x0800, true)
    view.setUint16(offset + 10, 0, true)
    view.setUint16(offset + 12, time, true)
    view.setUint16(offset + 14, date, true)
    view.setUint32(offset + 16, p.crc, true)
    view.setUint32(offset + 20, p.data.length, true)
    view.setUint32(offset + 24, p.data.length, true)
    view.setUint16(offset + 28, p.nameBytes.length, true)
    view.setUint16(offset + 30, 0, true) // extra
    view.setUint16(offset + 32, 0, true) // comment
    view.setUint16(offset + 34, 0, true) // disk number
    view.setUint16(offset + 36, 0, true) // internal attributes
    // The directory bit, which is what makes a zero-length `foo/` entry a
    // FOLDER rather than an empty file of that name.
    view.setUint32(offset + 38, p.isDir ? 0x10 : 0, true)
    view.setUint32(offset + 42, offsets[i] as number, true)
    out.set(p.nameBytes, offset + 46)
    offset += 46 + p.nameBytes.length
  }

  view.setUint32(offset, 0x06054b50, true)
  view.setUint16(offset + 4, 0, true) // this disk
  view.setUint16(offset + 6, 0, true) // disk with central directory
  view.setUint16(offset + 8, prepared.length, true)
  view.setUint16(offset + 10, prepared.length, true)
  view.setUint32(offset + 12, offset - centralStart, true)
  view.setUint32(offset + 16, centralStart, true)
  view.setUint16(offset + 20, 0, true) // no archive comment

  return out
}

export interface ZipListing {
  name: string
  size: number
  crc: number
  text: string
}

/**
 * Read a STORE zip back, from its central directory.
 *
 * Here so the tests check the archive the way a reader does — through the
 * central directory and its offsets — instead of re-deriving the same layout
 * the writer just used and agreeing with itself. It is also what the browser
 * verification uses to look inside a downloaded package.
 */
export function readZip(bytes: Uint8Array): ZipListing[] {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  let eocd = -1
  for (let i = bytes.length - 22; i >= 0; i--) {
    if (view.getUint32(i, true) === 0x06054b50) {
      eocd = i
      break
    }
  }
  if (eocd < 0) throw new Error('not a zip: no end-of-central-directory record')
  const count = view.getUint16(eocd + 10, true)
  let offset = view.getUint32(eocd + 16, true)

  const decoder = new TextDecoder()
  const out: ZipListing[] = []
  for (let i = 0; i < count; i++) {
    if (view.getUint32(offset, true) !== 0x02014b50) {
      throw new Error(`central directory entry ${i} has a bad signature`)
    }
    const crc = view.getUint32(offset + 16, true)
    const size = view.getUint32(offset + 24, true)
    const nameLen = view.getUint16(offset + 28, true)
    const extraLen = view.getUint16(offset + 30, true)
    const commentLen = view.getUint16(offset + 32, true)
    const local = view.getUint32(offset + 42, true)
    const name = decoder.decode(bytes.subarray(offset + 46, offset + 46 + nameLen))

    if (view.getUint32(local, true) !== 0x04034b50) {
      throw new Error(`local header for ${name} has a bad signature`)
    }
    const localNameLen = view.getUint16(local + 26, true)
    const localExtraLen = view.getUint16(local + 28, true)
    const start = local + 30 + localNameLen + localExtraLen
    const data = bytes.subarray(start, start + size)
    out.push({ name, size, crc, text: decoder.decode(data) })

    offset += 46 + nameLen + extraLen + commentLen
  }
  return out
}
