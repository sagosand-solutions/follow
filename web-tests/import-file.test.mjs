import test from 'node:test';
import assert from 'node:assert/strict';
import { readImportFile } from '../public/js/import-file.js';
import { MAX_IMPORT_BYTES } from '../public/js/store.js';

test('a file over the byte limit is refused before text() is ever read', async () => {
  let called = false;
  const file = { size: MAX_IMPORT_BYTES + 1, text: async () => { called = true; return 'x'; } };
  assert.deepEqual(await readImportFile(file), { ok: false, error: 'too-large' });
  assert.equal(called, false, 'text() must not be called for an over-limit file');
});

test('exactly the limit is allowed through to the normal read', async () => {
  assert.deepEqual(await readImportFile({ size: MAX_IMPORT_BYTES, text: async () => 'ok' }), { ok: true, text: 'ok' });
});

test('a read failure is a calm error, never a thrown exception', async () => {
  const file = { size: 10, text: async () => { throw new Error('disk on fire'); } };
  assert.deepEqual(await readImportFile(file), { ok: false, error: 'unreadable' });
});

test('a missing or shapeless file is refused as invalid', async () => {
  for (const bad of [null, undefined, {}, { size: 'x' }, 7]) {
    assert.deepEqual(await readImportFile(bad), { ok: false, error: 'invalid-format' }, String(bad));
  }
});

test('the import limit is exactly 1 MiB, as the user-facing message promises', () => {
  assert.equal(MAX_IMPORT_BYTES, 1_048_576);
});

test('course.js reads through readImportFile before touching the store, never file.text() directly', async () => {
  const { readFileSync } = await import('node:fs');
  const source = readFileSync(new URL('../public/js/views/course.js', import.meta.url), 'utf8');
  assert.match(source, /readImportFile\(file\)/);
  assert.match(source, /store\.importJson\(read\.text\)/);
  assert.doesNotMatch(source, /file\.text\(\)/);
  assert.doesNotMatch(source, /store\.importJson\(await /);
});
