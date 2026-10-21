import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// "You practised …", "New word" cards and the end of a beginner course, from the optional lesson fields. The course is the
// pinned fixture built from the approved pilot questions and their authors' lesson notes; the other courses are the site's.
const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const fixtures = new URL('./fixtures/b1-pilots/', import.meta.url);
const site = new URL('../public/data/', import.meta.url);
const realCatalog = JSON.parse(readFileSync(new URL('catalog.json', site), 'utf8'));
const course = JSON.parse(readFileSync(new URL('course.json', fixtures), 'utf8'));
const B1 = { id: course.id, title: course.title, description: 'No programming experience needed.', language: 'python', free_questions: 4, start_question: 'b1-1-robot-keeps-each-printed-line', audience: 'beginner', path_step: 1 };
// The decided marking: Python is the path's last step with no audience pill; Production and SQL are for people who already code.
const catalogWith = (...extra) => ({ ...realCatalog, courses: [B1, ...extra, ...realCatalog.courses.map((c) => (c.id === 'python' ? { ...c, path_step: 3 } : { ...c, audience: 'experienced' }))] });

class Node {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.className = ''; this.handlers = {}; this.parent = null; this.checked = false; this.hidden = false; this.textContent = ''; this.dataset = {}; this.style = {}; }
  get classList() { const n = this; return { add: (c) => { n.className = [...new Set([...n.className.split(' ').filter(Boolean), c])].join(' '); }, remove: (c) => { n.className = n.className.split(' ').filter((x) => x && x !== c).join(' '); }, contains: (c) => n.className.split(' ').includes(c), toggle() {} }; }
  setAttribute(k, v) { this.attrs[k] = v; if (k === 'class') this.className = v; if (k.startsWith('data-')) this.dataset[k.slice(5)] = v; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn); }
  removeEventListener() {}
  dispatchEvent(e) { for (const fn of this.handlers[e.type] || []) fn(e); if (this.parent && this.parent.dispatchEvent) this.parent.dispatchEvent(e); return true; }
  append(...nodes) { for (const n of nodes) { if (n && typeof n === 'object') n.parent = this; this.children.push(n); } }
  appendChild(node) { this.append(node); return node; }
  insertBefore(node) { this.append(node); return node; }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  focus() {} blur() {} scrollIntoView() {} getBoundingClientRect() { return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 }; }
  click() { if (this.tag === 'input') { this.checked = true; this.dispatchEvent({ type: 'change', target: this }); } this.dispatchEvent({ type: 'click', target: this, preventDefault() {} }); }
  all(pred, out = []) { if (pred(this)) out.push(this); for (const c of this.children) if (c && c.tag) c.all(pred, out); return out; }
  matches(sel) { return sel.split(',').some((part) => { const s = part.trim(); if (s.startsWith('.')) return this.className.split(' ').includes(s.slice(1)); if (s.startsWith('[data-option=')) return this.dataset.option === s.slice(14, -2); if (s.startsWith('#')) return this.attrs.id === s.slice(1); return this.tag === s; }); }
  querySelector(sel) { return this.all((n) => n !== this && n.matches(sel))[0] || null; }
  querySelectorAll(sel) { return this.all((n) => n !== this && n.matches(sel)); }
  closest() { return null; }
  text() { return (this.textContent || '') + this.children.map((c) => (c.tag ? c.text() : c.text)).join(''); }
  get value() { return this.attrs.value ?? ''; }
  set value(v) { this.attrs.value = v; }
  get firstChild() { return this.children[0] || null; }
  get isConnected() { return true; }
  insertAdjacentElement(where, node) { const list = this.parent.children; list.splice(list.indexOf(this) + 1, 0, node); node.parent = this.parent; return node; }
  get ownerDocument() { return globalThis.document; }
  get innerText() { return this.text(); }
}
const svgNode = (tag) => new Node(tag);
globalThis.document = { createElement: (t) => new Node(t), createDocumentFragment: () => new Node('#fragment'), createElementNS: (_ns, t) => svgNode(t), createTextNode: (text) => ({ text }), dispatchEvent() {}, addEventListener() {},
  getSelection: () => ({ rangeCount: 0, removeAllRanges() {}, addRange() {} }), createRange: () => ({ selectNodeContents() {} }), body: new Node('body'), documentElement: new Node('html') };
