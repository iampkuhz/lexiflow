import { deflateRawSync } from 'node:zlib';

const crc32 = (buf) => {
  let c = 0xffffffff;
  for (const b of buf) { c ^= b; for (let i = 0; i < 8; i++) c = (c >>> 1) ^ ((c & 1) ? 0xedb88320 : 0); }
  return (c ^ 0xffffffff) >>> 0;
};
const dosDate = 0x0021, dosTime = 0;

export function makeZip(entries, { deflate = false, dataDescriptor = false } = {}) {
  const locals = [], centrals = [];
  let offset = 0;
  for (const [nameValue, value] of entries) {
    const name = Buffer.from(nameValue), raw = Buffer.isBuffer(value) ? value : Buffer.from(value);
    const body = deflate ? deflateRawSync(raw) : raw;
    const crc = crc32(raw), flags = dataDescriptor ? 8 : 0, method = deflate ? 8 : 0;
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt16LE(flags, 6); local.writeUInt16LE(method, 8);
    local.writeUInt16LE(dosTime, 10); local.writeUInt16LE(dosDate, 12);
    if (!dataDescriptor) { local.writeUInt32LE(crc, 14); local.writeUInt32LE(body.length, 18); local.writeUInt32LE(raw.length, 22); }
    local.writeUInt16LE(name.length, 26);
    locals.push(local, name, body);
    if (dataDescriptor) { const dd = Buffer.alloc(16); dd.writeUInt32LE(0x08074b50, 0); dd.writeUInt32LE(crc, 4); dd.writeUInt32LE(body.length, 8); dd.writeUInt32LE(raw.length, 12); locals.push(dd); }
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(20, 4); central.writeUInt16LE(20, 6); central.writeUInt16LE(flags, 8);
    central.writeUInt16LE(method, 10); central.writeUInt16LE(dosTime, 12); central.writeUInt16LE(dosDate, 14);
    central.writeUInt32LE(crc, 16); central.writeUInt32LE(body.length, 20); central.writeUInt32LE(raw.length, 24); central.writeUInt16LE(name.length, 28); central.writeUInt32LE(offset, 42);
    centrals.push(central, name);
    offset += local.length + name.length + body.length + (dataDescriptor ? 16 : 0);
  }
  const cd = Buffer.concat(centrals), cdOffset = offset;
  const end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10); end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(cdOffset, 16);
  return Buffer.concat([...locals, cd, end]);
}

export function jarBytes(identity) {
  return makeZip([
    ['META-INF/lexiflow-build.json', `${JSON.stringify(identity)}\n`],
    ['META-INF/lexiflow-version.txt', `${identity.softwareVersion}\n`],
  ]);
}

export function extensionBytes(identity) {
  const build = `${JSON.stringify(identity)}\n`;
  const manifest = `${JSON.stringify({ manifest_version: 3, version: identity.chromeVersion, version_name: identity.softwareVersion, permissions: ['storage'], host_permissions: ['http://127.0.0.1:18080/*'] })}\n`;
  return makeZip([['build-identity.json', build], ['manifest.json', manifest]]);
}
