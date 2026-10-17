// Course catalog: one card per course (title, description, free count, honest premium note) and the premium note
// on a course page. Text is whole translated sentences; numbers come from the catalog and the course files.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { store } from '../progress.js';
import { courseHref, freeTotal } from '../catalog.js';
import { facts, pluralTemplate, courseCounts } from './ui.js';

// "Premium · coming soon" and what is true about it: more questions are in preparation, no date and no price.
// Shown only for a known status; the sentence never promises a date, a price or a way to buy.
export function premiumNote(entry) {
  if (!entry || !entry.premium) return null;
  const n = entry.premium.questions;
  return h('section', { class: 'banner premium-note', 'aria-labelledby': `premium-${entry.id}` }, h('h2', { class: 'banner__title', id: `premium-${entry.id}` }, t('catalog.premium')), h('p', {}, n ? t('catalog.premium.count', { n }) : t('catalog.premium.nocount')));
}

function progressLine(course) {
  if (!course) return null;
  const ids = [...new Set(course.levels.flatMap((l) => l.lessons.flatMap((x) => x.questions)))];
  const answered = ids.filter((id) => store.latest(id)).length;
  return answered ? t('course.lesson.progress', { answered, total: ids.length }) : t('course.lesson.notStarted');
}

// entries: parsed catalog list; courses: Map of id -> parsed course file (a course that failed to load has no entry).
export function catalogSection(entries, courses) {
  const total = freeTotal(entries);
  const cards = entries.map((entry) => {
    const course = courses.get(entry.id) || null;
    const free = entry.free !== null ? entry.free : course ? courseCounts(course).questions : null;
    const pills = facts([{ template: pluralTemplate(free, t('catalog.free.one'), t('catalog.free')), bold: free }, entry.premium ? { template: t('catalog.premium') } : null], t('facts.label'));
    return h(
      'li',
      { class: 'course-card' },
      h('h3', { class: 'course-card__title' }, h('a', { href: courseHref(entry.id) }, entry.title)),
      h('p', { class: 'course-card__text' }, entry.description),
      pills,
      entry.premium && entry.premium.questions ? h('p', { class: 'muted small' }, t('catalog.premium.count', { n: entry.premium.questions })) : null,
      h('p', { class: 'course-card__progress small' }, progressLine(course)),
    );
  });
  return h('section', { class: 'catalog', 'aria-labelledby': 'catalog-title' }, h('h2', { id: 'catalog-title' }, t('catalog.title')), total !== null ? h('p', { class: 'muted' }, t('catalog.lead', { n: total, m: entries.length })) : null, h('ul', { class: 'catalog__list' }, cards));
}
