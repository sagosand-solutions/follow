import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { checkQuestion, QUESTION_ID } from '../public/js/question-check.js';
import { currentAnswer } from '../public/js/answers.js';

const dir = new URL('../public/data/questions/', import.meta.url);
const files = readdirSync(dir).filter((f) => f.endsWith('.json'));
const load = (f) => JSON.parse(readFileSync(new URL(f, dir), 'utf8'));
const clone = (x) => JSON.parse(JSON.stringify(x));

test('every shipped question passes the client check', () => {
  assert.ok(files.length > 0);
  for (const f of files) {
    const q = load(f);
    assert.equal(checkQuestion(q, q.id), null, f);
  }
});

const base = () => clone(load(files[0]));

test('rejects wrong option structure', () => {
  const q = base();
  const two = clone(q);
  two.options = two.options.filter((o) => o.score === 1 || o.score === 0.5);
  assert.equal(checkQuestion(two, q.id), 'option count');

  const noHalf = clone(q);
  noHalf.options.find((o) => o.score === 0.5).score = 0;
  // The former half option now claims score 0 but still has partial_reason, which only a half option may have.
  assert.equal(checkQuestion(noHalf, q.id), 'wrong option has partial_reason');

  const twoFull = clone(q);
  twoFull.options.find((o) => o.score === 0).score = 1;
  assert.notEqual(checkQuestion(twoFull, q.id), null);

  const dup = clone(q);
  dup.options[1].id = dup.options[0].id;
  assert.equal(checkQuestion(dup, q.id), 'option id');
});

test('rejects bad scores, fields and ids', () => {
  const q = base();
  const bad = (mut) => { const c = clone(q); mut(c); return checkQuestion(c, q.id); };
  assert.notEqual(bad((c) => { c.options[0].score = 2; }), null);
  assert.notEqual(bad((c) => { c.options[0].feedback = ' '; }), null);
  assert.notEqual(bad((c) => { c.explanation = ''; }), null);
  assert.notEqual(bad((c) => { c.snippets = []; }), null);
  assert.notEqual(bad((c) => { c.snippets[0].highlight_lines = [0]; }), null);
  assert.notEqual(bad((c) => { c.schema_version = '0.3'; }), null);
  assert.notEqual(bad((c) => { c.type = 'quiz'; }), null);
  assert.notEqual(bad((c) => { delete c.author; }), null);
  assert.notEqual(checkQuestion(q, `${q.id}\n`), null);
  assert.notEqual(checkQuestion(null, 'abc'), null);
  assert.notEqual(checkQuestion([], 'abc'), null);
});

test('question ids with a trailing newline are rejected', () => {
  assert.equal(QUESTION_ID.test('abc'), true);
  assert.equal(QUESTION_ID.test('abc\n'), false);
  assert.equal(QUESTION_ID.test('ab'), false);
  assert.equal(QUESTION_ID.test('Abc'), false);
});

test('currentAnswer: stale, unknown and changed score', () => {
  const q = base();
  const full = q.options.find((o) => o.score === 1);
  assert.equal(currentAnswer(q, null), null);
  assert.equal(currentAnswer(q, { outcome: 'answered', option_id: 'removed-option', score: 1 }), null);
  assert.deepEqual(currentAnswer(q, { outcome: 'unknown', option_id: null, score: null }), { outcome: 'unknown', option: null, score: null });
  const cur = currentAnswer(q, { outcome: 'answered', option_id: full.id, score: 0 });
  assert.equal(cur.score, 1); // the current question decides, not the old record
});
