import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { stdoutAt, diffLocals, buildMoments, momentState, pickTrace, sha256Hex, clampStep, bindCase, stdinMatches, usableTraces, validEnvelope } from '../public/js/trace.js';

const dir = new URL('../public/data/traces/', import.meta.url).pathname;
const qdir = new URL('../public/data/questions/', import.meta.url).pathname;

test('diffLocals flags new and changed values and nothing without a before state', () => {
  assert.deepEqual(diffLocals({ x: '1' }, { x: '2', y: '5' }), [{ name: 'x', value: '2', change: 'changed' }, { name: 'y', value: '5', change: 'new' }]);
  assert.equal(diffLocals(null, { x: '1' })[0].change, 'same');
  assert.equal(diffLocals({ x: '1' }, { x: '1' })[0].change, 'same');
});

const S = (event, line, locals, extra = {}) => ({ event, line, depth: 0, function: '<module>', locals, stdout_len: 0, ...extra });

const D = (d, fn) => (event, line, locals, extra = {}) => ({ event, line, depth: d, function: fn, locals, stdout_len: 0, ...extra });
const M = D(0, '<module>');
const kinds = (m) => m.map((x) => `${x.kind}:${x.step.line}${x.step.event === 'line' ? '' : '/' + x.step.event}`);

test('buildMoments: plain lines are shown as ran with the state after, the empty start is skipped, the last print is kept', () => {
  const trace = { stdout: 'ab\n', steps: [M('call', 1, {}), M('line', 1, {}), M('line', 2, { x: '3' }), M('line', 3, { x: '3', y: '1' }), M('return', 3, { x: '3', y: '1' }, { stdout_len: 3, value: 'None' })] };
  const m = buildMoments(trace);
  assert.deepEqual(kinds(m), ['ran:1', 'ran:2', 'ran:3', 'event:3/return']);
  assert.deepEqual(momentState(trace, m[0]).rows, [{ name: 'x', value: '3', change: 'new' }], 'x = 3 after line 1, new');
  assert.equal(momentState(trace, m[2]).added, 'ab\n', 'output of the last line, from the terminal return step');
  assert.equal(momentState(trace, m[3]).mode, 'event');
});

test('buildMoments: a calling line is not "ran" before its callee is played; its result appears after the return', () => {
  const F = D(1, 'f');
  const trace = { stdout: '', steps: [M('call', 1, {}), M('line', 1, {}), M('line', 4, { f: '<f>' }), F('call', 1, { a: '2' }), F('line', 2, { a: '2' }), F('return', 2, { a: '2' }, { value: '2' }), M('line', 5, { f: '<f>', r: '2' }), M('return', 5, { f: '<f>', r: '2' })] };
  const m = buildMoments(trace);
  assert.deepEqual(kinds(m), ['ran:1', 'event:1/call', 'ran:2', 'event:2/return', 'ran:4', 'ran:5', 'event:5/return']);
  assert.equal(m[1].callerLine, 4, 'the call event says which line of the caller is calling');
  const callLine = m[4];
  assert.equal(callLine.safe, true, 'only deeper steps lay between, so the frame was never left');
});

test('buildMoments: changes are flagged only when the same frame is provable', () => {
  const F = D(1, 'f');
  const trace = { stdout: '', steps: [M('call', 1, {}), M('line', 4, { f: '<f>' }), F('call', 1, {}), F('return', 1, {}, { value: '2' }), M('line', 5, { f: '<f>', r: '2' }), M('return', 5, { f: '<f>', r: '2' })] };
  const ranCall = buildMoments(trace).find((x) => x.kind === 'ran' && x.step.line === 4);
  assert.equal(ranCall.safe, true, 'only deeper steps lay between: the frame was never left');
  assert.deepEqual(momentState(trace, ranCall).rows.find((r) => r.name === 'r'), { name: 'r', value: '2', change: 'new' });
});

