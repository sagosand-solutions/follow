import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = async (path) => readFile(new URL(path, import.meta.url), 'utf8');

test('review route delegates to the current progress-derived queue and has an empty state', async () => {
  const main = await source('../public/js/main.js');
  const view = await source('../public/js/views/review.js');
  assert.match(main, /registerRoute\('\/review', reviewView\)/);
  assert.match(main, /registerRoute\('\/course\/:id\/review', reviewView\)/);
  assert.match(view, /reviewQueue\(course, store\.answers\(\)\)/);
  assert.match(view, /queue\.length === 0/);
  assert.match(view, /questionView\(\{ id: current\.questionId, course, reviewMode: true \}\)/);
});

test('positive review counts expose links from home, course, and lesson end only', async () => {
  const [home, course, lesson] = await Promise.all([
    source('../public/js/views/home.js'), source('../public/js/views/course.js'), source('../public/js/views/lesson.js'),
  ]);
  for (const view of [home, course, lesson]) {
    assert.match(view, /reviewCount > 0/);
    assert.match(view, /href: reviewHref\(/);
    assert.match(view, /t\('review\.button', \{ count: reviewCount \}\)/);
  }
  assert.match(home, /if \(answeredCount > 0\)/);
});

test('review navigation recomputes from progress and adds no storage dependency', async () => {
  const [question, pure, route] = await Promise.all([
    source('../public/js/views/question.js'), source('../public/js/review.js'), source('../public/js/views/review.js'),
  ]);
  assert.match(question, /nextReview\(reviewQueue\(ctx\.course, store\.answers\(\)\), q\.id, ctx\.course\)/);
  assert.match(question, /review\.position/);
  assert.match(route, /new URLSearchParams\(rawQuery\)/);
  assert.doesNotMatch(pure, /localStorage|createStore|store\./);
});
