import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = new URL('../public/', import.meta.url).pathname;
const html = readFileSync(join(root, 'index.html'), 'utf8');

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const importMap = JSON.parse(/<script type="importmap">([\s\S]*?)<\/script>/.exec(html)[1]).imports;
const versions = [...html.matchAll(/\?v=([\w.-]+)/g)].map((m) => m[1]);

test('every ?v= in index.html is the same release', () => {
  assert.ok(versions.length > 0);
  assert.equal(new Set(versions).size, 1, `found ${[...new Set(versions)].join(', ')}`);
});

test('the import map covers every app module except the entry, with the release version', () => {
  const [release] = versions;
  const modules = walk(join(root, 'js'))
    .map((f) => `./${relative(root, f)}`)
    .filter((f) => f.endsWith('.js') && f !== './js/main.js')
    .sort();
  assert.deepEqual(Object.keys(importMap).sort(), modules);
  for (const [key, value] of Object.entries(importMap)) assert.equal(value, `${key}?v=${release}`);
});

test('entry script and every stylesheet link carry the release', () => {
  const [release] = versions;
  assert.match(html, new RegExp(`src="js/main\\.js\\?v=${release}"`));
  const sheets = [...html.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(sheets.length >= 3);
  for (const href of sheets) assert.ok(href.endsWith(`?v=${release}`), href);
});

test('data and translation fetches are versioned from the module URL', () => {
  for (const file of ['js/data.js', 'js/i18n.js']) {
    assert.match(readFileSync(join(root, file), 'utf8'), /release/, file);
  }
  assert.match(readFileSync(join(root, 'js/config.js'), 'utf8'), /import\.meta\.url/);
});

test('no module imports another app module by an absolute path', () => {
  for (const f of walk(join(root, 'js')).filter((p) => p.endsWith('.js'))) {
    assert.doesNotMatch(readFileSync(f, 'utf8'), /from '\/|import\('\//, f);
  }
});
