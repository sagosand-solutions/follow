import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Beginner labelling and entry points use the live catalog, course, and question files. Synthetic catalogs remain only for
// parser and negative/fallback cases.
const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const site = new URL('../public/data/', import.meta.url);
const realCatalog = JSON.parse(readFileSync(new URL('catalog.json', site), 'utf8'));
const B1 = realCatalog.courses.find((course) => course.id === 'read-like-a-computer');
assert.ok(B1, 'live catalog contains the B1 course');
const b1Course = JSON.parse(readFileSync(new URL(`courses/${B1.id}.json`, site), 'utf8'));
const startQuestion = JSON.parse(readFileSync(new URL(`questions/${B1.start_question}.json`, site), 'utf8'));
// The negative cases use their own catalog without the beginner fields, never the live one (which is marked).
const bareCatalog = { ...realCatalog, courses: realCatalog.courses.map(({ audience, path_step, ...rest }) => rest) };

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

let catalogFile = null;
globalThis.fetch = async (url) => {
  const path = String(url).replace(/^(.*\/)?data\//, '').replace(/\?.*$/, '');
  let body = null;
  if (path === 'catalog.json') body = catalogFile;
  else { try { body = JSON.parse(readFileSync(new URL(path, site), 'utf8')); } catch { body = null; } }
  return body ? { ok: true, status: 200, async json() { return body; } } : { ok: false, status: 404, async json() { return {}; } };
};

const { setMessages } = await import('../public/js/i18n.js');
setMessages(strings);
const { parseCatalog } = await import('../public/js/catalog.js');
const { catalogSection, alreadyCodeSign } = await import('../public/js/components/catalog-card.js');
const { store } = await import('../public/js/progress.js');
const data = await import('../public/js/data.js');
const text = (n) => (n.text !== undefined && !n.tag ? n.text : n.text());
const withCatalog = (raw) => { catalogFile = raw; data.clearDataCache(); };

test('the catalog keeps a valid audience and path step and nothing else', () => {
  const parsed = parseCatalog({ data_version: 1, courses: [
    { id: 'a', title: 'A', description: '', audience: 'beginner', path_step: 1 },
    { id: 'b', title: 'B', description: '', audience: 'expert', path_step: true },
    { id: 'c', title: 'C', description: '', audience: 'experienced', path_step: 4 },
    { id: 'd', title: 'D', description: '', path_step: '2' },
    { id: 'e', title: 'E', description: '', path_step: 2 },
    { id: 'f', title: 'F', description: '', path_step: 2 },
  ] });
  assert.deepEqual(parsed.map((c) => [c.id, c.audience, c.pathStep]), [['a', 'beginner', 1], ['b', null, null], ['c', 'experienced', null], ['d', null, null], ['e', null, null], ['f', null, null]]);
});

const courseMap = async (entries) => new Map(await Promise.all(entries.filter((e) => e.id !== 'missing').map(async (e) => [e.id, JSON.parse(readFileSync(new URL(`courses/${e.id}.json`, site), 'utf8'))])));

test('the path from the catalog: the beginner course is step 1 with “Skip ahead”; the headings say who the courses are for', async () => {
  store.reset();
  const entries = parseCatalog(realCatalog);
  const node = catalogSection(entries, await courseMap(entries));
  const steps = node.all((n) => n.className.split(' ').includes('path__step'));
  assert.equal(steps.length, 3);
  assert.match(text(steps[0]), /Read like a computer/);
  assert.match(text(steps[1]), /Coming soon/);
  assert.match(text(steps[2]), /Python: reading code/);
  for (const step of steps) assert.doesNotMatch(text(step), /For beginners|already code/, 'no audience pill under “Start here if you are new”');
  assert.equal(steps[0].all((n) => n.tag === 'a' && /#\/q\//.test(n.attrs.href || '')).length, 0, 'the page’s main button starts the course; no second Start');
  const deeper = node.all((n) => n.attrs.id === 'path-deeper')[0];
  assert.equal(text(deeper), 'For people who already code');
  let focused = null;
  deeper.focus = () => { focused = deeper; };
  steps[0].all((n) => n.tag === 'button' && /Skip ahead/.test(text(n)))[0].click();
  assert.equal(focused, deeper);
  const deeperCards = node.all((n) => n.tag === 'li' && n.className === 'course-card');
  assert.deepEqual(deeperCards.map((c) => /For people who already code/.test(text(c))), [false, false], 'the heading says it once');
  for (const card of node.all((n) => n.className.split(' ').includes('course-card'))) {
    assert.doesNotMatch(text(card), /Not started|in preparation/, 'a card shows no status before the first answer and no premium details');
  }
  store.recordAnswer({ questionId: 'b1-1-robot-keeps-each-printed-line', optionId: 'x', score: 1 });
  const again = catalogSection(entries, await courseMap(entries));
  assert.match(text(again.all((n) => n.className.split(' ').includes('path__step'))[0]), /1 of 30/);
  store.reset();
});

test('when a course off the path is not for people who already code, the heading stays “Go deeper” and the cards say who they are for', async () => {
  const entries = parseCatalog({ ...realCatalog, courses: realCatalog.courses.map((c) => (c.id === 'sql' ? (({ audience, ...rest }) => rest)(c) : c)) });
  const node = catalogSection(entries, await courseMap(entries));
  assert.equal(text(node.all((n) => n.attrs.id === 'path-deeper')[0]), 'Go deeper');
  const deeperCards = node.all((n) => n.tag === 'li' && n.className === 'course-card');
  assert.deepEqual(deeperCards.map((c) => /For people who already code/.test(text(c))), [true, false]);
});

test('no audience, no pill; a path course that is not loaded gets no link; a catalog without the fields keeps today’s path', async () => {
  const plain = parseCatalog(bareCatalog);
  const node = catalogSection(plain, await courseMap(plain));
  assert.doesNotMatch(text(node), /For beginners|already code/);
  const steps = node.all((n) => n.className.split(' ').includes('path__step'));
  assert.deepEqual(steps.map((s) => /Coming soon/.test(text(s))), [true, true, false]);
  assert.equal(node.all((n) => n.tag === 'button').length, 0, 'no skip button without a beginner course');
  const missing = parseCatalog({ ...bareCatalog, courses: [{ ...B1, id: 'missing' }, ...bareCatalog.courses] });
  const without = catalogSection(missing, await courseMap(missing));
  assert.match(text(without.all((n) => n.className.split(' ').includes('path__step'))[0]), /Coming soon/);
  assert.equal(without.all((n) => n.tag === 'a' && /#\/q\//.test(n.attrs.href || '')).length, 0);
});

test('“Already write code?” lists the courses marked for people who already code and the path’s non-beginner step, in catalog order', () => {
  const entries = parseCatalog({ ...bareCatalog, courses: [B1, { ...bareCatalog.courses[2], audience: 'experienced' }, bareCatalog.courses[1], { ...bareCatalog.courses[0], path_step: 3 }] });
  const sign = alreadyCodeSign(entries);
  assert.deepEqual(sign.all((n) => n.tag === 'a').map((a) => a.attrs.href), ['#/course/sql', '#/course'], 'production has neither mark: not listed');
  assert.match(text(sign), /^→Already write code\?This course starts from zero\. You will get more from:/);
  assert.equal(alreadyCodeSign(parseCatalog(bareCatalog)), null);
});

test('the live catalog marks Read like a computer as step 1 and keeps the other course labels', async () => {
  store.reset();
  const live = parseCatalog(realCatalog);
  assert.deepEqual(live.map((c) => [c.id, c.audience, c.pathStep]), [['python', null, 3], ['production', 'experienced', null], ['sql', 'experienced', null], ['read-like-a-computer', 'beginner', 1]]);
  const node = catalogSection(live, await courseMap(live));
  const steps = node.all((n) => n.className.split(' ').includes('path__step'));
  assert.deepEqual(steps.map((s) => /Coming soon/.test(text(s))), [false, true, false], 'step 1 loads, step 2 remains coming soon, and Python remains step 3');
  assert.match(text(steps[0]), /Read like a computer|For beginners/);
  assert.match(text(steps[2]), /Python: reading code/);
  assert.doesNotMatch(text(steps[2]), /For beginners|already code/);
  assert.equal(text(node.all((n) => n.attrs.id === 'path-deeper')[0]), 'For people who already code');
  assert.deepEqual(alreadyCodeSign(live).all((n) => n.tag === 'a').map((a) => a.attrs.href), ['#/course', '#/course/production', '#/course/sql']);
});

test('the beginner course page: “Beginner course · Free” and the sign; other course pages unchanged', async () => {
  withCatalog(realCatalog);
  store.reset();
  const { courseView } = await import('../public/js/views/course.js');
  const page = (await courseView({ id: B1.id })).node;
  assert.match(text(page), /Beginner course · Free/);
  assert.ok(page.all((n) => n.className === 'note already-code')[0]);
  const firstLesson = b1Course.levels[0].lessons[0];
  assert.equal(firstLesson.questions[0], B1.start_question, 'the course data begins at the catalog start question');
  assert.ok(page.all((n) => n.tag === 'a' && n.attrs.href === `#/lesson/${firstLesson.id}`).length, 'the course page links to its first real lesson');
  const { lessonView } = await import('../public/js/views/lesson.js');
  const lessonPage = (await lessonView({ id: firstLesson.id })).node;
  const firstQuestionLink = lessonPage.all((n) => n.tag === 'a' && n.attrs.href.startsWith('#/q/'))[0];
  assert.equal(firstQuestionLink.attrs.href, `#/q/${B1.start_question}`, 'the course page route reaches the catalog start question');
  const sql = (await courseView({ id: 'sql' })).node;
  assert.doesNotMatch(text(sql), /Beginner course|Already write code/);
  store.reset();
});

test('the sign is on the course page only: no question carries it, so the first question opens with its code', async () => {
  withCatalog(realCatalog);
  const { questionView } = await import('../public/js/views/question.js');
  for (const id of [B1.start_question, 'b1-1-robot-leaves-unprinted-values-quiet', 'b1-2-a-pocket-uses-its-latest-number']) {
    const other = (await questionView({ id, course: b1Course })).node;
    assert.equal(other.all((n) => n.className === 'note already-code').length, 0, id);
  }
});

test('direct link: the live B1 start question renders with its beginner-practice profile and four choices', async () => {
  withCatalog(realCatalog);
  store.reset();
  const { questionView } = await import('../public/js/views/question.js');
  const node = (await questionView({ id: B1.start_question })).node;
  assert.equal(startQuestion.answer_profile, 'beginner-practice');
  assert.match(text(node.all((n) => n.className === 'chips')[0]), /Practice/);
  const group = node.all((n) => n.className === 'options')[0];
  const choices = group.all((n) => n.tag === 'input' && n.attrs.name === 'answer');
  assert.equal(choices.filter((choice) => choice.attrs.value !== '__unknown').length, 4);
  assert.equal(choices.filter((choice) => choice.attrs.value === '__unknown').length, 1);
});

test('home: a new visitor’s main button starts the beginner course', async () => {
  withCatalog(realCatalog);
  store.reset();
  const { homeView } = await import('../public/js/views/home.js');
  const home = (await homeView()).node;
  const hero = home.all((n) => n.className === 'hero')[0];
  const main = hero.all((n) => n.tag === 'a' && /button--primary/.test(n.className))[0];
  assert.equal(main.attrs.href, `#/q/${B1.start_question}`);
});
