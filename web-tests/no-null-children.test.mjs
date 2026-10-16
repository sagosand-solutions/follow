import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('../public/js/', import.meta.url).pathname;
const walk = (d) => readdirSync(d).flatMap((n) => (statSync(join(d, n)).isDirectory() ? walk(join(d, n)) : [join(d, n)]));

// Node and Element replaceChildren/append turn null into the text "null". The h() helper skips null, the DOM methods do not.
test('no replaceChildren/append call gets a conditional null argument', () => {
  for (const f of walk(root).filter((p) => p.endsWith('.js'))) {
    const src = readFileSync(f, 'utf8');
    for (const m of src.matchAll(/\.(replaceChildren|append|prepend)\(([^;]*)\);/g)) {
      assert.doesNotMatch(m[2], /\?[^?:]*:\s*null\b/, `${f}: ${m[0].slice(0, 100)}`);
    }
  }
});
