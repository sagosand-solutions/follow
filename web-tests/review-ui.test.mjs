import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { reviewQueue, nextReview } from '../public/js/review.js';
import { reviewHref } from '../public/js/catalog.js';

const courseOf = (ids) => ({ levels: [{ level: 'beginner', title: 'Level', lessons: [{ id: 'l1', title: 'Lesson', questions: ids }] }] });
const record = (question_id, attempt, outcome, score) => ({ question_id, attempt, outcome, score, t: attempt });

test('the review queue holds exactly the unknown or wrong answers, in course order', () => {
  const course = courseOf(['q1', 'q2', 'q3', 'q4']);
  const queue = reviewQueue(course, [
    record('q1', 1, 'answered', 1),          // correct     -> out
    record('q2', 1, 'unknown', 0.5),         // "not sure"  -> in
    record('q3', 1, 'answered', 0),          // wrong       -> in
    record('q4', 1, 'answered', 0.5),        // half right  -> out
  ]);
  assert.deepEqual(queue.map((e) => e.questionId), ['q2', 'q3']);
});

test('only the latest attempt decides, so a later correct answer clears the item', () => {
  const course = courseOf(['q1', 'q2']);
  assert.deepEqual(reviewQueue(course, [record('q1', 1, 'answered', 0), record('q1', 2, 'answered', 1)]).map((e) => e.questionId), []);
  assert.deepEqual(reviewQueue(course, [record('q2', 1, 'answered', 1), record('q2', 2, 'unknown', 0)]).map((e) => e.questionId), ['q2']);
});

test('review links stay inside the site hash routes and carry an encoded question', () => {
  assert.equal(reviewHref(), '#/review');
  assert.equal(reviewHref('python'), '#/review');
  assert.equal(reviewHref('production'), '#/course/production/review');
  assert.equal(reviewHref('python', 'q 1'), '#/review?question=q%201');
});

test('the queue empties to nothing and nextReview returns null, advances in course order and wraps', () => {
  const course = courseOf(['q1', 'q2', 'q3']);
  assert.equal(nextReview([], 'q1'), null, 'an empty queue has nothing to advance to');

  const queue = reviewQueue(course, [record('q1', 1, 'unknown', 0), record('q3', 1, 'answered', 0)]);   // [q1, q3]
  assert.deepEqual(queue.map((e) => e.questionId), ['q1', 'q3']);
  assert.equal(nextReview(queue, 'q1', course).questionId, 'q3', 'the next item in course order');
  assert.equal(nextReview(queue, 'q3', course).questionId, 'q1', 'wraps to the first remaining item');
  assert.equal(nextReview(queue, 'q2', course).questionId, 'q3', 'the current item left the queue: continue after its place');
});

test('the review routes are wired and the queue module keeps no DOM or storage dependency', async () => {
  // Two properties a behaviour test cannot show: the router wiring in the entry point, and the queue module's own lack of a
  // store or DOM. Both are static invariants of the source, checked here on purpose (not behaviour).
  const main = await readFile(new URL('../public/js/main.js', import.meta.url), 'utf8');
  assert.match(main, /registerRoute\('\/review', reviewView\)/);
  assert.match(main, /registerRoute\('\/course\/:id\/review', reviewView\)/);
  const pure = await readFile(new URL('../public/js/review.js', import.meta.url), 'utf8');
  assert.doesNotMatch(pure, /localStorage|createStore|store\./, 'the queue rules carry no storage dependency');
});
