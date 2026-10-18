import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('../public/', import.meta.url).pathname;
const read = (file) => readFileSync(join(root, file), 'utf8');

// A small DOM: enough for h(), a modal <dialog> with Esc, focus and removal.
class Node {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.className = ''; this.handlers = {}; this.parent = null; this.open = false; this.focused = false; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn); }
  append(...nodes) { for (const n of nodes) { if (n && typeof n === 'object') n.parent = this; this.children.push(n); } }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() { this.focused = true; globalThis.document.active = this; }
  click() { this.fire('click'); }
  fire(type, event = {}) { const e = { type, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...event }; for (const fn of this.handlers[type] || []) fn(e); return e; }
  text() { return this.children.map((c) => (c.tag ? c.text() : c.text)).join(''); }
  find(pred, out = []) { if (pred(this)) out.push(this); for (const c of this.children) if (c.tag) c.find(pred, out); return out; }
}
function installDom() {
  const body = new Node('body');
  globalThis.document = { body, active: null, createElement: (tag) => new Node(tag), createTextNode: (text) => ({ text }) };
  return body;
}
async function strings() {
  const { setMessages } = await import('../public/js/i18n.js');
  setMessages(JSON.parse(read('i18n/en.json')));
}
const dialogs = (body) => body.find((n) => n.tag === 'dialog');

test('the dialog says it is a test, takes no payment, starts on Close and never shows the id', async () => {
  const body = installDom();
  await strings();
  const { openOverlayShim } = await import('../public/js/account/overlay-shim.js');
  const handle = openOverlayShim({ title: 'Python', price: '$5.00', transactionId: 'txn_secretsecret0000' });
  const [dialog] = dialogs(body);
  assert.equal(dialog.open, true);
  assert.equal(dialog.attrs['aria-labelledby'], 'shim-title');
  const text = dialog.text();
  assert.match(text, /Test checkout/);
  assert.match(text, /No payment is taken here/);
  assert.match(text, /Python · \$5\.00/);
  assert.doesNotMatch(JSON.stringify(dialog, (k, v) => (k === 'parent' ? undefined : v)), /txn_|secretsecret/);
  assert.doesNotMatch(text, /\bpaid\b/i);
  assert.equal(globalThis.document.active.text(), 'Close');
  handle.cancel();
});

test('Close, Esc and Simulate each give ONE outcome; later events are ignored; the dialog is removed', async () => {
  const body = installDom();
  await strings();
  const { openOverlayShim } = await import('../public/js/account/overlay-shim.js');
  const run = async (act) => {
    const handle = openOverlayShim({ title: 'P', price: '' });
    const [dialog] = dialogs(body);
    const buttons = dialog.find((n) => n.tag === 'button');
    act(dialog, buttons);
    const outcome = await handle.result;
    assert.equal(dialogs(body).length, 0, 'removed from the page');
    assert.equal(dialog.open, false);
    buttons.forEach((b) => b.click());           // late clicks change nothing
    dialog.fire('cancel');
    assert.equal(await handle.result, outcome);
    return outcome;
  };
  assert.equal(await run((d, b) => b[0].click()), 'closed');
  assert.equal(await run((d, b) => b[1].click()), 'finished');
  assert.equal(await run((d) => { const e = d.fire('cancel'); assert.equal(e.defaultPrevented, true); }), 'closed');
  assert.equal(await run((d, b) => { b[1].click(); b[1].click(); b[0].click(); }), 'finished', 'a double click is one outcome, the first');
});

test('cancel() removes the dialog without an outcome and ignores everything after', async () => {
  const body = installDom();
  await strings();
  const { openOverlayShim } = await import('../public/js/account/overlay-shim.js');
  const handle = openOverlayShim({});
  const [dialog] = dialogs(body);
  const buttons = dialog.find((n) => n.tag === 'button');
  handle.cancel();
  buttons.forEach((b) => b.click());
  assert.equal(await handle.result, null);
  assert.equal(dialogs(body).length, 0);
});

test('the dialog is not dismissed by a click on the backdrop (no click handler on the dialog itself)', async () => {
  const body = installDom();
  await strings();
  const { openOverlayShim } = await import('../public/js/account/overlay-shim.js');
  const handle = openOverlayShim({});
  const [dialog] = dialogs(body);
  assert.deepEqual(Object.keys(dialog.handlers).sort(), ['cancel']);
  handle.cancel();
});

