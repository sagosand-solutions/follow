import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { stdoutAt, variableRows, pickTrace, sha256Hex, clampStep } from '../public/js/trace.js';

const dir = new URL('../public/data/traces/', import.meta.url).pathname;
const qdir = new URL('../public/data/questions/', import.meta.url).pathname;

test('variableRows marks new and changed values only inside the same frame', () => {
  const a = { depth: 1, function: 'f', locals: { x: '1' } };
  const b = { depth: 1, function: 'f', locals: { x: '2', y: '5' } };
  assert.deepEqual(variableRows(a, b), [{ name: 'x', value: '2', change: 'changed' }, { name: 'y', value: '5', change: 'new' }]);
  const c = { depth: 2, function: 'g', locals: { x: '9' } };
  assert.equal(variableRows(b, c)[0].change, 'same');
  assert.equal(variableRows(null, b)[0].change, 'same');
});

test('stdoutAt, clampStep and pickTrace', () => {
  assert.equal(stdoutAt({ stdout: 'abc\n' }, { stdout_len: 2 }), 'ab');
  assert.equal(clampStep(-3, 5), 0);
  assert.equal(clampStep(9, 5), 4);
  assert.equal(clampStep(1, 0), 0);
  assert.equal(pickTrace({ traces: [{ snippet_id: 'main' }] }, 'main').snippet_id, 'main');
  assert.equal(pickTrace({ traces: [] }, 'main'), null);
  assert.equal(pickTrace(null, 'main'), null);
});

test('sha256Hex matches node:crypto and degrades without WebCrypto', async () => {
  assert.equal(await sha256Hex('héllo\n'), createHash('sha256').update('héllo\n').digest('hex'));
  assert.equal(await sha256Hex('x', null), null);
});

test('every shipped trace matches its question snippet by SHA-256 and replays consistently', async () => {
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.json'))) {
    const file = JSON.parse(readFileSync(dir + f, 'utf8'));
    const q = JSON.parse(readFileSync(qdir + f, 'utf8'));
    for (const t of file.traces) {
      const snip = q.snippets.find((s) => s.id === t.snippet_id);
      assert.ok(snip, `${f}: snippet ${t.snippet_id}`);
      assert.equal(await sha256Hex(snip.code), t.source_sha256, f);
      let last = 0;
      for (const s of t.steps) {
        assert.ok(s.stdout_len >= last && s.stdout_len <= t.stdout.length, `${f}: stdout_len`);
        assert.ok(s.line >= 1 && s.line <= snip.code.split('\n').length, `${f}: line ${s.line}`);
        last = s.stdout_len;
      }
    }
  }
});
