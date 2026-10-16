import test from 'node:test';
import assert from 'node:assert/strict';

function installFakeDom() {
  const mk = (tag) => ({ tag, attrs: {}, children: [], className: '', setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {}, append(...n) { this.children.push(...n); } });
  globalThis.document = { createElement: mk, createTextNode: (text) => ({ text }) };
}
const text = (n) => (n.text !== undefined ? n.text : n.children.map(text).join(''));

test('fact pills render the translated template with the number in bold, wherever the placeholder sits', async () => {
  installFakeDom();
  const { factPill, facts, courseCounts } = await import('../public/js/components/ui.js');
  const li = factPill('{b} questions', 34);
  assert.equal(text(li), '34 questions');
  assert.deepEqual(li.children.map((c) => c.tag || 'text'), ['strong', 'text']);
  const sv = factPill('Totalt {b} frågor', 34);
  assert.equal(text(sv), 'Totalt 34 frågor');
  assert.equal(text(factPill('plain', 1)), 'plain');
  const list = facts([{ template: '{b} lessons', bold: 13 }, { template: '{b} questions', bold: undefined }, { template: '{b} levels', bold: '' }, { template: 'No account needed' }, null]);
  assert.equal(list.children.length, 2, 'a pill with {b} and no value is left out; a plain pill stays');
  assert.deepEqual(courseCounts({ levels: [{ lessons: [{ questions: ['a', 'b'] }, { questions: ['b', 'c'] }] }, { lessons: [{ questions: ['d'] }] }] }), { levels: 2, lessons: 3, questions: 4 });
  assert.deepEqual(courseCounts(null), { levels: 0, lessons: 0, questions: 0 });
});

test('eyebrow, note and source card are plain text nodes', async () => {
  installFakeDom();
  const { eyebrow, noteBox, sourceCard, showInstructionNote, NOTE_LIMIT } = await import('../public/js/components/ui.js');
  assert.equal(eyebrow('PYTHON <b>').children[0].text, 'PYTHON <b>');
  const n = noteBox({ title: 'Read the code.', text: 'Take your time.' });
  assert.equal(n.className, 'note');
  assert.equal(n.children[0].attrs['aria-hidden'], 'true');
  assert.equal(noteBox({ title: 't' }).children.length, 2, 'no empty text paragraph');
  assert.equal(sourceCard({ title: 'Source', text: 'x' }).className, 'source');
  const withLink = sourceCard({ title: 'Source', text: 'Written by A. Licence:', link: { href: 'https://example.invalid/l', label: 'CC BY-SA 4.0' } });
  assert.equal(withLink.children[1].children.at(-1).attrs.href, 'https://example.invalid/l');
  assert.equal(NOTE_LIMIT, 3);
  assert.equal(showInstructionNote(0), true);
  assert.equal(showInstructionNote(2), true);
  assert.equal(showInstructionNote(3), false, 'the note is gone once three questions are answered');
  assert.equal(showInstructionNote(undefined), false);
});
