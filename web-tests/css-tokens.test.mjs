import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const dir = new URL('../public/css/', import.meta.url).pathname;
const files = readdirSync(dir).filter((f) => f.endsWith('.css'));
const all = files.map((f) => readFileSync(join(dir, f), 'utf8')).join('\n');
// Properties declared anywhere (tokens, or locally on a component such as --ln-w).
const declared = new Set([...all.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));

test('every var(--x) used in the stylesheets is declared somewhere', () => {
  const missing = [...new Set([...all.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]))].filter((n) => !declared.has(n));
  assert.deepEqual(missing, []);
});

test('components use tokens, not literal colours', () => {
  for (const f of ['base.css', 'components.css']) {
    const src = readFileSync(join(dir, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    assert.doesNotMatch(src, /#[0-9a-fA-F]{3,8}\b/, `${f} has a hex colour`);
  }
});