globalThis.window = { matchMedia: () => ({ matches: true }), scrollY: 0, innerHeight: 800, scrollTo() {}, addEventListener() {}, removeEventListener() {} };
globalThis.location = { hash: '' };
globalThis.requestAnimationFrame = (fn) => fn();
globalThis.Event = class { constructor(type) { this.type = type; } };

let catalogFile = catalogWith();
let extraCourses = {};
globalThis.fetch = async (url) => {
  const path = String(url).replace(/^(.*\/)?data\//, '').replace(/\?.*$/, '');
  let body = null;
  if (path === 'catalog.json') body = catalogFile;
  else if (path === `courses/${course.id}.json`) body = course;
  else if (extraCourses[path]) body = extraCourses[path];
  else if (path.startsWith('questions/b1-')) { try { body = JSON.parse(readFileSync(new URL(`questions/${path.slice('questions/'.length)}`, fixtures), 'utf8')); } catch { body = null; } }
  else { try { body = JSON.parse(readFileSync(new URL(path, site), 'utf8')); } catch { body = null; } }
  return body ? { ok: true, status: 200, async json() { return body; } } : { ok: false, status: 404, async json() { return {}; } };
};

const { setMessages } = await import('../public/js/i18n.js');
setMessages(strings);
const extras = await import('../public/js/lesson-extras.js');
const data = await import('../public/js/data.js');
const { store } = await import('../public/js/progress.js');
const { questionView } = await import('../public/js/views/question.js');
const { lessonView } = await import('../public/js/views/lesson.js');
const { courseView } = await import('../public/js/views/course.js');
const text = (n) => (n.text !== undefined && !n.tag ? n.text : n.text());
const lesson = (id) => course.levels[0].lessons.find((l) => l.id === id);
// A real option of each question (a record for an option that does not exist does not count as an answer).
const answerAll = (ids) => ids.forEach((id) => {
  const option = JSON.parse(readFileSync(new URL(`questions/${id}.json`, fixtures), 'utf8')).options[0];
  store.recordAnswer({ questionId: id, optionId: option.id, score: option.score });
});
const fresh = (catalog = catalogWith()) => { catalogFile = catalog; data.clearDataCache(); store.reset(); };

test('practice_summary: a short plain fragment or nothing', () => {
  assert.equal(extras.practiceSummary(lesson('b1-2')), 'following the numbers held by named pockets');
  for (const bad of ['x'.repeat(81), ' leading space', 'two\nlines', 'a\u0085b', 'a\u2028b', 'a\u2029b', '', 42, null]) assert.equal(extras.practiceSummary({ practice_summary: bad }), null, JSON.stringify(bad));
  assert.equal(extras.practiceSummary({}), null);
});

test('introductions: exact shape, a list of questions in the lesson, unique ids, in order; one card can come back', () => {
  assert.deepEqual(extras.introductionsFor(lesson('b1-2'), 'b1-2-a-pocket-uses-its-latest-number').map((x) => x.id), ['B12-I1', 'B12-I3']);
  assert.deepEqual(extras.introductionsFor(lesson('b1-2'), 'b1-2-reading-a-second-pocket-keeps-its-number').map((x) => x.id), ['B12-I5']);
  const [q1, q2] = lesson('b1-2').questions;
  const base = lesson('b1-2').introductions[0];
  const mk = (list) => extras.introductions({ questions: lesson('b1-2').questions, introductions: list }).map((x) => x.id);
  const reused = { questions: [q1, q2], introductions: [{ ...base, before_question_ids: [q1, q2] }, lesson('b1-2').introductions[2]] };
  assert.deepEqual(extras.introductionsFor(reused, q2).map((x) => x.id), ['B12-I1', 'B12-I5'], 'the same card before both questions, in lesson order');
  assert.deepEqual(mk([base]), ['B12-I1']);
  assert.deepEqual(mk([base, { ...base, term: 'Again' }]), [], 'an id used twice is nobody’s');
  assert.deepEqual(mk([{ ...base, extra: 1 }]), []);
  assert.deepEqual(mk([{ ...base, before_question_ids: ['b1-1-robot-keeps-each-printed-line'] }]), [], 'question outside the lesson');
  assert.deepEqual(mk([{ ...base, before_question_ids: [] }]), [], 'an empty list');
  assert.deepEqual(mk([{ ...base, before_question_ids: [q1, q1] }]), [], 'a question listed twice');
  assert.deepEqual(mk([{ ...base, before_question_ids: q1 }]), [], 'a single id is not a list');
  const { before_question_ids: _, ...singular } = base;
  assert.deepEqual(mk([{ ...singular, before_question_id: q1 }]), [], 'the old singular field is not read');
  assert.deepEqual(mk([{ ...base, term: 'x'.repeat(61) }]), []);
  assert.deepEqual(mk([{ ...base, id: 'bad id' }]), []);
  assert.deepEqual(mk([{ ...base, example: 7 }]), []);
});

test('“New word” cards above the code, every time the question opens, as text', async () => {
  fresh();
  const node = (await questionView({ id: 'b1-2-a-pocket-uses-its-latest-number', course })).node;
  const cards = node.all((n) => n.className === 'new-word');
  assert.equal(cards.length, 1, 'one block for the question');
  assert.deepEqual(cards[0].all((n) => n.className === 'new-word__term').map(text), ['Give a name a number (assignment)', 'The latest number for each name']);
  assert.match(text(cards[0]), /^New words/);
  assert.match(text(cards[0]), /For example: pocket = 1 stores 1;/);
  const one = (await questionView({ id: 'b1-2-reading-a-second-pocket-keeps-its-number', course })).node;
  assert.match(text(one.all((n) => n.className === 'new-word')[0]), /^New word(?!s)/, 'one word: singular label');
  assert.deepEqual(one.all((n) => n.className === 'new-word__term').map(text), ['Read another pocket now']);
  const top = node.children.map((c) => c.className);
  assert.ok(top.indexOf('new-word') < top.indexOf('q-grid'), 'above the code');
  answerAll(['b1-2-a-pocket-uses-its-latest-number']);
  const again = (await questionView({ id: 'b1-2-a-pocket-uses-its-latest-number', course })).node;
  assert.equal(again.all((n) => n.className === 'new-word__term').length, 2, 'still there after answering and reopening');
  const tricky = { ...course, levels: [{ ...course.levels[0], lessons: [{ ...lesson('b1-2'), introductions: [{ ...lesson('b1-2').introductions[0], term: '<b>bold</b>' }] }] }] };
  const raw = (await questionView({ id: 'b1-2-a-pocket-uses-its-latest-number', course: tricky })).node;
  const term = raw.all((n) => n.className === 'new-word__term')[0];
  assert.equal(text(term), '<b>bold</b>');
  assert.equal(term.all((n) => n.tag === 'b').length, 0, 'never markup');
  store.reset();
});

// A storage double for the per-word choice: a Map, or one that throws on every call (private mode, blocked storage).
const memoryStorage = () => { const m = new Map(); return { m, getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }; };
const brokenStorage = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } };
const words = (root) => root.all((n) => n.className.split(' ').includes('new-word__item')).map((item) => {
  const button = item.all((n) => n.tag === 'button')[0];
  const panel = item.all((n) => n.className === 'new-word__text')[0];
  const sign = button.all((n) => n.className === 'new-word__sign')[0];
  return { item, button, panel, term: text(button.all((n) => n.className === 'new-word__term')[0]), open: button.attrs['aria-expanded'] === 'true', hidden: 'hidden' in panel.attrs, sign: text(sign), met: item.className.split(' ').includes('new-word__item--met') };
});
const reusedCourse = () => ({ ...course, levels: [{ ...course.levels[0], lessons: course.levels[0].lessons.map((l) => (l.id !== 'b1-2' ? l : { ...l, introductions: l.introductions.map((x) => (x.id === 'B12-I1' ? { ...x, before_question_ids: l.questions } : x)) })) }] });
const [P1, P2] = ['b1-2-a-pocket-uses-its-latest-number', 'b1-2-reading-a-second-pocket-keeps-its-number'];

