import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// The checkout page rendered with the fake client in a small DOM, so ordering across awaits and rebuilds can be exercised.
class Node {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.className = ''; this.handlers = {}; this.parent = null; this.open = false; this.checked = false; this.textContent = ''; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn); }
  append(...nodes) { for (const n of nodes) { if (n && typeof n === 'object') n.parent = this; this.children.push(n); } }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
  fire(type) { const e = { type, preventDefault() {} }; for (const fn of this.handlers[type] || []) fn(e); return e; }
  find(pred, out = []) { if (pred(this)) out.push(this); for (const c of this.children) if (c.tag) c.find(pred, out); return out; }
  text() { return (this.textContent || '') + this.children.map((c) => (c.tag ? c.text() : c.text)).join(''); }
}
const body = new Node('body');
const windowListeners = [];
globalThis.document = { body, createElement: (tag) => new Node(tag), createTextNode: (text) => ({ text }) };
globalThis.addEventListener = (type, fn) => windowListeners.push([type, fn]);
globalThis.removeEventListener = (type, fn) => { const i = windowListeners.findIndex(([t, f]) => t === type && f === fn); if (i >= 0) windowListeners.splice(i, 1); };
globalThis.location = { hash: '#/checkout?scenario=signed-in' };
const hashchange = (to) => { globalThis.location.hash = to; for (const [type, fn] of [...windowListeners]) if (type === 'hashchange') fn(); };

const { setMessages } = await import('../public/js/i18n.js');
setMessages(JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8')));
const { CONFIG } = await import('../public/js/config.js');
const { getClient } = await import('../public/js/account/index.js');
const { checkoutView } = await import('../public/js/views/account.js');

const client = getClient();
client.state.checkoutEnabled = true;
client.state.checkoutMode = 'overlay';
const TXN = 'txn_01abcdefghjkmnpqrstvwxyz00';
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
let posts = 0;
let postGate = null;                                    // when set, the next POST waits for it
client.checkout = async () => {
  posts += 1;
  if (postGate) await postGate.promise;
  return { ok: true, kind: 'overlay', purchase_id: 'mock-purchase', transaction_id: TXN };
};
const origMe = client.me.bind(client);
let meGate = null;                                      // when set, the next me() waits for it
client.me = async () => { const gate = meGate; meGate = null; if (gate) await gate.promise; return origMe(); };

const pay = (view) => view.node.find((n) => n.tag === 'button' && n.className.includes('button--primary'))[0];
const consent = (view) => view.node.find((n) => n.attrs.id === 'consent')[0];
const reasonOf = (view) => view.node.find((n) => n.attrs.id === 'pay-reason')[0];
const dialogs = () => body.find((n) => n.tag === 'dialog');
const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));
async function built() {                                 // a mounted build with the consent ticked
  const view = await checkoutView();
  consent(view).checked = true;
  consent(view).fire('change');
  return view;
}
function reset() {
  hashchange('#/premium');                              // leave: releases the guard, cancels windows
  posts = 0; postGate = null; meGate = null;
  globalThis.location.hash = '#/checkout?scenario=signed-in';
}

test('a slow older build that finishes after a newer one is mounted cannot cancel its window or change the guard', async () => {
  CONFIG.accountsOverlay = 'dev-shim';
  reset();
  const gate = deferred();
  meGate = gate;
  const olderStarted = checkoutView();                  // build A: waits in me()
  await tick();
  const b = await built();                              // build B: the router drops A and mounts B
  pay(b).fire('click');
  await tick(20);
  assert.equal(dialogs().length, 1, 'B opened its window');
  gate.resolve();                                       // A finishes loading only now
  const a = await olderStarted;
  await tick(20);
  assert.equal(dialogs().length, 1, "A's late completion did not cancel B's window");
  assert.equal(dialogs()[0].open, true);
  assert.equal(pay(a), undefined, 'the superseded build is an empty placeholder: nothing to click, no handlers registered');
  assert.equal(posts, 1);
  const close = dialogs()[0].find((n) => n.tag === 'button')[0];
  close.fire('click');
  await tick(20);
  assert.equal(globalThis.location.hash, '#/purchase', "B's own outcome still navigates");
  reset();
});

