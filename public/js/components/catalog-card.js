// Course catalog: one card per course (title, description, free count, premium status) and the premium note on a
// course page. Text is whole translated sentences; numbers come from the catalog and the course files.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { store } from '../progress.js';
import { courseHref, DEFAULT_COURSE } from '../catalog.js';
import { facts, pluralTemplate, courseCounts } from './ui.js';
import { progressBadge } from './course-icons.js';

// "Premium · coming soon" and what is true about it: more questions are in preparation, no date and no price.
// Shown only for a known status; the sentence never promises a date, a price or a way to buy.
export function premiumNote(entry) {
  if (!entry || !entry.premium) return null;
  const n = entry.premium.questions;
  return h('section', { class: 'banner premium-note', 'aria-labelledby': `premium-${entry.id}` }, h('h2', { class: 'banner__title', id: `premium-${entry.id}` }, t('catalog.premium')), h('p', {}, n ? t('catalog.premium.count', { n }) : t('catalog.premium.nocount')));
}

function progressOf(course) {
  if (!course) return { answered: 0, total: 0 };
  const ids = [...new Set(course.levels.flatMap((l) => l.lessons.flatMap((x) => x.questions)))];
  return { answered: ids.filter((id) => store.latest(id)).length, total: ids.length };
}

// The course's symbol in its progress ring. Once something is answered the ring replaces the written count and carries it
// as its name ("10 of 30 read"); before that it is decoration.
export function courseBadge(id, course) {
  const { answered, total } = progressOf(course);
  return progressBadge(id, { answered, total, label: answered ? t('course.lesson.progress', { answered, total }) : null });
}

// entries: parsed catalog list; courses: Map of id -> parsed course file (a course that failed to load has no entry).
// The beginner path: two easier courses come before Python. They do not exist yet, so they are shown as
// "Coming soon" steps with no link. Their names are page text, not course data.
export const PATH_SOON = ['path.soon.1', 'path.soon.2'];
// Their symbols, by the course each step will become.
const SOON_ICON = { 'path.soon.1': 'read-like-a-computer', 'path.soon.2': 'small-programs' };

// Who a course is for, in words, from the catalog only. No pill when the catalog does not say.
function audiencePill(entry) {
  if (entry.audience === 'beginner') return { template: t('catalog.audience.beginner') };
  if (entry.audience === 'experienced') return { template: t('catalog.audience.experienced') };
  return null;
}

// On the path the section headings say who the courses are for, so the cards there carry no audience pill. A started
// course gets an accent edge and a filling ring (a course not started is neutral), and the premium details are on the
// course page.
function courseCard(entry, courses, tag = 'li', extraClass = '', { audience = true } = {}) {
  const course = courses.get(entry.id) || null;
  const free = entry.free !== null ? entry.free : course ? courseCounts(course).questions : null;
  const started = progressOf(course).answered > 0;
  const pills = facts([audience ? audiencePill(entry) : null, { template: pluralTemplate(free, t('catalog.free.one'), t('catalog.free')), bold: free }, entry.premium ? { template: t('catalog.premium') } : null], t('facts.label'));
  return h(
    tag,
    { class: `course-card${started ? ' course-card--started' : ''} ${extraClass}`.trim() },
    h('div', { class: 'course-card__head' }, courseBadge(entry.id, course), h('h3', { class: 'course-card__title' }, h('a', { href: courseHref(entry.id) }, entry.title))),
    h('p', { class: 'course-card__text' }, entry.description),
    pills,
  );
}

// The courses for people who already code, from the catalog only: those marked `experienced`, and a course on the path that
// is not a beginner course (the path's last step carries no audience pill, because it is both). In catalog order.
export const forPeopleWhoCode = (entries) => (entries || []).filter((e) => e.audience === 'experienced' || (e.pathStep !== null && e.pathStep !== undefined && e.audience !== 'beginner'));

// "Already write code?": a calm note that points people who can already program to the courses for them; nothing when
// there are none.
export function alreadyCodeSign(entries) {
  const deeper = forPeopleWhoCode(entries);
  if (!deeper.length) return null;
  const links = deeper.flatMap((e, i) => [i ? ' · ' : null, h('a', { href: courseHref(e.id) }, e.title)]);
  return h('section', { class: 'note already-code', 'aria-labelledby': 'already-code-title' }, h('span', { class: 'note__icon', 'aria-hidden': 'true' }, '→'), h('h2', { class: 'note__title', id: 'already-code-title' }, t('sign.title')), h('p', { class: 'note__text' }, t('sign.text'), ' ', ...links));
}

