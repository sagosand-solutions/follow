import test from 'node:test';
import assert from 'node:assert/strict';
import { createHttpClient, mapHttpError, appReturnTo } from '../public/js/account/http-client.js';

function mockFetch(routes) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    const route = routes.shift();
    assert.ok(route, `unexpected request ${url}`);
    return { ok: route.status >= 200 && route.status < 300, status: route.status, async json() { if (route.nonJson) throw new SyntaxError('not JSON'); return route.body; } };
  };
  return { calls, fetchImpl };
}
const ok = (body = {}) => ({ status: 200, body });
const token = { csrf_token: 'csrf-session' };

test('GET interfaces use same-origin credentials and exact API paths', async () => {
  const mock = mockFetch([ok({ user: { id: 'u' }, providers: [], csrf_token: 'csrf-session' }), ok({ products: [] }), ok({ entitlements: [] }), ok({ providers: [{ id: 'google', kind: 'oauth' }] }), ok({ purchases: [{ id: 'p1', product_id: 'python', state: 'pending', entitled: false }] })]);
  const c = createHttpClient({ baseUrl: '/follow/api', fetchImpl: mock.fetchImpl });
  await c.me(); await c.catalog(); await c.entitlements();
  assert.deepEqual(await c.providers(), [{ id: 'google', kind: 'oauth', available: true }]);
  assert.deepEqual(await c.purchase(), { id: 'p1', product_id: 'python', state: 'pending', entitled: false });
  assert.deepEqual(mock.calls.map((x) => x.url), ['/follow/api/me', '/follow/api/products', '/follow/api/entitlements', '/follow/api/auth/providers', '/follow/api/purchases']);
  for (const { init } of mock.calls) assert.equal(init.credentials, 'same-origin');
  assert.deepEqual(mock.calls.map((x) => x.init.method), ['GET', 'GET', 'GET', 'GET', 'GET']);
});

test('mutations carry CSRF, method, content type and exact bodies', async () => {
  const mock = mockFetch([ok(token), ok({ authorize_url: 'https://id.example/auth' }), ok({ csrf_token: 'csrf2' }), ok({ accepted: true }), ok({ csrf_token: 'csrf-session' }), ok({ state: 'pending' }), ok({ ok: true }), ok({ user: { id: 'u' }, csrf_token: 'csrf-session-2' }), ok({ ok: true })]);
  const c = createHttpClient({ baseUrl: '/api', fetchImpl: mock.fetchImpl });
  await c.startSignIn('google');
  await c.emailStart('person@example.test');
  // Seed the session token before the remaining mutating calls.
  await c.me();
  await c.checkout({ product_id: 'p', policy_version: 'v1', acknowledgements: ['ack'] });
  await c.logout(); await c.deleteAccount();
  const mutations = mock.calls.filter(({ init }) => init.method === 'POST');
  assert.deepEqual(mutations.map(({ url }) => url), ['/api/auth/google/start', '/api/auth/email/start', '/api/checkout', '/api/logout', '/api/account/delete']);
  for (const { init } of mutations) {
    assert.equal(init.credentials, 'same-origin');
    assert.ok(init.headers['X-CSRF-Token']);
  }
  assert.deepEqual(JSON.parse(mutations[0].init.body), { intent: 'login', return_to: '/#/account' });
  assert.deepEqual(JSON.parse(mutations[1].init.body), { email: 'person@example.test', intent: 'login', return_to: '/#/account' });
  assert.deepEqual(JSON.parse(mutations[2].init.body), { product_id: 'p', policy_version: 'v1', acknowledgements: ['ack'] });
  assert.equal(mutations[3].init.body, undefined);
  assert.equal(mutations[4].init.body, undefined);
});

test('logout obtains a preauth CSRF token if no session token is loaded', async () => {
  const mock = mockFetch([{ status: 401, body: { error: { code: 'no-session' } } }, ok({ csrf_token: 'preauth' }), ok({ ok: true })]);
  await createHttpClient({ baseUrl: '/api', fetchImpl: mock.fetchImpl }).logout();
  assert.deepEqual(mock.calls.map((x) => x.url), ['/api/me', '/api/auth/csrf', '/api/logout']);
  assert.equal(mock.calls[2].init.headers['X-CSRF-Token'], 'preauth');
});

