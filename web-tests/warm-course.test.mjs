import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { text } from './support/dom-stub.mjs';

// The warmth carried into the courses: the course's symbol in its progress ring beside the course page's title, and a
// compact preview on the phone's home page.
const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const site = new URL('../public/data/', import.meta.url);
const read = (path) => JSON.parse(readFileSync(new URL(path, site), 'utf8'));
const catalogRaw = read('catalog.json');
const B1 = catalogRaw.courses.find((c) => c.id === 'read-like-a-computer');
const first = read(`questions/${B1.start_question}.json`);
const css = readFileSync(new URL('../public/css/components.css', import.meta.url), 'utf8');

globalThis.fetch = async (url) => {
  const path = String(url).replace(/^(.*\/)?data\//, '').replace(/\?.*$/, '');
  let body = null;
  try { body = read(path); } catch { body = null; }
  return body ? { ok: true, status: 200, async json() { return body; } } : { ok: false, status: 404, async json() { return {}; } };
};

const { setMessages } = await import('../public/js/i18n.js');
setMessages(strings);
const { store } = await import('../public/js/progress.js');
const { courseView } = await import('../public/js/views/course.js');
const { heroPreview, COMPACT_LINES } = await import('../public/js/components/hero-preview.js');

const byClass = (node, cls) => node.all((n) => n.className.split(' ').includes(cls));
const total = (id) => new Set(read(`courses/${id}.json`).levels.flatMap((l) => l.lessons.flatMap((x) => x.questions))).size;

test('the course page: the course’s symbol in its ring beside the title, decoration before the first answer', async () => {
  store.reset();
  for (const c of catalogRaw.courses) {
    const page = (await courseView({ id: c.id })).node;
    const [row] = byClass(page, 'course-head__title');
    assert.ok(row, c.id);
    const [badge, h1] = row.children;
    assert.match(badge.className, /^course-badge$/);
    assert.equal(badge.attrs['aria-hidden'], 'true');
    assert.equal(badge.all((n) => n.attrs.class === `course-icon course-icon--${c.id}`).length, 1, `${c.id} has its own symbol`);
    assert.equal(badge.all((n) => n.attrs.class === 'progress-ring__fill').length, 0);
    assert.equal(h1.tag, 'h1');
    assert.equal(text(h1), read(`courses/${c.id}.json`).title);
  }
});

test('the course page: after an answer the ring fills and is named with the count', async () => {
  store.reset();
  store.recordAnswer({ questionId: B1.start_question, optionId: 'x', score: 1 });
  const page = (await courseView({ id: B1.id })).node;
  const badge = byClass(page, 'course-head__title')[0].children[0];
  assert.equal(badge.className, 'course-badge course-badge--started');
  assert.equal(badge.attrs.role, 'img');
  assert.equal(badge.attrs['aria-label'], `1 of ${total(B1.id)} read`);
  assert.equal(badge.attrs['aria-hidden'], undefined);
  assert.equal(badge.all((n) => n.attrs.class === 'progress-ring__fill').length, 1);
  store.reset();
});

test('the ring sits beside the title on the course page and beside the lesson line on a question, at their own sizes', () => {
  assert.match(css, /\.course-head__title \{ display: flex; align-items: center; gap: var\(--s-4\); \}/);
  assert.match(css, /\.course-head__title \.course-badge \{ inline-size: 3\.5rem; block-size: 3\.5rem; \}/);
  assert.match(css, /\.question__where \{ display: flex; align-items: center; gap: var\(--s-3\); \}/);
  assert.match(css, /\.question__where \.course-badge \{ inline-size: 2\.75rem; block-size: 2\.75rem; \}/);
  assert.match(css, /\n\.question__where \.eyebrow::before \{ display: none; \}/, 'the ring leads the lesson line, so no dash beside it');
  assert.match(css, /\n\.question__where > \.question__meta \{ min-inline-size: 0; \}/, 'the lesson line wraps beside the ring');
  assert.equal(strings['course.badge.progress'], '{course}: {answered} of {total} read');
});

const withLines = (n) => ({ ...first, snippets: [{ ...first.snippets[0], code: Array.from({ length: n }, (_, i) => `x${i} = ${i}`).join('\n') + '\n' }] });
const codeClass = (q) => byClass(heroPreview(q, { href: '' }), 'hero-preview__code')[0].className;

test('the compact preview marks code longer than three lines, so the phone fades it out', () => {
  assert.equal(COMPACT_LINES, 3);
  assert.equal(codeClass(withLines(3)), 'hero-preview__code');
  assert.equal(codeClass(withLines(4)), 'hero-preview__code hero-preview__code--more');
  assert.equal(codeClass(withLines(9)), 'hero-preview__code hero-preview__code--long hero-preview__code--more');
});

// The phone block, from its start to its end.
const phone = css.slice(css.indexOf('@media (max-width: 59.99rem) {\n  .home > .hero { display: contents; }'), css.indexOf('\n}\n', css.indexOf('@media (max-width: 59.99rem) {\n  .home > .hero { display: contents; }')));

test('on the phone the preview is compact: right under the buttons, three lines and the question, no caption and no choices', () => {
  assert.ok(phone.length > 0);
  assert.doesNotMatch(phone, /\.hero-stage \{ display: none; \}/, 'the preview is shown on the phone');
  assert.ok(phone.indexOf('.home .hero__text { order: 1; }') < phone.indexOf('.home .hero-preview { order: 1;'), 'after the buttons (same order, later in the page)');
  assert.ok(phone.indexOf('.home .hero-preview { order: 1;') < phone.indexOf('.home > .catalog { order: 2; }'), 'before the courses');
  assert.match(phone, /\n  \.home \.hero-preview \{ order: 1; margin-block-start: calc\(var\(--s-4\) - var\(--s-8\)\); \}/, 'close under the buttons, not a page gap away');
  assert.match(phone, /\n  \.home \.hero-preview \.code__caption, \.home \.hero-preview__choices \{ display: none; \}/);
  assert.match(phone, /\n  \.home \.hero-preview \.line:nth-child\(n\+4\) \{ display: none; \}/);
  assert.match(phone, /\n  \.home \.hero-preview__code \{ padding-block: var\(--s-3\); max-block-size: none; mask-image: none; \}/, 'the desktop cut does not apply');
  assert.match(phone, /\n  \.home \.hero-preview__code--more \{ mask-image: linear-gradient\(to bottom, currentColor 55%, transparent\); \}/);
  assert.ok(phone.indexOf('.home .hero-preview__code {') < phone.indexOf('.home .hero-preview__code--more {'), 'the fade comes after the reset');
  assert.match(phone, /\n  \.home \.hero-preview__prompt \{ font-size: var\(--text-base\); \}/);
});
