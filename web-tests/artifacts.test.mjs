import './support/dom-stub.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { text } from './support/dom-stub.mjs';

// Text artifacts (schema_version 0.3): typed display text that is shown as inert text and never run. The checks here are
// the browser's own; the author-side rules are in validator/tests/test_artifacts.py, and the URL cases are shared.
const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const urlCases = JSON.parse(readFileSync(new URL('./fixtures/artifact-url-cases.json', import.meta.url), 'utf8'));
const sha = (s) => createHash('sha256').update(s, 'utf8').digest('hex');

const fetched = [];
let questions = {};
globalThis.fetch = async (url) => {
  fetched.push(String(url));
  const path = String(url).replace(/^(.*\/)?data\//, '').replace(/\?.*$/, '');
  const m = path.match(/^questions\/(.+)\.json$/);
  const body = m ? questions[m[1]] : null;
  return body ? { ok: true, status: 200, async json() { return body; } } : { ok: false, status: 404, async json() { return {}; } };
};
const { setMessages } = await import('../public/js/i18n.js');
setMessages(strings);
const { checkQuestion, isPractice } = await import('../public/js/question-check.js');
const { urlFindings, checkArtifacts } = await import('../public/js/artifacts.js');
const { renderArtifact } = await import('../public/js/components/artifact.js');
const { questionView } = await import('../public/js/views/question.js');
const { store } = await import('../public/js/progress.js');

const DIFF = '--- a/total.py\n+++ b/total.py\n@@ -1,2 +1,2 @@\n def total(xs):\n-    return sum(xs)\n+    return sum(xs) + 1\n';
const KIND_FORMAT = { text: 'plain', diff: 'unified-diff', 'http-transcript': 'http-1.1-transcript', 'test-report': 'plain', policy: 'plain' };
const art = (body = DIFF, kind = 'diff', id = 'change', over = {}) => ({ id, kind, format: KIND_FORMAT[kind], text: body, sha256: sha(body), provenance: { fixture_id: 'fixture-one', fixture_output_sha256: sha(body) }, ...over });
const options = () => [1, 0.5, 0, 0, 0].map((score, i) => ({ id: `o${i}`, text: `Option ${i}`, score, feedback: `Feedback ${i}`, misconception: score === 1 ? null : 'py.some-misconception', ...(score === 0.5 ? { partial_reason: 'Part of it fits' } : {}) }));
const textOnly = (list = [art()], over = {}) => ({ schema_version: '0.3', answer_profile: 'standard', language: 'text', id: 'diff-reading-one', locale: 'en', type: 'output', level: 'beginner', difficulty: 0.2, concepts: ['py.values'], prompt: 'What does the change do?', artifacts: list, options: options(), explanation: 'Because.', verification: { status: 'specified', cases: [], rationale: 'r' }, author: { name: 'Test' }, license: 'CC-BY-SA-4.0', ...over });
const mixed = (list = [art()], over = {}) => ({ ...textOnly(list), language: 'python', snippets: [{ id: 'main', code: 'print(1)' }], verification: { status: 'specified', rationale: 'r', cases: [{ id: 'one', snippet_id: 'main', stdin: '', expected: { stdout: '1\n', exit_code: 0 } }] }, ...over });
const check = (q) => checkQuestion(q, q.id);
const without = (q, key) => { const c = structuredClone(q); delete c[key]; return c; };

test('text-only and mixed 0.3 questions pass the client check; every kind with its format', () => {
  assert.equal(check(textOnly()), null);
  assert.equal(check(mixed()), null);
  for (const kind of Object.keys(KIND_FORMAT)) assert.equal(check(textOnly([art('Line one\nLine two\n', kind)])), null, kind);
  const edge = `${'x'.repeat(49)}\n`.repeat(80);
  assert.equal(edge.length, 4000);
  assert.equal(check(textOnly([art(`${'a\n'.repeat(79)}b`, 'text')])), null, '80 lines, the last one unterminated');
  assert.equal(check(textOnly([art(edge, 'text', 'a-one'), art('b\n', 'policy', 'a-two'), art('c\n', 'test-report', 'a-three')])), null, 'three artifacts at the exact limits');
});

test('the profile of a 0.3 question is read like 0.2: an explicit field, practice only by that field', () => {
  assert.equal(check(without(textOnly(), 'answer_profile')), 'answer_profile');
  const q = textOnly([art()], { answer_profile: 'beginner-practice' });
  q.options = [1, 0.5, 0, 0].map((score, i) => ({ id: `o${i}`, text: `Option ${i}`, score, feedback: 'f', misconception: score === 1 ? null : 'py.x', ...(score === 0.5 ? { partial_reason: 'p' } : {}) }));
  assert.equal(check(q), null);
  assert.equal(isPractice(q), true, 'a 0.3 practice question is practice, not standard');
  assert.equal(isPractice(textOnly()), false);
});

test('what the client refuses: unknown versions, artifacts on older versions, disguised or empty content', () => {
  assert.equal(check({ ...textOnly(), schema_version: '0.4' }), 'unsupported schema_version');
  for (const v of ['0.1', '0.2']) {
    const old = { ...mixed(), schema_version: v };
    if (v === '0.1') delete old.answer_profile;
    assert.notEqual(check(old), null, `artifacts on ${v}`);
  }
  assert.notEqual(check(textOnly([art()], { language: 'python' })), null, 'text-only must not claim to be Python');
  assert.notEqual(check(mixed([art()], { language: 'text' })), null, 'language text carries no code');
  assert.notEqual(check({ ...textOnly(), snippets: [{ id: 'main', code: 'print(1)' }] }), null, 'text-only with a snippet');
  assert.notEqual(check(without(textOnly(), 'artifacts')), null, 'neither snippets nor artifacts');
  assert.notEqual(check(textOnly([])), null, 'an empty list says nothing');
  assert.notEqual(check(textOnly([art(), art('b\n', 'text', 'two'), art('c\n', 'text', 'three'), art('d\n', 'text', 'four')])), null, 'more than three');
});

test('what a single artifact may not be', () => {
  const bad = {
    'unknown kind': art(DIFF, 'diff', 'change', { kind: 'python' }),
    'format of another kind': art(DIFF, 'diff', 'change', { format: 'plain' }),
    'extra key url': art(DIFF, 'diff', 'change', { url: 'https://example.com/' }),
    'extra key src': art(DIFF, 'diff', 'change', { src: 'x' }),
    'extra key html': art(DIFF, 'diff', 'change', { html: '<b>x</b>' }),
    'extra key run': art(DIFF, 'diff', 'change', { run: true }),
    'bad id': art(DIFF, 'diff', 'Bad Id'),
    'text not a string': art(DIFF, 'diff', 'change', { text: 5 }),
    'empty text': art('', 'text'),
    'blank text': art('  \n', 'text'),
    'too long': art('x'.repeat(4001), 'text'),
    'too many lines': art('a\n'.repeat(81), 'text'),
    'last line unterminated makes 81': art(`${'a\n'.repeat(80)}b`, 'text'),
    'carriage return': art('a\r\nb\n', 'text'),
    'NUL': art('a\u0000b', 'text'),
    'bell': art('a\u0007b', 'text'),
    'C1': art('a\u0085b', 'text'),
    'line separator': art('a\u2028b', 'text'),
    'lone surrogate': art('a\ud800b', 'text'),
    'sha256 upper case': art(DIFF, 'diff', 'change', { sha256: sha(DIFF).toUpperCase() }),
    'sha256 short': art(DIFF, 'diff', 'change', { sha256: 'abc' }),
    'no provenance': (() => { const a = art(); delete a.provenance; return a; })(),
    'provenance extra key': art(DIFF, 'diff', 'change', { provenance: { fixture_id: 'f', fixture_output_sha256: sha(DIFF), path: '/tmp/x' } }),
    'provenance bad hash': art(DIFF, 'diff', 'change', { provenance: { fixture_id: 'f', fixture_output_sha256: 'zz' } }),
    'label too long': art(DIFF, 'diff', 'change', { label: 'x'.repeat(81) }),
  };
  for (const [name, a] of Object.entries(bad)) assert.notEqual(checkArtifacts([a], new Set()), null, name);
  assert.equal(checkArtifacts([art(DIFF, 'diff', 'change', { label: 'The change' })], new Set()), null, 'a short label is fine');
  assert.notEqual(checkArtifacts([art('a\n', 'text', 'same'), art('b\n', 'text', 'same')], new Set()), null, 'duplicate ids');
  assert.notEqual(checkArtifacts([art('a\n', 'text', 'main')], new Set(['main'])), null, 'an id may not repeat a snippet id');
});

test('the URL policy is the same as the author-side one (shared cases)', () => {
  for (const s of urlCases.reserved) assert.deepEqual(urlFindings(s), [], s);
  for (const s of urlCases.refused) assert.notDeepEqual(urlFindings(s), [], s);
  for (const s of urlCases.refused) assert.notEqual(check(textOnly([art(`${s}\n`, 'http-transcript')])), null, s);
  for (const s of urlCases.reserved) assert.equal(check(textOnly([art(`${s}\n`, 'http-transcript')])), null, s);
});

const HOSTILE = [
  '<script>alert(1)</script>',
  '<img src=x onerror=alert(1)>',
  '<a href="https://example.com/">link</a>',
  'javascript:alert(1)',
  '[link](https://example.com) **bold** `code` ![img](https://example.com/x.png)',
  '&amp; &lt;b&gt; <style>body{display:none}</style> <iframe src="https://example.com"></iframe>',
  '{{7*7}} ${process.exit()} %s',
].join('\n') + '\n';
const FORBIDDEN = ['a', 'img', 'script', 'iframe', 'style', 'link', 'object', 'embed', 'form', 'input', 'button', 'svg', 'code', 'strong', 'em'];

test('rendering: the text is the text, exactly, as one inert text node', () => {
  fetched.length = 0;
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (...args) => { calls.push(args); return originalFetch(...args); };
  try {
    const node = renderArtifact(art(HOSTILE, 'text'));
    const pre = node.all((n) => n.tag === 'pre')[0];
    assert.equal(pre.text(), HOSTILE, 'not one character changed');
    assert.equal(pre.children.length, 1, 'a single text node, nothing parsed out of it');
    assert.equal(pre.children[0].tag, undefined, 'it is a text node, not an element');
    for (const tag of FORBIDDEN) assert.equal(node.all((n) => n.tag === tag).length, 0, `no <${tag}> node`);
    for (const n of node.all(() => true)) for (const [k, v] of Object.entries(n.attrs)) assert.ok(!String(v).includes(HOSTILE.slice(0, 20)), `${k} carries no artifact text`);
    for (const n of node.all(() => true)) assert.ok(!Object.keys(n.attrs).some((k) => k.startsWith('on')), 'no event attribute');
    assert.equal(calls.length, 0, 'rendering makes no request');
    assert.equal(node.tag, 'figure');
    assert.match(text(node), /Shown as text\. Nothing here is run\./, 'it says it is shown as text and not run');
  } finally { globalThis.fetch = originalFetch; }
});

test('rendering: one named, described, focusable group per artifact and no landmark', () => {
  const node = renderArtifact(art('a\n', 'diff', 'the-change'));
  const pre = node.all((n) => n.tag === 'pre')[0];
  assert.equal(pre.attrs.role, 'group');
  assert.equal(pre.attrs.tabindex, '0', 'reachable by keyboard so a wide text can be scrolled');
  const byId = (id) => node.all((n) => n.attrs.id === id)[0];
  assert.equal(text(byId(pre.attrs['aria-labelledby'])), 'Change (diff)');
  assert.equal(text(byId(pre.attrs['aria-describedby'])), 'Shown as text. Nothing here is run.');
  assert.equal(node.all((n) => n.attrs.role === 'region').length, 0, 'not a landmark');
  const ids = node.all((n) => n.attrs.id).map((n) => n.attrs.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.ok(ids.every((id) => id.includes('the-change')), 'ids come from the artifact id');
  const two = [renderArtifact(art('a\n', 'text', 'one')), renderArtifact(art('b\n', 'text', 'two'))];
  const all = two.flatMap((fig) => fig.all((n) => n.attrs.id).map((n) => n.attrs.id));
  assert.equal(new Set(all).size, all.length, 'two artifacts never share an id');
});

test('prose wraps and columns scroll: the CSS says so (a layout rule a DOM stub cannot measure)', () => {
  const css = readFileSync(new URL('../public/css/components.css', import.meta.url), 'utf8');
  const rule = css.match(/\.artifact\[data-kind="text"\] \.artifact__text,\s*\.artifact\[data-kind="policy"\] \.artifact__text \{([^}]*)\}/);
  assert.ok(rule, 'a rule for the prose kinds');
  assert.match(rule[1], /white-space:\s*pre-wrap/);
  assert.match(rule[1], /overflow-wrap:\s*anywhere/);
  const base = css.match(/\.artifact__text \{([^}]*)\}/)[1];
  assert.match(base, /white-space:\s*pre;/, 'diff, transcript and report keep their columns and scroll');
  assert.match(base, /overflow-x:\s*auto/);
});

