/**
 * A minimal RGBA PNG encoder for small, generated textures in the render snapshot.
 *
 * The snapshot normally gets PNGs from a canvas, but a texture it has to
 * SYNTHESISE (the perforated rack door's hole pattern, drawn by a shader in
 * the viewport) must also be producible where there is no 2D canvas — jsdom —
 * and must be byte-identical everywhere so the Kit side's content-addressed
 * texture cache hits. Deflate uses stored (uncompressed) blocks: the images
 * this is for are a few kilobytes, and a valid zlib stream is all a PNG
 * reader asks for.
 */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

export function crc32(bytes: Uint8Array, start = 0, end = bytes.length): number {
  let c = 0xffffffff
  for (let i = start; i < end; i++) c = CRC_TABLE[(c ^ bytes[i]!) & 0xff]! ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

function adler32(bytes: Uint8Array): number {
  let a = 1
  let b = 0
  for (let i = 0; i < bytes.length; i++) {
    a = (a + bytes[i]!) % 65521
    b = (b + a) % 65521
  }
  return ((b << 16) | a) >>> 0
}

function zlibStored(raw: Uint8Array): Uint8Array {
  const blocks = Math.max(1, Math.ceil(raw.length / 65535))
  const out = new Uint8Array(2 + raw.length + blocks * 5 + 4)
  let o = 0
  out[o++] = 0x78 // CMF: deflate, 32 K window
  out[o++] = 0x01 // FLG: no dictionary, fastest; (0x7801 % 31 === 0)
  for (let i = 0; i < blocks; i++) {
    const start = i * 65535
    const len = Math.min(65535, raw.length - start)
    out[o++] = i === blocks - 1 ? 1 : 0 // BFINAL, BTYPE = 00 (stored)
    out[o++] = len & 0xff
    out[o++] = (len >>> 8) & 0xff
    out[o++] = ~len & 0xff
    out[o++] = (~len >>> 8) & 0xff
    out.set(raw.subarray(start, start + len), o)
    o += len
  }
  const adler = adler32(raw)
  out[o++] = (adler >>> 24) & 0xff
  out[o++] = (adler >>> 16) & 0xff
  out[o++] = (adler >>> 8) & 0xff
  out[o++] = adler & 0xff
  return out
}

function chunk(type: string, data: Uint8Array): Uint8Array {
  const out = new Uint8Array(12 + data.length)
  const view = new DataView(out.buffer)
  view.setUint32(0, data.length)
  for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i)
  out.set(data, 8)
  view.setUint32(8 + data.length, crc32(out, 4, 8 + data.length))
  return out
}

/** Encode `width` x `height` RGBA8 pixels (row 0 = top) as PNG bytes. */
export function encodePngRgba(width: number, height: number, rgba: Uint8Array): Uint8Array {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1) {
    throw new Error('PNG size must be positive integers')
  }
  if (rgba.length !== width * height * 4) throw new Error('RGBA buffer does not match the PNG size')
  const ihdr = new Uint8Array(13)
  const view = new DataView(ihdr.buffer)
  view.setUint32(0, width)
  view.setUint32(4, height)
  ihdr[8] = 8 // bit depth
  ihdr[9] = 6 // colour type: RGBA
  // compression, filter, interlace: all 0
  const stride = width * 4
  const raw = new Uint8Array((stride + 1) * height)
  for (let y = 0; y < height; y++) {
    raw[y * (stride + 1)] = 0 // filter: none
    raw.set(rgba.subarray(y * stride, (y + 1) * stride), y * (stride + 1) + 1)
  }
  const parts = [
    new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlibStored(raw)),
    chunk('IEND', new Uint8Array(0)),
  ]
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
  let o = 0
  for (const p of parts) {
    out.set(p, o)
    o += p.length
  }
  return out
}

export function pngDataUrl(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  }
  return `data:image/png;base64,${btoa(binary)}`
}
