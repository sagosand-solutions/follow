// Account screens (test mode, behind CONFIG.accountsUi). Free-course pages never link here except the quiet header link.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { navigate, refresh, currentPath } from '../router.js';
import { getClient, testStrip, query } from '../account/index.js';
import { safeHttpsUrl } from '../account/http-client.js';
import { runOverlay, createCheckoutGuard } from '../account/checkout-flow.js';
import { setCheckoutOutcome, takeCheckoutOutcome, clearCheckoutOutcome } from '../account/outcome.js';
import { CONFIG } from '../config.js';
import { PROVIDERS, formatPrice, purchaseStatus, canPay, payBlockedReason, safeNext, acknowledgementsFor } from '../account/model.js';

// Shared by every build of the checkout page (see createCheckoutGuard).
const checkoutGuard = createCheckoutGuard();

const page = (c, title, ...children) => ({ title, node: h('div', { class: 'stack account' }, testStrip(c), h('h1', {}, title), ...children) });

// The checkout window opener. Nothing is loaded unless the dev stand-in is switched on in the configuration (never by URL or
// storage); a real provider window is a separate, later step. Null means "no opener": the caller fails closed.
async function loadOverlayOpener() {
  if (CONFIG.accountsOverlay !== 'dev-shim') return null;
  return (await import('../account/overlay-shim.js')).openOverlayShim;
}

async function product(c) {
  const { products } = await c.catalog();
  return products[0];
}

// ---------- Premium: what you get ----------
export async function premiumView() {
  const c = getClient();
  const p = await product(c);
  const [me, ent, purchase] = await Promise.all([c.me(), c.entitlements(), c.purchase({ product_id: p.id })]);
  const status = purchaseStatus({ entitlements: ent, purchase }, p.id);
  const price = formatPrice(p.amount_minor, p.currency);
  const count = Number.isInteger(p.published_chapter_count) ? h('p', {}, t('premium.today', { n: p.published_chapter_count })) : null;
  let action;
  if (status === 'active') action = h('p', { class: 'account__note' }, t('premium.owned'));
  else if (status === 'pending') action = h('a', { class: 'button button--primary', href: '#/purchase' }, t('premium.seePending'));
  else if (!me.signed_in) action = h('a', { class: 'button button--primary', href: '#/signin?next=premium' }, t('premium.continueSignin'));
  else action = h('a', { class: 'button button--primary', href: '#/checkout' }, t('premium.buy', { price: price || '' }));
  return page(
    c,
    p.title,
    h('p', { class: 'lead' }, t('premium.lead', { price: price || '' })),
    h('section', { class: 'account__card' }, h('h2', {}, t('premium.get.title')), h('ul', {}, h('li', {}, t('premium.get.1')), h('li', {}, t('premium.get.2'))), h('h2', {}, t('premium.not.title')), h('p', {}, t('premium.not.text')), count),
    h('div', { class: 'actions' }, action),
    h('p', { class: 'muted small' }, t('refund.placeholder')),
    h('p', {}, h('a', { href: '#/course' }, t('account.backCourse'))),
  );
}

