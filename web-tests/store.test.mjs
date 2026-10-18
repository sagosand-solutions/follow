import test from 'node:test';
import assert from 'node:assert/strict';
import { createStore, MAX_IMPORT_BYTES } from '../public/js/store.js';

function fakeStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
  };
}
const mk = (storage, now) => createStore({ storage, prefix: 'p', now });

test('records answers and attempts', () => {
  let t = 1000;
  const s = mk(fakeStorage(), () => (t += 1));
  const a1 = s.recordAnswer({ questionId: 'q1', optionId: 'a', score: 0 });
  const a2 = s.recordAnswer({ questionId: 'q1', optionId: 'b', score: 1 });
  assert.equal(a1.attempt, 1);
  assert.equal(a2.attempt, 2);
  assert.equal(s.latest('q1').option_id, 'b');
  assert.equal(s.attempts('q1').length, 2);
  assert.equal(s.latest('nope'), null);
});

test('unknown is distinct from answered score 0', () => {
  const s = mk(fakeStorage());
  const u = s.recordUnknown({ questionId: 'q1' });
  const w = s.recordAnswer({ questionId: 'q2', optionId: 'x', score: 0 });
  assert.equal(u.outcome, 'unknown');
  assert.equal(u.option_id, null);
  assert.equal(u.score, null);
  assert.equal(w.outcome, 'answered');
  assert.equal(w.score, 0);
});

test('seed is stable across store instances', () => {
  const st = fakeStorage();
  const a = mk(st).seed();
  const b = mk(st).seed();
  assert.equal(a, b);
  assert.match(a, /^[0-9a-f]{16}$/);
});

test('works without storage', () => {
  const s = createStore({ storage: null, prefix: 'p' });
  assert.equal(s.persistent, false);
  s.recordAnswer({ questionId: 'q', optionId: 'a', score: 1 });
  assert.equal(s.answers().length, 1);
  assert.equal(s.seed(), s.seed());
});

test('falls back to memory when setItem throws', () => {
  const st = fakeStorage();
  st.setItem = () => { throw new Error('quota'); };
  const s = mk(st);
  s.recordAnswer({ questionId: 'q', optionId: 'a', score: 1 });
  assert.equal(s.persistent, false);
  assert.equal(s.answers().length, 1);
});

test('reset removes only its own key', () => {
  const st = fakeStorage({ other: 'keep' });
  const s = mk(st);
  s.recordAnswer({ questionId: 'q', optionId: 'a', score: 1 });
  s.reset();
  assert.equal(st.data.other, 'keep');
  assert.equal(s.answers().length, 0);
  assert.equal('p.progress' in st.data, false);
});

test('corrupt stored data starts fresh and is not overwritten by reads', () => {
  const st = fakeStorage({ 'p.progress': '{not json' });
  const s = mk(st);
  assert.deepEqual(s.answers(), []);
  assert.equal(st.data['p.progress'], '{not json');
});

test('export/import round trip and no duplicates on second import', () => {
  let t = 5;
  const a = mk(fakeStorage(), () => (t += 1));
  a.recordAnswer({ questionId: 'q1', optionId: 'a', score: 0.5 });
  a.recordUnknown({ questionId: 'q2' });
  const json = a.exportJson();
  const b = mk(fakeStorage());
  assert.deepEqual(b.importJson(json), { ok: true, imported: 2 });
  assert.deepEqual(b.importJson(json), { ok: true, imported: 0 });
  assert.equal(b.answers().length, 2);
  assert.equal(b.seed(), JSON.parse(json).seed);
});

test('import rejects bad input and leaves state unchanged', () => {
  const s = mk(fakeStorage());
  s.recordAnswer({ questionId: 'q', optionId: 'a', score: 1 });
  const before = JSON.stringify(s.answers());
  const good = { v: 1, seed: 'abc', answers: [] };
  const bad = [
    'garbage',
    JSON.stringify({ ...good, v: 2 }),
    JSON.stringify({ ...good, extra: 1 }),
    JSON.stringify({ ...good, answers: [{ question_id: 'q', option_id: 'a', outcome: 'answered', score: 3, t: 1, attempt: 1 }] }),
    JSON.stringify({ ...good, answers: [{ question_id: 'q', option_id: 'a', outcome: 'unknown', score: null, t: 1, attempt: 1 }] }),
    JSON.stringify({ ...good, answers: [{ question_id: 'q', option_id: null, outcome: 'unknown', score: null, t: 1, attempt: 1, more: true }] }),
    JSON.stringify({ ...good, answers: 'no' }),
    'null',
    '[]',
  ];
  for (const text of bad) {
    const r = s.importJson(text);
    assert.equal(r.ok, false, text);
    assert.ok(r.error);
  }
  assert.equal(JSON.stringify(s.answers()), before);
});

test('read failure keeps the last valid state in memory and reports not persistent', () => {
  const st = fakeStorage();
  const s = mk(st);
  const seed = s.seed();
  s.recordAnswer({ questionId: 'q', optionId: 'a', score: 1 });
  st.getItem = () => { throw new Error('unreadable'); };
  assert.equal(s.answers().length, 1);
  assert.equal(s.seed(), seed);
  assert.equal(s.persistent, false);
  s.recordAnswer({ questionId: 'q2', optionId: 'a', score: 0 });
  assert.equal(s.answers().length, 2);
  assert.equal(s.seed(), seed);
});

test('import errors are codes, not prose', () => {
  const s = mk(fakeStorage());
  assert.equal(s.importJson('nope').error, 'invalid-json');
  assert.equal(s.importJson('{}').error, 'invalid-format');
});

test('an import over the byte limit is refused before parsing, leaving progress intact', () => {
  const s = mk(fakeStorage(), () => 1);
  s.recordAnswer({ questionId: 'q1', optionId: 'a', score: 1 });
  const before = JSON.stringify(s.answers());
  assert.deepEqual(s.importJson('x'.repeat(MAX_IMPORT_BYTES + 1)), { ok: false, error: 'too-large' });   // one byte over
  assert.equal(JSON.stringify(s.answers()), before);
  assert.equal(s.importJson('\u00e9'.repeat(MAX_IMPORT_BYTES / 2 + 1)).error, 'too-large');             // multibyte counts as bytes
  assert.equal(s.importJson('x'.repeat(MAX_IMPORT_BYTES)).error, 'invalid-json');                       // exactly the limit reaches validation
  assert.equal(s.importJson(null).error, 'invalid-json');                                               // a non-string is not a bypass
});