// The first question of a course: the catalog's start question, else the first question of the course file.
export function firstQuestionOf(entry, course) {
  if (entry && entry.start) return entry.start;
  const lesson = course && course.levels && course.levels[0] && course.levels[0].lessons[0];
  return lesson && lesson.questions[0] ? lesson.questions[0] : null;
}

// entries: parsed catalog list; courses: Map of id -> parsed course file (a course that failed to load has no entry).
// The path comes from the catalog: step n is the course with `path_step: n`. Without one, steps 1 and 2 stay "Coming soon"
// (no link) and step 3 is the default course, as before. A beginner course on the path gets "Already write code? Skip
// ahead" (one line under the path's heading, for the whole path), which moves focus to the heading over the other courses;
// the page's main button already starts it. Without any path the page is a plain grid, and there each card says who it is for.
export function catalogSection(entries, courses, startId = DEFAULT_COURSE) {
  const head = h('h2', { id: 'catalog-title' }, t('catalog.title'));
  const byStep = new Map(entries.filter((e) => e.pathStep !== null && e.pathStep !== undefined && courses.has(e.id)).map((e) => [e.pathStep, e]));
  const start = entries.find((e) => e.id === startId);
  if (!byStep.has(3) && start && ![...byStep.values()].includes(start)) byStep.set(3, start);
  if (!byStep.size) return h('section', { class: 'catalog', 'aria-labelledby': 'catalog-title' }, head, h('ul', { class: 'catalog__list' }, entries.map((e) => courseCard(e, courses))));
  const onPath = new Set(byStep.values());
  const rest = entries.filter((e) => !onPath.has(e));
  // When every course off the path is for people who already code, the heading says so once instead of a pill per card.
  const allExperienced = rest.length > 0 && rest.every((e) => e.audience === 'experienced');
  const deeper = rest.length ? h('h3', { class: 'path__heading', id: 'path-deeper', tabindex: '-1' }, t(allExperienced ? 'catalog.audience.experienced' : 'path.deeper')) : null;
  const stepItem = (n) => {
    const entry = byStep.get(n);
    const label = h('span', { class: 'visually-hidden' }, t('path.step', { n }));
    if (!entry) {
      const key = PATH_SOON[n - 1];
      if (!key) return null;
      return h('li', { class: 'path__step path__step--soon' }, h('span', { class: 'path__n', 'aria-hidden': 'true' }, String(n)), h('div', { class: 'path__body' }, h('div', { class: 'course-card__head' }, progressBadge(SOON_ICON[key]), h('p', { class: 'path__title' }, label, t(`${key}.title`))), h('p', { class: 'small muted' }, t(`${key}.text`)), h('p', { class: 'path__soon small' }, t('path.soon'))));
    }
    return h('li', { class: 'path__step' }, h('span', { class: 'path__n', 'aria-hidden': 'true' }, String(n)), h('div', { class: 'path__body' }, label, courseCard(entry, courses, 'div', 'path__card', { audience: false })));
  };
  const beginnerOnPath = [...byStep.values()].some((e) => e.audience === 'beginner');
  let skip = null;
  if (beginnerOnPath && deeper) {
    const button = h('button', { type: 'button', class: 'path__skip small' }, t('path.skip'));
    button.addEventListener('click', () => { deeper.focus({ preventScroll: true }); deeper.scrollIntoView({ block: 'start', behavior: 'auto' }); });
    skip = h('p', { class: 'path__skipline' }, button);
  }
  return h(
    'section',
    { class: 'catalog', 'aria-labelledby': 'catalog-title' },
    head,
    h('h3', { class: 'path__heading' }, t('path.heading')),
    skip,
    h('ol', { class: 'path' }, [1, 2, 3].map(stepItem)),
    deeper,
    rest.length ? h('ul', { class: 'catalog__list' }, rest.map((e) => courseCard(e, courses, 'li', '', { audience: !allExperienced }))) : null,
  );
}