test('each new word is a disclosure: a real button with aria-expanded and aria-controls, a visible +/−, the panel follows', async () => {
  fresh();
  globalThis.localStorage = memoryStorage();
  try {
    const node = (await questionView({ id: P1, course })).node;
    const list = words(node);
    assert.equal(list.length, 2);
    for (const w of list) {
      assert.equal(w.button.tag, 'button', 'a native button, so Enter and Space work and it is in the Tab order');
      assert.equal(w.button.attrs.type, 'button');
      assert.equal(w.item.children[0].tag, 'h3', 'the term is a heading under "New words"');
      assert.equal(w.button.attrs['aria-controls'], w.panel.attrs.id);
      assert.match(w.panel.attrs.id, /^new-word-panel-B12-I[13]$/);
      assert.notEqual(w.panel.attrs.id, 'new-word-label');
      assert.equal(w.button.all((n) => n.className === 'new-word__sign')[0].attrs['aria-hidden'], 'true', 'the state is announced from aria-expanded, not from the sign');
      assert.deepEqual([w.open, w.hidden, w.sign], [true, false, '−'], 'open the first time');
    }
    list[0].button.click();
    let now = words(node)[0];
    assert.deepEqual([now.open, now.hidden, now.sign], [false, true, '+'], 'folded by its own button');
    assert.deepEqual([words(node)[1].open, words(node)[1].hidden], [true, false], 'the other word is not touched');
    list[0].button.click();
    now = words(node)[0];
    assert.deepEqual([now.open, now.hidden, now.sign], [true, false, '−'], 'and open again');
  } finally { delete globalThis.localStorage; store.reset(); }
});

