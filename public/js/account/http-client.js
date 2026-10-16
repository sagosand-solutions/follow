// Same-origin JSON client for the mock-mode accounts API. No bearer tokens are persisted.
const RETRY = 'Could not reach the account service. Please try again.';

export function mapHttpError(status, body) {
  const code = body?.error?.code;
  if (status === 401 || code === 'no-session') return { ok: false, code: 'signed_out', message: 'You are signed out. Please sign in again.' };
  if (status === 404 || code === 'not-found') return { ok: false, code: 'not_found', message: 'This item is unavailable.' };
  if (status === 409 || code === 'stale-policy' || code === 'identity-conflict') return { ok: false, code: 'conflict', message: 'This has changed. Please review and try again.' };
  if (status === 503 || code === 'unavailable') return { ok: false, code: 'unavailable', message: 'This service is currently unavailable.' };
  return { ok: false, code: 'request_failed', message: 'The request could not be completed.' };
}

export function createHttpClient({ baseUrl, fetchImpl = globalThis.fetch } = {}) {
  if (typeof baseUrl !== 'string' || !baseUrl.startsWith('/') || baseUrl.startsWith('//')) throw new TypeError('baseUrl must be a same-origin path');
  let csrfToken = null;
  let sessionCsrf = false;
  const base = baseUrl.replace(/\/$/, '');

  async function request(path, { method = 'GET', body, csrf = false } = {}) {
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (csrf) {
      if (!csrfToken) {
        const sessionRoute = ['/checkout', '/logout', '/account/delete'].includes(path);
        let tokenResult = sessionRoute ? await request('/me') : null;
        if (sessionRoute && !tokenResult.ok && !(path === '/logout' && tokenResult.code === 'signed_out')) return tokenResult;
        if (!tokenResult?.ok) tokenResult = await request('/auth/csrf');
        if (!tokenResult.ok) return tokenResult;
        csrfToken = tokenResult.data.csrf_token;
      }
      headers['X-CSRF-Token'] = csrfToken;
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
    let data = null;
    try { data = await response.json(); } catch { /* stable fallback below */ }
    if (!response.ok) {
      const error = mapHttpError(response.status, data);
      if (error.code === 'signed_out') { csrfToken = null; sessionCsrf = false; }
      return error;
    }
    if (path === '/me') { csrfToken = data?.csrf_token || null; sessionCsrf = !!csrfToken; }
    if (path === '/auth/csrf') { csrfToken = data?.csrf_token || null; sessionCsrf = false; }
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
    async providers() { return ['google', 'apple', 'discord', 'metamask', 'email'].map((id) => ({ id, available: true })); },
    async catalog() {
      const data = await result('/products');
      if (!data || !Array.isArray(data.products)) return data;
      return { ...data, products: data.products.map((product) => ({ ...product, checkout_enabled: data.checkout_enabled, policy: data.checkout_policy })) };
    },
    async entitlements() { const r = await result('/entitlements'); return r?.entitlements ?? r; },
    async purchase() { return null; },
    async startSignIn(provider) {
      const token = await request('/auth/csrf');
      if (!token.ok) return token;
      csrfToken = token.data?.csrf_token || null; sessionCsrf = false;
      const r = await request(`/auth/${encodeURIComponent(provider)}/start`, { method: 'POST', csrf: true, body: { intent: 'login', return_to: '/' } });
      if (!r.ok) return r;
      return { ok: true, authorize_url: r.data?.authorize_url };
    },
    async emailStart(email) {
      const token = await request('/auth/csrf');
      if (!token.ok) return token;
      csrfToken = token.data?.csrf_token || null; sessionCsrf = false;
      return result('/auth/email/start', { method: 'POST', csrf: true, body: { email, intent: 'login' } });
    },
    async checkout({ product_id, policy_version, acknowledgements }) { return result('/checkout', { method: 'POST', csrf: true, body: { product_id, policy_version, acknowledgements } }); },
    async logout() { return result('/logout', { method: 'POST', csrf: true }); },
    async exportData() { return result('/account/export'); },
    async deleteAccount() { return result('/account/delete', { method: 'POST', csrf: true }); },
  };
}
