import test from 'node:test';
import assert from 'node:assert/strict';
import { nextUp, nextLesson, buildsOnThis, hasProgress } from '../public/js/course-nav.js';

const course = {
  id: 'c',
  title: 'Course',
  language: 'python',
  levels: [
    {
      level: 'basics',
      title: 'Basics',
      lessons: [
        { id: 'l1', title: 'Lesson 1', builds_on: [], questions: ['q1', 'q2'] },
        { id: 'l2', title: 'Lesson 2', builds_on: ['l1'], questions: ['q3'] },
      ],
    },
    {
      level: 'advanced',
      title: 'Advanced',
      lessons: [{ id: 'l3', title: 'Lesson 3', builds_on: ['l1', 'l2'], questions: ['q4', 'q5'] }],
    },
  ],
};

const answered = (...pairs) => pairs.map(([question_id, t]) => ({ question_id, t }));

test('no history points at the first question with reason first-unanswered', () => {
  const target = nextUp(course, []);
  assert.deepEqual(target, { questionId: 'q1', lessonId: 'l1', lessonTitle: 'Lesson 1', position: 1, total: 2, reason: 'first-unanswered' });
  assert.equal(hasProgress(course, []), false);
});

test('mid-lesson continues with the next question', () => {
  const target = nextUp(course, answered(['q1', 1]));
  assert.equal(target.questionId, 'q2');
  assert.equal(target.lessonId, 'l1');
  assert.equal(target.position, 2);
  assert.equal(target.total, 2);
  assert.equal(target.reason, 'after-last');
});

test('last question in a lesson continues into the next lesson', () => {
  const target = nextUp(course, answered(['q1', 1], ['q2', 2]));
  assert.equal(target.questionId, 'q3');
  assert.equal(target.lessonId, 'l2');
  assert.equal(target.position, 1);
  assert.equal(target.total, 1);
});

test('everything answered returns null', () => {
  assert.equal(nextUp(course, answered(['q1', 1], ['q2', 2], ['q3', 3], ['q4', 4], ['q5', 5])), null);
});

test('an out-of-order answer sets the last answered position', () => {
  assert.equal(nextUp(course, answered(['q4', 5])).questionId, 'q5');
  // The last answered question is the last in the course, so fall back to the
  // first unanswered one overall.
  const target = nextUp(course, answered(['q5', 10], ['q1', 5]));
  assert.equal(target.questionId, 'q2');
  assert.equal(target.reason, 'first-unanswered');
});

test('unknown question ids are ignored', () => {
  assert.equal(hasProgress(course, answered(['nope', 5])), false);
  assert.equal(nextUp(course, answered(['nope', 5])).questionId, 'q1');
});

test('duplicates keep the newest timestamp per question', () => {
  const target = nextUp(course, answered(['q1', 1], ['q1', 9]));
  assert.equal(target.questionId, 'q2');
});

test('an equal newest timestamp goes to the later question in course order', () => {
  const a = nextUp(course, answered(['q1', 5], ['q2', 5]));
  const b = nextUp(course, answered(['q2', 5], ['q1', 5]));
  assert.equal(a.questionId, 'q3');
  assert.equal(b.questionId, 'q3');
  assert.equal(a.reason, 'after-last');
});

test('non-finite timestamps and malformed records are ignored', () => {
  const records = answered(['q1', NaN], ['q2', Infinity], ['q3', -Infinity]);
  records.push(null, {}, { question_id: 'q4' }, { question_id: 'q5', t: 'x' }, 42);
  assert.equal(hasProgress(course, records), false);
  assert.equal(nextUp(course, records).questionId, 'q1');
});

test('a not-sure style record still counts as progress', () => {
  assert.equal(hasProgress(course, answered(['q3', 1])), true);
  assert.equal(nextUp(course, answered(['q3', 1])).questionId, 'q4');
});

test('empty or broken courses return null without throwing', () => {
  for (const bad of [null, undefined, {}, { levels: null }, { levels: [null, { lessons: null }] }, { levels: [{ lessons: [{}] }] }]) {
    assert.equal(nextUp(bad, []), null);
    assert.equal(hasProgress(bad, []), false);
  }
  assert.equal(nextUp({ levels: [{ lessons: [{ id: 'x', title: 'X', questions: [] }] }] }, []), null);
});

test('nextLesson walks course order and stops at the end', () => {
  assert.deepEqual(nextLesson(course, 'l1'), { id: 'l2', title: 'Lesson 2' });
  assert.deepEqual(nextLesson(course, 'l2'), { id: 'l3', title: 'Lesson 3' });
  assert.equal(nextLesson(course, 'l3'), null);
  assert.equal(nextLesson(course, 'nope'), null);
});

test('buildsOnThis returns every dependent lesson in course order', () => {
  assert.deepEqual(buildsOnThis(course, 'l1'), [{ id: 'l2', title: 'Lesson 2' }, { id: 'l3', title: 'Lesson 3' }]);
  assert.deepEqual(buildsOnThis(course, 'l2'), [{ id: 'l3', title: 'Lesson 3' }]);
  assert.deepEqual(buildsOnThis(course, 'l3'), []);
});

test('compact level rule switches above 40 questions', async () => {
  const { isCompactLevel } = await import('../public/js/course-nav.js');
  assert.equal(isCompactLevel(0), false);
  assert.equal(isCompactLevel(40), false);
  assert.equal(isCompactLevel(41), true);
});

test('read state: none, some, all', async () => {
  const { readState } = await import('../public/js/course-nav.js');
  assert.equal(readState(0, 5), 'none');
  assert.equal(readState(3, 5), 'some');
  assert.equal(readState(5, 5), 'all');
});
