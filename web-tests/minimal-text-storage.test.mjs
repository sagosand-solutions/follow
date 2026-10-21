import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { text } from './support/dom-stub.mjs';

// With working browser storage the home page has no footnote at all: where progress lives is said on the course page
// and in About. (Without storage, minimal-text.test.mjs checks the one warning that stays.)
const backing = new Map();
globalThis.localStorage = { getItem: (k) => (backing.has(k) ? backing.get(k) : null), setItem: (k, v) => { backing.set(k, String(v)); }, removeItem: (k) => { backing.delete(k); } };
const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const site = new URL('../public/data/', import.meta.url);
globalThis.fetch = async (url) => {
  const path = String(url).replace(/^(.*\/)?data\//, '').replace(/\?.*$/, '');
  let body = null;
  try { body = JSON.parse(readFileSync(new URL(path, site), 'utf8')); } catch { body = null; }
  return body ? { ok: true, status: 200, async json() { return body; } } : { ok: false, status: 404, async json() { return {}; } };
};
const { setMessages } = await import('../public/js/i18n.js');
setMessages(strings);
const { store } = await import('../public/js/progress.js');
const { homeView } = await import('../public/js/views/home.js');

test('home with browser storage: no footnote under the hero', async () => {
  assert.equal(store.persistent, true);
  store.reset();
  const page = (await homeView()).node;
  assert.equal(page.all((n) => n.className.split(' ').includes('home-notes')).length, 0);
  assert.doesNotMatch(text(page), /Progress is|saved only|About 2 minutes|Early draft/);
});