// ---- the overlay flow (all effects injected)
function flow(overrides = {}) {
  const log = [];
  const handle = { result: Promise.resolve('finished'), cancel() { log.push('cancel'); } };
  const args = {
    result: { purchase_id: 'p1', transaction_id: 'txn_abcdefghij0123' },
    loadOpener: async () => (a) => { log.push(['open', a]); return handle; },
    args: { title: 'P', price: '$5' }, ui: { failed: () => log.push('failed') }, go: (p) => log.push(['go', p]),
    setOutcome: (...a) => log.push(['outcome', ...a]), active: () => true, register: () => log.push('registered'), ...overrides,
  };
  return { log, args, handle };
}

test('flow: without a configured opener it fails closed and opens, navigates and records nothing', async () => {
  const { runOverlay } = await import('../public/js/account/checkout-flow.js');
  for (const loadOpener of [async () => null, async () => { throw new Error('import failed'); }, async () => 'not a function']) {
    const { log, args } = flow({ loadOpener });
    assert.equal(await runOverlay(args), 'failed');
    assert.deepEqual(log, ['failed']);
  }
  const broken = flow({ loadOpener: async () => () => { throw new Error('could not open'); } });
  assert.equal(await runOverlay(broken.args), 'failed');
  assert.deepEqual(broken.log, ['failed']);
});

test('flow: both outcomes go to the payment page once, with the outcome recorded and the id only given to the opener', async () => {
  const { runOverlay } = await import('../public/js/account/checkout-flow.js');
  for (const outcome of ['finished', 'closed']) {
    const { log, args, handle } = flow();
    handle.result = Promise.resolve(outcome);
    assert.equal(await runOverlay(args), outcome);
    assert.deepEqual(log.filter((x) => x !== 'registered').map((x) => (Array.isArray(x) ? x[0] : x)), ['open', 'outcome', 'go']);
    assert.deepEqual(log.find((x) => x[0] === 'go'), ['go', '/purchase']);
    assert.deepEqual(log.find((x) => x[0] === 'outcome'), ['outcome', outcome, 'p1'], 'the outcome is tied to the purchase of this checkout');
    assert.equal(log.find((x) => x[0] === 'open')[1].transactionId, 'txn_abcdefghij0123');
    assert.equal(JSON.stringify(log.filter((x) => x[0] !== 'open')).includes('txn_'), false);
  }
});

test('flow: a leave before opening, or a cancelled window, never opens, records or navigates late', async () => {
  const { runOverlay } = await import('../public/js/account/checkout-flow.js');
  const early = flow({ active: () => false });
  assert.equal(await runOverlay(early.args), 'left');
  assert.deepEqual(early.log, []);
  const cancelled = flow();
  cancelled.handle.result = Promise.resolve(null);
  assert.equal(await runOverlay(cancelled.args), 'left');
  assert.equal(cancelled.log.some((x) => Array.isArray(x) && (x[0] === 'go' || x[0] === 'outcome')), false);
  let alive = true;
  const late = flow({ active: () => alive });
  late.handle.result = new Promise((resolve) => setTimeout(() => { alive = false; resolve('finished'); }, 5));
  assert.equal(await runOverlay(late.args), 'left');
  assert.equal(late.log.some((x) => Array.isArray(x) && (x[0] === 'go' || x[0] === 'outcome')), false);
  const garbage = flow();
  garbage.handle.result = Promise.resolve('paid');
  assert.equal(await runOverlay(garbage.args), 'left');
});

// ---- the one-time line
test('the outcome is read once, expires, is for one purchase and is dropped when the reader goes elsewhere', async () => {
  const { setCheckoutOutcome, takeCheckoutOutcome, clearCheckoutOutcome } = await import('../public/js/account/outcome.js');
  setCheckoutOutcome('paid', 'p1');
  assert.equal(takeCheckoutOutcome(), null, 'only finished or closed are kept');
  setCheckoutOutcome('closed');
  assert.equal(takeCheckoutOutcome(), null, 'a purchase id is required');
  setCheckoutOutcome('closed', '', 1000);
  assert.equal(takeCheckoutOutcome(1100), null);
  setCheckoutOutcome('closed', 'p1', 1000);
  assert.deepEqual(takeCheckoutOutcome(1100), { kind: 'closed', purchaseId: 'p1' });
  assert.equal(takeCheckoutOutcome(1100), null, 'read once');
  setCheckoutOutcome('finished', 'p1', 1000);
  assert.equal(takeCheckoutOutcome(1000 + 16000), null, 'expired');
  setCheckoutOutcome('finished', 'p1');
  clearCheckoutOutcome();
  assert.equal(takeCheckoutOutcome(), null);
  const listeners = {};
  globalThis.addEventListener = (type, fn) => { listeners[type] = fn; };
  globalThis.location = { hash: '#/purchase' };
  const fresh = await import('../public/js/account/outcome.js?hashchange');
  fresh.setCheckoutOutcome('closed', 'p1');
  globalThis.location.hash = '#/premium';
  listeners.hashchange();
  assert.equal(fresh.takeCheckoutOutcome(), null, 'any other page drops it');
  fresh.setCheckoutOutcome('closed', 'p1');
  globalThis.location.hash = '#/purchase';
  listeners.hashchange();
  assert.equal(fresh.takeCheckoutOutcome().kind, 'closed', 'the payment page keeps it');
  delete globalThis.addEventListener;
  delete globalThis.location;
});

