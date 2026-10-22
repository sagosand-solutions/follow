import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// The whole local chain for the synthetic fixtures: render -> answer / "I'm not sure" -> review -> (trace is bound in the
// validator tests), in a small DOM. The fixtures are synthetic public test data, never part of a course or the site data.
const fixture = (name) => JSON.parse(readFileSync(new URL(`./fixtures/${name}-question.json`, import.meta.url), 'utf8'));
const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));

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
  get innerText() { return this.text(); }
}
const svgNode = (tag) => new Node(tag);
globalThis.document = { createElement: (t) => new Node(t), createDocumentFragment: () => new Node('#fragment'), createElementNS: (_ns, t) => svgNode(t), createTextNode: (text) => ({ text }), dispatchEvent() {}, addEventListener() {},
  getSelection: () => ({ rangeCount: 0, removeAllRanges() {}, addRange() {} }), createRange: () => ({ selectNodeContents() {} }), body: new Node('body'), documentElement: new Node('html') };
globalThis.window = { matchMedia: () => ({ matches: true }), scrollY: 0, innerHeight: 800, scrollTo() {}, addEventListener() {}, removeEventListener() {} };
globalThis.location = { hash: '' };
globalThis.requestAnimationFrame = (fn) => fn();
globalThis.Event = class { constructor(type) { this.type = type; } };

