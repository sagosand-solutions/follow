import test from 'node:test';
import assert from 'node:assert/strict';

function installFakeDom() {
  const mk = (tag) => ({ tag, attrs: {}, children: [], className: '', setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {}, append(...n) { this.children.push(...n); } });
  globalThis.document = { createElement: mk, createTextNode: (text) => ({ text }) };
}
const text = (n) => (n.text !== undefined ? n.text : n.children.map(text).join(''));
const find = (n, cls, out = []) => {
  if (n && n.children) {
    if ((n.className || '').split(' ').includes(cls)) out.push(n);
    for (const c of n.children) find(c, cls, out);
  }
  return out;
};

test('every reading-map row states its count as visible text next to the cells', async () => {
  installFakeDom();
  const { setMessages } = await import('../public/js/i18n.js');
  setMessages({ 'course.lesson.progress': '{answered} of {total} read', 'level.beginner': 'Beginner', 'map.legend': 'Map key' });
  const { store } = await import('../public/js/progress.js');
  const { readingMap } = await import('../public/js/components/map.js');
  store.recordAnswer({ questionId: 'q1', optionId: 'a', score: 1 });
  store.recordUnknown({ questionId: 'q3' });
  const course = { levels: [{ level: 'beginner', title: 'Foundations', lessons: [{ questions: ['q1', 'q2'] }, { questions: ['q3', 'q4', 'q5'] }] }] };
  const map = readingMap(course, 'q2');
  const tracks = find(map, 'map__track');
  assert.equal(tracks.length, 1);
  const [cells, count] = tracks[0].children;
  assert.equal(cells.attrs['aria-hidden'], 'true', 'the cells stay decoration');
  assert.equal(count.className, 'map__count');
  assert.equal(text(count), '2 of 5 read', 'a "not sure" counts as read, like the lesson cards');
  assert.equal(count.attrs['aria-hidden'], undefined, 'the count is not hidden from anyone');
});