// ---- the guard shared by every build of the checkout page
test('guard: a start stays shut across a rebuild; the older build is no longer current and its window is cancelled', async () => {
  const { createCheckoutGuard } = await import('../public/js/account/checkout-flow.js');
  const guard = createCheckoutGuard();
  const first = guard.enter();
  assert.equal(guard.locked, false);
  guard.lock();
  const cancelled = [];
  guard.register({ cancel: () => cancelled.push('first window') });
  const second = guard.enter();                        // a refresh rebuilt the page without a navigation
  assert.deepEqual(cancelled, ['first window']);
  assert.equal(guard.isCurrent(first), false, 'late callbacks of the first build are ignored');
  assert.equal(guard.isCurrent(second), true);
  assert.equal(guard.locked, true, 'still locked: a second start needs an explicit visit');
  guard.leave();
  assert.equal(guard.locked, false);
  assert.equal(guard.isCurrent(second), false);
});

test('guard: leaving cancels an open window; releasing after an outcome does not cancel it twice', async () => {
  const { createCheckoutGuard } = await import('../public/js/account/checkout-flow.js');
  const guard = createCheckoutGuard();
  guard.enter();
  guard.lock();
  let cancels = 0;
  guard.register({ cancel: () => { cancels += 1; } });
  guard.release();
  guard.leave();
  assert.equal(cancels, 0);
  guard.register({ cancel: () => { cancels += 1; } });
  guard.leave();
  guard.leave();
  assert.equal(cancels, 1);
});

test('flow: a rebuild during the window ignores the late outcome (no record, no navigation)', async () => {
  const { runOverlay, createCheckoutGuard } = await import('../public/js/account/checkout-flow.js');
  const guard = createCheckoutGuard();
  const build = guard.enter();
  const log = [];
  let finish;
  const handle = { result: new Promise((resolve) => { finish = resolve; }), cancel: () => { log.push('cancel'); finish(null); } };
  const run = runOverlay({
    result: { purchase_id: 'p1', transaction_id: 'txn_abcdefghij0123' }, loadOpener: async () => () => handle, args: {},
    ui: { failed: () => log.push('failed') }, go: (p) => log.push(['go', p]), setOutcome: (...a) => log.push(['outcome', ...a]),
    active: () => guard.isCurrent(build), register: (h) => guard.register(h),
  });
  await new Promise((r) => setTimeout(r, 5));
  guard.enter();                                      // the page is rebuilt without a hash change
  let timer;
  const guardTimeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('the older window was not cancelled')), 300); });
  assert.equal(await Promise.race([run, guardTimeout]), 'left');
  clearTimeout(timer);
  assert.deepEqual(log, ['cancel']);
});

// ---- configuration and source rules
test('the dev window is off by default, loaded only by dynamic import, and has no network, storage or script loading', () => {
  assert.match(read('js/config.js'), /accountsOverlay: null/);
  const js = (dir) => readdirSync(join(root, dir)).flatMap((n) => (statSync(join(root, dir, n)).isDirectory() ? js(join(dir, n)) : n.endsWith('.js') ? [join(dir, n)] : []));
  for (const file of js('js')) {
    if (file.endsWith('overlay-shim.js')) continue;
    const source = read(file);
    assert.doesNotMatch(source.replace(/\/\/.*$/gm, ''), /^\s*import\s[^\n]*overlay-shim/m, `${file} must not import the dev window statically`);
  }
  assert.match(read('js/views/account.js'), /CONFIG\.accountsOverlay !== 'dev-shim'\) return null;\s*return \(await import\('\.\.\/account\/overlay-shim\.js'\)\)/);
  for (const file of ['js/account/overlay-shim.js', 'js/account/checkout-flow.js', 'js/account/outcome.js']) {
    assert.doesNotMatch(read(file), /fetch\(|XMLHttpRequest|sendBeacon|localStorage|sessionStorage|indexedDB|createElement\('script'\)|innerHTML|document\.cookie|location\.(assign|replace|hash\s*=)/, file);
    assert.doesNotMatch(read(file), /mockConfirmPayment/, file);
  }
  assert.doesNotMatch(read('js/account/overlay-shim.js'), /transactionId|transaction_id/, 'the window never reads the id');
  assert.doesNotMatch(read('js/account/overlay-shim.js').replace(/\/\/.*$/gm, ''), /\bpaid\b|granted|entitle/i);
  for (const file of js('js').concat(['index.html'])) assert.doesNotMatch(read(file), /paddle\.com|cdn\.paddle|Paddle\.(Setup|Checkout)/i, file);
});

