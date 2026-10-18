import test from 'node:test';
import assert from 'node:assert/strict';
import { createHttpClient, parseOverlayCheckout } from '../public/js/account/http-client.js';
import { createFakeClient } from '../public/js/account/client.js';

const TXN = 'txn_01abcdefghjkmnpqrstvwxyz00';
const good = () => ({ purchase_id: 'Abc_123-xyz', state: 'pending', provider: 'paddle', checkout: { mode: 'overlay', transaction_id: TXN } });

test('the overlay answer is accepted only in exactly the documented shape, and the result is a new allowlisted object', () => {
  const raw = good();
  const parsed = parseOverlayCheckout(raw);
  assert.deepEqual(parsed, { ok: true, kind: 'overlay', purchase_id: 'Abc_123-xyz', transaction_id: TXN });
  assert.notEqual(parsed, raw);
  assert.deepEqual(Object.keys(parsed).sort(), ['kind', 'ok', 'purchase_id', 'transaction_id']);
});

test('every deviation from the overlay shape is refused', () => {
  const mutate = (fn) => { const d = good(); fn(d); return d; };
  const cases = {
    null: null, array: [good()], string: 'x', number: 1,
    extra_top: mutate((d) => { d.role = 'admin'; }), extra_inner: mutate((d) => { d.checkout.url = 'https://evil.example'; }),
    missing_top: mutate((d) => { delete d.provider; }), missing_inner: mutate((d) => { delete d.checkout.mode; }),
    redirect_null: mutate((d) => { d.redirect_url = null; }), redirect_empty: mutate((d) => { d.redirect_url = ''; }),
    redirect_real: mutate((d) => { d.redirect_url = 'https://pay.example/c'; }),
    state_paid: mutate((d) => { d.state = 'paid'; }), state_missing_case: mutate((d) => { d.state = 'Pending'; }),
    provider_other: mutate((d) => { d.provider = 'mock'; }), mode_inline: mutate((d) => { d.checkout.mode = 'inline'; }),
    checkout_array: mutate((d) => { d.checkout = [TXN]; }), checkout_null: mutate((d) => { d.checkout = null; }), checkout_string: mutate((d) => { d.checkout = TXN; }),
    txn_short: mutate((d) => { d.checkout.transaction_id = 'txn_abc'; }), txn_long: mutate((d) => { d.checkout.transaction_id = 'txn_' + 'a'.repeat(41); }),
    txn_upper: mutate((d) => { d.checkout.transaction_id = 'txn_' + 'A'.repeat(20); }), txn_prefix: mutate((d) => { d.checkout.transaction_id = 'pri_' + 'a'.repeat(20); }),
    txn_number: mutate((d) => { d.checkout.transaction_id = 12345678901234567890; }), txn_object: mutate((d) => { d.checkout.transaction_id = { toString: () => TXN }; }),
    txn_space: mutate((d) => { d.checkout.transaction_id = ' ' + TXN; }),
    purchase_empty: mutate((d) => { d.purchase_id = ''; }), purchase_long: mutate((d) => { d.purchase_id = 'a'.repeat(65); }),
    purchase_number: mutate((d) => { d.purchase_id = 5; }), purchase_slash: mutate((d) => { d.purchase_id = 'a/b'; }), purchase_space: mutate((d) => { d.purchase_id = 'a b'; }),
  };
  for (const [name, data] of Object.entries(cases)) assert.equal(parseOverlayCheckout(data), null, name);
});

test('a trailing line break of any kind is refused (JavaScript has no \\Z)', () => {
  for (const tail of ['\n', '\r', '\r\n', ' ', ' ', '\u0085', '\0', '\v', '\f']) {
    const txn = good(); txn.checkout.transaction_id = TXN + tail;
    assert.equal(parseOverlayCheckout(txn), null, `txn + ${JSON.stringify(tail)}`);
    const purchase = good(); purchase.purchase_id += tail;
    assert.equal(parseOverlayCheckout(purchase), null, `purchase + ${JSON.stringify(tail)}`);
  }
});

function clientFor(body, status = 200) {
  const routes = [{ status: 200, body: { user: { id: 'u' }, csrf_token: 's' } }, { status, body }];
  const calls = [];
  const fetchImpl = async (url, init) => { calls.push({ url, init }); const r = routes.shift(); return { ok: r.status < 300, status: r.status, async json() { return r.body; } }; };
  return { client: createHttpClient({ baseUrl: '/api', checkoutOrigins: ['https://pay.example'], fetchImpl }), calls };
}
const request = { product_id: 'p', policy_version: 'v1', acknowledgements: ['ack'] };

test('checkout(): a valid overlay answer comes back parsed; a bad one is a generic error that never echoes an id', async () => {
  const ok = clientFor(good());
  await ok.client.me();
  assert.deepEqual(await ok.client.checkout(request), { ok: true, kind: 'overlay', purchase_id: 'Abc_123-xyz', transaction_id: TXN });
  const bad = good(); bad.checkout.transaction_id = 'txn_' + 'a'.repeat(5);
  const refused = clientFor(bad);
  await refused.client.me();
  const result = await refused.client.checkout(request);
  assert.deepEqual([result.ok, result.code], [false, 'invalid_response']);
  assert.doesNotMatch(JSON.stringify(result), /txn_|Abc_123/);
  const both = clientFor({ ...good(), redirect_url: 'https://pay.example/checkout/1' });
  await both.client.me();
  assert.equal((await both.client.checkout(request)).code, 'invalid_response', 'overlay and redirect together are refused');
});