const served = { 'questions/synthetic-practice-question.json': fixture('practice'), 'questions/synthetic-standard-question.json': fixture('standard') };
globalThis.fetch = async (url) => {
  const key = String(url).replace(/^(.*\/)?data\//, '').replace(/\?.*$/, '');
  return served[key] ? { ok: true, status: 200, async json() { return served[key]; } } : { ok: false, status: 404, async json() { return {}; } };
};

const { setMessages } = await import('../public/js/i18n.js');
setMessages(strings);
const { CONFIG } = await import('../public/js/config.js');
const { store } = await import('../public/js/progress.js');
const { questionView } = await import('../public/js/views/question.js');
const { reviewQueue } = await import('../public/js/review.js');

const text = (n) => (n.text !== undefined && !n.tag ? n.text : n.text());
const course = { id: 'synthetic', title: 'Synthetic', levels: [{ level: 'beginner', lessons: [{ id: 'l', title: 'L', questions: ['synthetic-practice-question', 'synthetic-standard-question'] }] }] };

async function render(id, reviewMode = false) {
  const view = await questionView({ id, course, reviewMode });
  return view.node;
}
const answerRadio = (root, value) => root.all((n) => n.tag === 'input' && n.attrs.value === value)[0];
const checkButton = (root) => root.all((n) => n.tag === 'button' && /Check/.test(n.text()))[0];

for (const scoringUi of [false, true]) {
  test(`practice question (scoringUi=${scoringUi}): practice chip, the note only where scores exist, four options plus "I'm not sure", no challenge link and no points`, async () => {
    CONFIG.scoringUi = scoringUi;
    store.reset();
    const root = await render('synthetic-practice-question');
    const all = text(root);
    assert.match(all, /Practice/);
    // The note explains that practice never counts toward a score or leaderboard; without scoring there is nothing to explain.
    if (scoringUi) assert.match(all, /four choices and no points/);
    else assert.doesNotMatch(all, /four choices and no points|leaderboard/);
    assert.equal(root.all((n) => n.tag === 'a' && /challenge/.test(n.attrs.href || '')).length, 0, 'no challenge link');
    const radios = root.all((n) => n.tag === 'input' && n.attrs.type === 'radio');
    assert.equal(radios.length, 5, 'four options and the separate "not sure" row');
    assert.equal(radios.filter((r) => r.attrs.value === '__unknown').length, 1);
    assert.doesNotMatch(all, /first try|Official challenge/i);
    CONFIG.scoringUi = false;
  });
}

test('a standard question with scoringUi on keeps its challenge link and shows no practice mark', async () => {
  CONFIG.scoringUi = true;
  store.reset();
  const root = await render('synthetic-standard-question');
  assert.equal(root.all((n) => n.tag === 'a' && /#\/challenge\//.test(n.attrs.href || '')).length, 1, 'the ordinary challenge entry is unchanged');
  assert.doesNotMatch(text(root), /four choices and no points/);
  assert.equal(root.all((n) => n.tag === 'input' && n.attrs.type === 'radio').length, 6);
  CONFIG.scoringUi = false;
});

test('answering a practice question records a plain answer, shows the verdict without points, and the answer reaches the review chain', async () => {
  store.reset();
  const root = await render('synthetic-practice-question');
  const wrong = answerRadio(root, 'opt-2');
  wrong.click();
  checkButton(root).dispatchEvent({ type: 'click', preventDefault() {} });
  const [record] = store.answers();
  assert.equal(record.question_id, 'synthetic-practice-question');
  assert.equal('profile' in record, false, 'recorded exactly like any other answer');
  assert.equal(record.option_id, 'opt-2');
  const verdict = root.all((n) => n.className.includes('verdict'))[0];
  assert.ok(verdict, 'the verdict is shown');
  assert.doesNotMatch(text(verdict), /point/i, 'no points in the verdict');
  const queue = reviewQueue(course, store.answers());
  assert.ok(queue.some((entry) => entry.questionId === 'synthetic-practice-question'), 'a missed practice question is reviewed like any other');
});

test('"I\'m not sure" on a practice question is a plain record and shows no points; the standard question still shows its points', async () => {
  store.reset();
  const root = await render('synthetic-practice-question');
  answerRadio(root, '__unknown').click();
  checkButton(root).dispatchEvent({ type: 'click', preventDefault() {} });
  const [record] = store.answers();
  assert.deepEqual([record.outcome, 'profile' in record], ['unknown', false]);
  assert.doesNotMatch(text(root.all((n) => n.className.includes('verdict'))[0]), /point/i);
  store.reset();
  const standard = await render('synthetic-standard-question');
  answerRadio(standard, 'opt-0').click();
  checkButton(standard).dispatchEvent({ type: 'click', preventDefault() {} });
  assert.equal('profile' in store.answers()[0], false, 'a standard record is byte-identical to before');
  assert.match(text(standard.all((n) => n.className.includes('verdict'))[0]), /1 point/);
});

test('reopening an answered practice question keeps the practice presentation (review and revisit)', async () => {
  store.reset();
  const first = await render('synthetic-practice-question');
  answerRadio(first, 'opt-0').click();
  checkButton(first).dispatchEvent({ type: 'click', preventDefault() {} });
  const again = await render('synthetic-practice-question');
  assert.match(text(again), /Practice/);
  assert.doesNotMatch(text(again.all((n) => n.className.includes('verdict'))[0]), /point/i);
  const review = await render('synthetic-practice-question', true);
  assert.match(text(review), /Practice/);
  store.reset();
});


// The course header of a question: the course's symbol in its ring beside the lesson line, filling as soon as the answer is in.
test('the question\'s course header: the ring is decoration before the answer, then fills and is named with the course and the count', async () => {
  store.reset();
  const root = await render('synthetic-practice-question');
  const badgeOf = (r) => {
    const [where] = r.all((n) => n.className === 'question__where');
    assert.ok(where, 'the course header');
    assert.equal(where.children.length, 2);
    assert.equal(where.children[1].className, 'question__meta', 'beside the lesson line');
    return where.children[0];
  };
  const before = badgeOf(root);
  assert.equal(before.className, 'course-badge');
  assert.equal(before.attrs['aria-hidden'], 'true');
  assert.equal(before.all((n) => n.attrs.class === 'progress-ring__fill').length, 0);
  answerRadio(root, 'opt-0').click();
  checkButton(root).dispatchEvent({ type: 'click', preventDefault() {} });
  const after = badgeOf(root);
  assert.equal(after.className, 'course-badge course-badge--started');
  assert.equal(after.attrs.role, 'img');
  assert.equal(after.attrs['aria-label'], 'Synthetic: 1 of 2 read');
  assert.equal(after.all((n) => n.attrs.class === 'progress-ring__fill').length, 1);
  store.reset();
});

test('a question without a course has no course header', async () => {
  store.reset();
  const view = await questionView({ id: 'synthetic-standard-question' });
  assert.equal(view.node.all((n) => n.attrs.id === 'q-title').length, 1, 'the question itself is drawn');
  assert.equal(view.node.all((n) => n.className === 'question__where' || n.className.startsWith('course-badge')).length, 0);
});

// The scoring profile and free leaderboard views are registered only with CONFIG.scoringUi on and are server-only; the
// local/mock branches were removed. The 027 isolation (practice excluded, unknown profiles unavailable) is enforced and
// tested in the scoring model: web-tests/answer-profile.test.mjs and web-tests/scoring.test.mjs.