test('buildMoments: yield is a pause, the assignment around it completes after the resume', () => {
  const G = D(1, 'gen');
  const trace = { stdout: '', steps: [M('call', 1, {}), M('line', 5, {}), G('call', 1, {}), G('line', 2, {}), G('yield', 2, {}, { value: "'ready'" }), M('line', 6, {}), G('resume', 2, {}), G('line', 3, { got: '3' }), G('return', 3, { got: '3' }, { value: 'None' }), M('return', 6, {})] };
  const m = buildMoments(trace);
  assert.deepEqual(kinds(m), ['event:1/call', 'event:2/yield', 'ran:5', 'event:2/resume', 'ran:3', 'event:3/return', 'ran:6', 'event:6/return']);
  assert.ok(!m.some((x) => x.kind === 'ran' && x.step.line === 2 && x.step.function === 'gen'), 'the line paused at the yield is never shown as ran');
});

test('buildMoments: a new frame at a depth never completes a paused generator line (yield g, then call h)', () => {
  const G = D(1, 'g');
  const Hh = D(1, 'h');
  const trace = { stdout: '', steps: [M('call', 1, {}), M('line', 10, {}), G('call', 1, {}), G('line', 2, { gl: 'g-local' }), G('yield', 2, { gl: 'g-local' }, { value: '1' }), M('line', 11, {}), Hh('call', 20, { arg: 'h-arg' }), Hh('line', 21, { arg: 'h-arg' }), Hh('return', 21, { arg: 'h-arg' }, { value: 'None' }), M('return', 11, {})] };
  const m = buildMoments(trace);
  assert.ok(!m.some((x) => x.kind === 'ran' && x.step.function === 'g'), 'g line 2 is never shown as ran: g is still paused');
  const hl = m.find((x) => x.kind === 'ran' && x.step.function === 'h');
  assert.deepEqual(momentState(trace, hl).rows.map((r) => r.name), ['arg'], 'h shows only its own locals');
});

test('buildMoments: two paused generator instances with the same name and depth are never guessed apart', () => {
  const G = D(1, 'gen');
  const trace = { stdout: '', steps: [M('call', 1, {}), M('line', 5, {}), G('call', 1, {}), G('line', 2, { id: 'first' }), G('yield', 2, { id: 'first' }, { value: '1' }), M('line', 6, {}), G('call', 1, {}), G('line', 2, { id: 'second' }), G('yield', 2, { id: 'second' }, { value: '2' }), M('line', 7, {}), G('resume', 2, { id: '?' }), G('line', 3, { id: '?' }), G('return', 3, { id: '?' }, { value: 'None' }), M('return', 7, {})] };
  const m = buildMoments(trace);
  const gen2 = m.filter((x) => x.kind === 'ran' && x.step.function === 'gen' && x.step.line === 2);
  assert.equal(gen2.length, 0, 'no completion is attributed to either paused instance');
  assert.ok(m.some((x) => x.kind === 'event' && x.step.event === 'resume'), 'the resume itself is still shown');
});

test('buildMoments: one generator yielding at depth 1, then 2 through a wrapper, then 1 again, never revives the old yield line', () => {
  const G1 = D(1, 'gen');
  const G2 = D(2, 'gen');
  const W = D(1, 'wrap');
  const trace = { stdout: '', steps: [M('call', 1, {}), M('line', 9, {}), G1('call', 1, {}), G1('line', 2, { a: 'd1' }), G1('yield', 2, { a: 'd1' }, { value: '1' }), M('line', 10, {}), W('call', 5, {}), W('line', 6, {}), G2('resume', 2, { a: 'd2' }), G2('line', 3, { a: 'd2' }), G2('yield', 3, { a: 'd2' }, { value: '2' }), W('line', 7, {}), W('return', 7, {}, { value: 'None' }), G1('resume', 3, { a: 'd1b' }), G1('line', 4, { a: 'd1b' }), G1('return', 4, { a: 'd1b' }, { value: 'None' }), M('return', 10, {})] };
  const m = buildMoments(trace);
  for (const x of m) if (x.kind === 'ran' && x.step.function === 'gen') assert.notEqual(x.step.line, 2, 'the d1 yield line 2 is never completed by a later d1 line');
  assert.ok(m.filter((x) => x.step.event === 'yield').length === 2 && m.filter((x) => x.step.event === 'resume').length === 2, 'every yield and resume is still shown, in order');
});

