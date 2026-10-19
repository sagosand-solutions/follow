import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

// Beginner practice with four pinned, approved B1 pilot questions and their recorded traces (CC BY-SA 4.0 test fixtures, not
// course or site data): the narrator line of the step player, the warm line under the verdict and the folded instruction.
const dir = new URL('./fixtures/b1-pilots/', import.meta.url);
const raw = (path) => readFileSync(new URL(path, dir));
const json = (path) => JSON.parse(raw(path).toString('utf8'));
const IDS = ['b1-1-robot-keeps-each-printed-line', 'b1-1-robot-leaves-unprinted-values-quiet', 'b1-2-a-pocket-uses-its-latest-number', 'b1-2-reading-a-second-pocket-keeps-its-number'];
const PINNED = {
  'questions/b1-1-robot-keeps-each-printed-line.json': '261bfd29d037fea45ad95f48e6ac1218df51359ef4d97ba0d059039adf4eafef',
  'questions/b1-1-robot-leaves-unprinted-values-quiet.json': '41f1ae91bcad26976de361640f22b7255b2661dbd7453f677fa27bb2a8b67fcd',
  'questions/b1-2-a-pocket-uses-its-latest-number.json': '8ada83a31558d06a6c3f0017ddc214d5c8931139b05a7e09899d3e554df7b475',
  'questions/b1-2-reading-a-second-pocket-keeps-its-number.json': '135ae1507b702bad5b1b5e90593431d6b6fd641551fb75a4154f60381e936a7e',
  'traces/b1-1-robot-keeps-each-printed-line.json': '655033f386a485406a93ae034bad12d934cb4e099e1da4351e2a1434b81e9fb6',
  'traces/b1-1-robot-leaves-unprinted-values-quiet.json': '5075f4c209dd1cfb50478def99a773cb5010cc9b787421bebb725cd7586283c4',
  'traces/b1-2-a-pocket-uses-its-latest-number.json': 'b454b4be584b1378ba678b46045f128c6dbd6cbe84bee9c3d39a785539881086',
  'traces/b1-2-reading-a-second-pocket-keeps-its-number.json': 'eecd1360de83e06bbad2d1adfedbd9c3938e5a1e1a841d2acb6bef870ecbf2db',
};
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