test('a refresh while the older POST waits: its late answer opens nothing and navigates nowhere, the rebuilt Pay stays shut', async () => {
  CONFIG.accountsOverlay = 'dev-shim';
  reset();
  const first = await built();
  postGate = deferred();
  pay(first).fire('click');
  await tick();
  assert.equal(posts, 1);
  const rebuilt = await built();                        // refresh(): a new build without a hash change
  assert.equal(pay(rebuilt).getAttribute('aria-disabled'), 'true', 'still locked');
  assert.match(reasonOf(rebuilt).text(), /Check your payment status/);
  pay(rebuilt).fire('click');
  consent(rebuilt).fire('change');
  await tick();
  assert.equal(posts, 1, 'no second start');
  postGate.resolve();                                   // the old POST finally answers
  await tick(30);
  assert.equal(dialogs().length, 0, 'no window opened for the replaced build');
  assert.equal(globalThis.location.hash, '#/checkout?scenario=signed-in', 'and no navigation');
  reset();
});

test('leaving to another page releases the lock and cancels the window; another query on the same page does not', async () => {
  CONFIG.accountsOverlay = 'dev-shim';
  reset();
  const view = await built();
  pay(view).fire('click');
  await tick(20);
  assert.equal(dialogs().length, 1);
  hashchange('#/checkout?scenario=pending');            // same page, other query
  assert.equal(dialogs().length, 1, 'the window stays');
  hashchange('#/checkout/');                             // the router matches a trailing slash: still this page
  assert.equal(dialogs().length, 1);
  hashchange('#/checkout/?scenario=pending');
  assert.equal(dialogs().length, 1);
  hashchange('#/premium');
  assert.equal(dialogs().length, 0, 'leaving cancels it');
  const again = await built();
  assert.equal(pay(again).getAttribute('aria-disabled'), 'false', 'an explicit new visit can start again');
  reset();
});

test('without the dev window configured the answer fails closed: no dialog, Pay shut, the way to the status page shown', async () => {
  CONFIG.accountsOverlay = null;
  reset();
  const view = await built();
  pay(view).fire('click');
  await tick(20);
  assert.equal(dialogs().length, 0);
  assert.equal(pay(view).getAttribute('aria-disabled'), 'true');
  assert.match(reasonOf(view).text(), /Check your payment status/);
  assert.equal(posts, 1);
  reset();
});

test('a sub-path the router does not match (/checkout/not-a-route) is leaving the page: window cancelled, lock released', async () => {
  CONFIG.accountsOverlay = 'dev-shim';
  for (const target of ['#/checkout/not-a-route', '#/checkoutx', '#/checkout/a/b?x=1']) {
    reset();
    const view = await built();
    pay(view).fire('click');
    await tick(20);
    assert.equal(dialogs().length, 1, target);
    hashchange(target);
    assert.equal(dialogs().length, 0, `${target}: the window is cancelled`);
    const again = await built();
    assert.equal(pay(again).getAttribute('aria-disabled'), 'false', `${target}: the lock is released`);
  }
  reset();
});

test('a sub-path hash change while the POST waits: its late answer opens nothing and navigates nowhere', async () => {
  CONFIG.accountsOverlay = 'dev-shim';
  reset();
  const view = await built();
  postGate = deferred();
  pay(view).fire('click');
  await tick();
  hashchange('#/checkout/not-a-route');
  postGate.resolve();
  await tick(30);
  assert.equal(dialogs().length, 0);
  assert.equal(globalThis.location.hash, '#/checkout/not-a-route', 'no navigation of its own');
  reset();
});