test('buildMoments: truncation picks the active caller line, not a generator that is paused deeper', () => {
  const G = D(1, 'g');
  const trace = { stdout: '', truncated: true, steps: [M('call', 1, {}), M('line', 5, {}), G('call', 1, {}), G('line', 2, {}), G('yield', 2, {}, { value: '1' }), M('line', 6, {})] };
  const m = buildMoments(trace);
  const last = m[m.length - 1];
  assert.equal(last.kind, 'ran');
  assert.equal(last.after, null);
  assert.equal(last.step.function, '<module>');
  assert.equal(last.step.line, 6);
});

test('buildMoments: an exception interrupts the line; unwind is not a normal return; the handler line carries on', () => {
  const F = D(1, 'f');
  const trace = { stdout: '', steps: [M('call', 1, {}), M('line', 3, {}), F('call', 1, {}), F('line', 2, {}), F('exception', 2, {}, { exception: { type: 'KeyError', message: "'k'", line: 2 } }), F('unwind', 2, {}, { exception: { type: 'KeyError', message: "'k'", line: 2 } }), M('line', 5, { e: 'KeyError' }), M('return', 5, { e: 'KeyError' }, { value: 'None' })] };
  const m = buildMoments(trace);
  assert.deepEqual(kinds(m), ['event:1/call', 'event:2/exception', 'event:2/unwind', 'interrupted:3', 'ran:5', 'event:5/return']);
  assert.equal(m[3].kind, 'interrupted');
  assert.equal(momentState(trace, m[3]).mode, 'event');
});

test('buildMoments: a truncated recording ends with the innermost pending line and no invented after state', () => {
  const trace = { stdout: '', truncated: true, steps: [M('call', 1, {}), M('line', 1, {}), M('line', 2, { x: '1' })] };
  const m = buildMoments(trace);
  assert.deepEqual(kinds(m), ['ran:1', 'ran:2']);
  assert.equal(m[1].after, null);
  assert.equal(momentState(trace, m[1]).mode, 'before');
});

test('buildMoments on the real corpus: first example, ordering, and output never shrinks or shows early', () => {
  const file = JSON.parse(readFileSync(dir + 'm1-assignment-snapshot.json', 'utf8'));
  const first = buildMoments(file.traces[0]);
  assert.deepEqual(momentState(file.traces[0], first[0]).rows, [{ name: 'x', value: '3', change: 'new' }]);
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.json'))) {
    for (const t of JSON.parse(readFileSync(dir + f, 'utf8')).traces) {
      const moments = buildMoments(t);
      assert.ok(moments.length > 0, f);
      let last = 0;
      for (const mm of moments) {
        const st = momentState(t, mm);
        const len = Array.from(st.shown).length + Array.from(st.added).length;
        assert.ok(len >= last, `${f}: output shrank at line ${mm.step.line}`);
        last = len;
      }
    }
  }
});

