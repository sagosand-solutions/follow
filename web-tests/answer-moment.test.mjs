import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const read = (p) => readFile(new URL(p, import.meta.url), 'utf8');

test('moment icons rest in their final state; animations only add an empty start', async () => {
  const css = await read('../public/css/components.css');
  // Final states are the defaults, so with animations removed (reduced motion) the icon is complete.
  assert.match(css, /\.moment-icon__tick \{[^}]*stroke-dashoffset: 0;/);
  assert.match(css, /\.moment-icon__pie \{[^}]*stroke-dasharray: 0\.5 1;/);
  assert.match(css, /@keyframes moment-draw \{ from \{ stroke-dashoffset: 1; \} \}/);
  assert.match(css, /@keyframes moment-pie \{ from \{ stroke-dasharray: 0 1; \} \}/);
  // Animations only on a fresh answer.
  assert.match(css, /\.moment-icon\.is-fresh \.moment-icon__tick \{ animation:/);
  assert.match(css, /\.option\.is-glow \{ animation:/);
  const base = await read('../public/css/base.css');
  assert.match(base, /prefers-reduced-motion: reduce\)[\s\S]*animation: none !important/);
});

test('after the answer: verdict, then the way on, then why, then read more', async () => {
  const src = await read('../public/js/views/question.js');
  const fb = src.slice(src.indexOf('function renderFeedback'));
  const order = ["class: `verdict verdict--${v.kind}`", 'frag.push(navActions(ctx))', "{ class: 'why' }", "{ class: 'read-more' }"].map((s) => fb.indexOf(s));
  assert.ok(order.every((i) => i > 0), JSON.stringify(order));
  assert.deepEqual([...order].sort((a, b) => a - b), order);
  assert.match(fb, /whyBox\.open = missed;/);
  assert.match(src, /ctx\.fresh = true;\n\s+draw\(ctx, record\);\n\s+ctx\.fresh = false;/);
});

test('course path: two coming-soon steps without links before the first real course', async () => {
  const src = await read('../public/js/components/catalog-card.js');
  assert.match(src, /export const PATH_SOON = \['path\.soon\.1', 'path\.soon\.2'\];/);
  const soon = src.slice(src.indexOf('...PATH_SOON.map'), src.indexOf("h('li', { class: 'path__step' }"));
  assert.doesNotMatch(soon, /href/);
  const en = JSON.parse(await read('../public/i18n/en.json'));
  for (const key of ['path.heading', 'path.deeper', 'path.soon', 'path.soon.1.title', 'path.soon.2.title']) assert.ok(en[key], key);
  assert.equal(en['path.soon'], 'Coming soon');
});
