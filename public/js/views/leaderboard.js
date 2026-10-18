// #/leaderboard?list=challenge|free&period=week|all&course=all|<id>. Test data (scoring/mock.js) until the server
// API exists. Points kept in this browser are unverified, so they are never placed among others on a list (the
// server's own row and neighbours come with the API). Behind CONFIG.scoringUi.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadCourses } from '../data.js';
import { store } from '../progress.js';
import { mockBoard } from '../scoring/mock.js';
import { RULES, firstTries, courseScore, totalPoints, sharedRanks, boardWindow, formatPoints, weekStart } from '../scoring/model.js';
import { eyebrow } from '../components/ui.js';
import { rulesList } from '../components/score-ui.js';
import { errorNode } from './errors.js';
import { CONFIG } from '../config.js';
import { getClient } from '../account/index.js';

const LISTS = ['challenge', 'free'];
const PERIODS = ['week', 'all'];

export function readBoardQuery(hash, courseIds) {
  const q = new URLSearchParams((hash || '').split('?')[1] || '');
  const pick = (value, allowed, fallback) => (allowed.includes(value) ? value : fallback);
  return { list: pick(q.get('list'), LISTS, 'challenge'), period: pick(q.get('period'), PERIODS, 'week'), course: pick(q.get('course'), ['all', ...courseIds], 'all') };
}

const queryString = (s) => `#/leaderboard?list=${s.list}&period=${s.period}&course=${s.course}`;

// The reader's own points from this browser for this period and course (free questions only), or null.
function ownPoints(state, entries, records, now) {
  if (state.list !== 'free') return null;
  const first = [...firstTries(records).values()];
  const inWindow = state.period === 'week' ? first.filter((r) => r.t >= weekStart(now)) : first;
  const scores = entries.map((e) => ({ id: e.id, ...courseScore(e.course, inWindow) }));
  const picked = state.course === 'all' ? scores : scores.filter((s) => s.id === state.course);
  const answered = picked.reduce((a, s) => a + s.answered, 0);
  if (!answered) return null;
  return totalPoints(picked);
}

function radios(name, values, current, legend, onPick) {
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

async function serverLeaderboardView() {
  const unavailable = (text) => ({ title: t('board.title'), node: h('div', { class: 'stack', role: 'status' }, h('h1', {}, t('board.title')), h('p', {}, text)) });
  if (!CONFIG.accountsApi) return unavailable('The scoring service is not configured. No local scores or sample rankings are shown.');
  let entries;
  try { entries = await loadCourses(); } catch { return unavailable('Course data could not be loaded.'); }
  const state = readBoardQuery(globalThis.location.hash, entries.map((e) => e.id));
  const client = getClient();
  if (client.mock !== false || typeof client.scoringLeaderboards !== 'function') return unavailable('A live scoring API is not configured.');
  const family = state.list === 'free' ? 'free' : 'official';
  const scope = state.course === 'all' ? 'total' : `course:${state.course}`;
  const data = await client.scoringLeaderboards({ family, scope, window: state.period, limit: 50 });
  if (!data.ok) return unavailable(data.code === 'unavailable' ? 'Scoring is off or temporarily unavailable.' : data.message || 'The leaderboard could not be loaded.');
  const caption = `${family === 'free' ? 'Free practice' : 'Official'} · ${state.period === 'week' ? 'This week' : 'All time'} · ${state.course === 'all' ? 'All courses' : entries.find((e) => e.id === state.course)?.course.title || state.course}`;
  const rows = (data.entries || []).map((entry) => h('tr', {}, h('td', {}, String(entry.rank)), h('td', {}, entry.alias), h('td', { class: 'num' }, formatPoints(entry.points)), h('td', { class: 'num' }, t('profile.ofPool', { n: entry.answered, pool: entry.pool }))));
  const change = (key, value) => { state[key] = value; globalThis.location.hash = queryString(state).slice(1); };
  const controls = h('div', { class: 'board-controls' }, radios('list', LISTS, state.list, t('board.list.label'), (list) => change('list', list)), radios('period', PERIODS, state.period, t('board.period.label'), (period) => change('period', period)));
  const courseSelect = h('select', { class: 'theme-select board-course', 'aria-label': t('board.course.label') }, h('option', { value: 'all' }, t('board.course.all')), entries.map((e) => h('option', { value: e.id }, e.course.title)));
  courseSelect.value = state.course;
  courseSelect.addEventListener('change', () => change('course', courseSelect.value));
  controls.append(courseSelect);
  return { title: t('board.title'), node: h('div', { class: 'stack stack--loose leaderboard' }, h('div', { class: 'course-head' }, eyebrow(t('board.eyebrow')), h('h1', {}, t('board.title')), h('p', { class: 'lead' }, `${caption} · ${data.participants || 0} published participants`)), controls, h('section', { class: 'score-card' }, h('h2', {}, caption), h('table', { class: 'score-table' }, h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('board.col.place')), h('th', { scope: 'col' }, t('board.col.name')), h('th', { scope: 'col', class: 'num' }, t('board.col.points')), h('th', { scope: 'col', class: 'num' }, t('board.col.answered')))), h('tbody', {}, rows)), h('p', { class: 'small muted' }, `${data.rules_version || ''} · ${data.catalog_version || ''}`))) };
}