test('checkout(): 503 and other failures carry no transaction id and one POST is all that is sent', async () => {
  for (const status of [503, 400, 403, 409, 500]) {
    const f = clientFor({ error: { code: 'unavailable', message: TXN } }, status);
    await f.client.me();
    const result = await f.client.checkout(request);
    assert.equal(result.ok, false, String(status));
    assert.doesNotMatch(JSON.stringify(result), /txn_/);
    assert.equal(f.calls.filter(({ init }) => init.method === 'POST').length, 1);
  }
});

test('checkout(): the redirect flow of the mock provider is unchanged', async () => {
  const f = clientFor({ state: 'pending', purchase_id: 'p1', redirect_url: 'https://pay.example/checkout/1' });
  await f.client.me();
  const result = await f.client.checkout(request);
  assert.equal(result.ok, true);
  assert.equal(result.redirect_url, 'https://pay.example/checkout/1');
  assert.equal(result.kind, undefined);
});

test('with two pending purchases the client reports the newest (the server lists newest first) and never invents an id', async () => {
  const f = clientFor({ purchases: [{ id: 'new', product_id: 'python', state: 'pending', entitled: false }, { id: 'old', product_id: 'python', state: 'pending', entitled: false }] });
  await f.client.me();
  const purchase = await f.client.purchase();
  assert.equal(purchase.id, 'new');
});

test('the fake client answers like the Paddle path only when asked, and a plain checkout is unchanged', async () => {
  const plain = createFakeClient({ checkoutEnabled: true });
  const base = { product_id: 'python-lifetime', policy_version: 'placeholder', acknowledgements: ['withdrawal-consent'] };
  assert.deepEqual(await plain.checkout(base), { ok: true });
  const overlay = createFakeClient({ checkoutEnabled: true, checkoutMode: 'overlay' });
  const result = await overlay.checkout(base);
  assert.deepEqual(parseOverlayCheckout({ purchase_id: result.purchase_id, state: 'pending', provider: 'paddle', checkout: { mode: 'overlay', transaction_id: result.transaction_id } }), result);
  assert.equal((await overlay.checkout({ ...base, acknowledgements: [] })).ok, false);
});

const listing = (purchases) => clientFor({ purchases: purchases.map(([id, product_id, state]) => ({ id, product_id, state, entitled: state === 'completed' })) });
const pick = async (purchases, options) => { const f = listing(purchases); await f.client.me(); return f.client.purchase(options); };

test('purchase(): only the named product, and the named purchase while it is still pending, never merely the newest pending', async () => {
  const rows = [['other-new', 'other', 'pending'], ['mine-new', 'python', 'pending'], ['mine-old', 'python', 'pending']];
  assert.equal((await pick(rows, { product_id: 'python' })).id, 'mine-new', 'another product is skipped');
  assert.equal((await pick(rows, { product_id: 'python', purchase_id: 'mine-old' })).id, 'mine-old', 'the purchase of this checkout wins over a newer pending one');
  assert.equal((await pick(rows, { product_id: 'python', purchase_id: 'other-new' })).id, 'mine-new', 'a purchase of another product is never returned');
  assert.equal((await pick(rows, { product_id: 'python', purchase_id: 'not-listed' })).id, 'mine-new');
  const terminal = [['mine-new', 'python', 'refunded'], ['mine-old', 'python', 'pending']];
  assert.equal((await pick(terminal, { product_id: 'python', purchase_id: 'mine-new' })).id, 'mine-old', 'a terminal purchase is not shown as the pending one');
  assert.equal(await pick([['x', 'other', 'pending']], { product_id: 'python' }), null);
  assert.equal(await pick([], { product_id: 'python' }), null);
  assert.equal((await pick(rows)).id, 'other-new', 'without options the old behaviour (newest pending) is kept');
});

// The payment page, rendered with the fake client: the one-time line is for the purchase of that window only.
test('the payment page shows the window line only for the purchase it belongs to and only while that one is pending', async () => {
  const made = [];
  const mk = (tag) => { const n = { tag, attrs: {}, children: [], className: '', setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {}, append(...x) { this.children.push(...x); } }; made.push(n); return n; };
  globalThis.document = { createElement: mk, createTextNode: (text) => ({ text }) };
  globalThis.location = { hash: '#/purchase?scenario=pending' };
  const { setMessages } = await import('../public/js/i18n.js');
  setMessages(JSON.parse((await import('node:fs')).readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8')));
  const { purchaseView } = await import('../public/js/views/account.js');
  const { setCheckoutOutcome } = await import('../public/js/account/outcome.js');
  const text = (n) => (n.text !== undefined ? n.text : n.children.map(text).join(''));
  const render = async () => text((await purchaseView()).node);
  setCheckoutOutcome('closed', 'mock-purchase');
  assert.match(await render(), /The checkout window was closed\./);
  assert.doesNotMatch(await render(), /The checkout window was closed\./, 'read once');
  setCheckoutOutcome('finished', 'some-other-purchase');
  assert.doesNotMatch(await render(), /Checkout finished|window was closed/, 'a window of another purchase says nothing here');
  const { getClient } = await import('../public/js/account/index.js');
  getClient().state.scenario = 'paid';
  setCheckoutOutcome('finished', 'mock-purchase');
  assert.doesNotMatch(await render(), /Checkout finished|not confirmed yet/, 'no line once the payment is not pending');
  delete globalThis.location;
});
