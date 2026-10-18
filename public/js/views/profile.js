// #/profile: the server-verified official scoring profile (total and per course) plus a pointer to local learning
// progress. The route is registered only with CONFIG.scoringUi on, and this view never computes points locally.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadCourses } from '../data.js';
import { store } from '../progress.js';
import { formatPoints } from '../scoring/model.js';
import { eyebrow } from '../components/ui.js';
import { bigNumber } from '../components/score-ui.js';
import { CONFIG } from '../config.js';
import { getClient } from '../account/index.js';

export async function profileView() {
  const unavailable = (text) => ({ title: t('profile.title'), node: h('div', { class: 'stack', role: 'status' }, h('h1', {}, t('profile.title')), h('p', {}, text)) });
  if (!CONFIG.accountsApi) return unavailable('The scoring service is not configured. No local score is used here.');
  const client = getClient();
  if (client.mock !== false || typeof client.scoringMe !== 'function') return unavailable('A live scoring API is not configured.');
  const data = await client.scoringMe({ family: 'official', window: 'all' });
  if (!data.ok) return unavailable(data.code === 'signed_out' ? 'Sign in to view your server-verified scoring profile.' : data.code === 'unavailable' ? 'Scoring is off or temporarily unavailable.' : data.message || 'The scoring profile could not be loaded.');
  let entries = [];
  try { entries = await loadCourses(); } catch { /* ids remain visible below */ }
  const titleFor = (id) => entries.find((entry) => entry.id === id)?.course.title || id;
  const courses = Array.isArray(data.courses) ? data.courses : [];
  return {
    title: t('profile.title'),
    node: h('div', { class: 'stack stack--loose profile' },
      h('div', { class: 'course-head' }, eyebrow(t('profile.eyebrow')), h('h1', {}, t('profile.title')), h('p', { class: 'lead' }, 'Server-verified official scoring. Local learning progress remains separate.')),
      h('section', { class: 'score-card score-card--challenge' }, h('h2', {}, 'Official score'), bigNumber(formatPoints(data.total?.points ?? 0), t('profile.challenge.unit')), h('p', { class: 'small muted' }, `${data.total?.answered ?? 0} / ${data.total?.pool ?? 0} questions · ${data.rules_version || 'unknown rules'} · ${data.catalog_version || 'unknown catalogue'}`), h('p', { class: 'small muted' }, data.published ? 'Your profile is opted into publication.' : 'Your profile is not published.'), h('p', {}, h('a', { href: '#/leaderboard' }, t('profile.toBoards')))),
      h('section', { class: 'score-card' }, h('h2', {}, t('profile.byCourse')), h('table', { class: 'score-table' }, h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('profile.col.course')), h('th', { scope: 'col', class: 'num' }, t('profile.col.challenge')), h('th', { scope: 'col', class: 'num' }, t('profile.col.first')))), h('tbody', {}, courses.map((item) => h('tr', {}, h('th', { scope: 'row' }, titleFor(item.course_id)), h('td', { class: 'num' }, formatPoints(item.points)), h('td', { class: 'num' }, t('profile.ofPool', { n: item.answered, pool: item.pool }))))))),
      h('section', { class: 'score-card' }, h('h2', {}, t('profile.learned.title')), h('p', {}, `${store.answers().length} locally stored learning attempts. These are not sent to or included in the scoring API.`)),
    ),
  };
}
