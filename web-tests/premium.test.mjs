import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { premiumState } from '../public/js/account/premium.js';

const root = new URL('../public/', import.meta.url).pathname;

test('premiumState maps the account service answer to an honest state', () => {
  const question = { id: 'prod-x' };
  assert.deepEqual(premiumState({ ok: true, question }), { state: 'ok', question });
  assert.deepEqual(premiumState({ ok: false, code: 'signed_out' }), { state: 'signin' });
  assert.deepEqual(premiumState({ ok: false, code: 'not_found' }), { state: 'unavailable' });
  assert.deepEqual(premiumState({ ok: false, code: 'network_error' }), { state: 'network' });
  for (const odd of [{ ok: false, code: 'request_failed' }, { ok: false, code: 'invalid_response' }, { ok: true }, { ok: true, question: null }, null, undefined, 'x']) assert.deepEqual(premiumState(odd), { state: 'error' }, JSON.stringify(odd));
});

test('every premium notice has its wording in the English strings', () => {
  const strings = JSON.parse(readFileSync(`${root}i18n/en.json`, 'utf8'));
  for (const state of ['signin', 'unavailable', 'network', 'error']) {
    assert.ok(strings[`premium.q.${state}.title`] && strings[`premium.q.${state}.text`], state);
  }
  assert.ok(strings['premium.q.retry'] && strings['premium.q.see']);
});

test('the question view asks the account service only when the account UI is on and the client is the real one, and guards the data', () => {
  const src = readFileSync(`${root}js/views/question.js`, 'utf8');
  assert.match(src, /if \(!CONFIG\.accountsUi\) return null;/);
  assert.match(src, /client\.mock !== false/);
  assert.match(src, /checkQuestion\(answer\.question, id\)/);
  assert.match(src, /err\.status === 404/);
  assert.doesNotMatch(src, /localStorage|sessionStorage/, 'premium content is never stored by the view');
});

test('the new module is in the import map with the page version', () => {
  const html = readFileSync(`${root}index.html`, 'utf8');
  const version = html.match(/css\/tokens\.css\?v=(\d+)/)[1];
  assert.ok(html.includes(`"./js/account/premium.js": "./js/account/premium.js?v=${version}"`));
});