export async function leaderboardView() {
  if (CONFIG.scoringUi) return serverLeaderboardView();
  let entries;
  try {
    entries = await loadCourses();
  } catch (err) {
    console.error(err);
    return { title: t('error.title'), node: errorNode(t('error.course')) };
  }
  const state = readBoardQuery(globalThis.location.hash, entries.map((e) => e.id));
  const slot = h('div', { class: 'board-slot' });

  const draw = () => {
    const now = Date.now();
    const pools = entries.filter((e) => state.course === 'all' || e.id === state.course).map((e) => courseScore(e.course, []).pool);
    const pool = pools.reduce((a, n) => a + n, 0);
    const board = mockBoard({ family: state.list, window: state.period, course: state.course, pool });
    const mine = ownPoints(state, entries, store.answers(), now);
    const ranked = sharedRanks(board.rows);
    const shown = boardWindow(ranked, null, { top: 10 });
    const courseTitle = state.course === 'all' ? t('board.course.all') : entries.find((e) => e.id === state.course).course.title;
    const caption = t('board.caption', { list: t(`board.list.${state.list}`), period: t(`board.period.${state.period}`), course: courseTitle });
    const rows = shown.map((r) =>
      r.gap
        ? h('tr', { class: 'board__gap' }, h('td', { colspan: '4' }, h('span', { 'aria-hidden': 'true' }, '…'), h('span', { class: 'visually-hidden' }, t('board.gap'))))
        : h('tr', { class: r.me ? 'board__me' : null, 'aria-current': r.me ? 'true' : null }, h('td', {}, String(r.rank)), h('td', {}, r.name), h('td', { class: 'num' }, formatPoints(r.points)), h('td', { class: 'num' }, t('profile.ofPool', { n: r.answered, pool: r.pool }))),
    );
    const own = state.list === 'challenge' ? t('board.challenge.none') : mine !== null ? t('board.local', { points: formatPoints(mine) }) : t('board.free.none');
    slot.replaceChildren(
      h(
        'section',
        { class: 'score-card', 'aria-labelledby': 'board-title' },
        h('h2', { id: 'board-title' }, caption, ' ', h('span', { class: `tag${state.list === 'challenge' ? ' tag--real' : ''}` }, t(`board.tag.${state.list}`))),
        h('table', { class: 'score-table' }, h('caption', { class: 'visually-hidden' }, caption), h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('board.col.place')), h('th', { scope: 'col' }, t('board.col.name')), h('th', { scope: 'col', class: 'num' }, t('board.col.points')), h('th', { scope: 'col', class: 'num' }, t('board.col.answered')))), h('tbody', {}, rows)),
        h('p', { class: 'small', role: 'status' }, own),
        h('p', { class: 'small muted' }, state.period === 'week' ? t('board.note.week', { version: RULES.version }) : t('board.note.all', { version: RULES.version })),
      ),
    );
  };
  const pick = (key) => (value) => {
    state[key] = value;
    // Keep the address in step without a new render (focus stays on the control the reader used).
    try {
      globalThis.history.replaceState(null, '', queryString(state));
    } catch {
      /* the list still changes */
    }
    draw();
  };
  const courseSelect = h('select', { class: 'theme-select board-course', 'aria-label': t('board.course.label') }, h('option', { value: 'all' }, t('board.course.all')), entries.map((e) => h('option', { value: e.id }, e.course.title)));
  courseSelect.value = state.course;
  courseSelect.addEventListener('change', () => pick('course')(courseSelect.value));
  draw();

  return {
    title: t('board.title'),
    node: h(
      'div',
      { class: 'stack stack--loose leaderboard' },
      h('p', { class: 'test-strip', role: 'note' }, t('board.testData')),
      h('div', { class: 'course-head' }, eyebrow(t('board.eyebrow')), h('h1', {}, t('board.title')), h('p', { class: 'lead' }, t('board.lead'))),
      h('div', { class: 'board-controls' }, radios('list', LISTS, state.list, t('board.list.label'), pick('list')), radios('period', PERIODS, state.period, t('board.period.label'), pick('period')), courseSelect),
      h(
        'div',
        { class: 'score-pair score-pair--board' },
        slot,
        h('section', { class: 'score-card', 'aria-labelledby': 'how-title' }, h('h2', { id: 'how-title' }, t('board.how.title')), rulesList(), h('p', { class: 'small' }, t('board.how.first')), h('p', { class: 'small muted' }, t('board.how.free')), h('p', { class: 'small muted' }, t('board.how.publish'))),
      ),
    ),
  };
}
