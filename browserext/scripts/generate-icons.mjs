import { mkdir, writeFile } from 'node:fs/promises';
import { deflateSync } from 'node:zlib';

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let i = 0; i < 8; i += 1) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
  return c >>> 0;
});

function crc32(buffer) {
  let c = 0xffffffff;
  for (const byte of buffer) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data = Buffer.alloc(0)) {
  const name = Buffer.from(type, 'ascii');
  const out = Buffer.alloc(12 + data.length);
  out.writeUInt32BE(data.length, 0);
  name.copy(out, 4);
  data.copy(out, 8);
  out.writeUInt32BE(crc32(Buffer.concat([name, data])), 8 + data.length);
  return out;
}

function renderRgba(size) {
  const samples = 4;
  const hi = size * samples;
  const high = new Uint8Array(hi * hi * 4);
  for (let y = 0; y < hi; y += 1) {
    for (let x = 0; x < hi; x += 1) {
      const nx = (x + 0.5) / hi;
      const ny = (y + 0.5) / hi;
      const circle = ((nx - 0.5) ** 2 + (ny - 0.5) ** 2) <= 0.47 ** 2;
      const outer = ((nx - 0.45) ** 2 + (ny - 0.58) ** 2) <= 0.205 ** 2;
      const inner = ((nx - 0.45) ** 2 + (ny - 0.58) ** 2) <= 0.115 ** 2;
      const glyph = (nx >= 0.57 && nx <= 0.67 && ny >= 0.21 && ny <= 0.76)
        || (outer && !inner);
      const i = (y * hi + x) * 4;
      if (circle) {
        high[i] = glyph ? 255 : 37;
        high[i + 1] = glyph ? 255 : 99;
        high[i + 2] = glyph ? 255 : 235;
        high[i + 3] = 255;
      }
    }
  }

  const rgba = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const sum = [0, 0, 0, 0];
      for (let sy = 0; sy < samples; sy += 1) {
        for (let sx = 0; sx < samples; sx += 1) {
          const i = (((y * samples + sy) * hi) + (x * samples + sx)) * 4;
          for (let c = 0; c < 4; c += 1) sum[c] += high[i + c];
        }
      }
      const o = (y * size + x) * 4;
      for (let c = 0; c < 4; c += 1) rgba[o + c] = Math.round(sum[c] / (samples * samples));
    }
  }
  return rgba;
}

function encodePng(size) {
  const rgba = renderRgba(size);
  const rows = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y += 1) {
    const row = y * (size * 4 + 1);
    rows[row] = 0;
    rgba.copy(rows, row + 1, y * size * 4, (y + 1) * size * 4);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8;
  header[9] = 6;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(rows)),
    chunk('IEND'),
  ]);
}

export async function generateIcons(outputDir) {
  await mkdir(outputDir, { recursive: true });
  await Promise.all([16, 32, 48, 128].map((size) =>
    writeFile(new URL(`dext${size}.png`, outputDir), encodePng(size))));
}
