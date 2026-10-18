// Course catalog: one card per course (title, description, free count, honest premium note) and the premium note
// on a course page. Text is whole translated sentences; numbers come from the catalog and the course files.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { store } from '../progress.js';
import { courseHref, DEFAULT_COURSE } from '../catalog.js';
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
// The beginner path: two easier courses come before Python. They do not exist yet, so they are shown as
// "Coming soon" steps with no link. Their names are page text, not course data.
export const PATH_SOON = ['path.soon.1', 'path.soon.2'];

function courseCard(entry, courses, tag = 'li', extraClass = '') {
  const course = courses.get(entry.id) || null;
  const free = entry.free !== null ? entry.free : course ? courseCounts(course).questions : null;
  const pills = facts([{ template: pluralTemplate(free, t('catalog.free.one'), t('catalog.free')), bold: free }, entry.premium ? { template: t('catalog.premium') } : null], t('facts.label'));
  return h(
    tag,
    { class: `course-card ${extraClass}`.trim() },
    h('h3', { class: 'course-card__title' }, h('a', { href: courseHref(entry.id) }, entry.title)),
    h('p', { class: 'course-card__text' }, entry.description),
    pills,
    entry.premium && entry.premium.questions ? h('p', { class: 'muted small' }, t('catalog.premium.count', { n: entry.premium.questions })) : null,
    h('p', { class: 'course-card__progress small' }, progressLine(course)),
  );
}

// entries: parsed catalog list; courses: Map of id -> parsed course file (a course that failed to load has no entry).
// With the default course in the catalog the page shows the path (1, 2 coming soon, 3 the default course) and the
// other courses under "Go deeper"; otherwise a plain grid.
export function catalogSection(entries, courses, startId = DEFAULT_COURSE) {
  const start = entries.find((e) => e.id === startId);
  const head = h('h2', { id: 'catalog-title' }, t('catalog.title'));
  if (!start) return h('section', { class: 'catalog', 'aria-labelledby': 'catalog-title' }, head, h('ul', { class: 'catalog__list' }, entries.map((e) => courseCard(e, courses))));
  const steps = [
    ...PATH_SOON.map((key, i) =>
      h(
        'li',
        { class: 'path__step path__step--soon' },
        h('span', { class: 'path__n', 'aria-hidden': 'true' }, String(i + 1)),
        h('div', { class: 'path__body' }, h('p', { class: 'path__title' }, h('span', { class: 'visually-hidden' }, t('path.step', { n: i + 1 })), t(`${key}.title`)), h('p', { class: 'small muted' }, t(`${key}.text`)), h('p', { class: 'path__soon small' }, t('path.soon'))),
      ),
    ),
    h('li', { class: 'path__step' }, h('span', { class: 'path__n', 'aria-hidden': 'true' }, String(PATH_SOON.length + 1)), h('div', { class: 'path__body' }, h('span', { class: 'visually-hidden' }, t('path.step', { n: PATH_SOON.length + 1 })), courseCard(start, courses, 'div', 'path__card'))),
  ];
  const rest = entries.filter((e) => e.id !== startId);
  return h(
    'section',
    { class: 'catalog', 'aria-labelledby': 'catalog-title' },
    head,
    h('h3', { class: 'path__heading' }, t('path.heading')),
    h('ol', { class: 'path' }, steps),
    rest.length ? h('h3', { class: 'path__heading' }, t('path.deeper')) : null,
    rest.length ? h('ul', { class: 'catalog__list' }, rest.map((e) => courseCard(e, courses))) : null,
  );
}
