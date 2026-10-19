// #/leaderboard?list=challenge|free&period=week|all&course=all|<id>. Server-verified rows only: the route is registered
// only with CONFIG.scoringUi on, and this view always asks the server. There is no local, sample or mock fallback here.
// The client never ranks or sorts: rows, places and scores are shown exactly as the server sent them, and every answer that
// is drawn must belong to the same board snapshot as the reader's own /scoring/me (otherwise: "start from the top").
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadCourses } from '../data.js';
import { refresh } from '../router.js';
import { eyebrow, pluralTemplate } from '../components/ui.js';
import { placementBlock, weekLine, focusPlaceNext, takePlaceFocus } from '../components/score-ui.js';
import { parseMe, parsePage, sameSnapshot, localScore, loadedRange } from '../scoring/board.js';
import { openShareDialog } from '../components/share-dialog.js';
import { CONFIG } from '../config.js';
import { getClient } from '../account/index.js';

const LISTS = ['challenge', 'free'];
const PERIODS = ['week', 'all'];
const PAGE = 50;
const AROUND = 5;                                        // the server's own cursor starts two places above you: 2 + you + 2

// The course in the address is only a request: it is used when the scoring catalog itself lists it (see below), never
// because the site happens to have a course with that id.
const SCORING_COURSE = /^[a-z][a-z0-9-]{0,63}$/;
export function readBoardQuery(hash) {
  const q = new URLSearchParams((hash || '').split('?')[1] || '');
  const pick = (value, allowed, fallback) => (allowed.includes(value) ? value : fallback);
  const course = q.get('course');
  return { list: pick(q.get('list'), LISTS, 'challenge'), period: pick(q.get('period'), PERIODS, 'week'), course: course && SCORING_COURSE.test(course) ? course : 'all' };
}

const queryString = (s) => `#/leaderboard?list=${s.list}&period=${s.period}&course=${s.course}`;

export function radios(name, values, current, legend, onPick) {
  return h(
    'fieldset',
    { class: 'seg' },
    h('legend', { class: 'visually-hidden' }, legend),
    values.map((v) => {
      const input = h('input', { type: 'radio', name, value: v, class: 'seg__input' });
      input.checked = v === current;
      input.addEventListener('change', () => onPick(v));
      return h('label', { class: 'seg__option' }, input, h('span', {}, t(`board.${name}.${v}`)));
    }),
  );
}

function boardRow(entry) {
  const name = entry.me ? [entry.alias, ' ', h('span', { class: 'tag board__you' }, t('lb.you'))] : entry.alias;
  return h('tr', { class: entry.me ? 'board__me' : null, 'aria-current': entry.me ? 'true' : null, tabindex: '-1' },
    h('td', {}, String(entry.rank)), h('td', {}, name), h('td', { class: 'num' }, localScore(entry.score_display)), h('td', { class: 'num' }, t('profile.ofPool', { n: entry.answered, pool: entry.pool })));
}

export function boardTable(caption, entries) {
  return h('table', { class: 'score-table' },
    h('caption', { class: 'score-table__caption' }, caption),
    h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('board.col.place')), h('th', { scope: 'col' }, t('board.col.name')), h('th', { scope: 'col', class: 'num' }, t('board.col.points')), h('th', { scope: 'col', class: 'num' }, t('board.col.answered')))),
    h('tbody', {}, entries.map(boardRow)));
}

const stateOf = (q) => ({ family: q.list === 'free' ? 'free' : 'official', scope: q.course === 'all' ? 'total' : `course:${q.course}`, window: q.period });

// One subscription for the whole view: a sign-in elsewhere, a sign-out or an ended session redraws the list for the new reader.
// The old view is emptied at once, before the new one loads, so no score or button of the previous reader stays usable.
let stopWatching = null;
export function watchAuth(client, route, node) {
  if (stopWatching) stopWatching();
  stopWatching = client.onAuthChange(() => {
    if (node) node.replaceChildren(h('p', { class: 'lb-status', role: 'status' }, t('lb.readerChanged')));
    if ((globalThis.location.hash || '').startsWith(route)) refresh();
  });
}

