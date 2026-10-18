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
  test(`practice question (scoringUi=${scoringUi}): practice chip and note, four options plus "I'm not sure", no challenge link and no points`, async () => {
    CONFIG.scoringUi = scoringUi;
    store.reset();
    const root = await render('synthetic-practice-question');
    const all = text(root);
    assert.match(all, /Practice/);
    assert.match(all, /four choices and no points/);
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


// ---- the local profile page over a mixed bank (standard + practice), fresh progress and a reload
const lessonCourse = (id, title, ...questions) => ({ id, title, levels: [{ level: 'beginner', title: 'L', lessons: [{ id: `${id}-lesson`, title: 'T', builds_on: [], questions }] }] });
const indexOf = (version, entries) => ({ data_version: version, onboarding_question: entries[0].id, questions: entries.map(([id, answer_profile]) => ({ id, type: 'output', level: 'beginner', difficulty: 0.2, concepts: ['py.assignment'], ...(answer_profile ? { answer_profile } : {}) })) });

function serveBank({ practiceIndex = indexOf(2, [['synthetic-practice-question', 'beginner-practice']]) } = {}) {
  Object.assign(served, {
    'catalog.json': { data_version: 1, courses: [{ id: 'python', title: 'Python', description: 'd', free_questions: 1 }, { id: 'practice', title: 'Practice course', description: 'd', free_questions: 1 }] },
    'courses/python.json': lessonCourse('python', 'Python', 'synthetic-standard-question'),
    'courses/practice.json': lessonCourse('practice', 'Practice course', 'synthetic-practice-question'),
    'index.json': indexOf(2, [['synthetic-standard-question', 'standard']]),
    'index-practice.json': practiceIndex,
  });
}
const { clearDataCache } = await import('../public/js/data.js');
const { profileView } = await import('../public/js/views/profile.js');
const totalText = (node) => node.all((n) => n.className.includes('score-big__value'))[0].text();
const rowOf = (node, title) => node.all((n) => n.tag === 'tr' && n.text().includes(title))[0];

test('the profile page: fresh progress, a mixed bank and a reload; practice never counts and unknown profiles give no points', async () => {
  CONFIG.scoringUi = false;
  serveBank();
  clearDataCache();
  store.reset();
  const fresh = (await profileView()).node;
  assert.equal(totalText(fresh), '0', 'fresh progress: the standard course alone makes the total (0), the practice-only course is not averaged in');
  assert.match(rowOf(fresh, 'Practice course').text(), /–/, 'a practice-only course shows no points');

  store.recordAnswer({ questionId: 'synthetic-practice-question', optionId: 'opt-0', score: 1 });
  store.recordAnswer({ questionId: 'synthetic-standard-question', optionId: 'opt-0', score: 1 });
  const answered = (await profileView()).node;
  assert.equal(totalText(answered), '1000', 'only the standard answer counts');
  clearDataCache();
  assert.equal(totalText((await profileView()).node), '1000', 'a reload gives the same result');

  serveBank({ practiceIndex: indexOf(1, [['synthetic-practice-question']]) });          // an old/stale index: profiles unknown
  clearDataCache();
  const unknown = (await profileView()).node;
  assert.equal(totalText(unknown), '–', 'unknown profiles: no total, never a guess');
  assert.match(rowOf(unknown, 'Practice course').text(), /–/);
  serveBank({ practiceIndex: indexOf(2, [['synthetic-practice-question']]) });         // current index but no profile on the entry
  clearDataCache();
  assert.equal(totalText((await profileView()).node), '–', 'an entry without a profile is refused, not read as standard');
  store.reset();
});

test('the local free list counts only standard answers, and shows nothing when the profiles are unknown', async () => {
  CONFIG.scoringUi = false;
  const { leaderboardView } = await import('../public/js/views/leaderboard.js');
  globalThis.location.hash = '#/leaderboard?list=free&period=all&course=all';
  const line = (node) => node.all((n) => n.tag === 'p' && n.attrs.role === 'status')[0].text();
  serveBank();
  clearDataCache();
  store.reset();
  store.recordAnswer({ questionId: 'synthetic-practice-question', optionId: 'opt-0', score: 1 });
  assert.equal(line((await leaderboardView()).node), strings['board.free.none'], 'a practice answer alone gives no local points');
  store.recordAnswer({ questionId: 'synthetic-standard-question', optionId: 'opt-0', score: 1 });
  assert.match(line((await leaderboardView()).node), /1000/, 'the standard answer counts, the practice one does not');
  serveBank({ practiceIndex: indexOf(1, [['synthetic-practice-question']]) });
  clearDataCache();
  assert.equal(line((await leaderboardView()).node), strings['board.free.none'], 'unknown profiles: no local points');
  store.reset();
  globalThis.location.hash = '';
});