test('HTTP statuses map to stable user-facing states and network errors offer retry', async () => {
  assert.equal(mapHttpError(401, {}).code, 'signed_out');
  assert.equal(mapHttpError(400, {}).code, 'invalid_request');
  assert.equal(mapHttpError(403, {}).code, 'reauth_required');
  assert.equal(mapHttpError(404, {}).code, 'not_found');
  assert.equal(mapHttpError(409, {}).code, 'conflict');
  assert.equal(mapHttpError(429, {}).code, 'rate_limited');
  assert.equal(mapHttpError(503, {}).code, 'unavailable');
  const c = createHttpClient({ baseUrl: '/api', fetchImpl: async () => { throw new Error('offline'); } });
  assert.match((await c.catalog()).message, /try again/i);
});

test('recent-auth 403s for export, logout and delete remain failures', async () => {
  const forbidden = { status: 403, body: { error: { code: 'forbidden' } } };
  const mock = mockFetch([ok({ user: { id: 'u' }, csrf_token: 'session-token' }), forbidden, forbidden, forbidden]);
  const c = createHttpClient({ baseUrl: '/api', fetchImpl: mock.fetchImpl });
  await c.me();
  assert.equal((await c.logout()).ok, false);
  assert.equal((await c.deleteAccount()).ok, false);
  assert.equal((await c.exportData()).ok, false);
});

test('preauth and session CSRF tokens stay separate across sign-in and account mutations', async () => {
  const mock = mockFetch([
    ok({ csrf_token: 'preauth-token' }),
    ok({ authorize_url: 'https://accounts.example/authorize' }),
    ok({ user: { id: 'u' }, providers: [], csrf_token: 'session-token' }),
    ok({ ok: true }),
  ]);
  const c = createHttpClient({ baseUrl: '/api', fetchImpl: mock.fetchImpl });
  await c.startSignIn('google');
  await c.logout();
  assert.equal(mock.calls[1].init.headers['X-CSRF-Token'], 'preauth-token');
  assert.equal(mock.calls[3].init.headers['X-CSRF-Token'], 'session-token');
});

test('authorize and checkout redirects require HTTPS and checkout origin allowlist', async () => {
  const badAuth = mockFetch([ok(token), ok({ authorize_url: 'http://accounts.example/authorize' })]);
  const authResult = await createHttpClient({ baseUrl: '/api', fetchImpl: badAuth.fetchImpl }).startSignIn('google');
  assert.equal(authResult.code, 'unsafe_redirect');

  const allowed = mockFetch([ok({ user: { id: 'u' }, csrf_token: 'session-token' }), ok({ state: 'pending', purchase_id: 'p1', redirect_url: 'https://pay.example/checkout/1' })]);
  const c = createHttpClient({ baseUrl: '/api', checkoutOrigins: ['https://pay.example'], fetchImpl: allowed.fetchImpl });
  await c.me();
  const result = await c.checkout({ product_id: 'p', policy_version: 'v1', acknowledgements: ['ack'] });
  assert.equal(result.ok, true);
  assert.equal(result.redirect_url, 'https://pay.example/checkout/1');

  for (const redirect_url of ['http://pay.example/checkout', 'https://evil.example/checkout']) {
    const mock = mockFetch([ok({ user: { id: 'u' }, csrf_token: 'session-token' }), ok({ state: 'pending', redirect_url })]);
    const client = createHttpClient({ baseUrl: '/api', checkoutOrigins: ['https://pay.example'], fetchImpl: mock.fetchImpl });
    await client.me();
    assert.equal((await client.checkout({ product_id: 'p', policy_version: 'v1', acknowledgements: ['ack'] })).code, 'unsafe_redirect');
  }
});

test('HTTP 200 with invalid JSON is an error, never an empty success', async () => {
  const mock = mockFetch([{ status: 200, nonJson: true }]);
  const result = await createHttpClient({ baseUrl: '/api', fetchImpl: mock.fetchImpl }).me();
  assert.equal(result.code, 'invalid_response');
  assert.match(result.message, /unexpected response/i);
});

test('views do not report failed account mutations as success', async () => {
  const source = await (await import('node:fs/promises')).readFile(new URL('../public/js/views/account.js', import.meta.url), 'utf8');
  assert.match(source, /const result = await c\.deleteAccount\(\);[\s\S]*if \(result\?\.ok !== true\)[\s\S]*dialog\.close\(\);[\s\S]*navigate\('\/'\);/);
  assert.match(source, /const result = await c\.logout\(\);[\s\S]*if \(result\?\.ok !== true\)[\s\S]*navigate\('\/'\);/);
  assert.match(source, /if \(data\?\.ok === false\)[\s\S]*account\.exportFailed/);
  assert.match(source, /const authorizeUrl = safeHttpsUrl\(r\.authorize_url\);[\s\S]*if \(authorizeUrl\) globalThis\.location\.assign\(authorizeUrl\)/);
  assert.match(source, /if \(r\.ok && r\.redirect_url\) globalThis\.location\.assign\(r\.redirect_url\)/);
});