// ---------- Sign in ----------
export async function signinView() {
  const c = getClient();
  const next = safeNext(query().get('next'));
  const providers = await c.providers();
  const status = h('p', { class: 'account__status', role: 'status' });
  const buttons = PROVIDERS.map((id) => {
    const on = !!providers.find((x) => x.id === id)?.available;
    const b = h('button', { type: 'button', class: 'button button--secondary provider', 'aria-disabled': on ? null : 'true', 'aria-describedby': on ? null : `unavail-${id}` }, t(`provider.${id}`));
    if (!on) {
      return h('div', { class: 'provider-row' }, b, h('span', { id: `unavail-${id}`, class: 'provider-note' }, t('provider.unavailable')));
    }
    b.addEventListener('click', async () => {
      const r = await c.startSignIn(id);
      if (r.ok && r.authorize_url) {
        const authorizeUrl = safeHttpsUrl(r.authorize_url);
        if (authorizeUrl) globalThis.location.assign(authorizeUrl);
        else status.textContent = t('signin.error');
      } else if (r.ok) navigate(`/${next}`);
      else status.textContent = r.message || t('signin.error');
    });
    return h('div', { class: 'provider-row' }, b);
  });
  const emailOn = !!providers.find((x) => x.id === 'email')?.available;
  const email = h('input', { type: 'email', id: 'email', name: 'email', autocomplete: 'email', required: true });
  const emailForm = h('form', { class: 'email-form' }, h('label', { for: 'email' }, t('signin.email.label')), email, h('button', { type: 'submit', class: 'button button--secondary', 'aria-disabled': emailOn ? null : 'true' }, t('signin.email.button')));
  emailForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!emailOn) return;
    const result = await c.emailStart(email.value);
    status.textContent = result?.ok === false ? (result.message || t('signin.error')) : t('signin.email.sent');
  });
  return page(
    c,
    t('signin.title'),
    h('p', { class: 'lead' }, t('signin.why')),
    h('section', { class: 'account__card' }, h('h2', {}, t('signin.keep.title')), h('p', {}, t('signin.keep.text'))),
    h('div', { class: 'providers' }, ...buttons),
    emailForm,
    status,
    h('p', { class: 'muted' }, t('signin.free'), ' ', h('a', { href: '#/course' }, t('account.backCourse'))),
  );
}

// ---------- Checkout review ----------
export async function checkoutView() {
  // FIRST, before any await: builds are ordered by when they start (the router drops all but the newest), so a slow older build
  // that finishes after a newer one is mounted can neither cancel its window nor change the guard.
  const build = checkoutGuard.enter();
  const current = () => checkoutGuard.isCurrent(build);
  const c = getClient();
  const [p, me] = await Promise.all([product(c), c.me()]);
  if (!current()) return page(c, t('checkout.title'));          // superseded while loading: the router drops this; it changes nothing
  const price = formatPrice(p.amount_minor, p.currency);
  const box = h('input', { type: 'checkbox', id: 'consent', name: 'consent' });
  const pay = h('button', { type: 'button', class: 'button button--primary', 'aria-disabled': 'true', 'aria-describedby': 'pay-reason' }, t('checkout.pay', { price: price || '' }));
  const reason = h('p', { id: 'pay-reason', class: 'hint', role: 'status' });
  // The guard is shared by every build of this page: from the click on, a start stays shut across a rebuild (a refresh) and is
  // released only by leaving the page, so a second start is always an explicit new visit, never a callback or a retry. A consent
  // change cannot re-enable Pay while a checkout is opening, open, or has failed (a purchase may exist).
  const sync = () => {
    const state = { signedIn: me.signed_in, checkoutEnabled: p.checkout_enabled, acknowledged: box.checked };
    pay.setAttribute('aria-disabled', canPay(state) && !checkoutGuard.locked ? 'false' : 'true');
    if (checkoutGuard.locked) return;
    const key = payBlockedReason(state);
    reason.textContent = key ? t(key) : '';
  };
  const showFailure = () => {
    reason.textContent = '';
    reason.append(t('checkout.error'), ' ', h('a', { href: '#/purchase' }, t('checkout.checkStatus')));
  };
  const onLeave = () => {
    if (/^\/checkout\/?$/.test(currentPath().split('?')[0])) return;      // the router's own checkout path (query and trailing slash kept): stays locked
    checkoutGuard.leave();
    globalThis.removeEventListener('hashchange', onLeave);
  };
  clearCheckoutOutcome();
  globalThis.addEventListener('hashchange', onLeave);
  box.addEventListener('change', sync);
  pay.addEventListener('click', async () => {
    if (!current() || checkoutGuard.locked || pay.getAttribute('aria-disabled') === 'true') return;
    checkoutGuard.lock();
    pay.setAttribute('aria-disabled', 'true');
    reason.textContent = t('checkout.opening');
    const r = await c.checkout({ product_id: p.id, policy_version: p.policy.version, acknowledgements: acknowledgementsFor(p.policy, box.checked) });
    if (!current()) return;
    if (r.ok && r.kind === 'overlay') {
      await runOverlay({
        result: r, loadOpener: loadOverlayOpener, args: { title: p.title, price: price || '' }, ui: { failed: showFailure },
        go: navigate, setOutcome: setCheckoutOutcome, active: current, register: (handle) => checkoutGuard.register(handle),
      });
      if (current()) checkoutGuard.release();
    } else if (r.ok && r.redirect_url) globalThis.location.assign(r.redirect_url);
    else if (r.ok) navigate('/purchase');
    else showFailure();
  });
  if (checkoutGuard.locked) showFailure();             // rebuilt while a start was in flight: its outcome is unknown, so show the way to the status page
  sync();
  return page(
    c,
    t('checkout.title'),
    h('section', { class: 'account__card' }, h('p', { class: 'account__product' }, p.title), h('p', {}, t('checkout.amount', { price: price || '' })), me.signed_in ? h('p', { class: 'muted' }, t('checkout.signedIn')) : h('p', {}, h('a', { href: '#/signin?next=checkout' }, t('premium.continueSignin')))),
    h('div', { class: 'consent' }, box, h('label', { for: 'consent' }, t('consent.placeholder'))),
    h('div', { class: 'actions' }, pay, reason),
    h('p', { class: 'muted small' }, t('refund.placeholder')),
  );
}

