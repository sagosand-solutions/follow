import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// The server-scored profile and leaderboard against the phase-1 contract: validated answers, one snapshot per drawing,
// exact places and scores exactly as the server sent them, and nothing from a reader who is no longer signed in. The views
// run with the REAL http client over a fake fetch, in a small DOM. Fixture catalog c7: python 60 / production 40 / sql 20.

const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const dataRoot = new URL('../public/data/', import.meta.url);

class Node {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.className = ''; this.handlers = {}; this.parent = null; this.hidden = false; this.checked = false; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'class') this.className = v; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn); }
  async fire(type) { for (const fn of this.handlers[type] || []) await fn({ type, target: this, preventDefault() {} }); }
  append(...nodes) { for (const n of nodes) { if (n && typeof n === 'object') n.parent = this; this.children.push(n); } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  focus() { focused = this; }
  all(pred, out = []) { if (pred(this)) out.push(this); for (const c of this.children) if (c && c.tag) c.all(pred, out); return out; }
  find(pred) { return this.all(pred)[0] || null; }
  querySelector(sel) { return this.find((n) => n !== this && (sel.startsWith('.') ? n.className.split(' ').includes(sel.slice(1)) : n.tag === sel)); }
  text() { return this.children.map((c) => (c && c.tag ? c.text() : c.text)).join(''); }
  get value() { return this.attrs.value ?? ''; }
  set value(v) { this.attrs.value = v; }
  set textContent(v) { this.replaceChildren({ text: String(v) }); }
  showModal() { this.open = true; }
  close() { if (this.open) { this.open = false; this.fire('close'); } }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; }
}
let focused = null;
globalThis.document = { createElement: (t) => new Node(t), createTextNode: (text) => ({ text }), createElementNS: (_n, t) => new Node(t), body: new Node('body') };
let clipboard = [];
Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async (text) => { clipboard.push(text); } } }, configurable: true });
globalThis.location = { hash: '#/leaderboard', pathname: '/' };
let replaced = [];
globalThis.history = { replaceState(_s, _t, url) { replaced.push(url); globalThis.location.hash = url; } };
globalThis.window = { addEventListener() {}, scrollTo() {} };

// ---- fixtures (team-free copies of the plan's derived numbers) ----
const W_EU = { kind: 'week', start: 1792368000, end: 1792972800 };
const head = (over = {}) => ({ family: 'official', rules_version: 'r2', catalog_version: 'c7', scope: 'total', window: W_EU, board_revision: '4182', ...over });
const SIG = '0123456789abcdef01234567';
const courses = [
  { course_id: 'python', points: 408.333, score_display: '408.33', answered: 30, pool: 60 },
  { course_id: 'production', points: 225.0, score_display: '225.00', answered: 10, pool: 40 },
  { course_id: 'sql', points: 0.0, score_display: '0.00', answered: 0, pool: 20 },
];
const total = { points: 211.111, score_display: '211.11', answered: 40, pool: 120 };
const meKim = (over = {}) => ({ ...head(), total, scope_score: { scope: 'total', score_display: '211.11', answered: 40, pool: 120 }, courses, attempts: [], rank: { kind: 'actual', value: 3, participants: 340 }, published: true, moderation: 'ok', own_cursor: `0.4182.${SIG}`, ...over });
const meNotPublished = () => meKim({ rank: { kind: 'hypothetical', value: 3, participants: 339 }, published: false, own_cursor: null });
const row = (rank, alias, display, extra = {}) => ({ rank, alias, points: Number(display), score_display: display, answered: 45, pool: 120, ...extra });
const others = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => row(from + i, `reader${from + i}`, '100.00'));
const pageTop = (over = {}, kimIsMe = true) => ({ ...head(), participants: 340, entries: [row(1, 'ada', '250.00'), row(1, 'lin', '250.00'), row(3, 'kim', '211.11', { answered: 40, ...(kimIsMe ? { is_me: true } : {}) }), ...others(4, 50)], next_cursor: `50.4182.${SIG}`, ...over });

const { setMessages } = await import('../public/js/i18n.js');
setMessages(strings);
const { CONFIG } = await import('../public/js/config.js');
const board = await import('../public/js/scoring/board.js');
const { mapHttpError, createHttpClient } = await import('../public/js/account/http-client.js');
const { rankText } = await import('../public/js/components/score-ui.js');

