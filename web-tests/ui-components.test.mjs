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

test('licenceInfo reads the question licence field and never assumes the open licence', async () => {
  installFakeDom();
  const { licenceInfo } = await import('../public/js/components/ui.js');
  assert.deepEqual(licenceInfo('CC-BY-SA-4.0'), { key: 'q.license', params: {}, link: true });
  assert.deepEqual(licenceInfo('LicenseRef-Proprietary'), { key: 'licence.proprietary', params: {}, link: false });
  assert.deepEqual(licenceInfo('SOMETHING-ELSE'), { key: 'licence.other', params: { license: 'SOMETHING-ELSE' }, link: false });
  assert.equal(licenceInfo(undefined).params.license, 'unknown');
});

test('pluralTemplate picks the singular only for exactly one', async () => {
  installFakeDom();
  const { pluralTemplate } = await import('../public/js/components/ui.js');
  assert.equal(pluralTemplate(1, '{b} question', '{b} questions'), '{b} question');
  for (const n of [0, 2, 13]) assert.equal(pluralTemplate(n, 'one', 'other'), 'other');
});

test('every lesson with one question gets a singular pill in the real course', async () => {
  installFakeDom();
  const { readFileSync } = await import('node:fs');
  const course = JSON.parse(readFileSync(new URL('../public/data/courses/python.json', import.meta.url), 'utf8'));
  const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
  const { pluralTemplate, factPill } = await import('../public/js/components/ui.js');
  const one = course.levels.flatMap((l) => l.lessons).filter((l) => l.questions.length === 1);
  assert.ok(one.length > 0);
  for (const l of one) {
    const li = factPill(pluralTemplate(l.questions.length, strings['facts.question'], strings['facts.questions']), l.questions.length);
    assert.equal(li.children.map((c) => (c.text !== undefined ? c.text : c.children.map((x) => x.text).join(''))).join(''), '1 question');
  }
});