test('the keyword-evaluation example: line 10 completes only after the calls, with result 7', () => {
  const t = JSON.parse(readFileSync(dir + 'py-keyword-evaluation-before-binding.json', 'utf8')).traces[0];
  const m = buildMoments(t);
  const i10 = m.findIndex((x) => x.kind === 'ran' && x.step.line === 10);
  const firstCall = m.findIndex((x) => x.kind === 'event' && x.step.event === 'call' && x.step.function === 'mark');
  assert.ok(firstCall >= 0 && firstCall < i10, 'the calls are played before line 10 is shown as ran');
  assert.deepEqual(momentState(t, m[i10]).rows.find((r) => r.name === 'result'), { name: 'result', value: '7', change: 'new' });
  assert.equal(m[firstCall].callerLine, 10);
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

test('every shipped trace binds to its question case and its stdin hash', async () => {
  const versions = new Set();
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.json'))) {
    const file = JSON.parse(readFileSync(dir + f, 'utf8'));
    const q = JSON.parse(readFileSync(qdir + f, 'utf8'));
    assert.ok(['0.2', '0.3'].includes(file.schema_version), `${f}: supported schema version`);
    versions.add(file.schema_version);
    for (const t of file.traces) {
      assert.ok(bindCase(q, t.snippet_id, t, file), `${f}: ${t.snippet_id}`);
      assert.equal(await stdinMatches(t), true, `${f}: stdin hash`);
      if (t.truncated) assert.equal(t.truncated_at_step, t.steps.length, f);
    }
  }
  assert.ok(versions.has('0.2'), 'existing traces retain the 0.2 format');
  assert.ok(versions.has('0.3'), 'new multi-course sample traces use 0.3');
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

test('usableTraces: the real path rejects bad envelopes and malformed traces without throwing', async () => {
  const q = JSON.parse(readFileSync(qdir + 'm1-negative-index-guard.json', 'utf8'));
  const good = JSON.parse(readFileSync(dir + 'm1-negative-index-guard.json', 'utf8'));
  assert.equal((await usableTraces(q, good)).length, 1);
  assert.equal((await usableTraces(q, { ...good, schema_version: '0.1' })).length, 0);
  assert.equal((await usableTraces(q, { ...good, schema_version: '9.9', traces: [{ snippet_id: 'main' }] })).length, 0, 'unknown version without steps');
  assert.equal((await usableTraces(q, { ...good, question_id: 'other-question' })).length, 0);
  assert.equal((await usableTraces(q, { ...good, traces: [{ ...good.traces[0], steps: undefined }] })).length, 0, 'missing steps');
  assert.equal((await usableTraces(q, { ...good, traces: [{ ...good.traces[0], steps: [] }] })).length, 0, 'empty steps');
  assert.equal((await usableTraces(q, { ...good, traces: 'nope' })).length, 0);
  assert.equal((await usableTraces(q, null)).length, 0);
  assert.equal((await usableTraces(q, good, async () => null)).length, 0, 'no WebCrypto: fail closed');
  assert.equal(validEnvelope(good, q.id), true);
  const cut = { ...good.traces[0], truncated: true, truncated_at_step: good.traces[0].steps.length + 1 };
  assert.equal((await usableTraces(q, { ...good, traces: [cut] })).length, 0, 'truncated_at_step must equal the step count');
});

// ---- format 0.3: frame_id, scope, globals ----
const V = (fid, depth, fn, scope = 'function') => (event, line, locals, globals = {}, extra = {}) => ({
  event, line, depth, function: fn, locals, stdout_len: 0, frame_id: fid, scope, globals, locals_omitted: 0, globals_omitted: 0, ...extra,
});
const MV = V(1, 0, '<module>', 'module');

test('0.3 buildMoments: a global changed inside a callee shows on the calling line, and unchanged globals are reported', () => {
  const F = V(2, 1, 'log');
  const trace = { stdout: '', steps: [MV('call', 1, {}), MV('line', 1, {}), MV('line', 4, {}, { events: '[]' }), F('call', 2, { x: '1' }, { events: '[]' }), F('line', 3, { x: '1' }, { events: '[]' }), F('return', 3, { x: '1' }, { events: '[1]' }, { value: 'None' }), MV('line', 5, {}, { events: '[1]' }), MV('return', 5, {}, { events: '[1]' }, { value: 'None' })] };
  const m = buildMoments(trace);
  assert.deepEqual(kinds(m), ['ran:1', 'event:2/call', 'ran:3', 'event:3/return', 'ran:4', 'ran:5', 'event:5/return']);
  assert.equal(m[1].callerLine, 4);
  const calling = momentState(trace, m[4]);
  assert.deepEqual(calling.globals, [{ name: 'events', value: '[1]', change: 'changed' }], 'the append inside log() is visible');
  assert.equal(calling.globalsUnchanged, false);
  assert.equal(calling.scope, 'module');
  const quiet = momentState(trace, m[5]);
  assert.equal(quiet.globalsUnchanged, true, '"Globals unchanged" is stated explicitly, not implied by a still locals table');
});

test('0.3 buildMoments: two generators of the same function and depth stay apart; a yielding line completes after its resume', () => {
  const A = V(2, 1, 'g');
  const B = V(3, 1, 'g');
  const trace = { stdout: '', steps: [
    MV('call', 1, {}), MV('line', 6, {}),
    A('call', 1, {}), A('line', 2, {}), A('yield', 2, {}, {}, { value: '1' }),
    B('call', 1, {}), B('line', 2, {}), B('yield', 2, {}, {}, { value: '1' }),
    A('resume', 2, {}), A('line', 3, { r: '9' }), A('return', 3, { r: '9' }, {}, { value: 'None' }),
    MV('return', 7, {}, {}, { value: 'None' }),
  ] };
  const m = buildMoments(trace);
  const ranA = m.filter((x) => x.kind === 'ran' && x.step.frame_id === 2);
  assert.deepEqual(ranA.map((x) => [x.step.line, x.after.line]), [[2, 3], [3, 3]], 'line 2 (the yield line) completes at A\'s next line, not at B\'s steps');
  assert.ok(ranA.every((x) => x.safe === true), 'same frame_id: provably the same frame');
  assert.equal(m.filter((x) => x.kind === 'ran' && x.step.frame_id === 3).length, 0, 'B never completes a line, nothing is attributed to it');
});

test('0.3 momentState reports omitted names and the scope of the shown step', () => {
  const s = MV('line', 2, {}, { a: '1' }, { globals_omitted: 3 });
  const trace = { stdout: '', steps: [MV('call', 1, {}), s, MV('return', 2, {}, { a: '1' }, { value: 'None', globals_omitted: 3 })] };
  const m = buildMoments(trace);
  assert.equal(momentState(trace, m[0]).omitted, 3);
  assert.equal(momentState(trace, m[0]).frameId, 1);
});

test('0.3 usableTraces: bad or mixed shapes are refused (fail closed); 0.2 still works', async () => {
  const code = 'x = 1\n';
  const q = { id: 'syn-question', snippets: [{ id: 'main', code }], verification: { cases: [{ id: 'c1', snippet_id: 'main', stdin: '' }] } };
  const base = { snippet_id: 'main', source_sha256: await sha256Hex(code), case_id: 'c1', stdin: '', stdin_sha256: await sha256Hex(''), stdout: '', exception: null, truncated: false };
  const good = [MV('call', 1, {}), MV('line', 1, {}), MV('return', 1, {}, { x: '1' }, { value: 'None' })];
  const file = (version, steps) => ({ schema_version: version, question_id: q.id, traces: [{ ...base, steps }] });
  assert.equal((await usableTraces(q, file('0.3', good))).length, 1);
  assert.equal((await usableTraces(q, file('0.3', [good[0], { ...good[1], frame_id: undefined }, good[2]]))).length, 0, 'missing frame_id');
  assert.equal((await usableTraces(q, file('0.3', [good[0], { ...good[1], globals: undefined }, good[2]]))).length, 0, 'missing globals');
  assert.equal((await usableTraces(q, file('0.3', [good[0], { ...good[1], scope: 'heap' }, good[2]]))).length, 0, 'unknown scope');
  assert.equal((await usableTraces(q, file('0.3', [good[0], { ...good[1], globals: { x: 1 } }, good[2]]))).length, 0, 'non-string global');
  assert.equal((await usableTraces(q, file('0.2', good))).length, 0, '0.3 steps under a 0.2 envelope');
  assert.equal((await usableTraces(q, file('0.4', good))).length, 0, 'unknown version');
  const plain = good.map(({ frame_id, scope, globals, locals_omitted, globals_omitted, ...rest }) => rest);
  assert.equal((await usableTraces(q, file('0.2', plain))).length, 1, '0.2 unchanged');
  assert.equal((await usableTraces(q, file('0.3', plain))).length, 0, '0.2 steps under a 0.3 envelope');
});

// 0.3 steps (the player shows globals as their own table; the claims come only from the recorded globals).
const V3 = (event, line, scope, locals, globals, extra = {}) => ({ event, line, depth: scope === 'module' ? 0 : 1, function: scope === 'module' ? '<module>' : 'f', frame_id: scope === 'module' ? 1 : 2, scope, locals, globals, locals_omitted: 0, globals_omitted: 0, stdout_len: 0, ...extra });

test('0.3 moments: globals are their own namespace; "unchanged" comes only from the recorded globals', () => {
  const trace = { schema_version: '0.3', stdout: '', steps: [V3('call', 1, 'module', {}, {}), V3('line', 1, 'module', {}, {}), V3('line', 2, 'module', {}, { g: '1' }), V3('call', 5, 'function', {}, { g: '1' }), V3('line', 6, 'function', {}, { g: '1' }), V3('line', 7, 'function', { x: '2' }, { g: '1' }), V3('line', 8, 'function', { x: '3' }, { g: '9' })] };
  const m = buildMoments(trace);
  const byLine = (n) => m.find((x) => x.kind === 'ran' && x.step.line === n);
  const first = momentState(trace, byLine(1));
  assert.equal(first.scope, 'module');
  assert.deepEqual(first.globals, [{ name: 'g', value: '1', change: 'new' }]);
  const local = momentState(trace, byLine(6));
  assert.deepEqual(local.rows, [{ name: 'x', value: '2', change: 'new' }]);
  assert.equal(local.globalsUnchanged, true, 'a local changed, the recorded globals did not');
  const glob = momentState(trace, byLine(7));
  assert.equal(glob.globalsUnchanged, false, 'the global changed even though the local table did not');
  assert.deepEqual(glob.globals, [{ name: 'g', value: '9', change: 'changed' }]);
});

test('0.3 moments: omitted counts add up and are reported', () => {
  const trace = { schema_version: '0.3', stdout: '', steps: [V3('call', 1, 'module', {}, {}), V3('line', 1, 'module', {}, {}, { locals_omitted: 2, globals_omitted: 3 }), V3('return', 1, 'module', {}, { g: '1' }, { value: 'None', locals_omitted: 2, globals_omitted: 3 })] };
  const m = buildMoments(trace);
  assert.equal(momentState(trace, m[0]).omitted, 5);
});

test('0.3 momentState: "globals unchanged" is never claimed when the recording left globals out', () => {
  // Reproduces the review finding: 45 globals, the 41st-45th are not recorded, one of them changes inside f().
  const kept = Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`v${i}`, String(i)]));
  const F = (event, line, extra = {}) => V3(event, line, 'function', {}, kept, { globals_omitted: 5, ...extra });
  const trace = { schema_version: '0.3', stdout: '', steps: [V3('call', 1, 'module', {}, kept, { globals_omitted: 5 }), F('call', 2), F('line', 3), F('line', 4), F('return', 4, { value: 'None' })] };
  const m = buildMoments(trace);
  const ran = m.find((x) => x.kind === 'ran' && x.step.line === 3);
  const state = momentState(trace, ran);
  assert.equal(state.mode, 'after');
  assert.deepEqual(state.globals.filter((r) => r.change !== 'same'), [], 'the recorded globals really are equal');
  assert.equal(state.globalsUnchanged, false, 'but an omitted global may have changed: no claim');
  assert.equal(state.globalsOmitted, 5);
  assert.equal(state.omitted, 5);
  // The same steps with a complete recording do get the claim.
  const complete = { schema_version: '0.3', stdout: '', steps: trace.steps.map((s) => ({ ...s, globals_omitted: 0 })) };
  const again = momentState(complete, buildMoments(complete).find((x) => x.kind === 'ran' && x.step.line === 3));
  assert.equal(again.globalsUnchanged, true);
  assert.equal(again.globalsOmitted, 0);
  // Omitted at either observation is enough to withhold the claim.
  const late = { schema_version: '0.3', stdout: '', steps: trace.steps.map((s, i) => ({ ...s, globals_omitted: i === 3 ? 1 : 0 })) };
  assert.equal(momentState(late, buildMoments(late).find((x) => x.kind === 'ran' && x.step.line === 3)).globalsUnchanged, false);
});

