// The result moment when every question of a course has a first try.
// Score and learning side by side, then an honest invitation; no rank, because nothing leaves this browser.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { CONFIG } from '../config.js';
import { scoreWithProfiles, formatPoints } from '../scoring/model.js';
import { facts } from './ui.js';

// `profiles` is the Map of validated answer profiles of the course (data.js). Without it (unknown) there is no result: nothing is
// scored on a guess, and a course whose questions are all practice has no scoring pool and no result.
export function courseFinished(course, records, profiles = null) {
  const s = scoreWithProfiles(course, records, undefined, null, profiles);
  return !s.unavailable && s.pool > 0 && s.answered === s.pool;
}

export function resultCard(course, records, isRightNow = null, profiles = null) {
  const s = scoreWithProfiles(course, records, undefined, isRightNow, profiles);
  const half = (n) => (Number.isInteger(n) ? String(n) : n.toFixed(1));
  return h(
    'section',
    { class: 'continue-card result-card', 'aria-labelledby': 'result-title' },
    h('p', { class: 'kicker' }, t('result.kicker', { course: course.title })),
    h('h2', { id: 'result-title' }, t('result.points', { points: formatPoints(s.points) })),
    h('p', {}, t('result.raw', { sum: half(s.sum), pool: s.pool })),
    facts([{ template: t('profile.pill.right'), bold: s.counts.right }, { template: t('profile.pill.partial'), bold: s.counts.partial }, { template: t('profile.pill.unsure'), bold: s.counts.unsure }, { template: t('profile.pill.wrong'), bold: s.counts.wrong }], t('result.kicker', { course: course.title })),
    isRightNow ? h('p', { class: 'muted' }, t('result.learned', { n: s.learned, pool: s.pool })) : null,
    h(
      'div',
      { class: 'banner result-card__invite' },
      h('p', { class: 'banner__title' }, t('result.invite.title')),
      h('p', {}, t('result.invite.text')),
      h('div', { class: 'actions' }, CONFIG.accountsUi ? h('a', { class: 'button button--secondary button--small', href: '#/signin' }, t('result.invite.account')) : null, h('a', { href: '#/leaderboard?list=free' }, t('result.invite.how'))),
    ),
  );
}