// ---------- Pending / received ----------
export async function purchaseView() {
  const c = getClient();
  const p = await product(c);
  const outcome = takeCheckoutOutcome();            // read once; it only says what happened in the window, never the payment status
  const [ent, purchase] = await Promise.all([c.entitlements(), c.purchase({ product_id: p.id, purchase_id: outcome ? outcome.purchaseId : null })]);
  const status = purchaseStatus({ entitlements: ent, purchase }, p.id);
  // The line is for the purchase that window belonged to, and only while that one is the pending purchase shown.
  const outcomeLine = outcome && status === 'pending' && purchase && purchase.id === outcome.purchaseId ? h('p', { role: 'status' }, t(`purchase.after.${outcome.kind}`)) : null;
  const live = h('p', { class: 'visually-hidden', role: 'status' });
  const again = h('button', { type: 'button', class: 'button button--secondary' }, t('purchase.again'));
  again.addEventListener('click', () => {
    live.textContent = t('purchase.checking');
    refresh();
  });
  let body;
  if (status === 'active') body = [h('h2', {}, t('purchase.received')), h('div', { class: 'actions' }, h('a', { class: 'button button--primary', href: '#/premium' }, t('purchase.open')), h('a', { href: '#/receipt' }, t('purchase.receipt')))];
  else if (status === 'pending') body = [outcomeLine, h('h2', {}, t('purchase.pending')), h('p', {}, t('purchase.pending.text')), h('div', { class: 'actions' }, again, h('a', { href: '#/course' }, t('account.backCourse')))];
  else if (status === 'revoked') body = [h('p', {}, t('purchase.revoked'))];
  else body = [h('p', {}, t('purchase.none')), h('a', { href: '#/course' }, t('account.backCourse'))];
  return page(c, t('purchase.title'), h('section', { class: 'account__card' }, ...body), live);
}

