// Shared bits for the account screens: the client instance, the test-mode strip, the query helper.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { refresh } from '../router.js';
import { createFakeClient, SCENARIOS } from './client.js';
import { createHttpClient } from './http-client.js';
import { CONFIG } from '../config.js';

let client = null;

export function query() {
  const q = location.hash.split('?')[1] || '';
  return new URLSearchParams(q);
}

export function getClient() {
  if (!client) {
    const initial = query().get('scenario');
    client = CONFIG.accountsApi === null
      ? createFakeClient({ scenario: SCENARIOS.includes(initial) ? initial : 'signed-out' })
      : createHttpClient({ baseUrl: CONFIG.accountsApi, checkoutOrigins: CONFIG.accountsCheckoutOrigins });
  }
  return client;
}

// Test mode strip: always visible while a fake client is in use, so nothing here can be mistaken for a real payment.
export function testStrip(c) {
  if (!c.mock) return null;
  const select = h('select', { id: 'mock-scenario', class: 'mock__select' }, SCENARIOS.map((s) => h('option', { value: s, selected: s === c.state.scenario }, t(`mock.scenario.${s}`))));
  select.addEventListener('change', () => {
    c.state.scenario = select.value;
    refresh();
  });
  const toggle = (id, label, get, set) => {
    const box = h('input', { type: 'checkbox', id, checked: get() });
    box.addEventListener('change', () => {
      set(box.checked);
      refresh();
    });
    return h('label', { class: 'mock__check', for: id }, box, label);
  };
  const confirm = h('button', { type: 'button', class: 'button button--secondary button--small' }, t('mock.confirm'));
  confirm.addEventListener('click', () => {
    c.mockConfirmPayment();
    refresh();
  });
  return h(
    'section',
    { class: 'mock', 'aria-label': t('mock.label') },
    h('p', { class: 'mock__title' }, t('mock.title')),
    h('div', { class: 'mock__controls' }, h('label', { for: 'mock-scenario' }, t('mock.scenario')), select, toggle('mock-providers', t('mock.allProviders'), () => c.state.providersOn.apple, (on) => Object.keys(c.state.providersOn).forEach((k) => { c.state.providersOn[k] = on || k === 'email' || k === 'google'; })), toggle('mock-checkout', t('mock.checkoutOn'), () => c.state.checkoutEnabled, (on) => { c.state.checkoutEnabled = on; }), toggle('mock-chapters', t('mock.chaptersOn'), () => c.state.chapterCount !== null, (on) => { c.state.chapterCount = on ? 2 : null; }), confirm),
  );
}
