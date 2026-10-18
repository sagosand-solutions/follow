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

// Where the browser should land after sign-in: this app's real location (its own base path plus a hash route), same origin.
// The server only accepts a local absolute path, so anything odd falls back to the site root.
export function appReturnTo(route = 'account', location = globalThis.location) {
  const path = typeof location?.pathname === 'string' && /^\/(?!\/)[^\\?#\s]*$/.test(location.pathname) ? location.pathname : '/';
  return `${path}#/${route}`;
}

// The Paddle-path checkout answer, strictly: exactly these keys, a pending state, and ids of the documented shape. The result is a
// NEW object (never a spread of the raw answer) and nothing in it echoes input. A full-string match is checked twice (anchors and
// matched length) so a trailing line break of any kind is refused; JavaScript has no \Z anchor.
const OVERLAY_TOP = ['checkout', 'provider', 'purchase_id', 'state'];
const OVERLAY_INNER = ['mode', 'transaction_id'];
const TXN_ID = /^txn_[a-z0-9]{10,40}$/;
const PURCHASE_ID = /^[A-Za-z0-9_-]{1,64}$/;
const fullMatch = (re, value) => typeof value === 'string' && re.test(value) && re.exec(value)[0].length === value.length;
const exactKeys = (value, keys) => !!value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join() === keys.join();

// The ONLY scoring scopes the server contract accepts: exactly `total`, or a full `course:<id>` where the id starts with a
// lowercase letter and is 1..64 characters of [a-z0-9-]. `fullMatch` also refuses a trailing line break (JS `$` allows one).
// Shared by scoringMe and scoringLeaderboards so both speak the server's format; the server uses the same shape.
const COURSE_SCOPE = /^course:[a-z][a-z0-9-]{0,63}$/;
export const validScope = (scope) => scope === 'total' || fullMatch(COURSE_SCOPE, scope);

export function parseOverlayCheckout(data) {
  if (!exactKeys(data, OVERLAY_TOP) || data.state !== 'pending' || data.provider !== 'paddle' || !fullMatch(PURCHASE_ID, data.purchase_id)) return null;
  const inner = data.checkout;
  if (!exactKeys(inner, OVERLAY_INNER) || inner.mode !== 'overlay' || !fullMatch(TXN_ID, inner.transaction_id)) return null;
  return { ok: true, kind: 'overlay', purchase_id: data.purchase_id, transaction_id: inner.transaction_id };
}

export function mapHttpError(status, body) {
  const code = body?.error?.code;
  if (status === 401 || code === 'no-session') return { ok: false, code: 'signed_out', message: 'You are signed out. Please sign in again.' };
  if (status === 400 || code === 'invalid') return { ok: false, code: 'invalid_request', message: 'Please check the details and try again.' };
  if (status === 403 || code === 'forbidden') return { ok: false, code: 'reauth_required', message: 'Please sign in again and retry this request.' };
  if (status === 404 || code === 'not-found') return { ok: false, code: 'not_found', message: 'This item is unavailable.' };
  if (status === 429 || code === 'throttled') return { ok: false, code: 'rate_limited', message: 'Too many attempts. Please wait before trying again.' };
  if (status === 409 || code === 'stale-policy' || code === 'identity-conflict') {
    const scoringCode = ['expired', 'stale', 'locked', 'idempotency_conflict', 'claim_conflict'].includes(code) ? code : 'conflict';
    const error = { ok: false, code: scoringCode, message: scoringCode === 'locked' ? 'This question has already been answered.' : 'This has changed. Please review and try again.' };
    if (body?.attempt && typeof body.attempt === 'object') error.attempt = body.attempt;
    return error;
  }
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
      const needsSessionCsrf = sessionCsrfPaths.has(path) || path.startsWith('/scoring/');
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
        ...(path.startsWith('/scoring/') || path === '/leaderboards' || path.startsWith('/leaderboards?') ? { cache: 'no-store' } : {}),
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
    // Signed out is a state, not an error: no entitlements, no purchase (same answers as the fake client).
    async entitlements() { const r = await result('/entitlements'); return r?.code === 'signed_out' ? [] : (r?.entitlements ?? r); },
    // The pending purchase to show: only this product's, and the one with `purchase_id` when it is still pending (the server lists
    // newest first, so without an id it is the newest pending one).
    async purchase({ product_id = null, purchase_id = null } = {}) {
      const data = await result('/purchases');
      if (data?.code === 'signed_out') return null;
      if (!data || !Array.isArray(data.purchases)) return data?.ok === false ? data : null;
      const pending = data.purchases.filter((purchase) => purchase.state === 'pending' && (product_id === null || purchase.product_id === product_id));
      return (purchase_id !== null && pending.find((purchase) => purchase.id === purchase_id)) || pending[0] || null;
    },
    async startSignIn(provider) {
      const token = await request('/auth/csrf');
      if (!token.ok) return token;
      preauthCsrfToken = token.data?.csrf_token || null;
      const next = new URLSearchParams(globalThis.location?.hash?.split('?')[1] || '').get('next');
      const returnTo = appReturnTo(['premium', 'checkout', 'account'].includes(next) ? next : 'account');
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
      return result('/auth/email/start', { method: 'POST', csrf: true, body: { email, intent: 'login', return_to: appReturnTo('account') } });
    },
    // A premium question the server lets this session read: { ok:true, question } or { ok:false, code }. Never cached or stored.
    async premiumQuestion(id) {
      const r = await request(`/content/question/${encodeURIComponent(id)}`);
      return r.ok ? { ok: true, question: r.data } : r;
    },
    async scoringIssue(questionId) {
      if (typeof questionId !== 'string' || !questionId) return { ok: false, code: 'invalid_request', message: 'This question is unavailable.' };
      const r = await request(`/scoring/questions/${encodeURIComponent(questionId)}/issue`, { method: 'POST', csrf: true, body: {} });
      return r.ok ? { ok: true, ...r.data } : r;
    },
    async scoringAnswer({ issue_id, outcome, option_id = null, idempotency_key }) {
      if (typeof issue_id !== 'string' || !['answer', 'not_sure'].includes(outcome) || typeof idempotency_key !== 'string') {
        return { ok: false, code: 'invalid_request', message: 'Please choose an answer.' };
      }
      if ((outcome === 'answer' && typeof option_id !== 'string') || (outcome === 'not_sure' && option_id !== null)) {
        return { ok: false, code: 'invalid_request', message: 'Please choose an answer.' };
      }
      const r = await request('/scoring/answers', { method: 'POST', csrf: true, body: { issue_id, outcome, option_id, idempotency_key } });
      return r.ok ? { ok: true, ...r.data } : r;
    },
    async scoringMe({ family, window = 'all', scope = 'total' } = {}) {
      if (!['official', 'free'].includes(family) || !['all', 'week'].includes(window) || !validScope(scope)) {
        return { ok: false, code: 'invalid_request', message: 'Choose a scoring list.' };
      }
      const r = await request(`/scoring/me?${new URLSearchParams({ family, scope, window })}`);
      return r.ok ? { ok: true, ...r.data } : r;
    },
    async scoringLeaderboards({ family, scope = 'total', window = 'all', limit = 50, cursor = null }) {
      if (!['official', 'free'].includes(family) || !['all', 'week'].includes(window) || !Number.isInteger(limit) || limit < 1 || limit > 50 || !validScope(scope)) {
        return { ok: false, code: 'invalid_request', message: 'Choose a scoring list.' };
      }
      const query = new URLSearchParams({ family, scope, window, limit: String(limit) });
      if (cursor) query.set('cursor', cursor);
      const r = await request(`/leaderboards?${query}`);
      return r.ok ? { ok: true, ...r.data } : r;
    },
    async checkout({ product_id, policy_version, acknowledgements }) {
      const data = await result('/checkout', { method: 'POST', csrf: true, body: { product_id, policy_version, acknowledgements } });
      if (data?.ok === false) return data;
      if (data && typeof data === 'object' && 'checkout' in data) {          // the Paddle path: an overlay answer, never together with a redirect
        return parseOverlayCheckout(data) || { ok: false, code: 'invalid_response', message: BAD_RESPONSE };
      }
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