const statusLine = (text) => h('p', { class: 'lb-status', role: 'status', tabindex: '-1' }, text);

// The answer for a list that changed under the reader: no rows at all, and one way on.
function staleBlock() {
  const restart = h('button', { type: 'button', class: 'button button--primary' }, t('lb.restart'));
  restart.addEventListener('click', () => refresh());
  return { node: h('div', { class: 'stack lb-stale' }, h('p', { role: 'status' }, t('lb.stale')), h('p', {}, restart)), focus: restart };
}

export async function leaderboardView(params, depth = 0) {
  const shell = (body, after = null, intro = null) => ({ title: t('board.title'), node: h('div', { class: 'stack stack--loose leaderboard' }, h('div', { class: 'course-head' }, eyebrow(t('board.eyebrow')), h('h1', {}, t('board.title')), intro), body), after });
  const fail = (text, before = null) => { const s = statusLine(text); return shell([before, s], () => s.focus()); };
  if (!CONFIG.accountsApi) return fail(t('lb.notConfigured'));
  let courses;
  try { courses = await loadCourses(); } catch { courses = []; }   // titles only: a course id stays readable as text
  const client = getClient();
  if (client.mock !== false || typeof client.scoringLeaderboards !== 'function') return fail(t('lb.notConfigured'));
  const query = readBoardQuery(globalThis.location.hash);
  const state = stateOf(query);
  const gen = client.authGeneration();
  // Who is signed in changed while an answer was on the way: drop everything and ask again as the new reader.
  const again = () => (depth < 2 ? leaderboardView(params, depth + 1) : fail(t('rank.unavailable')));
  const [account, meAnswer, first] = await Promise.all([client.me(), client.scoringMe(state), client.scoringLeaderboards({ ...state, limit: PAGE })]);
  if (client.authGeneration() !== gen) return again();
  const signedIn = account?.signed_in === true;
  const change = (key, value) => { query[key] = value; globalThis.location.hash = queryString(query).slice(1); };
  const controls = h('div', { class: 'board-controls' }, radios('list', LISTS, query.list, t('board.list.label'), (v) => change('list', v)), radios('period', PERIODS, query.period, t('board.period.label'), (v) => change('period', v)));

  // Your own answer is optional (a reader who never played may have none), but when it is there it must match the page.
  const me = meAnswer.ok ? parseMe(meAnswer) : null;
  // The course filter comes only from the scoring catalog: the course ids of your own /scoring/me for this family and
  // catalog version. A course it does not list (unknown, gone after a catalog change, or no answer to check against) is not
  // shown: the address falls back to all courses, without a new history entry.
  const scoringCourses = me ? me.courses.map((c) => c.course_id) : null;
  if (query.course !== 'all' && !(scoringCourses && scoringCourses.includes(query.course))) {
    if (depth >= 2) return fail(t('rank.unavailable'));
    globalThis.history?.replaceState?.(null, '', queryString({ ...query, course: 'all' }));
    if (!globalThis.history?.replaceState) globalThis.location.hash = queryString({ ...query, course: 'all' }).slice(1);
    return leaderboardView(params, depth + 1);
  }
  const titleOf = (id) => courses.find((e) => e.id === id)?.course.title || id;
  if (scoringCourses && scoringCourses.length) {
    const courseSelect = h('select', { class: 'theme-select board-course', 'aria-label': t('board.course.label') }, h('option', { value: 'all' }, t('board.course.all')), scoringCourses.map((id) => h('option', { value: id }, titleOf(id))));
    courseSelect.value = query.course;
    courseSelect.addEventListener('change', () => change('course', courseSelect.value));
    controls.append(courseSelect);
  }
  const page = first.ok ? parsePage(first) : null;
  if (!page) return fail(t('rank.unavailable'), controls);
  if (meAnswer.ok && !me) return fail(t('rank.unavailable'), controls);
  const stale = () => { const s = staleBlock(); return shell([controls, s.node], () => s.focus.focus()); };
  if (me && !sameSnapshot(me, page)) return stale();
  let own = null;
  if (me && me.rank.kind === 'actual' && me.own_cursor && !page.entries.some((e) => e.me)) {
    const around = await client.scoringLeaderboards({ ...state, cursor: me.own_cursor, limit: AROUND });
    if (client.authGeneration() !== gen) return again();
    if (!around.ok && around.code !== 'stale_cursor') return fail(t('rank.unavailable'), controls);
    own = around.ok ? parsePage(around) : null;
    if (!own || !sameSnapshot(me, own) || !own.entries.some((e) => e.me)) return stale();
  }

  const courseTitle = query.course === 'all' ? t('board.course.all') : titleOf(query.course);
  const caption = t('lb.caption', { list: t(`board.list.${query.list}`), period: t(`board.period.${query.period}`), course: courseTitle, catalog: page.catalog_version, rules: page.rules_version });
  const table = boardTable(caption, page.entries);
  const tbody = table.querySelector('tbody');
  const status = h('p', { class: 'lb-status small muted', role: 'status', tabindex: '-1' });
  const endNote = h('p', { class: 'small muted', id: 'lb-end' }, t('lb.end'));
  const moreButton = h('button', { type: 'button', class: 'button button--secondary', 'aria-describedby': 'lb-end' }, t('lb.more'));
  let cursor = page.next_cursor;
  let shown = page.entries.length;
  let busy = false;
  const section = h('section', { class: 'score-card' }, table);
  const setMore = () => {
    if (cursor) { moreButton.removeAttribute('aria-disabled'); endNote.hidden = true; } else { moreButton.setAttribute('aria-disabled', 'true'); endNote.hidden = false; }
  };
  setMore();
  moreButton.addEventListener('click', async () => {
    if (busy || !cursor) return;
    busy = true;
    const answer = await client.scoringLeaderboards({ ...state, cursor, limit: PAGE });
    busy = false;
    if (client.authGeneration() !== gen) { refresh(); return; }
    if (!answer.ok && answer.code !== 'stale_cursor') { status.textContent = t('rank.unavailable'); status.focus(); return; }
    const next = answer.ok ? parsePage(answer) : null;
    if (!next || !sameSnapshot(page, next)) {               // never append rows from another snapshot
      const s = staleBlock();
      section.replaceChildren(s.node);
      s.focus.focus();
      return;
    }
    const range = loadedRange(shown, next);
    const rows = next.entries.map(boardRow);
    tbody.append(...rows);
    shown += next.entries.length;
    cursor = next.next_cursor;
    setMore();
    status.textContent = range ? t('lb.loaded', range) : t('lb.end');
    if (rows[0]) rows[0].focus();
  });
  section.append(h('div', { class: 'actions actions--row' }, moreButton), endNote, status);
  const ownBlock = own ? h('section', { class: 'score-card' }, h('p', { class: 'board__gap', 'aria-hidden': 'true' }, '…'), boardTable(t('lb.yourPlace'), own.entries)) : null;

  const publish = (optIn) => async () => {
    if (client.authGeneration() !== gen) return { ok: false, code: 'stale_auth' };
    const r = await client.scoringPreference({ family: state.family, opt_in: optIn, gen });
    if (r.ok) { focusPlaceNext(); refresh(); }
    return r;
  };
  const alias = (page.entries.find((e) => e.me) || own?.entries.find((e) => e.me))?.alias || null;
  const onShare = () => openShareDialog({ me, listLabel: t(`board.list.${query.list}`), scopeLabel: courseTitle, alias, client, gen });
  const place = me ? h('section', { class: 'score-card' }, h('h2', {}, t('lb.yours')), placementBlock({ me, signedIn, onPublish: publish(true), onUnpublish: publish(false), onShare })) : null;
  const view = shell([controls, place, page.entries.length ? section : statusLine(t('lb.empty')), ownBlock], null, [h('p', { class: 'lead' }, pluralTemplate(page.participants, t('lb.participants.one', { n: page.participants }), t('lb.participants', { n: page.participants }))), weekLine(page.window)]);
  view.after = () => { watchAuth(client, '#/leaderboard', view.node); takePlaceFocus(view.node); };
  return view;
}