// ---- fake server ----
let routes = {};
let calls = [];
const reply = (status, body) => ({ ok: status < 400, status, async json() { return body; } });
globalThis.fetch = async (url, init = {}) => {
  const u = String(url);
  calls.push({ url: u, method: init.method || 'GET', body: init.body });
  if (u.includes('data/')) {
    const path = u.replace(/^.*data\//, '').replace(/\?.*$/, '');
    try { return reply(200, JSON.parse(readFileSync(new URL(path, dataRoot), 'utf8'))); } catch { return reply(404, {}); }
  }
  const path = u.replace(/^\/api/, '');
  for (const [prefix, handler] of Object.entries(routes)) if (path.startsWith(prefix)) return handler(path, init);
  return reply(404, { error: { code: 'not-found' } });
};
const signedIn = (id = 'u-kim') => () => reply(200, { user: { id, status: 'active' }, providers: ['google'], session: {}, csrf_token: 'csrf' });
const signedOut = () => reply(401, { error: { code: 'no-session' } });

CONFIG.accountsApi = '/api';
// A mounted router, so that "Start from the top" and publishing can re-render (to a blank page here).
const router = await import('../public/js/router.js');
router.setNotFound(async () => ({ title: '', node: new Node('div') }));
await router.startRouter(new Node('main'), null);
const { leaderboardView } = await import('../public/js/views/leaderboard.js');
const { profileView } = await import('../public/js/views/profile.js');

const text = (node) => node.text();
const rowsOf = (node) => node.all((n) => n.tag === 'tr' && n.parent?.tag === 'tbody');
const buttons = (node, label) => node.all((n) => n.tag === 'button' && n.text() === label);

function serve({ account = signedIn(), me = () => reply(200, meKim()), boards = () => reply(200, pageTop()) } = {}) {
  calls = [];
  routes = { '/me': account, '/scoring/me': me, '/leaderboards': boards };
}

// ---- pure contract ----
test('parseMe accepts the fixtures and refuses shapes that would claim a place without a published, visible account', () => {
  assert.equal(board.parseMe(meKim()).rank.value, 3);
  assert.equal(board.parseMe(meNotPublished()).rank.kind, 'hypothetical');
  assert.equal(board.parseMe(meKim({ scope_score: null, rank: { kind: 'none', value: null, participants: 0 }, own_cursor: null })).rank.kind, 'none');
  for (const bad of [
    meKim({ published: false }),                                                    // actual but not published
    meKim({ moderation: 'hidden' }),                                                // actual but hidden
    meNotPublished().own_cursor === null && { ...meNotPublished(), own_cursor: `0.4182.${SIG}` }, // a cursor without a place
    meKim({ rank: { kind: 'hypothetical', value: 3, participants: 339 }, scope_score: null, own_cursor: null }), // a place for no score
    meKim({ total: { ...total, score_display: '211.1' } }),
    meKim({ total: { ...total, score_display: 211.11 } }),
    meKim({ board_revision: 4182 }),
    meKim({ window: { kind: 'week', start: 1792972800, end: 1792368000 } }),
    meKim({ rank: { kind: 'around', value: 3, participants: 340 } }),
    meKim({ moderation: 'review' }),
    meKim({ scope_score: { scope: 'course:sql', score_display: '0.00', answered: 0, pool: 20 } }),
  ]) assert.equal(board.parseMe(bad), null, JSON.stringify(bad).slice(0, 120));
});

test('parsePage keeps the server order and shared places, and refuses rising places or two rows marked as yours', () => {
  const page = board.parsePage(pageTop());
  assert.deepEqual(page.entries.slice(0, 3).map((e) => [e.rank, e.alias, e.me]), [[1, 'ada', false], [1, 'lin', false], [3, 'kim', true]]);
  assert.equal(board.parsePage(pageTop({ entries: [row(3, 'kim', '211.11'), row(1, 'ada', '250.00')] })), null);
  assert.equal(board.parsePage(pageTop({ entries: [row(1, 'a', '1.00', { is_me: true }), row(2, 'b', '0.50', { is_me: true })] })), null);
  assert.equal(board.parsePage(pageTop({ next_cursor: 'page2' })), null);
  assert.equal(board.parsePage(pageTop({ entries: [row(1, '', '1.00')] })), null);
});

test('one snapshot: family, versions, scope, window and board revision must all match', () => {
  const me = board.parseMe(meKim());
  assert.equal(board.sameSnapshot(me, board.parsePage(pageTop())), true);
  for (const over of [{ board_revision: '4183' }, { catalog_version: 'c8' }, { rules_version: 'r3' }, { scope: 'course:python' }, { family: 'free' }, { window: { kind: 'week', start: 1791763200, end: 1792368000 } }, { window: { kind: 'all', start: null, end: null } }]) {
    assert.equal(board.sameSnapshot(me, board.parsePage(pageTop(over))), false, JSON.stringify(over));
  }
});

test('score_display is shown digit for digit: a real minus, no -0.00, the locale decimal mark, a dash for anything else', () => {
  assert.equal(board.localScore('0.13'), '0.13');
  assert.equal(board.localScore('-0.13'), '−0.13');
  assert.equal(board.localScore('0.38'), '0.38');
  assert.equal(board.localScore('-0.00'), '0.00');
  assert.equal(board.localScore('1000.00'), '1000.00');
  assert.equal(board.localScore('-500.00'), '−500.00');
  assert.equal(board.localScore('211.11', 'sv'), '211,11');
  assert.equal(board.localScore('211.105'), '–');
  assert.equal(board.localScore(211.11), '–');
  // two different exact scores may print the same; the server's places still differ and are shown as given
  const page = board.parsePage(pageTop({ entries: [row(1, 'a', '0.13'), row(2, 'b', '0.13')] }));
  assert.deepEqual(page.entries.map((e) => [e.rank, board.localScore(e.score_display)]), [[1, '0.13'], [2, '0.13']]);
});

test('the week: dates in UTC and the reset in the reader’s zone, named, across both DST changes', () => {
  const plain = { kind: 'week', start: 1791763200, end: 1792368000 };
  const us = { kind: 'week', start: 1792972800, end: 1793577600 };
  const sthlm = (w) => board.weekText(w, { timeZone: 'Europe/Stockholm' });
  const ny = (w) => board.weekText(w, { timeZone: 'America/New_York' });
  assert.deepEqual([sthlm(plain).from, sthlm(plain).to], ['Mon, Oct 12', 'Mon, Oct 19']);
  assert.deepEqual([sthlm(plain).resetTime, sthlm(plain).zone], ['02:00', 'GMT+2']);
  assert.deepEqual([sthlm(W_EU).resetTime, sthlm(W_EU).zone, sthlm(W_EU).resetDay], ['01:00', 'GMT+1', 'Mon, Oct 26']);
  assert.deepEqual([ny(W_EU).resetTime, ny(W_EU).zone, ny(W_EU).resetDay], ['20:00', 'EDT', 'Sun, Oct 25']);
  assert.deepEqual([ny(us).resetTime, ny(us).zone, ny(us).resetDay], ['19:00', 'EST', 'Sun, Nov 1']);
  assert.equal(board.weekText({ kind: 'all', start: null, end: null }), null);
  assert.doesNotMatch(strings['rank.week'], /midnight/i);
  assert.match(strings['rank.week'], /\(UTC\)/);
});

test('placement text: exact place, an exact comparison that says you are not on the list, hidden, and no number without one', () => {
  const p = (d) => rankText(board.parseMe(d));
  assert.equal(p(meKim()), 'Place 3 of 340');
  assert.equal(p(meNotPublished()), 'For comparison: 339 readers are on this list, and this score would place 3. You are not on the list.');
  assert.equal(p(meKim({ published: false, moderation: 'hidden', rank: { kind: 'hypothetical', value: 3, participants: 339 }, own_cursor: null })), 'Your entry is hidden from public lists.');
  const none = p(meKim({ rank: { kind: 'none', value: null, participants: 0 }, own_cursor: null }));
  const noScore = p(meKim({ scope_score: null, rank: { kind: 'none', value: null, participants: 0 }, own_cursor: null }));
  for (const s of [none, noScore]) assert.doesNotMatch(s, /\d/);
  assert.notEqual(none, noScore);
});

test('mapHttpError: 409 stale_cursor keeps its code; a foreign cursor (400 invalid) is not a stale list', () => {
  assert.equal(mapHttpError(409, { error: { code: 'stale_cursor' } }).code, 'stale_cursor');
  assert.equal(mapHttpError(400, { error: { code: 'invalid' } }).code, 'invalid_request');
  assert.equal(mapHttpError(409, { error: { code: 'something-new' } }).code, 'conflict');
});

test('the auth generation rises on another account, a sign-out, an ended session and account deletion, never on the first look', async () => {
  let who = 'u-a';
  routes = { '/me': () => (who ? signedIn(who)() : signedOut()), '/logout': () => reply(200, { ok: true }), '/account/delete': () => reply(200, { ok: true }) };
  const c = createHttpClient({ baseUrl: '/api' });
  const seen = [];
  c.onAuthChange((g) => seen.push(g));
  await c.me();
  assert.equal(c.authGeneration(), 0);
  await c.me();
  assert.equal(c.authGeneration(), 0, 'same reader');
  who = 'u-b';
  await c.me();
  assert.equal(c.authGeneration(), 1, 'another account');
  who = null;
  await c.me();
  assert.equal(c.authGeneration(), 2, 'session ended');
  who = 'u-b';
  await c.me();
  assert.equal(c.authGeneration(), 3, 'signed in again');
  await c.logout();
  assert.equal(c.authGeneration(), 4);
  await c.me();
  await c.deleteAccount();
  assert.ok(c.authGeneration() >= 5);
  assert.deepEqual(seen.slice(0, 4), [1, 2, 3, 4]);
});

// ---- the views ----
test('published and in the top 50: your row is marked in the list, the place matches /me, and nothing offers publishing', async () => {
  serve();
  const { node } = await leaderboardView();
  const rows = rowsOf(node);
  assert.equal(rows.length, 50);
  const mine = rows.filter((r) => r.getAttribute('aria-current') === 'true');
  assert.equal(mine.length, 1);
  assert.match(text(mine[0]), /^3kim You211\.1140 of 120$/);
  assert.deepEqual(rows.slice(0, 3).map((r) => text(r.children[0])), ['1', '1', '3']);
  assert.match(text(node), /Place 3 of 340/);
  assert.equal(buttons(node, 'Publish my score').length, 0);
  assert.match(text(node.find((n) => n.tag === 'caption')), /^Official list · This week · All courses · catalog c7 · rules r2$/);
  assert.match(text(node), /This week: Mon, Oct 19 – Mon, Oct 26 \(UTC\)\. Resets .+ at \d\d:\d\d your time \(.+\)\./);
  assert.equal(calls.filter((c) => c.url.includes('/leaderboards')).length, 1, 'no neighbour fetch when your row is on the page');
});

test('a different board revision between /me and the first page draws no rows and offers “Start from the top”', async () => {
  serve({ boards: () => reply(200, pageTop({ board_revision: '4183' })) });
  const view = await leaderboardView();
  assert.equal(rowsOf(view.node).length, 0);
  assert.match(text(view.node), /The list changed while you were reading it\./);
  view.after();
  assert.equal(text(focused), 'Start from the top');
});

test('after the first 50: a gap, then your place with two above and two below, fetched with your own cursor', async () => {
  const mo = meKim({ rank: { kind: 'actual', value: 112, participants: 340 }, own_cursor: `109.4182.${SIG}` });
  const around = { ...head(), participants: 340, entries: [row(110, 'r110', '90.00'), row(111, 'r111', '89.00'), row(112, 'mo', '88.00', { is_me: true }), row(113, 'r113', '87.00'), row(114, 'r114', '86.00')], next_cursor: `114.4182.${SIG}` };
  serve({ me: () => reply(200, mo), boards: (path) => reply(200, path.includes('cursor=') ? around : pageTop({}, false)) });
  const { node } = await leaderboardView();
  const fetches = calls.filter((c) => c.url.includes('/leaderboards'));
  assert.equal(fetches.length, 2);
  assert.match(fetches[1].url, /cursor=109\.4182\.0123456789abcdef01234567/);
  assert.match(fetches[1].url, /[?&]limit=5(&|$)/);
  const tables = node.all((n) => n.tag === 'table');
  assert.equal(tables.length, 2);
  assert.equal(text(tables[1].children[0]), 'Your place');
  assert.deepEqual(rowsOf(tables[1]).map((r) => text(r.children[0])), ['110', '111', '112', '113', '114']);
  assert.ok(node.find((n) => n.className === 'board__gap'));
  assert.match(text(node), /Place 112 of 340/);
});

test('a neighbour page from another snapshot is never drawn next to the list', async () => {
  const mo = meKim({ rank: { kind: 'actual', value: 112, participants: 340 }, own_cursor: `109.4182.${SIG}` });
  serve({ me: () => reply(200, mo), boards: (path) => (path.includes('cursor=') ? reply(409, { error: { code: 'stale_cursor' } }) : reply(200, pageTop({}, false))) });
  const { node } = await leaderboardView();
  assert.equal(rowsOf(node).length, 0);
  assert.match(text(node), /The list changed/);
});

test('signed in, not published: the exact comparison place and “Publish my score”, no row of yours; publishing sends only the family', async () => {
  serve({ me: () => reply(200, meNotPublished()), boards: () => reply(200, pageTop({}, false)) });
  routes['/scoring/preferences'] = () => reply(200, { family: 'official', opted_in: true, alias: 'Reader-abc123' });
  const { node } = await leaderboardView();
  assert.match(text(node), /For comparison: 339 readers are on this list, and this score would place 3\. You are not on the list\./);
  assert.equal(rowsOf(node).filter((r) => r.getAttribute('aria-current')).length, 0);
  const [publish] = buttons(node, 'Publish my score');
  assert.ok(publish);
  await publish.fire('click');
  const sent = calls.find((c) => c.url.endsWith('/scoring/preferences'));
  assert.deepEqual(JSON.parse(sent.body), { family: 'official', opt_in: true });
  // the next drawing returns focus to the placement line, not the page title
  serve();
  const next = await leaderboardView();
  next.after();
  assert.equal(focused.className, 'score-place__rank');
  assert.equal(text(focused), 'Place 3 of 340');
  const later = await leaderboardView();
  focused = null;
  later.after();
  assert.equal(focused, null, 'only once');
});

test('signed out: the comparison place, the practice note and “Sign in”, never a publish button or a row of yours', async () => {
  serve({ account: signedOut, me: () => reply(200, meNotPublished()), boards: () => reply(200, pageTop({}, false)) });
  const { node } = await leaderboardView();
  assert.match(text(node), /For comparison: 339 readers are on this list, and this score would place 3\./);
  assert.match(text(node), /Your practice stays with you\. Ranked points start with questions you haven't seen yet\./);
  assert.equal(buttons(node, 'Publish my score').length, 0);
  assert.ok(node.find((n) => n.tag === 'a' && n.getAttribute('href') === '#/signin'));
});

test('hidden: the hidden line, no publish button and no row of yours', async () => {
  serve({ me: () => reply(200, meKim({ published: false, moderation: 'hidden', rank: { kind: 'hypothetical', value: 3, participants: 339 }, own_cursor: null })), boards: () => reply(200, pageTop({}, false)) });
  const { node } = await leaderboardView();
  assert.match(text(node), /Your entry is hidden from public lists\./);
  assert.equal(buttons(node, 'Publish my score').length, 0);
});

test('no answers in this scope: no number at all, not a comparison place for 0', async () => {
  const none = meKim({ scope: 'course:sql', scope_score: null, rank: { kind: 'none', value: null, participants: 0 }, own_cursor: null });
  serve({ me: () => reply(200, none), boards: () => reply(200, pageTop({ scope: 'course:sql' }, false)) });
  globalThis.location.hash = '#/leaderboard?list=challenge&period=week&course=sql';
  const { node } = await leaderboardView();
  globalThis.location.hash = '#/leaderboard';
  const place = node.find((n) => n.className === 'score-place');
  assert.equal(text(place).includes('No ranked answers in this list yet.'), true);
  assert.doesNotMatch(text(place).replace(/Sign in/, ''), /place \d/i);
});

test('load more: the next 50 rows are appended in order with a status line; a stale cursor replaces the list, never mixes it', async () => {
  const second = { ...head(), participants: 340, entries: others(51, 100), next_cursor: null };
  serve({ boards: (path) => reply(200, path.includes('cursor=') ? second : pageTop()) });
  const { node } = await leaderboardView();
  const [more] = buttons(node, 'Load more');
  assert.equal(more.getAttribute('aria-disabled'), null);
  await more.fire('click');
  assert.equal(rowsOf(node).length, 100);
  assert.match(text(node), /Rows 51–100 loaded\./);
  assert.equal(more.getAttribute('aria-disabled'), 'true');
  assert.equal(text(focused.children[0]), '51');
  const before = calls.length;
  await more.fire('click');
  assert.equal(calls.length, before, 'an exhausted list sends nothing');

  serve({ boards: (path) => (path.includes('cursor=') ? reply(409, { error: { code: 'stale_cursor' } }) : reply(200, pageTop())) });
  const again = await leaderboardView();
  await buttons(again.node, 'Load more')[0].fire('click');
  assert.equal(rowsOf(again.node).length, 0);
  assert.equal(text(focused), 'Start from the top');

  serve({ boards: (path) => reply(200, path.includes('cursor=') ? { ...second, board_revision: '4183' } : pageTop()) });
  const third = await leaderboardView();
  await buttons(third.node, 'Load more')[0].fire('click');
  assert.equal(rowsOf(third.node).length, 0, 'a changed revision on page 2 is stale as well');
});

test('a late signed-out answer after a sign-in is dropped: no publish button, no foreign place, the view asks again', async () => {
  // First round: the account answer says a different user than the client saw before, so the generation rises mid-flight.
  const { getClient } = await import('../public/js/account/index.js');
  serve({ account: signedIn('u-first') });
  await getClient().me();
  let round = 0;
  serve({
    account: () => { round += 1; return signedIn(round === 1 ? 'u-second' : 'u-second')(); },
    me: () => reply(200, round <= 1 ? meNotPublished() : meKim()),
    boards: () => reply(200, round <= 1 ? pageTop({}, false) : pageTop()),
  });
  const { node } = await leaderboardView();
  assert.ok(round >= 2, 'asked again as the new reader');
  assert.equal(buttons(node, 'Publish my score').length, 0);
  assert.match(text(node), /Place 3 of 340/);
});

test('profile: total, by course and recent answers, with “Practice, not ranked” for answers that do not count', async () => {
  const attempts = [
    { question_id: 'q1', family: 'official', course_id: 'python', outcome: 'correct', score_units: 2, accepted_at: 1792400000, rules_version: 'r2', catalog_version: 'c7', rank_eligible: true },
    { question_id: 'q2', family: 'official', course_id: 'sql', outcome: 'wrong', score_units: -1, accepted_at: 1792300000, rules_version: 'r2', catalog_version: 'c7', rank_eligible: false },
  ];
  const kimAll = meKim({ window: { kind: 'all', start: null, end: null }, attempts });
  const around = { ...head({ window: { kind: 'all', start: null, end: null } }), participants: 340, entries: [row(1, 'ada', '250.00'), row(1, 'lin', '250.00'), row(3, 'kim', '211.11', { is_me: true })], next_cursor: null };
  serve({ me: () => reply(200, kimAll), boards: () => reply(200, around) });
  globalThis.location.hash = '#/profile?list=challenge&period=all';
  const { node } = await profileView();
  globalThis.location.hash = '#/leaderboard';
  const all = text(node);
  assert.match(all, /211\.11/);
  assert.match(all, /Place 3 of 340/);
  for (const s of ['408.33', '225.00', '0.00', '30 of 60', '10 of 40', '0 of 20']) assert.ok(all.includes(s), s);
  const history = node.all((n) => n.tag === 'tr' && n.parent?.tag === 'tbody' && /Ranked|Practice, not ranked/.test(n.text()));
  assert.deepEqual(history.map((r) => text(r.children[3])), ['Ranked', 'Practice, not ranked']);
  assert.ok(!all.includes('q1') && !all.includes('q2'), 'no question ids in the history');
  assert.match(calls.find((c) => c.url.includes('/scoring/me')).url, /family=official&scope=total&window=all/);
});

test('profile without a scoring identity: a calm state with “Sign in”, never “unavailable”', async () => {
  serve({ account: signedOut, me: signedOut });
  globalThis.location.hash = '#/profile?list=free&period=all';
  const { node } = await profileView();
  globalThis.location.hash = '#/leaderboard';
  assert.match(text(node), /No score on this list yet\./);
  assert.doesNotMatch(text(node), /unavailable/);
  assert.ok(node.find((n) => n.tag === 'a' && n.getAttribute('href') === '#/signin'));
});

// ---- sharing ----
const share = await import('../public/js/components/share-dialog.js');
const PRODUCT = CONFIG.productName || strings['site.brand'];
const parts = (me, alias = null) => share.shareParts({ me: board.parseMe(me), listLabel: 'Official', scopeLabel: 'All courses', alias });

test('the share text is a whitelist: product, list, scope, window, score, kind of place, catalog and rules', () => {
  assert.equal(share.shareText(parts(meKim())), `${PRODUCT} · Official list · All courses · week of Mon, Oct 19 (UTC) · 211.11 points · place 3 of 340 · catalog c7 · rules r2`);
  assert.equal(share.shareText(parts(meNotPublished())), `${PRODUCT} · Official list · All courses · week of Mon, Oct 19 (UTC) · 211.11 points · not on the list (for comparison: place 3, with 339 readers on it) · catalog c7 · rules r2`);
  assert.match(share.shareText(parts(meKim({ rank: { kind: 'none', value: null, participants: 0 }, own_cursor: null }))), /211\.11 points · unranked · catalog c7/);
  assert.match(share.shareText(parts(meKim({ window: { kind: 'all', start: null, end: null } }))), / · all time · /);
  const withAttempts = meKim({ attempts: [{ question_id: 'secret-question-7', course_id: 'python', outcome: 'correct', accepted_at: 1792400000, rank_eligible: true, issue_id: 'iss-1' }] });
  const text = share.shareText(parts(withAttempts, 'kim'));
  for (const leak of ['secret-question-7', 'iss-1', '4182', SIG, 'is_me', 'http', '#/', '?']) assert.ok(!text.includes(leak), leak);
});

test('the public name only for a published, visible row, and only when asked for', () => {
  assert.ok(share.shareText(parts(meKim(), 'kim')).startsWith(`${PRODUCT} · kim · Official list`));
  assert.doesNotMatch(share.shareText(parts(meNotPublished(), 'kim')), /kim/);
  const hidden = meKim({ published: false, moderation: 'hidden', rank: { kind: 'hypothetical', value: 3, participants: 339 }, own_cursor: null });
  assert.doesNotMatch(share.shareText(parts(hidden, 'kim')), /kim/);
  assert.equal(share.canShowAlias(board.parseMe(meKim()), ''), false);
});

test('the dialog: preview first, the name box unchecked, copy without any request, and nothing left after a sign-out', async () => {
  serve();
  const { node } = await leaderboardView();
  const [open] = buttons(node, 'Share my score');
  await open.fire('click');
  const dialog = document.body.children.at(-1);
  assert.equal(dialog.tag, 'dialog');
  assert.equal(dialog.open, true);
  const preview = dialog.find((n) => n.className === 'share__preview');
  assert.doesNotMatch(text(preview), /kim/);
  const box = dialog.find((n) => n.tag === 'input' && n.getAttribute('type') === 'checkbox');
  assert.equal(box.checked, false);
  assert.equal(box.getAttribute('checked'), null, 'unchecked from the start');
  box.checked = true;
  await box.fire('change');
  assert.match(text(preview), /· kim ·/);
  const before = calls.length;
  clipboard = [];
  await buttons(dialog, 'Copy text')[0].fire('click');
  assert.deepEqual(clipboard, [text(preview)]);
  assert.equal(calls.length, before, 'copying sends nothing');
  // a sign-out while the dialog is open: it closes, empties the preview and leaves the page
  routes['/logout'] = () => reply(200, { ok: true });
  const { getClient } = await import('../public/js/account/index.js');
  await getClient().logout();
  assert.equal(dialog.open, false);
  assert.equal(text(preview), '');
  assert.ok(!document.body.children.includes(dialog));
});

test('no name box for a reader who is not on the list; no share without a score, with the reason, still focusable', async () => {
  serve({ me: () => reply(200, meNotPublished()), boards: () => reply(200, pageTop({}, false)) });
  const { node } = await leaderboardView();
  await buttons(node, 'Share my score')[0].fire('click');
  const dialog = document.body.children.at(-1);
  assert.equal(dialog.find((n) => n.tag === 'input'), null);
  dialog.close();
  serve({ me: () => reply(200, meKim({ scope_score: null, rank: { kind: 'none', value: null, participants: 0 }, own_cursor: null })), boards: () => reply(200, pageTop({}, false)) });
  const empty = await leaderboardView();
  const [off] = buttons(empty.node, 'Share my score');
  assert.equal(off.getAttribute('aria-disabled'), 'true');
  assert.equal(off.getAttribute('disabled'), null, 'aria-disabled, not disabled: it stays in the tab order');
  assert.match(text(empty.node.find((n) => n.getAttribute('id') === off.getAttribute('aria-describedby'))), /no score to share/);
  const count = document.body.children.length;
  await off.fire('click');
  assert.equal(document.body.children.length, count, 'no dialog');
});

// ---- the course filter comes from the scoring catalog, never from the site's course list ----
const options = (node) => node.all((n) => n.tag === 'option').map((o) => [o.attrs.value, text(o)]);

test('course filter: the courses of your own /scoring/me, titled by a known id or shown as the id', async () => {
  const few = meKim({ courses: [courses[0], { course_id: 'dev-intro', points: 0, score_display: '0.00', answered: 0, pool: 6 }] });
  serve({ me: () => reply(200, few) });
  const { node } = await leaderboardView();
  assert.deepEqual(options(node), [['all', 'All courses'], ['python', 'Python: reading code'], ['dev-intro', 'dev-intro']]);
});

test('a course the scoring catalog does not list (or no list at all) falls back to all courses, without a history entry', async () => {
  replaced = [];
  serve({ me: () => reply(200, meKim()) });
  globalThis.location.hash = '#/leaderboard?list=challenge&period=week&course=not-a-course';
  let view = await leaderboardView();
  assert.deepEqual(replaced, ['#/leaderboard?list=challenge&period=week&course=all']);
  assert.match(text(view.node.find((n) => n.tag === 'caption')), /All courses/);
  // signed out with no scoring identity: there is no authoritative list, so no course filter and no course scope
  replaced = [];
  serve({ account: signedOut, me: signedOut });
  globalThis.location.hash = '#/leaderboard?list=challenge&period=week&course=sql';
  view = await leaderboardView();
  assert.deepEqual(replaced, ['#/leaderboard?list=challenge&period=week&course=all']);
  assert.equal(view.node.all((n) => n.tag === 'select').length, 0);
  assert.ok(calls.filter((c) => c.url.includes('/leaderboards')).at(-1).url.includes('scope=total'));
  globalThis.location.hash = '#/leaderboard';
});

test('one reader: singular words, and no share dialog without a score', async () => {
  const lone = meKim({ rank: { kind: 'hypothetical', value: 2, participants: 1 }, published: false, own_cursor: null });
  serve({ me: () => reply(200, lone), boards: () => reply(200, pageTop({ participants: 1, entries: [row(1, 'ada', '250.00')], next_cursor: null }, false)) });
  const { node } = await leaderboardView();
  assert.match(text(node), /1 published participant(?!s)/);
  assert.match(text(node), /For comparison: 1 reader is on this list, and this score would place 2\./);
  assert.match(share.shareText(share.shareParts({ me: board.parseMe(lone), listLabel: 'Official', scopeLabel: 'All courses' })), /with 1 reader on it\)/);
  const none = board.parseMe(meKim({ scope_score: null, rank: { kind: 'none', value: null, participants: 0 }, own_cursor: null }));
  const before = document.body.children.length;
  assert.equal(share.openShareDialog({ me: none, listLabel: 'Official', scopeLabel: 'All courses', client: { authGeneration: () => 0, onAuthChange: () => () => {} }, gen: 0 }), null);
  assert.equal(document.body.children.length, before);
});

// ---- actions are bound to the reader they were shown to (deferred answers) ----
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const posts = () => calls.filter((c) => c.method === 'POST' && c.url.endsWith('/scoring/preferences')).length;

test('a change of reader empties the old view at once; its Publish, Stop publishing and Share never act for the new reader', async () => {
  const { getClient } = await import('../public/js/account/index.js');
  const client = getClient();
  serve({ account: signedIn('u-kim'), me: () => reply(200, meNotPublished()), boards: () => reply(200, pageTop({}, false)) });
  await client.me();
  const old = await leaderboardView();
  old.after();
  const [publish] = buttons(old.node, 'Publish my score');
  const [share] = buttons(old.node, 'Share my score');
  serve({ account: signedIn('u-kim'), me: () => reply(200, meKim()), boards: () => reply(200, pageTop()) });
  const published = await leaderboardView();
  published.after();
  const [stop] = buttons(published.node, 'Stop publishing my score');
  routes['/scoring/preferences'] = () => reply(200, { family: 'official', opted_in: true, alias: 'x' });
  // count calls to the client too: the view must not even ask (the client's own check is a second, separate layer)
  const original = client.scoringPreference;
  let asked = 0;
  client.scoringPreference = (args) => { asked += 1; return original(args); };
  // another account appears while these views are still on the page
  routes['/me'] = signedIn('u-other');
  await client.me();
  assert.match(text(published.node), /^Loading the lists for whoever is signed in now…$/, 'emptied synchronously, before any reload');
  const dialogs = document.body.children.length;
  for (const button of [publish, stop, share]) await button.fire('click');
  assert.equal(posts(), 0, 'no scoring POST from an old view');
  assert.equal(asked, 0, 'the old view does not even ask the client');
  client.scoringPreference = original;
  assert.equal(document.body.children.length, dialogs, 'no share preview of the old reader');
  // a fresh view for the new reader works
  serve({ account: signedIn('u-other'), me: () => reply(200, meNotPublished()), boards: () => reply(200, pageTop({}, false)) });
  routes['/scoring/preferences'] = () => reply(200, { family: 'official', opted_in: true, alias: 'y' });
  const fresh = await leaderboardView();
  await buttons(fresh.node, 'Publish my score')[0].fire('click');
  assert.equal(posts(), 1);
  await buttons(fresh.node, 'Share my score')[0].fire('click');
  assert.equal(document.body.children.length, dialogs + 1);
  document.body.children.at(-1).close();
});

test('a reader change while the CSRF token is fetched stops the POST; a late /me after sign-out installs nothing', async () => {
  const c = createHttpClient({ baseUrl: '/api' });
  calls = [];
  routes = { '/me': signedIn('u-a'), '/logout': () => reply(200, { ok: true }), '/scoring/preferences': () => reply(200, { opted_in: true }) };
  await c.me();
  await c.logout();
  const gen = c.authGeneration();
  // the token must be fetched again; while it is on the way, another account signs in
  const pending = deferred();
  routes['/me'] = () => pending.promise;
  const sent = c.scoringPreference({ family: 'official', opt_in: true, gen });
  pending.resolve(signedIn('u-b')());
  assert.equal((await sent).code, 'stale_auth');
  assert.equal(posts(), 0, 'nothing sent with the new reader’s token');
  // a /me sent before a sign-out answers after it: its token and user are dropped
  const late = deferred();
  routes['/me'] = () => late.promise;
  const asked = c.me();
  await c.logout();
  const afterLogout = c.authGeneration();
  late.resolve(signedIn('u-b')());
  assert.equal((await asked).code, 'stale_auth');
  assert.equal(c.authGeneration(), afterLogout, 'no new reader installed by the late answer');
  routes['/me'] = signedOut;
  const r = await c.scoringPreference({ family: 'official', opt_in: true, gen: afterLogout });
  assert.equal(r.code, 'signed_out', 'the late token was not kept: a fresh /me was needed and refused');
  assert.equal(posts(), 0);
  assert.equal((await c.scoringPreference({ family: 'official', opt_in: true })).code, 'invalid_request', 'a POST without a generation is refused');
});

test('the profile too: after a change of reader its old buttons do nothing', async () => {
  const { getClient } = await import('../public/js/account/index.js');
  const client = getClient();
  serve({ account: signedIn('u-kim'), me: () => reply(200, meNotPublished()) });
  await client.me();
  globalThis.location.hash = '#/profile?list=challenge&period=week';
  const view = await profileView();
  view.after();
  const [publish] = buttons(view.node, 'Publish my score');
  const [share] = buttons(view.node, 'Share my score');
  const original = client.scoringPreference;
  let asked = 0;
  client.scoringPreference = (args) => { asked += 1; return original(args); };
  routes['/me'] = signedIn('u-other');
  await client.me();
  assert.match(text(view.node), /^Loading the lists for whoever is signed in now…$/);
  const dialogs = document.body.children.length;
  await publish.fire('click');
  await share.fire('click');
  client.scoringPreference = original;
  globalThis.location.hash = '#/leaderboard';
  assert.equal(asked, 0);
  assert.equal(posts(), 0);
  assert.equal(document.body.children.length, dialogs);
});