test('the stage and the note speak of text when the question has text', async () => {
  const q = textOnly([art('a\n', 'text', 'one'), art('b\n', 'policy', 'two')], { id: 'two-blocks-one' });
  questions = { [q.id]: q };
  store.reset();
  const node = (await questionView({ id: q.id })).node;
  assert.equal(node.all((n) => (n.className || '').split(' ').includes('q-stage'))[0].attrs['aria-label'], 'Code and text');
  assert.match(text(node), /Read the text, then choose the answer\./);
  assert.doesNotMatch(text(node), /Read the code/);
  const m = mixed([art('a\n', 'text', 'one')], { id: 'mixed-two-one' });
  questions = { [m.id]: m };
  const mixedNode = (await questionView({ id: m.id })).node;
  assert.match(text(mixedNode), /Read the code, then choose what it does\./, 'a question with code keeps the code wording');
});

test('rendering: an author label is text too, in the caption and in the region name', () => {
  const label = '<img src=x onerror=alert(1)> & <b>';
  const node = renderArtifact(art('x\n', 'text', 'change', { label }));
  assert.equal(check(textOnly([art('x\n', 'text', 'change', { label })])), null, 'a label with angle brackets is still only text');
  const caption = node.all((n) => (n.className || '').includes('artifact__label'))[0];
  assert.equal(caption.text(), label);
  assert.equal(caption.children.length, 1);
  assert.equal(caption.children[0].tag, undefined, 'a text node');
  for (const tag of ['img', 'b', 'a', 'script']) assert.equal(node.all((n) => n.tag === tag).length, 0, tag);
});

