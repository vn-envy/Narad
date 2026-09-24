// The install icons, checked the way Android uses them: `npm test`.
// A maskable icon is cropped to a circle or squircle by the launcher, so
// everything that is not background must sit inside the central safe zone
// (a circle of 40% of the width around the centre), and the background must
// reach every edge. Decodes the PNGs with node:zlib, no dependencies.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { inflateSync } from 'node:zlib'

const publicDir = new URL('../public/', import.meta.url)
const manifest = JSON.parse(readFileSync(new URL('manifest.webmanifest', publicDir), 'utf8'))

/** RGB(A) pixels of an 8-bit, non-interlaced PNG. */
function decodePng(buffer) {
  assert.equal(buffer.readUInt32BE(0), 0x89504e47, 'not a PNG')
  let offset = 8
  let width = 0
  let height = 0
  let channels = 0
  const data = []
  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset)
    const type = buffer.toString('ascii', offset + 4, offset + 8)
    const body = buffer.subarray(offset + 8, offset + 8 + length)
    if (type === 'IHDR') {
      width = body.readUInt32BE(0)
      height = body.readUInt32BE(4)
      const [depth, colour, , , interlace] = body.subarray(8, 13)
      assert.equal(depth, 8, 'only 8-bit icons are checked')
      assert.equal(interlace, 0, 'only non-interlaced icons are checked')
      channels = { 2: 3, 6: 4 }[colour]
      assert.ok(channels, `colour type ${colour} is not RGB or RGBA`)
    } else if (type === 'IDAT') {
      data.push(body)
    }
    offset += 12 + length
  }
  const raw = inflateSync(Buffer.concat(data))
  const stride = width * channels
  const pixels = Buffer.alloc(height * stride)
  for (let y = 0; y < height; y += 1) {
    const filter = raw[y * (stride + 1)]
    for (let x = 0; x < stride; x += 1) {
      const value = raw[y * (stride + 1) + 1 + x]
      const left = x >= channels ? pixels[y * stride + x - channels] : 0
      const up = y > 0 ? pixels[(y - 1) * stride + x] : 0
      const upLeft = x >= channels && y > 0 ? pixels[(y - 1) * stride + x - channels] : 0
      let predicted = 0
      if (filter === 1) predicted = left
      else if (filter === 2) predicted = up
      else if (filter === 3) predicted = (left + up) >> 1
      else if (filter === 4) {
        const p = left + up - upLeft
        const [pa, pb, pc] = [Math.abs(p - left), Math.abs(p - up), Math.abs(p - upLeft)]
        predicted = pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft
      }
      pixels[y * stride + x] = (value + predicted) & 0xff
    }
  }
  return { width, height, channels, pixel: (x, y) => [...pixels.subarray(y * stride + x * channels, y * stride + x * channels + 3)] }
}

const near = (a, b, tolerance = 24) => a.every((value, index) => Math.abs(value - b[index]) <= tolerance)

test('the manifest offers 192 and 512 px icons and a separate maskable one', () => {
  const sizes = manifest.icons.filter(icon => !icon.purpose || icon.purpose === 'any').map(icon => icon.sizes)
  assert.ok(sizes.includes('192x192') && sizes.includes('512x512'), 'needs 192 and 512 px "any" icons')
  const maskable = manifest.icons.filter(icon => String(icon.purpose || '').split(' ').includes('maskable'))
  assert.equal(maskable.length, 1)
  assert.equal(maskable[0].sizes, '512x512')
  // A maskable icon must not double as the plain one: it would show as a small mark on a big square.
  assert.ok(!String(maskable[0].purpose).includes('any'))
})

for (const icon of manifest.icons) {
  test(`${icon.src} is ${icon.sizes}`, () => {
    const { width, height } = decodePng(readFileSync(new URL(`.${icon.src}`, publicDir)))
    assert.equal(`${width}x${height}`, icon.sizes)
  })
}

test('the maskable icon keeps its mark inside the safe zone, on a full-bleed background', () => {
  const icon = manifest.icons.find(item => String(item.purpose || '').includes('maskable'))
  const { width, height, pixel } = decodePng(readFileSync(new URL(`.${icon.src}`, publicDir)))
  const background = pixel(0, 0)
  // Every edge is background, so no launcher shape shows a hard corner or a gap.
  for (let i = 0; i < width; i += 8) {
    for (const [x, y] of [[i, 0], [i, height - 1], [0, i], [width - 1, i]]) {
      assert.ok(near(pixel(x, y), background), `edge pixel ${x},${y} is not background`)
    }
  }
  // Nothing but background outside the 40% radius circle.
  const radius = width * 0.4
  let outside = 0
  let mark = 0
  for (let y = 0; y < height; y += 2) {
    for (let x = 0; x < width; x += 2) {
      if (near(pixel(x, y), background)) continue
      mark += 1
      if (Math.hypot(x - width / 2, y - height / 2) > radius) outside += 1
    }
  }
  assert.ok(mark > 0, 'the icon has no mark at all')
  assert.equal(outside, 0, `${outside} sampled pixels of the mark fall outside the safe zone`)
})

test('the manifest colours match the app chrome', () => {
  assert.equal(manifest.display, 'standalone')
  assert.match(manifest.theme_color, /^#[0-9a-f]{6}$/i)
  assert.match(manifest.background_color, /^#[0-9a-f]{6}$/i)
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
  assert.ok(html.includes(`content="${manifest.theme_color}" media="(prefers-color-scheme: light)"`), 'light theme-color differs from the manifest')
  assert.ok(html.includes('media="(prefers-color-scheme: dark)"'), 'no dark theme-color')
  assert.ok(!/fonts\.(googleapis|gstatic)\.com/.test(html), 'index.html still reaches for Google Fonts')
})
