// Client for the account API (accounts contract: /me, /auth/*, /products, /entitlements, /checkout, ...).
// This file holds the interface and the FAKE client used for screens in test mode. The real client is added with
// the server; until then `CONFIG.accountsUi` stays off and none of this is loaded.

import { PROVIDERS } from './model.js';

export const SCENARIOS = ['signed-out', 'signed-in', 'pending', 'paid', 'refunded'];

const PRODUCT = { id: 'python-lifetime', title: 'Python, beginner to advanced', amount_minor: 500, currency: 'USD', published_chapter_count: null, checkout_enabled: false, policy: { version: 'placeholder', required_acknowledgements: ['withdrawal-consent'] } };

export function createFakeClient({ scenario = 'signed-out', providersOn = { google: true, apple: false, discord: false, metamask: false, email: true }, checkoutEnabled = false, chapterCount = null } = {}) {
  const state = { scenario, providersOn: { ...providersOn }, checkoutEnabled, chapterCount, consent: false };
  const signedIn = () => state.scenario !== 'signed-out';
  return {
    mock: true,
    state,
    async me() {
      return signedIn() ? { signed_in: true, identities: [{ provider: 'google', connected: true }] } : { signed_in: false, identities: [] };
    },
    async providers() {
      return [...PROVIDERS, 'email'].map((id) => ({ id, available: !!state.providersOn[id] }));
    },
    async catalog() {
      return { products: [{ ...PRODUCT, published_chapter_count: state.chapterCount, checkout_enabled: state.checkoutEnabled }] };
    },
    async entitlements() {
      if (state.scenario === 'paid') return [{ product_id: PRODUCT.id, state: 'active' }];
      if (state.scenario === 'refunded') return [{ product_id: PRODUCT.id, state: 'revoked' }];
      return [];
    },
    async purchase() {
      if (state.scenario === 'pending') return { product_id: PRODUCT.id, state: 'pending' };
      return null;
    },
    async startSignIn(provider) {
      if (!state.providersOn[provider]) return { ok: false, code: 'provider_unavailable' };
      state.scenario = 'signed-in';
      return { ok: true };
    },
    async emailStart(email) {
      return { ok: typeof email === 'string' && email.includes('@'), accepted: true };
    },
    async checkout({ product_id, policy_version, acknowledgements }) {
      if (!state.checkoutEnabled) return { ok: false, code: 'checkout_disabled' };
      if (product_id !== PRODUCT.id || policy_version !== PRODUCT.policy.version || !acknowledgements.includes('withdrawal-consent')) return { ok: false, code: 'policy_required' };
      state.scenario = 'pending';
      return { ok: true };
    },
    async logout() {
      state.scenario = 'signed-out';
      return { ok: true };
    },
    async exportData() {
      return { format: 'account-export-test', identities: [{ provider: 'google' }], purchases: state.scenario === 'signed-out' ? [] : [{ product_id: PRODUCT.id, state: state.scenario }] };
    },
    async deleteAccount() {
      state.scenario = 'signed-out';
      return { ok: true };
    },
    // Test-mode only: what the server would do when it confirms or refunds a payment.
    mockConfirmPayment() {
      if (state.scenario === 'pending') state.scenario = 'paid';
    },
  };
}
