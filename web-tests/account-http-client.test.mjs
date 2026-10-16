import test from 'node:test';
import assert from 'node:assert/strict';
import { createHttpClient, mapHttpError } from '../public/js/account/http-client.js';

function mockFetch(routes) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    const route = routes.shift();
    assert.ok(route, `unexpected request ${url}`);
    return { ok: route.status >= 200 && route.status < 300, status: route.status, async json() { return route.body; } };
  };
  return { calls, fetchImpl };
}
const ok = (body = {}) => ({ status: 200, body });
const token = { csrf_token: 'csrf-session' };

test('GET interfaces use same-origin credentials and exact API paths', async () => {
  const mock = mockFetch([ok({ user: { id: 'u' }, csrf_token: 'csrf-session' }), ok({ products: [] }), ok({ entitlements: [] })]);
  const c = createHttpClient({ baseUrl: '/follow/api', fetchImpl: mock.fetchImpl });
  await c.me(); await c.catalog(); await c.entitlements();
  assert.deepEqual(mock.calls.map((x) => x.url), ['/follow/api/me', '/follow/api/products', '/follow/api/entitlements']);
  for (const { init } of mock.calls) assert.equal(init.credentials, 'same-origin');
  assert.deepEqual(mock.calls.map((x) => x.init.method), ['GET', 'GET', 'GET']);
});

test('mutations carry CSRF, method, content type and exact bodies', async () => {
  const mock = mockFetch([ok(token), ok({ authorize_url: 'https://id.example/auth' }), ok({ csrf_token: 'csrf2' }), ok({ accepted: true }), ok({ csrf_token: 'csrf-session' }), ok({ state: 'pending' }), ok({ ok: true }), ok({ ok: true })]);
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
  assert.deepEqual(JSON.parse(mutations[0].init.body), { intent: 'login', return_to: '/' });
  assert.deepEqual(JSON.parse(mutations[1].init.body), { email: 'person@example.test', intent: 'login' });
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
  assert.equal(mapHttpError(404, {}).code, 'not_found');
  assert.equal(mapHttpError(409, {}).code, 'conflict');
  assert.equal(mapHttpError(503, {}).code, 'unavailable');
  const c = createHttpClient({ baseUrl: '/api', fetchImpl: async () => { throw new Error('offline'); } });
  assert.match((await c.catalog()).message, /try again/i);
});

test('client never accesses browser storage', async () => {
  const source = await (await import('node:fs/promises')).readFile(new URL('../public/js/account/http-client.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /localStorage|sessionStorage|innerHTML/);
});
