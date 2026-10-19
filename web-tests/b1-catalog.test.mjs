import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Beginner labelling in the catalog, on the home path, on the course page and once in the first lesson, from catalog data
// only (`audience`, `path_step`). The beginner course is a fixture built from the pinned pilot questions; the other courses
// and their files are the site's real data.
const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const pilots = new URL('./fixtures/b1-pilots/questions/', import.meta.url);
const site = new URL('../public/data/', import.meta.url);
const realCatalog = JSON.parse(readFileSync(new URL('catalog.json', site), 'utf8'));
const B1 = { id: 'read-like-a-computer', title: 'Read like a computer', description: 'No programming experience needed. You read short programs and predict what the computer does, one line at a time.', language: 'python', free_questions: 4, start_question: 'b1-1-robot-keeps-each-printed-line', audience: 'beginner', path_step: 1 };
const b1Course = { id: B1.id, title: B1.title, language: 'python', levels: [{ level: 'beginner', title: 'Read like a computer', lessons: [
  { id: 'b1-1', title: 'The robot prints', questions: ['b1-1-robot-keeps-each-printed-line', 'b1-1-robot-leaves-unprinted-values-quiet'] },
  { id: 'b1-2', title: 'Pockets', questions: ['b1-2-a-pocket-uses-its-latest-number', 'b1-2-reading-a-second-pocket-keeps-its-number'] }] }] };
const marked = (courses) => courses.map((c) => ({ ...c, audience: 'experienced' }));

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
  else if (path === `courses/${B1.id}.json`) body = b1Course;
  else if (path.startsWith('questions/b1-')) { try { body = JSON.parse(readFileSync(new URL(path.slice('questions/'.length), pilots), 'utf8')); } catch { body = null; } }
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

const courseMap = async (entries) => new Map(await Promise.all(entries.filter((e) => e.id !== 'missing').map(async (e) => [e.id, e.id === B1.id ? b1Course : JSON.parse(readFileSync(new URL(`courses/${e.id}.json`, site), 'utf8'))])));

test('the path from the catalog: the beginner course is step 1 with Start and “Skip ahead”; every card says who it is for', async () => {
  store.reset();
  const entries = parseCatalog({ ...realCatalog, courses: [B1, ...marked(realCatalog.courses)] });
  const node = catalogSection(entries, await courseMap(entries));
  const steps = node.all((n) => n.className.split(' ').includes('path__step'));
  assert.equal(steps.length, 3);
  assert.match(text(steps[0]), /Read like a computer/);
  assert.match(text(steps[0]), /For beginners/);
  assert.match(text(steps[1]), /Coming soon/);
  assert.match(text(steps[2]), /Python: reading code/);
  const start = steps[0].all((n) => n.tag === 'a' && text(n) === 'Start')[0];
  assert.equal(start.attrs.href, '#/q/b1-1-robot-keeps-each-printed-line');
  assert.match(start.className, /button--primary/, 'the main button for a reader with no answers');
  const deeper = node.all((n) => n.attrs.id === 'path-deeper')[0];
  assert.equal(text(deeper), 'Go deeper');
  let focused = null;
  deeper.focus = () => { focused = deeper; };
  steps[0].all((n) => n.tag === 'button' && /Skip ahead/.test(text(n)))[0].click();
  assert.equal(focused, deeper);
  const deeperCards = node.all((n) => n.tag === 'li' && n.className === 'course-card');
  assert.deepEqual(deeperCards.map((c) => /For people who already code/.test(text(c))), [true, true]);
  store.recordAnswer({ questionId: 'b1-1-robot-keeps-each-printed-line', optionId: 'x', score: 1 });
  const again = catalogSection(entries, await courseMap(entries));
  assert.match(again.all((n) => n.tag === 'a' && text(n) === 'Start')[0].className, /button--secondary/);
  store.reset();
});

test('no audience, no pill; a path course that is not loaded gets no link; the real catalog keeps today’s path', async () => {
  const plain = parseCatalog(realCatalog);
  const node = catalogSection(plain, await courseMap(plain));
  assert.doesNotMatch(text(node), /For beginners|already code/);
  const steps = node.all((n) => n.className.split(' ').includes('path__step'));
  assert.deepEqual(steps.map((s) => /Coming soon/.test(text(s))), [true, true, false]);
  assert.equal(node.all((n) => n.tag === 'button').length, 0, 'no skip button without a beginner course');
  const missing = parseCatalog({ ...realCatalog, courses: [{ ...B1, id: 'missing' }, ...realCatalog.courses] });
  const without = catalogSection(missing, await courseMap(missing));
  assert.match(text(without.all((n) => n.className.split(' ').includes('path__step'))[0]), /Coming soon/);
  assert.equal(without.all((n) => n.tag === 'a' && /#\/q\//.test(n.attrs.href || '')).length, 0);
});

test('“Already write code?” lists only courses marked for people who already code, in catalog order', () => {
  const entries = parseCatalog({ ...realCatalog, courses: [B1, { ...realCatalog.courses[2], audience: 'experienced' }, realCatalog.courses[1], { ...realCatalog.courses[0], audience: 'experienced' }] });
  const sign = alreadyCodeSign(entries);
  assert.deepEqual(sign.all((n) => n.tag === 'a').map((a) => a.attrs.href), ['#/course/sql', '#/course']);
  assert.match(text(sign), /^→Already write code\?This course starts from zero\. You will get more from:/);
  assert.equal(alreadyCodeSign(parseCatalog(realCatalog)), null);
});

test('the beginner course page: “Beginner course · Free” and the sign; other course pages unchanged', async () => {
  withCatalog({ ...realCatalog, courses: [B1, ...marked(realCatalog.courses)] });
  const { courseView } = await import('../public/js/views/course.js');
  const page = (await courseView({ id: B1.id })).node;
  assert.match(text(page), /Beginner course · Free/);
  assert.ok(page.all((n) => n.className === 'note already-code')[0]);
  const sql = (await courseView({ id: 'sql' })).node;
  assert.doesNotMatch(text(sql), /Beginner course|Already write code/);
});

test('the sign once in the first lesson: above its first question only', async () => {
  withCatalog({ ...realCatalog, courses: [B1, ...marked(realCatalog.courses)] });
  const { questionView } = await import('../public/js/views/question.js');
  const first = (await questionView({ id: 'b1-1-robot-keeps-each-printed-line', course: b1Course })).node;
  assert.equal(first.all((n) => n.className === 'note already-code').length, 1);
  for (const id of ['b1-1-robot-leaves-unprinted-values-quiet', 'b1-2-a-pocket-uses-its-latest-number']) {
    const other = (await questionView({ id, course: b1Course })).node;
    assert.equal(other.all((n) => n.className === 'note already-code').length, 0, id);
  }
});

test('home: a new visitor’s main button starts the beginner course', async () => {
  withCatalog({ ...realCatalog, courses: [B1, ...marked(realCatalog.courses)] });
  store.reset();
  const { homeView } = await import('../public/js/views/home.js');
  const home = (await homeView()).node;
  const hero = home.all((n) => n.className === 'hero')[0];
  const main = hero.all((n) => n.tag === 'a' && /button--primary/.test(n.className))[0];
  assert.equal(main.attrs.href, '#/q/b1-1-robot-keeps-each-printed-line');
});