const served = Object.fromEntries(IDS.flatMap((id) => [[`questions/${id}.json`, json(`questions/${id}.json`)], [`traces/${id}.json`, json(`traces/${id}.json`)]]));
globalThis.fetch = async (url) => {
  const key = String(url).replace(/^(.*\/)?data\//, '').replace(/\?.*$/, '');
  return served[key] ? { ok: true, status: 200, async json() { return served[key]; } } : { ok: false, status: 404, async json() { return {}; } };
};

const { setMessages } = await import('../public/js/i18n.js');
setMessages(strings);
const { store } = await import('../public/js/progress.js');
const { questionView } = await import('../public/js/views/question.js');
const { buildMoments } = await import('../public/js/trace.js');
const { narrate, tokensText, fill } = await import('../public/js/narrate.js');
const { createStepPlayer } = await import('../public/js/components/step-player.js');

const text = (n) => (n.text !== undefined && !n.tag ? n.text : n.text());
const traceOf = (id) => json(`traces/${id}.json`).traces[0];
// Every narrated sentence of a recording, in order; null where the player keeps its own sentence.
const said = (trace) => { const m = buildMoments(trace); return m.map((_, i) => { const s = narrate(trace, m, i); return s ? tokensText(s) : null; }); };

test('the fixtures are the approved bytes, pinned', () => {
  for (const [path, sum] of Object.entries(PINNED)) assert.equal(createHash('sha256').update(raw(path)).digest('hex'), sum, path);
  for (const id of IDS) assert.equal(json(`questions/${id}.json`).answer_profile, 'beginner-practice');
});

test('narrator on the pilots: new and changed values, printed lines, and a quiet line', () => {
  assert.deepEqual(said(traceOf('b1-2-a-pocket-uses-its-latest-number')), ['Line 1: pocket now holds 2.', 'Line 2: pocket changes from 2 to 5.', 'Line 3 prints 5. No variable changes.', null]);
  assert.deepEqual(said(traceOf('b1-2-reading-a-second-pocket-keeps-its-number')), ['Line 1: red now holds 4.', 'Line 2: blue now holds 4.', 'Line 3: red changes from 4 to 9.', 'Line 4 prints 4. No variable changes.', null]);
  assert.deepEqual(said(traceOf('b1-1-robot-keeps-each-printed-line')), ['Line 1 prints GO. No variable changes.', 'Line 2 prints GO. No variable changes.', 'Line 3 prints 2. No variable changes.', null]);
  assert.deepEqual(said(traceOf('b1-1-robot-leaves-unprinted-values-quiet')), ['Line 1 prints GO. No variable changes.', 'Line 2 runs. No variable changes.', 'Line 3 prints STOP. No variable changes.', null]);
});

test('names, values and output are code tokens, never markup built from text', () => {
  const trace = traceOf('b1-2-a-pocket-uses-its-latest-number');
  const m = buildMoments(trace);
  assert.deepEqual(narrate(trace, m, 1), [{ text: 'Line ' }, { text: '2' }, { text: ': ' }, { code: 'pocket' }, { text: ' changes from ' }, { code: '2' }, { text: ' to ' }, { code: '5' }, { text: '.' }]);
  assert.deepEqual(fill('{a} <b>{b}</b>', { a: { code: '<i>' }, b: 'x' }), [{ code: '<i>' }, { text: ' <b>' }, { text: 'x' }, { text: '</b>' }]);
});

// Small recordings in the trace format, for what the pilots do not show.
const rec = (stdout, steps) => ({ case_id: 'c', snippet_id: 'main', stdin: '', stdout, truncated: false, exception: null, steps: [{ depth: 0, event: 'call', function: '<module>', line: 1, locals: {}, stdout_len: 0 }, ...steps.map(([event, line, locals, len]) => ({ depth: 0, event, function: '<module>', line, locals, stdout_len: len }))] });

test('several changes and output in one step become one sentence; an empty printed line is named', () => {
  assert.equal(said(rec('', [['line', 1, {}, 0], ['return', 1, { a: '1', b: '2' }, 0]]))[0], 'Line 1: a now holds 1, and b now holds 2.');
  assert.equal(said(rec('', [['line', 1, { a: '0' }, 0], ['return', 1, { a: '1', b: '2', c: '3' }, 0]]))[0], 'Line 1: a changes from 0 to 1, b now holds 2, and c now holds 3.');
  assert.equal(said(rec('5\n', [['line', 1, {}, 0], ['return', 1, { x: '5' }, 2]]))[0], 'Line 1: x now holds 5, and it prints 5.');
  assert.equal(said(rec('\n', [['line', 1, {}, 0], ['return', 1, {}, 1]]))[0], 'Line 1 prints an empty line. No variable changes.');
  assert.equal(said(rec('a\nb\n', [['line', 1, {}, 0], ['return', 1, {}, 4]]))[0], 'Line 1 prints a then b. No variable changes.');
});

test('a jump is told neutrally as the next step, never as a skipped line', () => {
  const s = said(rec('', [['line', 1, {}, 0], ['line', 3, { n: '1' }, 0], ['line', 1, { n: '1' }, 0], ['return', 1, { n: '1' }, 0]]));
  assert.equal(s[0], 'Line 1: n now holds 1. Next step: line 3.');
  assert.equal(s[1], 'Line 3 runs. No variable changes. Next step: line 1.');
  for (const line of s.filter(Boolean)) assert.doesNotMatch(line, /skip/i);
});

test('no sentence where the recording cannot prove it: unsafe frame, names left out, inside a function', () => {
  const trace = traceOf('b1-2-a-pocket-uses-its-latest-number');
  const m = buildMoments(trace);
  assert.equal(narrate(trace, [{ ...m[0], safe: false }], 0), null);
  const same = rec('', [['line', 1, { x: '1' }, 0], ['return', 1, { x: '2' }, 0]]);
  const sm = buildMoments(same);
  assert.equal(tokensText(narrate(same, sm, 0)), 'Line 1: x changes from 1 to 2.');
  assert.equal(narrate(same, [{ ...sm[0], safe: false }], 0), null, 'not provably the same frame: no diff, so no claim');
  assert.equal(narrate(trace, m, m.length - 1), null, 'the end event keeps the player sentence');
  const v3 = (scope, omitted) => ({ ...rec('', []), steps: [
    { depth: 0, event: 'call', function: '<module>', line: 1, locals: {}, globals: {}, locals_omitted: 0, globals_omitted: 0, scope: 'module', frame_id: 1, stdout_len: 0 },
    { depth: 0, event: 'line', function: '<module>', line: 1, locals: {}, globals: {}, locals_omitted: 0, globals_omitted: 0, scope, frame_id: 1, stdout_len: 0 },
    { depth: 0, event: 'return', function: '<module>', line: 1, locals: {}, globals: { a: '1' }, locals_omitted: 0, globals_omitted: omitted, scope, frame_id: 1, stdout_len: 0 }] });
  assert.equal(said(v3('module', 0))[0], 'Line 1: a now holds 1.');
  assert.equal(said(v3('module', 2))[0], null, 'names left out: no "no change" claims');
  assert.equal(said(v3('function', 0))[0], null);
});

test('the player says the narrator sentence only for practice, with code elements, and announces it once', () => {
  const trace = traceOf('b1-2-a-pocket-uses-its-latest-number');
  const entry = () => [{ id: 'main', label: 'Code 1', trace, figure: new Node('figure'), input: { stdin: '', total: 1 } }];
  const plain = createStepPlayer(entry());
  assert.equal(text(plain.all((n) => n.className === 'sp-now')[0]), 'Ran line 1.');
  const told = createStepPlayer(entry(), { narrate: true });
  const now = told.all((n) => n.className === 'sp-now')[0];
  assert.equal(text(now), 'Line 1: pocket now holds 2.');
  assert.deepEqual(now.all((n) => n.tag === 'code').map(text), ['pocket', '2']);
  const next = told.all((n) => n.tag === 'button' && text(n) === 'Next step')[0];
  next.click();
  assert.equal(text(now), 'Line 2: pocket changes from 2 to 5.');
  const live = told.all((n) => n.attrs['aria-live'] === 'polite')[0];
  assert.equal(text(live).split('Line 2: pocket changes from 2 to 5.').length, 2, 'said once in the live region');
});

const course = { id: 'b1-fixture', title: 'Fixture', levels: [{ level: 'beginner', lessons: [{ id: 'b1-2', title: 'Pockets', questions: ['b1-2-a-pocket-uses-its-latest-number', 'b1-2-reading-a-second-pocket-keeps-its-number'] }] }] };
const answerRadio = (root, value) => root.all((n) => n.tag === 'input' && n.attrs.value === value)[0];
const checkButton = (root) => root.all((n) => n.tag === 'button' && /Check/.test(n.text()))[0];

test('warm line under the verdict for each outcome, and still no points', async () => {
  const cases = [['latest-for-name', 'You read it the way the computer does.'], ['last-anywhere', 'Part of your reasoning fits. Watch the rest run, line by line.'], ['first-stays', 'Not quite. Watch it run, line by line.'], ['__unknown', 'Good call to check. Watch it run, line by line.']];
  for (const [option, line] of cases) {
    store.reset();
    const { node } = await questionView({ id: 'b1-2-a-pocket-uses-its-latest-number', course });
    answerRadio(node, option).click();
    checkButton(node).dispatchEvent({ type: 'click', preventDefault() {} });
    const verdict = node.all((n) => n.className.split(' ').includes('verdict'))[0];
    assert.equal(text(verdict.all((n) => n.className === 'verdict__sub')[0]), line, option);
    assert.doesNotMatch(text(verdict), /point/i);
  }
  store.reset();
});

test('the instruction: whole on the first question of a lesson, folded to "How to answer" elsewhere, and there on a direct link', async () => {
  store.reset();
  const first = (await questionView({ id: 'b1-2-a-pocket-uses-its-latest-number', course })).node;
  assert.equal(first.all((n) => n.tag === 'details').length, 0);
  assert.match(text(first), /Read the code, then choose what it does\./);
  const second = (await questionView({ id: 'b1-2-reading-a-second-pocket-keeps-its-number', course })).node;
  const fold = second.all((n) => n.tag === 'details')[0];
  assert.equal(text(fold.all((n) => n.tag === 'summary')[0]), 'How to answer');
  assert.match(text(fold), /Read the code, then choose what it does\. Take your time/);
  // many answers later the standard note has faded, but practice keeps its folded instruction
  for (let i = 0; i < 5; i += 1) store.recordAnswer({ questionId: `other-${i}`, optionId: 'x', score: 1 });
  const later = (await questionView({ id: 'b1-2-reading-a-second-pocket-keeps-its-number', course })).node;
  assert.equal(later.all((n) => n.tag === 'details').length, 1);
  store.reset();
});

test('after answering a practice question, the player mounted from its recorded trace narrates', async () => {
  store.reset();
  const { node } = await questionView({ id: 'b1-2-reading-a-second-pocket-keeps-its-number', course });
  answerRadio(node, json('questions/b1-2-reading-a-second-pocket-keeps-its-number.json').options.find((o) => o.score === 1).id).click();
  checkButton(node).dispatchEvent({ type: 'click', preventDefault() {} });
  let now = null;
  for (let i = 0; i < 50 && !now; i += 1) { await new Promise((r) => setTimeout(r, 10)); now = node.all((n) => n.className === 'sp-now')[0]; }
  assert.ok(now, 'the player is mounted from the pinned trace');
  assert.equal(text(now), 'Line 1: red now holds 4.');
  store.reset();
});

test('"No variable changes" only with proof: not after a del, an incomplete before, a value cut short, or a 0.2 function frame', () => {
  // del y: y is gone after line 2 and no row says so
  assert.deepEqual(said(rec('', [['line', 1, { x: '1', y: '2' }, 0], ['line', 2, { x: '1' }, 0], ['return', 2, { x: '1' }, 0]])).slice(0, 1), [null]);
  assert.equal(said(rec('ok\n', [['line', 1, { y: '2' }, 0], ['return', 1, {}, 3]]))[0], 'Line 1 prints ok.', 'output still told, without the claim');
  // a value cut short by the recorder can hide a change
  const long = '[1, 2, 3, 4, 5, 6, 7, 8, …]';
  assert.equal(said(rec('', [['line', 1, { xs: long }, 0], ['return', 1, { xs: long }, 0]]))[0], null);
  assert.equal(said(rec('', [['line', 1, { s: "'wait...'" }, 0], ['return', 1, { s: "'wait...'" }, 0]]))[0], null);
  // format 0.2 inside a function: only the frame's locals are recorded, a global may change unseen
  const inFn = { ...rec('', []), steps: [
    { depth: 0, event: 'call', function: '<module>', line: 1, locals: {}, stdout_len: 0 },
    { depth: 1, event: 'call', function: 'bump', line: 1, locals: {}, stdout_len: 0 },
    { depth: 1, event: 'line', function: 'bump', line: 3, locals: {}, stdout_len: 0 },
    { depth: 1, event: 'return', function: 'bump', line: 3, locals: {}, stdout_len: 0, value: 'None' }] };
  assert.ok(said(inFn).every((x) => x === null), 'no claim in a 0.2 function frame');
  // format 0.3: a "before" with names left out proves nothing about them
  const v3 = (beforeOmitted) => ({ ...rec('', []), steps: [
    { depth: 0, event: 'call', function: '<module>', line: 1, locals: {}, globals: {}, locals_omitted: 0, globals_omitted: 0, scope: 'module', frame_id: 1, stdout_len: 0 },
    { depth: 0, event: 'line', function: '<module>', line: 1, locals: {}, globals: { a: '1' }, locals_omitted: 0, globals_omitted: beforeOmitted, scope: 'module', frame_id: 1, stdout_len: 0 },
    { depth: 0, event: 'return', function: '<module>', line: 1, locals: {}, globals: { a: '1' }, locals_omitted: 0, globals_omitted: 0, scope: 'module', frame_id: 1, stdout_len: 0 }] });
  assert.equal(said(v3(0))[0], 'Line 1 runs. No variable changes.');
  assert.equal(said(v3(1))[0], null);
});
