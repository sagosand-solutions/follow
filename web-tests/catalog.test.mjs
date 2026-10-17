import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCatalog, courseHref, reviewHref, freeTotal, continueAcross, activeCourse, isCourseId } from '../public/js/catalog.js';

const raw = (over = {}) => ({
  data_version: 1,
  courses: [
    { id: 'python', title: 'Python', description: 'd', free_questions: 34, premium: { status: 'coming-soon', questions: 153 }, start_question: 'a' },
    { id: 'production', title: 'Prod', description: 'd', free_questions: 5, premium: { status: 'coming-soon', questions: 85 } },
    { id: 'sql', title: 'SQL', description: 'd', free_questions: 5, premium: { status: 'coming-soon', questions: 70 } },
  ],
  ...over,
});

const course = (id, ...qs) => ({ id, levels: [{ level: 'beginner', title: 'L', lessons: [{ id: `${id}-l`, title: 'T', questions: qs }] }] });
const rec = (question_id, t) => ({ question_id, attempt: 1, t, outcome: 'answered', score: 1 });

test('parseCatalog keeps valid courses and the free count', () => {
  const list = parseCatalog(raw());
  assert.deepEqual(list.map((c) => c.id), ['python', 'production', 'sql']);
  assert.equal(freeTotal(list), 44);
  assert.deepEqual(list[1].premium, { status: 'coming-soon', questions: 85 });
});

test('parseCatalog is fail-closed on unusable input', () => {
  for (const bad of [null, 'x', {}, { data_version: 2, courses: [] }, { data_version: 1, courses: [] }, { data_version: 1, courses: [{ id: '../x', title: 't', description: 'd' }] }]) {
    assert.equal(parseCatalog(bad), null);
  }
});

test('parseCatalog skips bad entries and duplicates, and drops an unknown premium status', () => {
  const list = parseCatalog(raw({ courses: [{ id: 'a', title: 'A', description: 'd', premium: { status: 'on-sale', questions: 3 } }, { id: 'a', title: 'B', description: 'd' }, { id: 'Bad', title: 'x', description: 'd' }, { id: 'b', title: '', description: 'd' }] }));
  assert.equal(list.length, 1);
  assert.equal(list[0].premium, null);
  assert.equal(freeTotal(list), null);
});

test('premium question count must be a whole number', () => {
  const list = parseCatalog(raw({ courses: [{ id: 'a', title: 'A', description: 'd', free_questions: -1, premium: { status: 'coming-soon', questions: '70' } }] }));
  assert.equal(list[0].free, null);
  assert.equal(list[0].premium.questions, null);
});

test('hrefs: Python keeps its addresses, other courses live under /course/<id>', () => {
  assert.equal(courseHref('python'), '#/course');
  assert.equal(courseHref(), '#/course');
  assert.equal(courseHref('sql'), '#/course/sql');
  assert.equal(reviewHref('python'), '#/review');
  assert.equal(reviewHref('sql'), '#/course/sql/review');
  assert.equal(reviewHref('sql', 'sql-x y'), '#/course/sql/review?question=sql-x%20y');
  assert.ok(isCourseId('production') && !isCourseId('a/b') && !isCourseId('') && !isCourseId(null));
});

test('continueAcross follows the course read last and skips finished courses', () => {
  const entries = [{ id: 'python', course: course('python', 'p1', 'p2') }, { id: 'sql', course: course('sql', 's1', 's2') }];
  assert.equal(continueAcross(entries, []), null);
  assert.deepEqual([continueAcross(entries, [rec('p1', 10)]).courseId, continueAcross(entries, [rec('p1', 10)]).questionId], ['python', 'p2']);
  const both = [rec('p1', 10), rec('s1', 20)];
  assert.equal(continueAcross(entries, both).courseId, 'sql');
  assert.equal(continueAcross(entries, both).questionId, 's2');
  // sql is newest but finished: Continue falls back to Python
  const done = [rec('p1', 10), rec('s1', 20), rec('s2', 30)];
  assert.equal(continueAcross(entries, done).courseId, 'python');
  assert.equal(continueAcross(entries, [rec('p1', 1), rec('p2', 2), rec('s1', 3), rec('s2', 4)]), null);
  // answers from another course never count
  assert.equal(continueAcross([entries[1]], [rec('p1', 10)]), null);
});

test('activeCourse is the newest-read course, Python before any progress', () => {
  const entries = [{ id: 'sql', course: course('sql', 's1') }, { id: 'python', course: course('python', 'p1') }];
  assert.equal(activeCourse(entries, []).id, 'python');
  assert.equal(activeCourse(entries, [rec('s1', 5)]).id, 'sql');
  assert.equal(activeCourse(entries, [rec('s1', 5), rec('p1', 6)]).id, 'python');
  assert.equal(activeCourse([], []), null);
});
