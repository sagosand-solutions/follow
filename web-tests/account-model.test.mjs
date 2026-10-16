import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { formatPrice, purchaseStatus, canPay, payBlockedReason, safeNext, PROVIDERS } from '../public/js/account/model.js';
import { createFakeClient, SCENARIOS } from '../public/js/account/client.js';

const root = new URL('../public/', import.meta.url).pathname;

test('formatPrice formats minor units and refuses bad input', () => {
  assert.equal(formatPrice(500, 'USD'), '$5.00');
  assert.equal(formatPrice(-1, 'USD'), null);
  assert.equal(formatPrice(5.5, 'USD'), null);
  assert.equal(formatPrice(500, 'NOT-A-CURRENCY'), null);
});

test('only an active entitlement means paid; pending is never paid', () => {
  assert.equal(purchaseStatus({ entitlements: [], purchase: { product_id: 'p', state: 'pending' } }, 'p'), 'pending');
  assert.equal(purchaseStatus({ entitlements: [{ product_id: 'p', state: 'active' }] }, 'p'), 'active');
  assert.equal(purchaseStatus({ entitlements: [{ product_id: 'p', state: 'revoked' }] }, 'p'), 'revoked');
  assert.equal(purchaseStatus({ entitlements: [{ product_id: 'other', state: 'active' }] }, 'p'), 'none');
  assert.equal(purchaseStatus({ purchase: { product_id: 'p', state: 'paid' } }, 'p'), 'none', 'a purchase record alone never unlocks');
});

test('Pay needs sign-in, an open checkout and the consent tick, and says why not', () => {
  const all = { signedIn: true, checkoutEnabled: true, acknowledged: true };
  assert.equal(canPay(all), true);
  assert.equal(payBlockedReason(all), null);
  assert.equal(payBlockedReason({ ...all, signedIn: false }), 'checkout.blocked.signin');
  assert.equal(payBlockedReason({ ...all, checkoutEnabled: false }), 'checkout.blocked.disabled');
  assert.equal(payBlockedReason({ ...all, acknowledged: false }), 'checkout.blocked.consent');
  for (const k of Object.keys(all)) assert.equal(canPay({ ...all, [k]: false }), false);
});

test('safeNext only allows known internal routes', () => {
  assert.equal(safeNext('premium'), 'premium');
  assert.equal(safeNext('https://evil.example'), 'account');
  assert.equal(safeNext('//evil.example'), 'account');
  assert.equal(safeNext(null), 'account');
});

test('the fake client: unavailable providers refuse, checkout needs the open flag and the consent, only a server confirmation grants', async () => {
  const c = createFakeClient();
  assert.deepEqual((await c.startSignIn('apple')), { ok: false, code: 'provider_unavailable' });
  assert.equal((await c.me()).signed_in, false);
  assert.equal((await c.startSignIn('google')).ok, true);
  assert.equal((await c.me()).signed_in, true);
  const base = { product_id: 'python-lifetime', policy_version: 'placeholder', acknowledgements: ['withdrawal-consent'] };
  assert.equal((await c.checkout(base)).code, 'checkout_disabled');
  c.state.checkoutEnabled = true;
  assert.equal((await c.checkout({ ...base, acknowledgements: [] })).code, 'policy_required');
  assert.equal((await c.checkout(base)).ok, true);
  assert.deepEqual(await c.entitlements(), [], 'pending grants nothing');
  c.mockConfirmPayment();
  assert.equal((await c.entitlements())[0].state, 'active');
  assert.deepEqual(SCENARIOS, ['signed-out', 'signed-in', 'pending', 'paid', 'refunded']);
  assert.ok(PROVIDERS.includes('metamask'));
});

test('every t() key used in the app exists in the English strings', () => {
  const strings = JSON.parse(readFileSync(join(root, 'i18n/en.json'), 'utf8'));
  const walk = (d) => readdirSync(d).flatMap((n) => (statSync(join(d, n)).isDirectory() ? walk(join(d, n)) : [join(d, n)]));
  const missing = [];
  for (const f of walk(join(root, 'js')).filter((p) => p.endsWith('.js'))) {
    for (const m of readFileSync(f, 'utf8').matchAll(/\bt\(\s*'([\w.-]+)'/g)) if (!(m[1] in strings)) missing.push(`${f}: ${m[1]}`);
  }
  assert.deepEqual(missing, []);
});

test('the account UI is off by default and never rendered with innerHTML', () => {
  assert.match(readFileSync(join(root, 'js/config.js'), 'utf8'), /accountsUi: false/);
  for (const f of ['js/views/account.js', 'js/account/index.js', 'js/account/client.js']) assert.doesNotMatch(readFileSync(join(root, f), 'utf8'), /innerHTML|insertAdjacentHTML/, f);
});