test('new words open the first time the reader meets them and fold after that; the reader’s own choice is remembered per word', async () => {
  fresh();
  const storage = memoryStorage();
  globalThis.localStorage = storage;
  const reused = reusedCourse();
  // Each word's panel is hidden exactly when its button says it is folded, in both states.
  const state = async (id) => Object.fromEntries(words((await questionView({ id, course: reused })).node).map((w) => { assert.equal(w.hidden, !w.open, `${w.term}: panel hidden iff folded`); return [w.term, w.open]; }));
  try {
    assert.deepEqual(await state(P1), { 'Give a name a number (assignment)': true, 'The latest number for each name': true }, 'first visit: open');
    assert.deepEqual(await state(P1), { 'Give a name a number (assignment)': false, 'The latest number for each name': false }, 'a revisit: folded');
    assert.deepEqual(await state(P2), { 'Give a name a number (assignment)': false, 'Read another pocket now': true }, 'a later question: the known word folded, the new one open');
    // The reader opens a folded word: it stays open on every question where it comes back.
    const node = (await questionView({ id: P2, course: reused })).node;
    words(node).find((w) => w.term.startsWith('Give'))?.button.click();
    assert.equal(storage.m.get('follow.v0.word.read-like-a-computer.b1-2.B12-I1'), 'open');
    assert.equal((await state(P1))['Give a name a number (assignment)'], true, 'remembered on the other question');
    // And a word folded by hand stays folded, even one that was open by default.
    fresh();
    storage.m.clear();
    const first = (await questionView({ id: P1, course: reused })).node;
    words(first).find((w) => w.term.startsWith('The latest')).button.click();
    assert.equal(storage.m.get('follow.v0.word.read-like-a-computer.b1-2.B12-I3'), 'closed');
    assert.equal((await state(P1))['The latest number for each name'], false);
  } finally { delete globalThis.localStorage; store.reset(); }
});

const block = (root) => root.all((n) => n.className.split(' ').includes('new-word'))[0];
const label = (root) => text(block(root).all((n) => n.className === 'new-word__label')[0]);

