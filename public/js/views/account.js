// Account screens (test mode, behind CONFIG.accountsUi). Free-course pages never link here except the quiet header link.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { navigate, refresh } from '../router.js';
import { getClient, testStrip, query } from '../account/index.js';
import { PROVIDERS, formatPrice, purchaseStatus, canPay, payBlockedReason, safeNext } from '../account/model.js';

const page = (c, title, ...children) => ({ title, node: h('div', { class: 'stack account' }, testStrip(c), h('h1', {}, title), ...children) });

async function product(c) {
  const { products } = await c.catalog();
  return products[0];
}

// ---------- Premium: what you get ----------
export async function premiumView() {
  const c = getClient();
  const [p, me, ent, purchase] = await Promise.all([product(c), c.me(), c.entitlements(), c.purchase()]);
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
    const on = providers.find((x) => x.id === id).available;
    const b = h('button', { type: 'button', class: 'button button--secondary provider', 'aria-disabled': on ? null : 'true', 'aria-describedby': on ? null : `unavail-${id}` }, t(`provider.${id}`));
    if (!on) {
      return h('div', { class: 'provider-row' }, b, h('span', { id: `unavail-${id}`, class: 'provider-note' }, t('provider.unavailable')));
    }
    b.addEventListener('click', async () => {
      const r = await c.startSignIn(id);
      if (r.ok && r.authorize_url) globalThis.location.assign(r.authorize_url);
      else if (r.ok) navigate(`/${next}`);
      else status.textContent = t('signin.error');
    });
    return h('div', { class: 'provider-row' }, b);
  });
  const emailOn = providers.find((x) => x.id === 'email').available;
  const email = h('input', { type: 'email', id: 'email', name: 'email', autocomplete: 'email', required: true });
  const emailForm = h('form', { class: 'email-form' }, h('label', { for: 'email' }, t('signin.email.label')), email, h('button', { type: 'submit', class: 'button button--secondary', 'aria-disabled': emailOn ? null : 'true' }, t('signin.email.button')));
  emailForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!emailOn) return;
    await c.emailStart(email.value);
    status.textContent = t('signin.email.sent');
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
  const c = getClient();
  const [p, me] = await Promise.all([product(c), c.me()]);
  const price = formatPrice(p.amount_minor, p.currency);
  const box = h('input', { type: 'checkbox', id: 'consent', name: 'consent' });
  const pay = h('button', { type: 'button', class: 'button button--primary', 'aria-disabled': 'true', 'aria-describedby': 'pay-reason' }, t('checkout.pay', { price: price || '' }));
  const reason = h('p', { id: 'pay-reason', class: 'hint', role: 'status' });
  const sync = () => {
    const state = { signedIn: me.signed_in, checkoutEnabled: p.checkout_enabled, acknowledged: box.checked };
    const ok = canPay(state);
    pay.setAttribute('aria-disabled', ok ? 'false' : 'true');
    const key = payBlockedReason(state);
    reason.textContent = key ? t(key) : '';
  };
  box.addEventListener('change', sync);
  pay.addEventListener('click', async () => {
    if (pay.getAttribute('aria-disabled') === 'true') return;
    const r = await c.checkout({ product_id: p.id, policy_version: p.policy.version, acknowledgements: box.checked ? ['withdrawal-consent'] : [] });
    if (r.ok) navigate('/purchase');
    else reason.textContent = t('checkout.error');
  });
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
  const [ent, purchase] = await Promise.all([c.entitlements(), c.purchase()]);
  const status = purchaseStatus({ entitlements: ent, purchase }, p.id);
  const live = h('p', { class: 'visually-hidden', role: 'status' });
  const again = h('button', { type: 'button', class: 'button button--secondary' }, t('purchase.again'));
  again.addEventListener('click', () => {
    live.textContent = t('purchase.checking');
    refresh();
  });
  let body;
  if (status === 'active') body = [h('h2', {}, t('purchase.received')), h('div', { class: 'actions' }, h('a', { class: 'button button--primary', href: '#/premium' }, t('purchase.open')), h('a', { href: '#/receipt' }, t('purchase.receipt')))];
  else if (status === 'pending') body = [h('h2', {}, t('purchase.pending')), h('p', {}, t('purchase.pending.text')), h('p', {}, t('purchase.noRepay')), h('div', { class: 'actions' }, again)];
  else if (status === 'revoked') body = [h('p', {}, t('purchase.revoked'))];
  else body = [h('p', {}, t('purchase.none')), h('a', { href: '#/premium' }, t('premium.back'))];
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
  const status = purchaseStatus({ entitlements: ent, purchase: await c.purchase() }, p.id);
  const message = h('p', { class: 'account__status', role: 'status' });
  const exportBtn = h('button', { type: 'button', class: 'button button--secondary' }, t('account.export'));
  exportBtn.addEventListener('click', async () => {
    const data = await c.exportData();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = h('a', { href: url, download: 'account-export.json' });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message.textContent = t('account.exported');
  });
  const dialog = h('dialog', { class: 'dialog', 'aria-labelledby': 'del-title' }, h('h2', { id: 'del-title' }, t('delete.title')), h('p', {}, t('delete.what')), h('p', {}, t('delete.kept')), h('div', { class: 'actions actions--row' }, h('button', { type: 'button', class: 'button button--secondary', autofocus: true, onclick: () => dialog.close() }, t('delete.cancel')), h('button', { type: 'button', class: 'button button--primary', onclick: async () => { await c.deleteAccount(); dialog.close(); navigate('/'); } }, t('delete.confirm'))));
  const del = h('button', { type: 'button', class: 'button button--secondary', onclick: () => dialog.showModal() }, t('account.delete'));
  const out = h('button', { type: 'button', class: 'button button--secondary', onclick: async () => { await c.logout(); navigate('/'); } }, t('account.signout'));
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
