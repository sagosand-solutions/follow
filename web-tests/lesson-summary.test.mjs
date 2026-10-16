import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const root = new URL('../public/', import.meta.url).pathname;
const course = JSON.parse(readFileSync(`${root}data/courses/python.json`, 'utf8'));

test('lesson summaries are single-line text of 1 to 200 code points', () => {
  for (const level of course.levels) {
    for (const lesson of level.lessons) {
      if (lesson.summary === undefined) continue;
      assert.equal(typeof lesson.summary, 'string', lesson.id);
      const points = Array.from(lesson.summary).length;
      assert.ok(points >= 1 && points <= 200, `${lesson.id}: ${points}`);
      assert.doesNotMatch(lesson.summary, /[\u0000-\u001f\u007f]/, lesson.id);
    }
  }
});

// A minimal DOM: just enough for the h() helper. It records what a view would put in the page.
function installFakeDom() {
  const mk = (tag) => ({ tag, attrs: {}, children: [], className: '', setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {}, append(...n) { this.children.push(...n); } });
  globalThis.document = { createElement: mk, createTextNode: (text) => ({ text }) };
}

test('summaryNode renders markup characters as literal text and nothing when absent', async () => {
  installFakeDom();
  const { summaryNode } = await import('../public/js/components/summary.js');
  const node = summaryNode('Read <em>text</em> and `code` with n < 10', 'lead lesson-summary');
  assert.equal(node.tag, 'p');
  assert.equal(node.className, 'lead lesson-summary');
  assert.equal(node.children.length, 1, 'one child only: no element was created from the text');
  assert.equal(node.children[0].text, 'Read <em>text</em> and `code` with n < 10');
  assert.equal(summaryNode('x', 'c', 'span').tag, 'span');
  for (const absent of [undefined, null, '']) assert.equal(summaryNode(absent, 'c'), null);
});

test('both views use summaryNode (no inline markup path)', () => {
  for (const file of ['js/views/course.js', 'js/views/lesson.js']) {
    const src = readFileSync(`${root}${file}`, 'utf8');
    assert.match(src, /summaryNode\(lesson\.summary/, file);
    assert.doesNotMatch(src, /innerHTML|insertAdjacentHTML/, file);
  }
});
