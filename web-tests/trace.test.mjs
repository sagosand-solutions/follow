import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { stdoutAt, variableRows, pickTrace, sha256Hex, clampStep, bindCase, isNextLine } from '../public/js/trace.js';

const dir = new URL('../public/data/traces/', import.meta.url).pathname;
const qdir = new URL('../public/data/questions/', import.meta.url).pathname;

test('variableRows marks new and changed values only inside the same frame', () => {
  const a = { event: 'line', depth: 1, function: 'f', locals: { x: '1' } };
  const b = { event: 'line', depth: 1, function: 'f', locals: { x: '2', y: '5' } };
  assert.deepEqual(variableRows(a, b), [{ name: 'x', value: '2', change: 'changed' }, { name: 'y', value: '5', change: 'new' }]);
  const c = { depth: 2, function: 'g', locals: { x: '9' } };
  assert.equal(variableRows(b, c)[0].change, 'same');
  const call = { event: 'call', depth: 1, function: 'f', locals: { x: '1' } };
  const line = { event: 'line', depth: 1, function: 'f', locals: { x: '2' } };
  assert.equal(variableRows(call, line)[0].change, 'same', 'no comparison across a call event');
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

test('stdoutAt counts Unicode code points like Python len, not UTF-16 units', () => {
  const trace = { stdout: '😀x\n' };
  assert.equal(stdoutAt(trace, { stdout_len: 1 }), '😀');
  assert.equal(stdoutAt(trace, { stdout_len: 2 }), '😀x');
  assert.equal(stdoutAt(trace, { stdout_len: 3 }), '😀x\n');
  assert.equal(stdoutAt(trace, { stdout_len: 0 }), '');
});

test('only line events name the next line', () => {
  assert.equal(isNextLine({ event: 'line' }), true);
  for (const event of ['call', 'return', 'exception']) assert.equal(isNextLine({ event }), false);
});

test('bindCase fails closed unless snippet, case index and recorded stdout agree', () => {
  const q = { verification: { cases: [
    { id: 'a', snippet_id: 'other', stdin: 'zz\n', expected: { stdout: 'o\n' } },
    { id: 'b', snippet_id: 'main', stdin: '-1\n', expected: { stdout: 'green\n' } },
    { id: 'c', snippet_id: 'main', stdin: '3\n', expected: { stdout: '' } } ] } };
  const ok = { snippet_id: 'main', stdin_case: 1, stdout: 'green\n' };
  assert.deepEqual(bindCase(q, 'main', ok), { stdin: '-1\n', total: 2 });
  assert.equal(bindCase(q, 'main', { ...ok, stdin_case: 2 }), null, 'not the first case of the snippet');
  assert.equal(bindCase(q, 'main', { ...ok, stdin_case: 0 }), null, 'case belongs to another snippet');
  assert.equal(bindCase(q, 'main', { ...ok, stdout: 'red\n' }), null, 'recorded stdout differs from the case');
  assert.equal(bindCase(q, 'other', ok), null, 'trace is for another snippet');
  assert.equal(bindCase({ verification: { cases: [] } }, 'main', { snippet_id: 'main', stdin_case: 0, stdout: '' })?.total, 0);
  assert.equal(bindCase({}, 'main', { snippet_id: 'main', stdin_case: 1, stdout: '' }), null);
});

test('every shipped trace binds to its question case', () => {
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.json'))) {
    const file = JSON.parse(readFileSync(dir + f, 'utf8'));
    const q = JSON.parse(readFileSync(qdir + f, 'utf8'));
    assert.equal(file.question_id, q.id);
    for (const t of file.traces) assert.ok(bindCase(q, t.snippet_id, t), `${f}: ${t.snippet_id}`);
  }
});

test('the corpus has the three ending kinds the player must show honestly', () => {
  const kinds = new Set();
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.json'))) {
    for (const t of JSON.parse(readFileSync(dir + f, 'utf8')).traces) {
      if (t.truncated) kinds.add('truncated');
      if (t.exception) kinds.add('escaping');
      if (t.steps.some((s) => s.event === 'exception')) kinds.add('event');
    }
  }
  assert.ok(kinds.has('escaping') && kinds.has('event'), [...kinds].join());
});
