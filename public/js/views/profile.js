// #/profile?list=challenge|free&period=week|all: the server-verified scoring profile (total, placement, your row with its
// neighbours, per course, recent answers) plus a pointer to local learning progress. The route is registered only with
// CONFIG.scoringUi on, and this view never computes points or places locally: everything shown comes from the server.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadCourses } from '../data.js';
import { store } from '../progress.js';
import { eyebrow } from '../components/ui.js';
import { bigNumber, placementBlock, weekLine, focusPlaceNext, takePlaceFocus } from '../components/score-ui.js';
import { readBoardQuery, boardTable, radios, watchAuth } from './leaderboard.js';
import { parseMe, parsePage, sameSnapshot, localScore } from '../scoring/board.js';
import { openShareDialog } from '../components/share-dialog.js';
import { refresh } from '../router.js';
import { CONFIG } from '../config.js';
import { getClient } from '../account/index.js';

const HISTORY = 20;

const queryString = (q) => `#/profile?list=${q.list}&period=${q.period}`;

const day = (seconds) => new Intl.DateTimeFormat(CONFIG.defaultLocale, { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(seconds * 1000));

export async function profileView(params, depth = 0) {
  const shell = (body, after = null) => ({ title: t('profile.title'), node: h('div', { class: 'stack stack--loose profile' }, h('div', { class: 'course-head' }, eyebrow(t('profile.eyebrow')), h('h1', {}, t('profile.title')), h('p', { class: 'lead' }, t('profile.server.lead'))), body), after });
  const fail = (text, before = null) => { const s = h('p', { class: 'lb-status', role: 'status', tabindex: '-1' }, text); return shell([before, s], () => s.focus()); };
  if (!CONFIG.accountsApi) return fail(t('lb.notConfigured'));
  const client = getClient();
  if (client.mock !== false || typeof client.scoringMe !== 'function') return fail(t('lb.notConfigured'));
  let courses = [];
  try { courses = await loadCourses(); } catch { /* ids remain visible below */ }
  const query = readBoardQuery(globalThis.location.hash);
  const state = { family: query.list === 'free' ? 'free' : 'official', scope: 'total', window: query.period };
  const gen = client.authGeneration();
  const again = () => (depth < 2 ? profileView(params, depth + 1) : fail(t('rank.unavailable')));
  const [account, answer] = await Promise.all([client.me(), client.scoringMe(state)]);
  if (client.authGeneration() !== gen) return again();
  const signedIn = account?.signed_in === true;
  const change = (key, value) => { query[key] = value; globalThis.location.hash = queryString(query).slice(1); };
  const controls = h('div', { class: 'board-controls' }, radios('list', ['challenge', 'free'], query.list, t('board.list.label'), (v) => change('list', v)), radios('period', ['week', 'all'], query.period, t('board.period.label'), (v) => change('period', v)));
  // No scoring identity yet (signed out and never played here), or the official list for a reader without an account: a state,
  // not an error.
  if (!answer.ok && (answer.code === 'signed_out' || answer.code === 'not_found')) {
    const note = h('p', { class: 'lb-status', role: 'status', tabindex: '-1' }, answer.code === 'not_found' || (!signedIn && state.family === 'official') ? t('profile.server.signIn') : t('profile.server.none'));
    return shell([controls, h('section', { class: 'score-card' }, note, signedIn ? null : h('p', {}, h('a', { href: '#/signin' }, t('nav.signin'))))], () => watchAuth(client, '#/profile'));
  }
  if (!answer.ok) return fail(t('rank.unavailable'), controls);
  const me = parseMe(answer);
  if (!me) return fail(t('rank.unavailable'), controls);

  // Your row with its neighbours, from the cursor the server gave with your own answer, in the same snapshot.
  let own = null;
  if (me.rank.kind === 'actual' && me.own_cursor) {
    const around = await client.scoringLeaderboards({ ...state, cursor: me.own_cursor, limit: 5 });
    if (client.authGeneration() !== gen) return again();
    own = around.ok ? parsePage(around) : null;
    if (!own || !sameSnapshot(me, own) || !own.entries.some((e) => e.me)) {
      const restart = h('button', { type: 'button', class: 'button button--primary' }, t('lb.restart'));
      restart.addEventListener('click', () => refresh());
      return shell([controls, h('div', { class: 'stack lb-stale' }, h('p', { role: 'status' }, t('lb.stale')), h('p', {}, restart))], () => restart.focus());
    }
  }

  const titleFor = (id) => courses.find((entry) => entry.id === id)?.course.title || id;
  const publish = (optIn) => async () => {
    const r = await client.scoringPreference({ family: state.family, opt_in: optIn });
    if (r.ok) { focusPlaceNext(); refresh(); }
    return r;
  };
  const boardHref = `#/leaderboard?list=${query.list}&period=${query.period}&course=all`;
  const listName = t(`board.list.${query.list}`);
  const alias = own?.entries.find((e) => e.me)?.alias || null;
  const onShare = () => openShareDialog({ me, listLabel: listName, scopeLabel: t('board.course.all'), alias, client });
  const scoreCard = h('section', { class: 'score-card score-card--challenge' },
    h('h2', {}, t('profile.server.title', { list: listName })),
    bigNumber(localScore(me.total.score_display), t('profile.challenge.unit')),
    h('p', { class: 'small muted' }, t('profile.server.counts', { n: me.total.answered, pool: me.total.pool, catalog: me.catalog_version, rules: me.rules_version })),
    weekLine(me.window),
    placementBlock({ me, signedIn, onPublish: publish(true), onUnpublish: publish(false), onShare, showScore: false }),
    h('p', {}, h('a', { href: boardHref }, t('profile.toBoards'))));
  const ownCard = own ? h('section', { class: 'score-card' }, boardTable(t('lb.yourPlace'), own.entries)) : null;
  const byCourse = h('section', { class: 'score-card' }, h('h2', {}, t('profile.byCourse')), h('table', { class: 'score-table' },
    h('caption', { class: 'visually-hidden' }, t('profile.byCourse')),
    h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('profile.col.course')), h('th', { scope: 'col', class: 'num' }, t('board.col.points')), h('th', { scope: 'col', class: 'num' }, t('profile.col.first')))),
    h('tbody', {}, me.courses.map((item) => h('tr', {}, h('th', { scope: 'row' }, titleFor(item.course_id)), h('td', { class: 'num' }, localScore(item.score_display)), h('td', { class: 'num' }, t('profile.ofPool', { n: item.answered, pool: item.pool })))))));
  const recent = me.attempts.slice(0, HISTORY);
  const history = recent.length ? h('section', { class: 'score-card' }, h('h2', {}, t('profile.recent.title')), h('table', { class: 'score-table' },
    h('caption', { class: 'visually-hidden' }, t('profile.recent.title')),
    h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('profile.col.course')), h('th', { scope: 'col' }, t('profile.recent.answer')), h('th', { scope: 'col' }, t('profile.recent.date')), h('th', { scope: 'col' }, t('profile.recent.counts')))),
    h('tbody', {}, recent.map((a) => h('tr', {}, h('th', { scope: 'row' }, titleFor(a.course_id)), h('td', {}, t(`profile.outcome.${a.outcome}`)), h('td', {}, day(a.accepted_at)), h('td', {}, a.rank_eligible ? t('profile.recent.ranked') : t('profile.recent.practice'))))))) : null;
  const local = h('section', { class: 'score-card' }, h('h2', {}, t('profile.learned.title')), h('p', {}, t('profile.server.local', { n: store.answers().length })));
  const view = shell([controls, scoreCard, ownCard, byCourse, history, local]);
  view.after = () => { watchAuth(client, '#/profile'); takePlaceFocus(view.node); };
  return view;
}