test('new words come first and open under “New words”; reminders follow, folded and quieter; all reminders get their own heading', async () => {
  fresh();
  globalThis.localStorage = memoryStorage();
  const reused = reusedCourse();
  try {
    const first = (await questionView({ id: P1, course: reused })).node;
    assert.equal(label(first), 'New words', 'all new');
    assert.deepEqual(words(first).map((w) => [w.term, w.open, w.met]), [['Give a name a number (assignment)', true, false], ['The latest number for each name', true, false]]);
    assert.equal(block(first).className, 'new-word');
    // Mixed: one word met on the first question, one new here. The new one leads, whatever the lesson's order.
    const mixed = (await questionView({ id: P2, course: reused })).node;
    assert.equal(label(mixed), 'New word', 'the heading names the new word');
    assert.deepEqual(words(mixed).map((w) => [w.term, w.open, w.met]), [['Read another pocket now', true, false], ['Give a name a number (assignment)', false, true]]);
    assert.equal(block(mixed).className, 'new-word', 'a block with a new word keeps its attention edge');
    // All reminders: a neutral heading, a quieter block, every word folded and still a working disclosure.
    const again = (await questionView({ id: P1, course: reused })).node;
    assert.equal(label(again), "Words you've met");
    assert.equal(block(again).className, 'new-word new-word--met');
    const met = words(again);
    assert.deepEqual(met.map((w) => [w.open, w.met, w.sign]), [[false, true, '+'], [false, true, '+']]);
    for (const w of met) {
      assert.equal(w.button.tag, 'button');
      assert.equal(w.button.attrs['aria-controls'], w.panel.attrs.id);
    }
    met[0].button.click();
    assert.deepEqual([words(again)[0].open, words(again)[0].hidden, words(again)[0].sign, words(again)[0].met], [true, false, '−', true], 'opened by hand, still a reminder');
    const one = (await questionView({ id: P2, course: reused })).node;
    assert.equal(label(one), "Words you've met");
    fresh();
    globalThis.localStorage = memoryStorage();
    const single = { ...course, levels: [{ ...course.levels[0], lessons: course.levels[0].lessons.map((l) => (l.id !== 'b1-2' ? l : { ...l, introductions: l.introductions.filter((x) => x.id === 'B12-I5') })) }] };
    const s1 = (await questionView({ id: P2, course: single })).node;
    assert.equal(label(s1), 'New word');
    const s2 = (await questionView({ id: P2, course: single })).node;
    assert.equal(label(s2), "A word you've met");
  } finally { delete globalThis.localStorage; store.reset(); }
});

test('a word that is new here stays new and first when answering redraws the page', async () => {
  fresh();
  globalThis.localStorage = memoryStorage();
  const reused = reusedCourse();
  try {
    await questionView({ id: P1, course: reused });
    const node = (await questionView({ id: P2, course: reused })).node;
    node.all((n) => n.tag === 'input' && n.attrs.type === 'radio')[0].click();
    node.all((n) => n.tag === 'button' && /Check/.test(n.text()))[0].dispatchEvent({ type: 'click', preventDefault() {} });
    assert.ok(node.all((n) => n.className.split(' ').includes('verdict')).length, 'answered and redrawn');
    assert.equal(label(node), 'New word');
    assert.deepEqual(words(node).map((w) => [w.term, w.met]), [['Read another pocket now', false], ['Give a name a number (assignment)', true]]);
  } finally { delete globalThis.localStorage; store.reset(); }
});

test('answering redraws the page but keeps each word as the reader left it', async () => {
  fresh();
  globalThis.localStorage = memoryStorage();
  try {
    const node = (await questionView({ id: P1, course })).node;
    words(node)[0].button.click();
    node.all((n) => n.tag === 'input' && n.attrs.type === 'radio')[0].click();
    node.all((n) => n.tag === 'button' && /Check/.test(n.text()))[0].dispatchEvent({ type: 'click', preventDefault() {} });
    assert.ok(node.all((n) => n.className.split(' ').includes('verdict')).length, 'answered and redrawn');
    assert.deepEqual(words(node).map((w) => w.open), [false, true], 'not reset to the default by the redraw');
  } finally { delete globalThis.localStorage; store.reset(); }
});

test('without storage, or with storage that throws, every word opens every time and the buttons still work', async () => {
  for (const storage of [undefined, brokenStorage]) {
    fresh();
    if (storage) globalThis.localStorage = storage;
    try {
      for (let visit = 0; visit < 2; visit += 1) {
        const node = (await questionView({ id: P1, course })).node;
        assert.deepEqual(words(node).map((w) => [w.open, w.met]), [[true, false], [true, false]], `visit ${visit + 1}`);
        assert.equal(label(node), 'New words', 'nothing remembered: every word is new');
        words(node)[0].button.click();
        assert.equal(words(node)[0].open, false, 'the button works in memory mode');
      }
    } finally { delete globalThis.localStorage; store.reset(); }
  }
});

test('“You practised …” only once the lesson is fully answered, on the lesson page and its card', async () => {
  fresh();
  const before = (await lessonView({ id: 'b1-2' })).node;
  assert.doesNotMatch(text(before), /You practised/);
  answerAll(lesson('b1-2').questions.slice(0, 1));
  const half = (await courseView({ id: course.id })).node;
  assert.doesNotMatch(text(half), /You practised/, 'not on a card with one of two answered');
  answerAll(lesson('b1-2').questions);
  const after = (await lessonView({ id: 'b1-2' })).node;
  assert.match(text(after), /You practised following the numbers held by named pockets\./);
  const page = (await courseView({ id: course.id })).node;
  const cards = page.all((n) => n.className === 'lesson-card');
  assert.deepEqual(cards.map((c) => /You practised/.test(text(c))), [false, true]);
  store.reset();
});

