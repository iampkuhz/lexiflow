import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const helper = path.join(path.dirname(fileURLToPath(import.meta.url)), 'release_host_identity.py');
const exactKeys = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join('\0') === ['scheme', 'sha256'].sort().join('\0');

export function resolveHostBinding() {
  let output;
  try {
    output = execFileSync('python3', ['-I', helper, '--json'], {
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 10_000, maxBuffer: 4096,
    });
  } catch {
    throw new Error('HOST_IDENTITY_RESOLUTION_FAILED');
  }
  let binding;
  try { binding = JSON.parse(output); } catch { throw new Error('HOST_IDENTITY_RESOLUTION_FAILED'); }
  if (!exactKeys(binding) || binding.scheme !== 'lexiflow.host-binding.v1'
    || typeof binding.sha256 !== 'string' || !/^[a-f0-9]{64}$/u.test(binding.sha256))
    throw new Error('HOST_IDENTITY_RESOLUTION_FAILED');
  return binding;
}
