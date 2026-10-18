import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { compile } from 'json-schema-to-typescript';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const schemaPath = 'packages/schema/src/aica_schema/aica.schema.json';
const revision = 'f0fc2942d517c793447140d93670cf5428164a83';
const local = resolve(root, schemaPath);
const schema = JSON.parse(existsSync(local) ? readFileSync(local, 'utf8')
  : execFileSync('git', ['show', `${revision}:${schemaPath}`], { cwd: root, encoding: 'utf8' }));
const generated = await compile({ ...schema, title: 'AicaArchive' }, 'AicaArchive', {
  bannerComment: `/* Generated from A's ${schemaPath}. Git fallback: ${revision}. Run npm run generate:contract. */`,
  additionalProperties: true,
});
writeFileSync(fileURLToPath(new URL('../src/schema.generated.ts', import.meta.url)), generated);