test('client never accesses browser storage', async () => {
  const source = await (await import('node:fs/promises')).readFile(new URL('../public/js/account/http-client.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /localStorage|sessionStorage|innerHTML/);
});

test('return_to is the app\'s own location: base path plus hash route, same origin, never an odd path', async () => {
  assert.equal(appReturnTo('checkout', { pathname: '/follow/' }), '/follow/#/checkout');
  assert.equal(appReturnTo('account', { pathname: '/follow/index.html' }), '/follow/index.html#/account');
  assert.equal(appReturnTo('account', { pathname: '/' }), '/#/account');
  for (const odd of ['//evil.example/x', 'javascript:alert(1)', '/a b', '/a\\b', '', undefined, null, 5]) assert.equal(appReturnTo('account', { pathname: odd }), '/#/account', String(odd));
  assert.equal(appReturnTo('account', undefined), '/#/account');
  // And the sign-in request carries it, following ?next= from the current hash.
  const saved = globalThis.location;
  globalThis.location = { pathname: '/follow/', hash: '#/signin?next=checkout' };
  try {
    const mock = mockFetch([ok(token), ok({ authorize_url: 'https://id.example/a' })]);
    const c = createHttpClient({ baseUrl: '/follow/api', fetchImpl: mock.fetchImpl });
    await c.startSignIn('google');
    assert.equal(JSON.parse(mock.calls[1].init.body).return_to, '/follow/#/checkout');
    globalThis.location = { pathname: '/follow/', hash: '#/signin?next=evil' };
    const mock2 = mockFetch([ok(token), ok({ authorize_url: 'https://id.example/a' })]);
    await createHttpClient({ baseUrl: '/follow/api', fetchImpl: mock2.fetchImpl }).startSignIn('google');
    assert.equal(JSON.parse(mock2.calls[1].init.body).return_to, '/follow/#/account');
  } finally {
    globalThis.location = saved;
  }
});

test('signed out is a state, not an error: no entitlements and no purchase; other failures stay failures', async () => {
  const mock = mockFetch([{ status: 401, body: { error: { code: 'no-session' } } }, { status: 401, body: { error: { code: 'no-session' } } }, { status: 503, body: { error: { code: 'unavailable' } } }, { status: 503, body: { error: { code: 'unavailable' } } }]);
  const c = createHttpClient({ baseUrl: '/follow/api', fetchImpl: mock.fetchImpl });
  assert.deepEqual(await c.entitlements(), []);
  assert.equal(await c.purchase(), null);
  assert.equal((await c.entitlements()).code, 'unavailable');
  assert.equal((await c.purchase()).code, 'unavailable');
});

test('premiumQuestion reads /content/question/<id> and reports honest states; nothing is stored', async () => {
  const q = { id: 'prod-x', prompt: 'p' };
  const mock = mockFetch([ok(q), { status: 401, body: { error: { code: 'no-session' } } }, { status: 404, body: { error: { code: 'not-found' } } }, { status: 200, nonJson: true }]);
  const c = createHttpClient({ baseUrl: '/follow/api', fetchImpl: mock.fetchImpl });
  assert.deepEqual(await c.premiumQuestion('prod-x'), { ok: true, question: q });
  assert.equal((await c.premiumQuestion('prod-x')).code, 'signed_out');
  assert.equal((await c.premiumQuestion('a b/../c')).code, 'not_found');
  assert.equal((await c.premiumQuestion('prod-x')).code, 'invalid_response');
  assert.equal(mock.calls[0].url, '/follow/api/content/question/prod-x');
  assert.equal(mock.calls[2].url, '/follow/api/content/question/a%20b%2F..%2Fc', 'the id is encoded, never a path');
  assert.equal(mock.calls[0].init.method, 'GET');
  const failing = createHttpClient({ baseUrl: '/follow/api', fetchImpl: async () => { throw new TypeError('offline'); } });
  assert.equal((await failing.premiumQuestion('prod-x')).code, 'network_error');
  assert.deepEqual(await createFakeClientPremium(), { ok: false, code: 'not_found' });
});

async function createFakeClientPremium() {
  const { createFakeClient } = await import('../public/js/account/client.js');
  return createFakeClient().premiumQuestion('prod-x');
}
