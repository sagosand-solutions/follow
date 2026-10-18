// #/leaderboard?list=challenge|free&period=week|all&course=all|<id>. Server-verified rows only: the route is registered
// only with CONFIG.scoringUi on, and this view always asks the server. There is no local, sample or mock fallback here.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadCourses } from '../data.js';
import { formatPoints } from '../scoring/model.js';
import { eyebrow } from '../components/ui.js';
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

export async function leaderboardView() {
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
