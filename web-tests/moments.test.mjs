import test from 'node:test';
import assert from 'node:assert/strict';
import { changedToFit, lastRead, revisitedSummary } from '../public/js/moments.js';
import { createSeenFlag } from '../public/js/seen.js';

const rec = (question_id, attempt, outcome, score, t, option_id = 'a') => ({ question_id, attempt, outcome, score, t, option_id });
const course = { levels: [{ lessons: [{ id: 'l1', title: 'One', questions: ['q1', 'q2'] }, { id: 'l2', title: 'Two', questions: ['q3'] }] }] };

test('changedToFit only fires when a not-sure or wrong answer becomes fully right', () => {
  assert.deepEqual(changedToFit(rec('q', 1, 'unknown', null, 1, null), rec('q', 2, 'answered', 1, 2)), { kind: 'unknown' });
  assert.deepEqual(changedToFit(rec('q', 1, 'answered', 0, 1, 'c'), rec('q', 2, 'answered', 1, 2)), { kind: 'wrong', optionId: 'c' });
  assert.equal(changedToFit(rec('q', 1, 'answered', 0.5, 1), rec('q', 2, 'answered', 1, 2)), null, 'half credit is not a change to fit');
  assert.equal(changedToFit(rec('q', 1, 'answered', 1, 1), rec('q', 2, 'answered', 1, 2)), null);
  assert.equal(changedToFit(rec('q', 1, 'answered', 0, 1), rec('q', 2, 'answered', 0, 2)), null);
  assert.equal(changedToFit(rec('q', 1, 'answered', 0, 1), rec('q', 2, 'unknown', null, 2)), null);
  assert.equal(changedToFit(null, rec('q', 1, 'answered', 1, 2)), null, 'a first answer is never a change');
});

test('lastRead names the lesson and position of the latest answer, ignoring unknown ids and bad records', () => {
  assert.equal(lastRead(course, []), null);
  assert.equal(lastRead(null, [rec('q1', 1, 'answered', 1, 1)]), null);
  const r = lastRead(course, [rec('q1', 1, 'answered', 1, 1), rec('q3', 1, 'answered', 0, 5), rec('nope', 1, 'answered', 1, 9), { question_id: 'q2', t: NaN }, null]);
  assert.deepEqual(r, { questionId: 'q3', lessonId: 'l2', lessonTitle: 'Two', position: 1 });
});

test('revisitedSummary counts questions answered more than once and finds the latest', () => {
  assert.deepEqual(revisitedSummary(null), { count: 0, lastId: null });
  assert.deepEqual(revisitedSummary([rec('q1', 1, 'answered', 0, 1)]), { count: 0, lastId: null });
  const s = revisitedSummary([rec('q1', 1, 'answered', 0, 1), rec('q1', 2, 'answered', 1, 2), rec('q2', 1, 'unknown', null, 3), rec('q2', 2, 'answered', 1, 4), rec('q2', 3, 'answered', 1, 5), rec('q3', 1, 'answered', 1, 6)]);
  assert.deepEqual(s, { count: 2, lastId: 'q2' });
});

test('the seen flag keeps one bit, and in memory mode it is never kept', () => {
  const mem = new Map();
  const storage = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, v) };
  const flag = createSeenFlag('first-run', { storage, prefix: 'p' });
  assert.equal(flag.isSeen(), false);
  flag.markSeen();
  assert.equal(flag.isSeen(), true);
  assert.equal(mem.get('p.seen.first-run'), '1');
  const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  const none = createSeenFlag('first-run', { storage: broken, prefix: 'p' });
  none.markSeen();
  assert.equal(none.isSeen(), false, 'memory mode shows the moment every time');
  assert.equal(createSeenFlag('x', { storage: null, prefix: 'p' }).isSeen(), false);
});
