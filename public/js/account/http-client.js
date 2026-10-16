// Same-origin JSON client for the mock-mode accounts API. No bearer tokens are persisted.
const RETRY = 'Could not reach the account service. Please try again.';
const BAD_RESPONSE = 'The account service returned an unexpected response. Please try again.';

export function safeHttpsUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

export function mapHttpError(status, body) {
  const code = body?.error?.code;
  if (status === 401 || code === 'no-session') return { ok: false, code: 'signed_out', message: 'You are signed out. Please sign in again.' };
  if (status === 400 || code === 'invalid') return { ok: false, code: 'invalid_request', message: 'Please check the details and try again.' };
  if (status === 403 || code === 'forbidden') return { ok: false, code: 'reauth_required', message: 'Please sign in again and retry this request.' };
  if (status === 404 || code === 'not-found') return { ok: false, code: 'not_found', message: 'This item is unavailable.' };
  if (status === 429 || code === 'throttled') return { ok: false, code: 'rate_limited', message: 'Too many attempts. Please wait before trying again.' };
  if (status === 409 || code === 'stale-policy' || code === 'identity-conflict') return { ok: false, code: 'conflict', message: 'This has changed. Please review and try again.' };
  if (status === 503 || code === 'unavailable') return { ok: false, code: 'unavailable', message: 'This service is currently unavailable.' };
  return { ok: false, code: 'request_failed', message: 'The request could not be completed.' };
}

export function createHttpClient({ baseUrl, checkoutOrigins = [], fetchImpl = globalThis.fetch } = {}) {
  if (typeof baseUrl !== 'string' || !baseUrl.startsWith('/') || baseUrl.startsWith('//')) throw new TypeError('baseUrl must be a same-origin path');
  const allowedCheckoutOrigins = new Set(Array.isArray(checkoutOrigins)
    ? checkoutOrigins.map((value) => { const safe = safeHttpsUrl(value); return safe ? new URL(safe).origin : null; }).filter(Boolean)
    : []);
  let preauthCsrfToken = null;
  let sessionCsrfToken = null;
  const base = baseUrl.replace(/\/$/, '');
  const sessionCsrfPaths = new Set(['/checkout', '/logout', '/account/delete']);

  async function request(path, { method = 'GET', body, csrf = false } = {}) {
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (csrf) {
      const needsSessionCsrf = sessionCsrfPaths.has(path);
      let token = needsSessionCsrf ? sessionCsrfToken : preauthCsrfToken;
      if (!token) {
        let tokenResult = needsSessionCsrf ? await request('/me') : await request('/auth/csrf');
        let loggedOutLogout = false;
        if (needsSessionCsrf && !tokenResult.ok) {
          if (path !== '/logout' || tokenResult.code !== 'signed_out') return tokenResult;
          tokenResult = await request('/auth/csrf');
          loggedOutLogout = true;
        }
        if (!tokenResult.ok) return tokenResult;
        token = loggedOutLogout ? preauthCsrfToken : (needsSessionCsrf ? sessionCsrfToken : preauthCsrfToken);
        if (!token) return { ok: false, code: 'invalid_response', message: BAD_RESPONSE };
      }
      headers['X-CSRF-Token'] = token;
    }
    let response;
    try {
      response = await fetchImpl(`${base}${path}`, {
        method, credentials: 'same-origin', headers,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch {
      return { ok: false, code: 'network_error', message: RETRY };
    }
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      const error = mapHttpError(response.status, data);
      if (error.code === 'signed_out') sessionCsrfToken = null;
      return error;
    }
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      return { ok: false, code: 'invalid_response', message: BAD_RESPONSE };
    }
    if (path === '/me') sessionCsrfToken = typeof data.csrf_token === 'string' ? data.csrf_token : null;
    if (path === '/auth/csrf') preauthCsrfToken = typeof data.csrf_token === 'string' ? data.csrf_token : null;
    return { ok: true, data };
  }
  const result = async (path, options) => {
    const r = await request(path, options);
    return r.ok ? r.data : r;
  };
  return {
    mock: false,
    async me() {
      const r = await request('/me');
      if (!r.ok) return r.code === 'signed_out' ? { signed_in: false, identities: [] } : r;
      return { ...r.data, signed_in: true, identities: (r.data.providers || []).map((provider) => ({ provider, connected: true })) };
    },
    async providers() {
      const data = await result('/auth/providers');
      if (!data || !Array.isArray(data.providers)) return [];
      return data.providers.map((provider) => ({ ...provider, available: true }));
    },
    async catalog() {
      const data = await result('/products');
      if (!data || !Array.isArray(data.products)) return data;
      return { ...data, products: data.products.map((product) => ({ ...product, checkout_enabled: data.checkout_enabled, policy: data.checkout_policy })) };
    },
    async entitlements() { const r = await result('/entitlements'); return r?.entitlements ?? r; },
    async purchase() {
      const data = await result('/purchases');
      if (!data || !Array.isArray(data.purchases)) return data?.ok === false ? data : null;
      return data.purchases.find((purchase) => purchase.state === 'pending') || null;
    },
    async startSignIn(provider) {
      const token = await request('/auth/csrf');
      if (!token.ok) return token;
      preauthCsrfToken = token.data?.csrf_token || null;
      const next = new URLSearchParams(globalThis.location?.hash?.split('?')[1] || '').get('next');
      const returnTo = `/${['premium', 'checkout', 'account'].includes(next) ? next : 'account'}`;
      const r = await request(`/auth/${encodeURIComponent(provider)}/start`, { method: 'POST', csrf: true, body: { intent: 'login', return_to: returnTo } });
      if (!r.ok) return r;
      const authorizeUrl = safeHttpsUrl(r.data?.authorize_url);
      if (!authorizeUrl) return { ok: false, code: 'unsafe_redirect', message: 'The sign-in provider returned an invalid address.' };
      return { ok: true, authorize_url: authorizeUrl };
    },
    async emailStart(email) {
      const token = await request('/auth/csrf');
      if (!token.ok) return token;
      preauthCsrfToken = token.data?.csrf_token || null;
      return result('/auth/email/start', { method: 'POST', csrf: true, body: { email, intent: 'login', return_to: '/account' } });
    },
    async checkout({ product_id, policy_version, acknowledgements }) {
      const data = await result('/checkout', { method: 'POST', csrf: true, body: { product_id, policy_version, acknowledgements } });
      if (data?.ok === false) return data;
      const redirectUrl = safeHttpsUrl(data?.redirect_url);
      if (!redirectUrl || !allowedCheckoutOrigins.has(new URL(redirectUrl).origin)) {
        return { ok: false, code: 'unsafe_redirect', message: 'The payment provider returned an invalid address.' };
      }
      return { ok: true, ...data, redirect_url: redirectUrl };
    },
    async logout() {
      const data = await result('/logout', { method: 'POST', csrf: true });
      if (data?.ok === true) sessionCsrfToken = null;
      return data;
    },
    async exportData() { return result('/account/export'); },
    async deleteAccount() {
      const data = await result('/account/delete', { method: 'POST', csrf: true });
      if (data?.ok === true) sessionCsrfToken = null;
      return data;
    },
  };
}