// ---------- Receipt ----------
export async function receiptView() {
  const c = getClient();
  const p = await product(c);
  const ent = await c.entitlements();
  const active = purchaseStatus({ entitlements: ent, purchase: null }, p.id) === 'active';
  const print = h('button', { type: 'button', class: 'button button--secondary', onclick: () => window.print() }, t('receipt.print'));
  return page(
    c,
    t('receipt.title'),
    active
      ? h('section', { class: 'account__card' }, h('dl', { class: 'receipt' }, h('dt', {}, t('receipt.product')), h('dd', {}, p.title), h('dt', {}, t('receipt.amount')), h('dd', {}, formatPrice(p.amount_minor, p.currency) || ''), h('dt', {}, t('receipt.ref')), h('dd', {}, t('receipt.refTest'))), h('p', { class: 'muted small' }, t('receipt.legal')), print)
      : h('p', {}, t('purchase.none')),
  );
}

// ---------- Account ----------
export async function accountView() {
  const c = getClient();
  const me = await c.me();
  if (!me.signed_in) return page(c, t('account.title'), h('p', {}, t('account.signedOut')), h('a', { class: 'button button--primary', href: '#/signin?next=account' }, t('signin.title')));
  const p = await product(c);
  const [ent] = await Promise.all([c.entitlements()]);
  const status = purchaseStatus({ entitlements: ent, purchase: await c.purchase({ product_id: p.id }) }, p.id);
  const message = h('p', { class: 'account__status', role: 'status' });
  const exportBtn = h('button', { type: 'button', class: 'button button--secondary' }, t('account.export'));
  exportBtn.addEventListener('click', async () => {
    const data = await c.exportData();
    if (data?.ok === false) {
      message.textContent = data.message || t('account.exportFailed');
      return;
    }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = h('a', { href: url, download: 'account-export.json' });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message.textContent = t('account.exported');
  });
  const deleteConfirm = h('button', { type: 'button', class: 'button button--primary' }, t('delete.confirm'));
  const deleteStatus = h('p', { class: 'account__status', role: 'status' });
  const dialog = h('dialog', { class: 'dialog', 'aria-labelledby': 'del-title' }, h('h2', { id: 'del-title' }, t('delete.title')), h('p', {}, t('delete.what')), h('p', {}, t('delete.kept')), deleteStatus, h('div', { class: 'actions actions--row' }, h('button', { type: 'button', class: 'button button--secondary', autofocus: true, onclick: () => dialog.close() }, t('delete.cancel')), deleteConfirm));
  deleteConfirm.addEventListener('click', async () => {
    const result = await c.deleteAccount();
    if (result?.ok !== true) {
      deleteStatus.textContent = result?.message || t('account.deleteFailed');
      return;
    }
    clearCheckoutOutcome();
    dialog.close();
    navigate('/');
  });
  const del = h('button', { type: 'button', class: 'button button--secondary' }, t('account.delete'));
  del.addEventListener('click', () => { deleteStatus.textContent = ''; dialog.showModal(); });
  const out = h('button', { type: 'button', class: 'button button--secondary' }, t('account.signout'));
  out.addEventListener('click', async () => {
    const result = await c.logout();
    if (result?.ok !== true) {
      message.textContent = result?.message || t('account.logoutFailed');
      return;
    }
    clearCheckoutOutcome();
    navigate('/');
  });
  const purchaseLine = status === 'active' ? t('account.purchase.active', { title: p.title }) : status === 'pending' ? t('account.purchase.pending', { title: p.title }) : status === 'revoked' ? t('account.purchase.revoked', { title: p.title }) : t('account.purchase.none');
  return page(
    c,
    t('account.title'),
    h('section', { class: 'account__card' }, h('h2', {}, t('account.signins')), h('ul', {}, me.identities.map((i) => h('li', {}, t(`provider.${i.provider}`), ' · ', t('account.connected')))), h('p', { class: 'muted small' }, t('account.addMethod'))),
    h('section', { class: 'account__card' }, h('h2', {}, t('account.purchases')), h('p', {}, purchaseLine), status === 'active' ? h('a', { href: '#/receipt' }, t('purchase.receipt')) : null),
    h('section', { class: 'account__card' }, h('h2', {}, t('account.data')), h('div', { class: 'actions' }, exportBtn, del), message),
    h('div', { class: 'actions' }, out),
    dialog,
  );
}