test('the checkout view keeps Pay shut from the click, after any failure and while the window is open, and never retries', () => {
  const source = read('js/views/account.js');
  assert.match(source, /if \(!current\(\) \|\| checkoutGuard\.locked \|\| pay\.getAttribute\('aria-disabled'\) === 'true'\) return;\s*checkoutGuard\.lock\(\);/);
  assert.match(source, /export async function checkoutView\(\) \{[^}]*?const build = checkoutGuard\.enter\(\);[\s\S]*?const c = getClient\(\);\s*const \[p, me\] = await Promise\.all/, 'the build is taken before the first await');
  assert.match(source, /canPay\(state\) && !checkoutGuard\.locked \? 'false' : 'true'/);
  assert.match(source, /if \(!current\(\)\) return;/, 'a replaced build ignores the late answer');
  assert.match(source, /if \(checkoutGuard\.locked\) showFailure\(\);/, 'a rebuild while a start is in flight shows the way to the status page');
  assert.match(source, /else showFailure\(\);/);
  assert.equal((source.match(/c\.checkout\(/g) || []).length, 1, 'exactly one start per click path');
  const checkout = source.slice(source.indexOf('export async function checkoutView'), source.indexOf('export async function purchaseView')).replace(/\/\/.*$/gm, '');
  assert.doesNotMatch(checkout, /setTimeout|setInterval|retry/i);
  assert.match(source, /clearCheckoutOutcome\(\);\s*dialog\.close\(\);\s*navigate\('\/'\)/, 'delete clears it');
  assert.match(source, /clearCheckoutOutcome\(\);\s*navigate\('\/'\)/, 'logout clears it');
});

test('the payment page line is only for a pending purchase and the pending text makes no promise of timing', () => {
  const source = read('js/views/account.js');
  assert.match(source, /outcome && status === 'pending' && purchase && purchase\.id === outcome\.purchaseId/);
  assert.match(source, /c\.purchase\(\{ product_id: p\.id, purchase_id: outcome \? outcome\.purchaseId : null \}\)/);
  assert.equal((source.match(/c\.purchase\(\)/g) || []).length, 0, 'every purchase lookup names the product');
  assert.equal((source.match(/href: '#\/course' \}, t\('account\.backCourse'\)/g) || []).length >= 3, true, 'the purchase page links to the course page');
  const purchase = source.slice(source.indexOf('export async function purchaseView'), source.indexOf('export async function receiptView'));
  assert.doesNotMatch(purchase, /premium\.back|noRepay|href: '#\/premium' \}, t\('premium/);
  assert.equal(JSON.parse(read('i18n/en.json'))['purchase.pending.text'], 'If you finished paying, your access turns on only when the payment is confirmed. Please do not pay again while this is pending.');
  assert.equal(JSON.parse(read('i18n/en.json'))['purchase.after.finished'], 'The checkout window was completed.');
  const strings = JSON.parse(read('i18n/en.json'));
  for (const key of ['purchase.pending', 'purchase.pending.text', 'purchase.after.finished', 'purchase.after.closed']) {
    assert.doesNotMatch(strings[key], /few minutes|usually|shortly|soon|automatic/i, key);
    assert.doesNotMatch(strings[key], /thank|received|paid\b/i, key);
  }
  assert.doesNotMatch(strings['checkout.overlay.body'].replace('No payment is taken here', ''), /payment is taken/i);
  assert.match(strings['checkout.overlay.body'], /No payment is taken here/);
  for (const [key, text] of Object.entries(strings)) if (key !== 'checkout.overlay.body') assert.doesNotMatch(text, /No payment is taken here/, key);
});
