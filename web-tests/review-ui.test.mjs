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

test('the view wiring for every review entry point is pinned (justified source checks)', async () => {
  // These are the three view-level guards the previous file carried. They cannot be behaviour-tested here without rendering the
  // views (the browser-only DOM stack); each one kills a real regression mutant, so they stay, grouped and justified. The queue
  // rules themselves are covered by the behaviour tests above. Rule: a guard is only removed once a behaviour test kills the same mutant.
  const read = (path) => readFile(new URL(`../public/js/${path}`, import.meta.url), 'utf8');
  const [main, view, home, course, lesson, question] = await Promise.all([
    read('main.js'), read('views/review.js'), read('views/home.js'), read('views/course.js'), read('views/lesson.js'), read('views/question.js'),
  ]);
  // router wiring (main.js cannot be imported in a test)
  assert.match(main, /registerRoute\('\/review', reviewView\)/);
  assert.match(main, /registerRoute\('\/course\/:id\/review', reviewView\)/);
  // the review view: the queue is derived from progress, the empty state exists and the question view is opened in review mode
  assert.match(view, /reviewQueue\(course, store\.answers\(\)\)/);       // mutant: ignore progress -> dies here
  assert.match(view, /queue\.length === 0/);                             // mutant: drop the empty state -> dies here
  assert.match(view, /questionView\(\{ id: current\.questionId, course, reviewMode: true \}\)/);
  // the three entry points only offer the link with a positive count, from home, course and lesson end
  for (const source of [home, course, lesson]) {
    assert.match(source, /reviewCount > 0/);                             // mutant: always/never show -> dies here
    assert.match(source, /href: reviewHref\(/);
    assert.match(source, /t\('review\.button', \{ count: reviewCount \}\)/);
  }
  assert.match(home, /if \(answeredCount > 0\)/);
  // the question view navigates to the next review item after an answer
  assert.match(question, /nextReview\(reviewQueue\(ctx\.course, store\.answers\(\)\), q\.id, ctx\.course\)/);   // mutant: never advance -> dies here
  assert.match(question, /review\.position/);
  // the queue rules still carry no DOM or storage dependency (a negative property, not behaviour)
  const pure = await read('review.js');
  assert.doesNotMatch(pure, /localStorage|createStore|store\./);
});
