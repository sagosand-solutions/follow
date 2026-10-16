import test from 'node:test';
import assert from 'node:assert/strict';
import { nextReview, reviewQueue } from '../public/js/review.js';

const course = {
  levels: [
    { lessons: [
      { id: 'l1', title: 'First lesson', questions: ['q1', 'q2'] },
      { id: 'l2', title: 'Second lesson', questions: ['q3'] },
    ] },
  ],
};
const record = (question_id, attempt, outcome, score, t = attempt) => ({ question_id, attempt, outcome, score, t });

test('reviewQueue selects only latest unknown or zero-score records in course order', () => {
  const records = [
    record('q1', 1, 'answered', 0),
    record('q1', 2, 'answered', 1), // superseded wrong answer
    record('q2', 1, 'answered', 1),
    record('q2', 2, 'unknown', null),
    record('q3', 1, 'unknown', null),
    record('q3', 2, 'answered', 0.5), // half credit is excluded
    record('not-in-course', 1, 'answered', 0),
  ];
  assert.deepEqual(reviewQueue(course, records), [
    { order: 1, questionId: 'q2', lessonId: 'l1', lessonTitle: 'First lesson', position: 2, total: 2 },
  ]);
});

test('reviewQueue includes latest zero scores and ignores malformed records', () => {
  const records = [
    record('q1', 1, 'answered', 1, 10),
    record('q1', 2, 'answered', 0, 20),
    record('q2', 1, 'unknown', null, 5),
    null,
    {},
    { question_id: 'q3', attempt: 2, outcome: 'answered', score: 0, t: Infinity },
    { question_id: 'q3', attempt: 2, outcome: 'answered', score: 0, t: 3 },
  ];
  assert.deepEqual(reviewQueue(course, records).map((entry) => entry.questionId), ['q1', 'q2', 'q3']);
});

test('nextReview advances, wraps, and restarts at the first remaining item if current was resolved', () => {
  const queue = reviewQueue(course, [
    record('q1', 1, 'unknown', null),
    record('q2', 1, 'answered', 0),
    record('q3', 1, 'answered', 0),
  ]);
  assert.equal(nextReview(queue, 'q1').questionId, 'q2');
  assert.equal(nextReview(queue, 'q3').questionId, 'q1');
  assert.equal(nextReview(queue.slice(1), 'q1').questionId, 'q2');
  assert.equal(nextReview([], 'q1'), null);
  assert.equal(nextReview(null, 'q1'), null);
});

test('malformed and empty courses yield an empty queue without mutating records', () => {
  const records = [record('q1', 1, 'unknown', null)];
  const before = structuredClone(records);
  for (const invalid of [null, undefined, {}, { levels: null }]) assert.deepEqual(reviewQueue(invalid, records), []);
  assert.deepEqual(reviewQueue(course, null), []);
  assert.deepEqual(records, before);
});

test('nextReview with the course: a resolved current item continues AFTER it in course order, not at the start', () => {
  const course = { levels: [{ lessons: [{ id: 'l', title: 'L', questions: ['q1', 'q2', 'q3', 'q4'] }] }] };
  const rec = (id, outcome, score) => ({ question_id: id, attempt: 1, t: 1, outcome, score });
  const before = reviewQueue(course, [rec('q1', 'answered', 0), rec('q2', 'unknown', null), rec('q3', 'answered', 0)]);
  assert.deepEqual(before.map((e) => e.questionId), ['q1', 'q2', 'q3']);
  // q2 was answered right, so it left the queue
  const after = reviewQueue(course, [rec('q1', 'answered', 0), { question_id: 'q2', attempt: 2, t: 2, outcome: 'answered', score: 1 }, rec('q3', 'answered', 0)]);
  assert.equal(nextReview(after, 'q2', course).questionId, 'q3', 'forward, not back to q1');
  assert.equal(nextReview(after, 'q4', course).questionId, 'q1', 'nothing after q4: wrap to the first remaining');
  assert.equal(nextReview(after, 'q2').questionId, 'q1', 'without a course it restarts at the first remaining item (old behaviour)');
});