test('0.3 momentState: empty recorded globals with omitted names expose globalsOmitted so no "no globals" text is said', () => {
  const trace = { schema_version: '0.3', stdout: '', steps: [V3('call', 1, 'module', {}, {}, { globals_omitted: 2 }), V3('line', 2, 'function', { a: '1' }, {}, { globals_omitted: 2 }), V3('line', 3, 'function', { a: '2' }, {}, { globals_omitted: 2 })] };
  const state = momentState(trace, buildMoments(trace).find((x) => x.kind === 'ran'));
  assert.deepEqual(state.globals, []);
  assert.equal(state.globalsOmitted, 2);
  assert.equal(state.globalsUnchanged, false);
});

test('0.3 diff: a name that was merely unrecorded in the earlier observation is never labelled "new"', () => {
  const g40 = Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`v${i}`, String(i)]));
  const g41 = { ...g40, v40: '40' };
  // 41 globals: the first observation recorded 40 (globals_omitted 1), the next one recorded all 41 (budget freed up).
  const trace = { schema_version: '0.3', stdout: '', steps: [V3('call', 1, 'module', {}, g40, { globals_omitted: 1 }), V3('line', 2, 'module', {}, g40, { globals_omitted: 1 }), V3('line', 3, 'module', {}, g41, { globals_omitted: 0 })] };
  const state = momentState(trace, buildMoments(trace).find((x) => x.kind === 'ran' && x.step.line === 2));
  assert.equal(state.mode, 'after');
  assert.equal(state.globals.find((r) => r.name === 'v40').change, 'same', 'v40 may have existed before: no "new" claim');
  assert.equal(state.globals.some((r) => r.change === 'new'), false);
  // A complete earlier observation still labels a really new name.
  const full = { schema_version: '0.3', stdout: '', steps: trace.steps.map((s, i) => (i < 2 ? { ...s, globals_omitted: 0 } : s)) };
  assert.equal(momentState(full, buildMoments(full).find((x) => x.kind === 'ran' && x.step.line === 2)).globals.find((r) => r.name === 'v40').change, 'new');
  // A name present in both observations keeps its "changed" even when names were omitted.
  const changed = { schema_version: '0.3', stdout: '', steps: [V3('call', 1, 'module', {}, g40, { globals_omitted: 1 }), V3('line', 2, 'module', {}, g40, { globals_omitted: 1 }), V3('line', 3, 'module', {}, { ...g41, v0: '99' }, { globals_omitted: 0 })] };
  const rows = momentState(changed, buildMoments(changed).find((x) => x.kind === 'ran' && x.step.line === 2)).globals;
  assert.equal(rows.find((r) => r.name === 'v0').change, 'changed');
  assert.equal(rows.find((r) => r.name === 'v40').change, 'same');
});

test('0.3 diff: unrecorded locals are not "new" either; 0.2 behaviour is unchanged', () => {
  const F = V(2, 1, 'f');
  const trace = { stdout: '', steps: [MV('call', 1, {}), MV('line', 2, {}), F('call', 3, {}, {}, { locals_omitted: 1 }), F('line', 4, { a: '1' }, {}, { locals_omitted: 1 }), F('line', 5, { a: '1', b: '2' }, {}, { locals_omitted: 0 })] };
  const m = buildMoments(trace);
  assert.equal(momentState(trace, m.find((x) => x.kind === 'ran' && x.step.line === 4)).rows.find((r) => r.name === 'b').change, 'same');
  assert.deepEqual(diffLocals({ x: '1' }, { x: '1', y: '2' }), [{ name: 'x', value: '1', change: 'same' }, { name: 'y', value: '2', change: 'new' }], '0.2: unchanged');
});
