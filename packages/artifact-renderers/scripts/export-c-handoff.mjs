import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const files = ['apps/web/src/Studio.tsx', 'apps/web/src/PreviewDock.tsx', 'apps/web/package.json',
  'apps/web/package-lock.json', 'apps/web/vite.config.ts', 'apps/web/README.md'];
const patch = execFileSync('git', ['diff', '--binary', '--', ...files], { cwd: root, encoding: 'utf8' })
  .replace(/^ \r?$/gm, '');
const directory = fileURLToPath(new URL('../handoff/', import.meta.url));
mkdirSync(directory, { recursive: true });
writeFileSync(`${directory}/c-integration.patch`, patch);
console.log(JSON.stringify({ files, bytes: Buffer.byteLength(patch) }));