test('rendering: every kind gets its own label and the same inert treatment', () => {
  const labels = new Set();
  for (const kind of Object.keys(KIND_FORMAT)) {
    const node = renderArtifact(art('x\n', kind));
    labels.add(text(node.all((n) => (n.className || '').includes('artifact__label'))[0]));
    assert.equal(node.all((n) => n.tag === 'pre').length, 1, kind);
  }
  assert.equal(labels.size, 5, 'text, diff, http transcript, test report and policy each have their own name');
});

test('the question view shows a text-only question without any code stage, stepper or request for the artifact text', async () => {
  const q = textOnly([art(HOSTILE, 'text', 'change')]);
  questions = { [q.id]: q };
  fetched.length = 0;
  const view = await questionView({ id: q.id });
  const node = view.node;
  const figures = node.all((n) => n.tag === 'figure' && (n.className || '').includes('artifact'));
  assert.equal(figures.length, 1);
  assert.equal(node.all((n) => (n.className || '').split(' ').includes('code')).length, 0, 'no code block');
  assert.equal(figures[0].all((n) => n.tag === 'pre')[0].text(), HOSTILE);
  for (const tag of ['a', 'img', 'script', 'iframe']) assert.equal(figures[0].all((n) => n.tag === tag).length, 0, tag);
  assert.ok(fetched.every((u) => /(^|\/)data\//.test(u)), `only the site's own data was requested: ${fetched.join(', ')}`);
  assert.ok(!fetched.some((u) => u.includes('example.com') || u.includes('javascript')), 'nothing named in the text was requested');
});

test('an answered text-only question asks for no trace: artifacts are not code and nothing steps through them', async () => {
  const q = textOnly([art('see\n', 'text', 'note')], { id: 'answered-text-one' });
  questions = { [q.id]: q };
  store.reset();
  store.recordAnswer({ questionId: q.id, optionId: 'o0', score: 1 });
  fetched.length = 0;
  const node = (await questionView({ id: q.id })).node;
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(node.all((n) => n.tag === 'figure').length, 1, 'the artifact is still shown after answering');
  assert.deepEqual(fetched.filter((u) => /traces\//.test(u)), [], 'no request for a trace');
  store.reset();
});

test('a mixed question shows its code and its artifact, the code first', async () => {
  const q = mixed([art('see\n', 'text', 'note')]);
  q.id = 'mixed-reading-one';
  questions = { [q.id]: q };
  const node = (await questionView({ id: q.id })).node;
  assert.equal(node.all((n) => (n.className || '').split(' ').includes('code')).length, 1);
  assert.equal(node.all((n) => n.tag === 'figure' && (n.className || '').includes('artifact')).length, 1);
  const order = node.all((n) => { const c = (n.className || '').split(' '); return c.includes('code') || c.includes('artifact'); }).map((n) => (n.className.split(' ').includes('artifact') ? 'artifact' : 'code'));
  assert.deepEqual(order, ['code', 'artifact'], 'the code stage comes before the text artifact');
});

test('an unsupported or malformed artifact question is not rendered', async () => {
  const q = textOnly([art(DIFF, 'diff', 'change', { url: 'https://example.com/' })], { id: 'bad-artifact-one' });
  questions = { [q.id]: q };
  const view = await questionView({ id: q.id });
  assert.equal(view.node.all((n) => n.tag === 'figure').length, 0, 'no artifact is shown from a question that fails the check');
});
