import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const root = new URL('../public/', import.meta.url).pathname;
const course = JSON.parse(readFileSync(`${root}data/courses/python.json`, 'utf8'));

test('lesson summaries are single-line plain text of 1 to 200 code points', () => {
  for (const level of course.levels) {
    for (const lesson of level.lessons) {
      if (lesson.summary === undefined) continue;
      assert.equal(typeof lesson.summary, 'string', lesson.id);
      const points = Array.from(lesson.summary).length;
      assert.ok(points >= 1 && points <= 200, `${lesson.id}: ${points}`);
      assert.doesNotMatch(lesson.summary, /[\u0000-\u001f\u007f]|<|`/, lesson.id);
      assert.equal(lesson.summary, lesson.summary.trim(), lesson.id);
    }
  }
});

test('views render summary as text nodes, never through markup, and omit the node when absent', () => {
  for (const file of ['js/views/course.js', 'js/views/lesson.js']) {
    const src = readFileSync(`${root}${file}`, 'utf8');
    assert.match(src, /lesson\.summary \? h\(/, file);
    assert.doesNotMatch(src, /innerHTML|insertAdjacentHTML|renderInline\(lesson\.summary|renderText\(lesson\.summary/, file);
  }
});
