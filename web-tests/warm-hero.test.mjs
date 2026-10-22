import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { text } from './support/dom-stub.mjs';

// The hero's preview: a real B1 question drawn on the stage, one link to where the main button goes, and a soft glow behind.
const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const site = new URL('../public/data/', import.meta.url);
const read = (path) => JSON.parse(readFileSync(new URL(path, site), 'utf8'));
const catalogRaw = read('catalog.json');
const B1 = catalogRaw.courses.find((c) => c.id === 'read-like-a-computer');
const first = read(`questions/${B1.start_question}.json`);
const css = (name) => readFileSync(new URL(`../public/css/${name}`, import.meta.url), 'utf8');

globalThis.fetch = async (url) => {
  const path = String(url).replace(/^(.*\/)?data\//, '').replace(/\?.*$/, '');
  let body = null;
  try { body = read(path); } catch { body = null; }
  return body ? { ok: true, status: 200, async json() { return body; } } : { ok: false, status: 404, async json() { return {}; } };
};

const { setMessages } = await import('../public/js/i18n.js');
setMessages(strings);
const { store } = await import('../public/js/progress.js');
const { answerPart, previewChoices, heroPreview } = await import('../public/js/components/hero-preview.js');
const { homeView } = await import('../public/js/views/home.js');

const byClass = (node, cls) => node.all((n) => n.className.split(' ').includes(cls));
const interactive = (n) => ['a', 'button', 'input', 'select', 'textarea', 'summary'].includes(n.tag) || n.attrs.tabindex !== undefined || n.attrs.role === 'button';

test('a choice shows its answer only: the first sentence without its full stop, no repeats, at most three', () => {
  assert.equal(answerPart('`2`. Only the final print stays on the screen.'), '`2`');
  assert.equal(answerPart('No full stop here'), 'No full stop here');
  assert.equal(answerPart('Ends here.'), 'Ends here');
  assert.equal(answerPart('`a. b` then `c`. Why.'), '`a. b` then `c`', 'a full stop inside inline code does not end the answer');
  assert.deepEqual(previewChoices(first.options), ['`"GO"` then `"GO"` then `2`', '`2`', '`GO` then `GO` then `2`']);
  assert.deepEqual(previewChoices([{ text: 'A. x' }, { text: 'A. y' }, { text: 'B' }, { text: 'C' }, { text: 'D' }]), ['A', 'B', 'C']);
});

test('the preview is one link with a clear name; inside it is a picture that cannot be focused or answered', () => {
  const link = heroPreview(first, { href: '#/q/x', name: 'Read your first piece of code' });
  assert.equal(link.tag, 'a');
  assert.equal(link.attrs.href, '#/q/x');
  assert.equal(link.attrs['aria-label'], 'Read your first piece of code');
  const picture = byClass(link, 'hero-preview__picture')[0];
  assert.equal(picture.attrs['aria-hidden'], 'true');
  assert.deepEqual(link.all((n) => n !== link && interactive(n)), [], 'no control and no focus stop inside');
  assert.equal(byClass(link, 'code__copy').length + byClass(link, 'code__scroll').length, 0);
  assert.equal(text(byClass(link, 'hero-preview__prompt')[0]), first.prompt);
  const choices = byClass(link, 'hero-preview__choice');
  assert.equal(choices.length, 3);
  assert.deepEqual(choices.map((c) => c.all((n) => n.tag === 'code').length), [3, 1, 3], 'backticks become inline code');
  assert.deepEqual(choices.map((c) => text(c)), ['"GO" then "GO" then 2', '2', 'GO then GO then 2']);
  const coded = heroPreview({ ...first, prompt: 'What does `total` hold?' }, { href: '#/q/x', name: 'n' });
  assert.equal(byClass(coded, 'hero-preview__prompt')[0].all((n) => n.tag === 'code').length, 1, 'inline code in the prompt too');
  assert.equal(link.all((n) => n.className === 'line').length, first.snippets[0].code.trimEnd().split('\n').length, 'the real code, line by line');
  assert.match(link.className, /\bhero-stage\b/, 'kept off the phone layout with the stage it replaces');
});

test('a new visitor: the preview shows B1’s first question and leads where the Start button does, with its name', async () => {
  store.reset();
  const page = (await homeView()).node;
  const link = byClass(page, 'hero-preview')[0];
  const start = page.all((n) => n.tag === 'a' && /button--primary/.test(n.className))[0];
  assert.equal(link.attrs.href, `#/q/${B1.start_question}`);
  assert.equal(link.attrs.href, start.attrs.href);
  assert.equal(link.attrs['aria-label'], strings['home.start']);
  assert.equal(text(byClass(link, 'hero-preview__prompt')[0]), first.prompt);
});

test('after an answer the preview shows the question Continue opens, leads there and is named Continue', async () => {
  store.reset();
  // A reader who has only touched Python: the picture is Python's next question, not B1's first.
  const python = catalogRaw.courses.find((c) => c.id === 'python');
  store.recordAnswer({ questionId: python.start_question, optionId: 'x', score: 1 });
  const page = (await homeView()).node;
  const link = byClass(page, 'hero-preview')[0];
  const cont = page.all((n) => n.tag === 'a' && text(n) === strings['home.continue'] && /button--primary/.test(n.className))[0];
  assert.ok(cont, 'Continue is there');
  assert.equal(link.attrs.href, cont.attrs.href);
  assert.notEqual(link.attrs.href, `#/q/${B1.start_question}`);
  assert.equal(link.attrs['aria-label'], strings['home.continue']);
  const shown = read(`questions/${cont.attrs.href.replace('#/q/', '')}.json`);
  assert.equal(text(byClass(link, 'hero-preview__prompt')[0]), text(heroPreview(shown, { href: '', name: '' }).all((n) => n.className === 'hero-preview__prompt')[0]), 'the picture is the question the link opens');
  assert.notEqual(shown.prompt, first.prompt);
  store.reset();
});

test('when a course is all read the preview shows its first question and leads to the course, named like that button', async () => {
  store.reset();
  const ids = read(`courses/${B1.id}.json`).levels.flatMap((l) => l.lessons.flatMap((x) => x.questions));
  ids.forEach((id) => store.recordAnswer({ questionId: id, optionId: 'x', score: 1 }));
  const page = (await homeView()).node;
  const link = byClass(page, 'hero-preview')[0];
  const browse = page.all((n) => n.tag === 'a' && text(n) === strings['home.browse'])[0];
  assert.ok(browse, 'the browse button is there');
  assert.equal(link.attrs.href, browse.attrs.href);
  assert.equal(link.attrs['aria-label'], strings['home.browse']);
  assert.equal(text(byClass(link, 'hero-preview__prompt')[0]), first.prompt);
  store.reset();
});

test('without a beginner course on the path the preview falls back to the onboarding question', async () => {
  store.reset();
  const data = await import('../public/js/data.js');
  const bare = { ...catalogRaw, courses: catalogRaw.courses.map(({ audience, path_step, ...rest }) => rest) };
  const original = globalThis.fetch;
  globalThis.fetch = async (url) => (/catalog\.json/.test(String(url)) ? { ok: true, status: 200, async json() { return bare; } } : original(url));
  data.clearDataCache();
  try {
    const page = (await homeView()).node;
    const onboarding = read('index.json').onboarding_question;
    assert.equal(byClass(page, 'hero-preview')[0].attrs.href, `#/q/${onboarding}`);
    assert.equal(text(byClass(page, 'hero-preview__prompt')[0]), text(heroPreview(read(`questions/${onboarding}.json`), { href: '', name: '' }).all((n) => n.className === 'hero-preview__prompt')[0]));
  } finally {
    globalThis.fetch = original;
    data.clearDataCache();
  }
});

test('long code is cut after eight lines and fades; short code is whole', () => {
  const long = read('questions/prod-timeout-does-not-cancel-the-work.json');
  assert.equal(byClass(heroPreview(long, { href: '', name: '' }), 'hero-preview__code--long').length, 1);
  assert.equal(byClass(heroPreview(first, { href: '', name: '' }), 'hero-preview__code--long').length, 0);
  const c = css('components.css');
  assert.match(c, /\.hero-preview__code--long \{ max-block-size: calc\(8 \* var\(--lh-code\) \* var\(--text-code\) \+ 2 \* var\(--s-4\)\); mask-image: linear-gradient\(to bottom, currentColor 70%, transparent\); \}/);
});

test('the glow: one token per theme at 3–5 % in the accent’s tone, behind the hero only, never in forced colours', () => {
  const tokens = css('tokens.css');
  const values = [...tokens.matchAll(/--hero-glow:\s*rgba\((\d+), (\d+), (\d+), ([\d.]+)\)/g)];
  assert.equal(values.length, 7, ':root, both Night blocks, Paper, Slate, Contrast and prefers-contrast');
  for (const [, r, g, b, a] of values) {
    assert.ok(Number(a) >= 0.03 && Number(a) <= 0.05, `alpha ${a}`);
    assert.ok(Number(r) > Number(b) && Number(g) > Number(b), 'a warm, yellow tone');
  }
  const c = css('components.css');
  assert.match(c, /\.home::before \{[^}]*background: radial-gradient\(closest-side, var\(--hero-glow\), transparent\); pointer-events: none; \}/);
  assert.match(c, /\.home \{ position: relative; isolation: isolate; \}/, 'the glow stays behind the page’s content');
  assert.match(c, /@media \(forced-colors: active\) \{ \.home::before \{ display: none; \} \}/);
  assert.match(c, /\.home::before \{[^}]*inset-inline: 0;/, 'never wider than the page (no sideways scroll)');
});

test('the preview moves like a card, keeps a visible focus ring, and draws its choices as faint raised boxes', () => {
  const c = css('components.css');
  assert.match(c, /\.hero-preview:hover \{ transform: translateY\(-2px\); box-shadow: var\(--shadow-card-hover\); \}/);
  assert.match(c, /\.hero-preview:focus-visible \{ outline: 3px solid var\(--focus\); outline-offset: 3px; \}/);
  assert.match(c, /@media \(prefers-reduced-motion: reduce\) \{ \.hero-preview:hover \{ transform: none; \} \}/);
  assert.match(c, /\.hero-preview__choice \{[^}]*background: var\(--stage-bg-raised\);[^}]*box-shadow: 0 2px 0 var\(--stage-line\);/);
  assert.match(c, /\.hero-preview__choice code \{[^}]*background: var\(--stage-bg\); color: var\(--stage-ink\);/, 'inline code reads on the stage in every theme');
  assert.match(c, /@media \(forced-colors: active\) \{ \.hero-preview__choice \{ border: 1px solid CanvasText; box-shadow: none; \} \}/);
});
