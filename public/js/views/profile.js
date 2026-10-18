// #/profile: two numbers that never mix (challenge score from first tries, what you have learned from every try),
// per course, and a calm week-by-week history. Shown only when CONFIG.scoringUi is on. Data: this browser only.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadCourses, loadQuestion } from '../data.js';
import { currentAnswer } from '../answers.js';
import { store } from '../progress.js';
import { courseHref, reviewHref } from '../catalog.js';
import { reviewQueue } from '../review.js';
import { RULES, courseScore, totalPoints, formatPoints, recentWeeks } from '../scoring/model.js';
import { eyebrow, facts } from '../components/ui.js';
import { bigNumber } from '../components/score-ui.js';
import { errorNode } from './errors.js';
import { CONFIG } from '../config.js';
import { getClient } from '../account/index.js';

const weekLabel = (start, now, i) => (i === 0 ? t('profile.week.this') : t('profile.week.of', { date: new Date(start).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }) }));

async function serverProfileView() {
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

export async function profileView() {
  if (CONFIG.scoringUi) return serverProfileView();
  let entries;
  try {
    entries = await loadCourses();
  } catch (err) {
    console.error(err);
    return { title: t('error.title'), node: errorNode(t('error.course')) };
  }
  if (!entries.length) return { title: t('error.title'), node: errorNode(t('error.course')) };
  const records = store.answers();
  // "Right now" is checked against each question as it is today; a question that changed or failed to load does not count.
  const ids = [...new Set(records.map((r) => r.question_id))];
  const questions = new Map((await Promise.all(ids.map((id) => loadQuestion(id).then((q) => [id, q], () => null)))).filter(Boolean));
  const isRightNow = (r) => {
    const q = questions.get(r.question_id);
    const cur = q ? currentAnswer(q, r) : null;
    return !!cur && cur.outcome === 'answered' && cur.score === 1;
  };
  const scores = entries.map((e) => ({ id: e.id, title: e.course.title, course: e.course, ...courseScore(e.course, records, RULES, isRightNow) }));
  const total = totalPoints(scores);
  const sum = (key) => scores.reduce((a, s) => a + s.counts[key], 0);
  const answered = scores.reduce((a, s) => a + s.answered, 0);
  const learned = scores.reduce((a, s) => a + s.learned, 0);
  const improved = scores.reduce((a, s) => a + s.improved, 0);
  const reviews = scores.map((s) => ({ id: s.id, n: reviewQueue(s.course, records).length })).filter((r) => r.n > 0);
  const reviewTotal = reviews.reduce((a, r) => a + r.n, 0);

  const challenge = h(
    'section',
    { class: 'score-card score-card--challenge', 'aria-labelledby': 'challenge-title' },
    h('h2', { id: 'challenge-title' }, t('profile.challenge.title')),
    bigNumber(formatPoints(total), t('profile.challenge.unit')),
    h('p', { class: 'small muted' }, t('profile.challenge.average', { n: scores.length })),
    h('p', { class: 'score-lock small' }, t('profile.challenge.lock')),
    facts(
      [
        { template: t('profile.pill.first'), bold: answered },
        { template: t('profile.pill.right'), bold: sum('right') },
        { template: t('profile.pill.partial'), bold: sum('partial') },
        { template: t('profile.pill.unsure'), bold: sum('unsure') },
        { template: t('profile.pill.wrong'), bold: sum('wrong') },
      ],
      t('profile.challenge.title'),
    ),
    h('p', {}, h('a', { href: '#/leaderboard' }, t('profile.toBoards'))),
  );

  const learnedCard = h(
    'section',
    { class: 'score-card', 'aria-labelledby': 'learned-title' },
    h('h2', { id: 'learned-title' }, t('profile.learned.title')),
    bigNumber(String(learned), t('profile.learned.unit', { n: answered })),
    h('p', { class: 'small muted' }, t('profile.learned.text')),
    facts([{ template: t('profile.pill.improved'), bold: improved }, { template: t('profile.pill.review'), bold: reviewTotal }], t('profile.learned.title')),
    reviews.length ? h('p', {}, h('a', { href: reviewHref(reviews[0].id) }, t('review.button', { count: reviews[0].n }))) : null,
  );

  const table = h(
    'table',
    { class: 'score-table' },
    h('caption', { class: 'visually-hidden' }, t('profile.byCourse')),
    h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('profile.col.course')), h('th', { scope: 'col', class: 'num' }, t('profile.col.challenge')), h('th', { scope: 'col', class: 'num' }, t('profile.col.first')), h('th', { scope: 'col', class: 'num' }, t('profile.col.learned')))),
    h(
      'tbody',
      {},
      scores.map((s) =>
        h('tr', {}, h('th', { scope: 'row' }, h('a', { href: courseHref(s.id) }, s.title)), h('td', { class: 'num' }, formatPoints(s.points)), h('td', { class: 'num' }, t('profile.ofPool', { n: s.answered, pool: s.pool })), h('td', { class: 'num' }, t('profile.ofPool', { n: s.learned, pool: s.pool }))),
      ),
    ),
  );

  const now = Date.now();
  const weeks = recentWeeks(records, now, 8);
  // Weeks without reading after this week are folded into one quiet line per stretch.
  const items = [];
  weeks.forEach((w, i) => {
    const last = items[items.length - 1];
    if (i > 0 && !w.read && last && last.empty) last.from = w.start;
    else items.push({ ...w, i, empty: i > 0 && !w.read, from: w.start, to: w.start });
  });
  const day = (ms) => new Date(ms).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
  const history = h(
    'ul',
    { class: 'score-history' },
    items.map((w) =>
      h(
        'li',
        {},
        h('span', { class: 'muted' }, w.empty && w.from !== w.to ? t('profile.week.range', { from: day(w.from), to: day(w.to) }) : weekLabel(w.start, now, w.i)),
        h('span', {}, w.read ? (w.cleared ? t('profile.week.readCleared', { n: w.read, c: w.cleared }) : t('profile.week.read', { n: w.read })) : t('profile.week.none')),
      ),
    ),
  );

  return {
    title: t('profile.title'),
    node: h(
      'div',
      { class: 'stack stack--loose profile' },
      h('div', { class: 'course-head' }, eyebrow(t('profile.eyebrow')), h('h1', {}, t('profile.title')), h('p', { class: 'lead' }, t('profile.lead'))),
      h('div', { class: 'score-pair' }, challenge, learnedCard),
      h('section', { class: 'score-card', 'aria-labelledby': 'by-course' }, h('h2', { id: 'by-course' }, t('profile.byCourse')), table),
      h('section', { class: 'score-card', 'aria-labelledby': 'weeks-title' }, h('h2', { id: 'weeks-title' }, t('profile.weeks')), history),
      h('section', { class: 'banner', 'aria-labelledby': 'publish-title' }, h('h2', { class: 'banner__title', id: 'publish-title' }, t('profile.publish.title')), h('p', {}, t('profile.publish.text'))),
    ),
  };
}
