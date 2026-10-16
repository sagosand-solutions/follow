import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { stdoutAt, variableRows, pickTrace, sha256Hex, clampStep, bindCase, stdinMatches, isNextLine } from '../public/js/trace.js';

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
  for (const event of ['call', 'resume', 'yield', 'return', 'unwind', 'exception']) assert.equal(isNextLine({ event }), false);
});

test('bindCase (0.2) fails closed unless version, snippet, case id, stdin and stdout agree', () => {
  const q = { id: 'q-one', verification: { cases: [
    { id: 'other-case', snippet_id: 'other', stdin: 'zz\n', expected: { stdout: 'o\n' } },
    { id: 'minus-one', snippet_id: 'main', stdin: '-1\n', expected: { stdout: 'green\n' } },
    { id: 'three', snippet_id: 'main', stdin: '3\n', expected: { stdout: '' } } ] } };
  const trace = { snippet_id: 'main', case_id: 'minus-one', stdin: '-1\n', stdout: 'green\n' };
  const file = { schema_version: '0.2', question_id: 'q-one' };
  assert.deepEqual(bindCase(q, 'main', trace, file), { stdin: '-1\n', total: 2 });
  assert.equal(bindCase(q, 'main', trace, { ...file, schema_version: '0.1' }), null, 'old format is rejected');
  assert.equal(bindCase(q, 'main', trace, { ...file, question_id: 'q-two' }), null);
  assert.equal(bindCase(q, 'main', { ...trace, case_id: 'three' }, file), null, 'not the first case of the snippet');
  assert.equal(bindCase(q, 'main', { ...trace, case_id: 'other-case' }, file), null, 'case belongs to another snippet');
  assert.equal(bindCase(q, 'main', { ...trace, stdin: '-2\n' }, file), null, 'stdin text differs from the case');
  assert.equal(bindCase(q, 'main', { ...trace, stdout: 'red\n' }, file), null, 'recorded stdout differs from the case');
  assert.equal(bindCase(q, 'other', trace, file), null, 'trace is for another snippet');
  assert.equal(bindCase({ id: 'q-one' }, 'main', trace, file), null, 'no case: fail closed');
});

test('stdinMatches compares the SHA-256 of stdin', async () => {
  const stdin = 'héllo\n';
  const good = createHash('sha256').update(stdin).digest('hex');
  assert.equal(await stdinMatches({ stdin, stdin_sha256: good }), true);
  assert.equal(await stdinMatches({ stdin, stdin_sha256: good.replace(/^./, 'f') }), false);
  assert.equal(await stdinMatches({ stdin, stdin_sha256: good }, async () => null), false, 'no WebCrypto: fail closed');
});

test('every shipped 0.2 trace binds to its question case and its stdin hash', async () => {
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.json'))) {
    const file = JSON.parse(readFileSync(dir + f, 'utf8'));
    const q = JSON.parse(readFileSync(qdir + f, 'utf8'));
    assert.equal(file.schema_version, '0.2', f);
    for (const t of file.traces) {
      assert.ok(bindCase(q, t.snippet_id, t, file), `${f}: ${t.snippet_id}`);
      assert.equal(await stdinMatches(t), true, `${f}: stdin hash`);
      if (t.truncated) assert.equal(t.truncated_at_step, t.steps.length, f);
    }
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