test('the end of the course: practised, the next step on the path (coming soon), and a way on for more', async () => {
  fresh();
  answerAll(course.levels[0].lessons[0].questions);
  assert.equal((await courseView({ id: course.id })).node.all((n) => n.className === 'course-end').length, 0, 'not before the end');
  answerAll(lesson('b1-2').questions);
  const page = (await courseView({ id: course.id })).node;
  const end = page.all((n) => n.className === 'course-end')[0];
  assert.match(text(end), /^You finished Read like a computer\.You practised reading short programs line by line\.Next on the path: Small programs from everyday life\. Coming soon\.Ready for more\? Python: reading code$/);
  assert.equal(end.all((n) => n.tag === 'a').map((a) => a.attrs.href).join(), '#/course');
  const last = (await lessonView({ id: 'b1-2' })).node;
  assert.equal(last.all((n) => n.className === 'course-end').length, 1, 'also at the end of the last lesson');
  store.reset();
});

test('the next step links only to a course that loads', async () => {
  const B2 = { id: 'small-programs', title: 'Small programs from everyday life', description: 'Next.', language: 'python', audience: 'beginner', path_step: 2 };
  fresh(catalogWith(B2));
  answerAll(course.levels[0].lessons.flatMap((l) => l.questions));
  let end = (await courseView({ id: course.id })).node.all((n) => n.className === 'course-end')[0];
  assert.match(text(end), /Next on the path: Small programs from everyday life\. Coming soon\./, 'catalog entry without a course file: no link');
  extraCourses = { 'courses/small-programs.json': { id: 'small-programs', title: B2.title, language: 'python', levels: [] } };
  data.clearDataCache();
  end = (await courseView({ id: course.id })).node.all((n) => n.className === 'course-end')[0];
  assert.equal(end.all((n) => n.tag === 'a')[0].attrs.href, '#/course/small-programs');
  assert.match(text(end), /Next: Small programs from everyday life/);
  extraCourses = {};
  store.reset();
});

test('a course that is not for beginners has no end card and no practised line', async () => {
  fresh({ ...realCatalog, courses: [{ ...B1, audience: 'experienced', path_step: undefined }, ...realCatalog.courses.map(({ audience, path_step, ...rest }) => rest)] });
  answerAll(course.levels[0].lessons.flatMap((l) => l.questions));
  const page = (await courseView({ id: course.id })).node;
  assert.equal(page.all((n) => n.className === 'course-end').length, 0);
  store.reset();
});

test('a reminder looks quieter without new text: muted term, thin sign, a hairline after the new words, a block of reminders without the attention edge', () => {
  const css = readFileSync(new URL('../public/css/components.css', import.meta.url), 'utf8');
  assert.match(css, /\.new-word__item--met \.new-word__term \{ color: var\(--muted\); \}/);
  assert.match(css, /\.new-word__item--met \.new-word__sign \{ border-width: 1px; color: var\(--muted\); font-weight: 400; \}/);
  assert.match(css, /\.new-word__item--met \.new-word__toggle \{ font-weight: 600; \}/);
  assert.match(css, /\.new-word__item:not\(\.new-word__item--met\) \+ \.new-word__item--met \{ border-top: 1px solid var\(--line\);/);
  assert.match(css, /\.new-word--met \{ border-inline-start-color: var\(--line-strong\); \}/);
});

test('the word block on its own: without a set of new words every word is new and open', async () => {
  const { newWordCards } = await import('../public/js/components/beginner.js');
  const intros = lesson('b1-2').introductions.slice(0, 2).map((x) => ({ id: x.id, questionIds: x.before_question_ids, term: x.term, explanation: x.explanation, example: x.example }));
  const [node] = newWordCards(intros);
  assert.equal(node.className, 'new-word');
  assert.equal(text(node.all((n) => n.className === 'new-word__label')[0]), 'New words');
  assert.deepEqual(node.all((n) => n.className.split(' ').includes('new-word__item')).map((i) => i.className), ['new-word__item', 'new-word__item']);
  assert.deepEqual(newWordCards([]), []);
});
