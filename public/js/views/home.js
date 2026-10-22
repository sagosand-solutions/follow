import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadIndex, loadCourses, loadCatalog, loadQuestion, allQuestionIds } from '../data.js';
import { activeCourse, courseHref, reviewHref, freeTotal } from '../catalog.js';
import { catalogSection, firstQuestionOf } from '../components/catalog-card.js';
import { progressBadge } from '../components/course-icons.js';
import { store } from '../progress.js';
import { renderCodeBlock } from '../components/code-block.js';
import { readingMap, summaryText } from '../components/map.js';
import { nextUp } from '../course-nav.js';
import { lastRead } from '../moments.js';
import { reviewQueue } from '../review.js';
import { facts, courseCounts, pluralTemplate } from '../components/ui.js';
import { CONFIG } from '../config.js';

// The headline carries one highlighted phrase; both strings are translated whole.
function headline() {
  const full = t('home.title');
  const mark = t('home.titleMark');
  const at = mark ? full.indexOf(mark) : -1;
  if (at === -1) return [full];
  return [full.slice(0, at), h('span', { class: 'hl' }, mark), full.slice(at + mark.length)];
}

export async function homeView() {
  let index = null;
  let course = null;
  let entries = [];
  try {
    index = await loadIndex();
  } catch {
    /* start button is hidden when no onboarding question is known */
  }
  try {
    entries = await loadCourses();
  } catch {
    /* course link still works and shows its own error */
  }
  const catalog = await loadCatalog();
  // The hero and the map speak about the course the reader touched last (Python until something is answered).
  const active = activeCourse(entries, store.answers());
  course = active ? active.course : null;
  const browseHref = courseHref(active ? active.id : undefined);
  const ids = course ? allQuestionIds(course) : [];
  const answeredCount = ids.filter((id) => store.latest(id)).length;
  // The same Continue rule everywhere: the home link must match the header and the course page.
  const records = store.answers();
  const target = course ? nextUp(course, records) : null;
  const reviewCount = course ? reviewQueue(course, records).length : 0;
  const nextId = target ? target.questionId : null;

  // The real onboarding code is the hero: the visitor sees what they will be reading.
  let stage = null;
  if (index && index.onboarding_question) {
    try {
      const q = await loadQuestion(index.onboarding_question);
      const s = q.snippets[0];
      stage = renderCodeBlock({ source: s.code, language: q.language || 'python', label: s.label || null, highlightLines: [] });
      stage.classList.add('hero-stage');
    } catch {
      stage = null;
    }
  }

  const actions = h('div', { class: 'actions actions--row' });
  const intro = [];
  const after = [];
  if (answeredCount > 0) {
    // The same ring as on the course card, beside the sentence that already says the count (so here it is decoration).
    const welcome = h('p', { class: 'welcome' }, nextId ? t('home.welcomeText', { count: answeredCount, total: ids.length }) : t('home.allDone'));
    intro.push(h('div', { class: 'welcome-row' }, progressBadge(active.id, { answered: answeredCount, total: ids.length }), welcome));
    const where = course ? lastRead(course, records) : null;
    if (where) intro.push(h('p', { class: 'lead' }, t('moment.readingAt', { lesson: where.lessonTitle, n: where.position })));
    if (nextId) actions.append(h('a', { class: 'button button--primary button--block', href: `#/q/${nextId}` }, t('home.continue')));
    actions.append(h('a', { class: nextId ? 'button button--secondary button--block' : 'button button--primary button--block', href: browseHref }, t('home.browse')));
    if (reviewCount > 0) actions.append(h('a', { class: 'button button--secondary button--block', href: reviewHref(active ? active.id : undefined) }, t('review.button', { count: reviewCount })));
  } else {
    intro.push(h('p', { class: 'lead' }, t('home.lead')));
    // A new visitor starts with the beginner course on the path when there is one (and it loads), else the onboarding question.
    const beginner = catalog ? catalog.find((e) => e.audience === 'beginner' && e.pathStep === 1) : null;
    const beginnerCourse = beginner ? entries.find((e) => e.id === beginner.id) : null;
    const firstId = beginnerCourse ? firstQuestionOf(beginner, beginnerCourse.course) : index && index.onboarding_question;
    if (firstId) actions.append(h('a', { class: 'button button--primary button--block', href: `#/q/${firstId}` }, t('home.start')));
    // With a course catalog right below, a second "browse" button would only push the cards down.
    if (!catalog || catalog.length < 2 || !(index && index.onboarding_question)) actions.append(h('a', { class: index && index.onboarding_question ? 'button button--secondary button--block' : 'button button--primary button--block', href: browseHref }, t('home.browse')));
  }
  // Only a storage mode that loses answers needs saying here (each course page says where progress lives, and About's
  // privacy section says it stays in this browser).
  if (!store.persistent) after.push(h('p', { class: 'hero__facts' }, t('home.facts.memory')));

  // Fact pills: the numbers come from the course data; "No account needed" is true only while the account UI is off.
  const counts = courseCounts(course);
  const free = catalog ? freeTotal(catalog) : null;
  const platformPills = catalog && catalog.length > 1 && free !== null;
  const pills = facts(
    platformPills ? [{ template: pluralTemplate(free, t('catalog.free.one'), t('catalog.free')), bold: free }, CONFIG.accountsUi ? null : { template: t('facts.noAccount') }] : [
      { template: pluralTemplate(counts.questions, t('facts.question'), t('facts.questions')), bold: counts.questions || null },
      { template: pluralTemplate(counts.lessons, t('facts.lesson'), t('facts.lessons')), bold: counts.lessons || null },
      CONFIG.accountsUi ? null : { template: t('facts.noAccount') },
    ],
    t('facts.label'),
  );
  const hero = h('div', { class: 'hero' }, h('div', { class: 'hero__text' }, h('h1', {}, ...headline()), ...intro, pills, actions), stage);

  const parts = [hero];
  if (catalog) parts.push(catalogSection(catalog, new Map(entries.map((e) => [e.id, e.course]))));
  // The map is the reader's own: it appears once something is answered (a new visitor sees the courses, not empty squares).
  if (course && answeredCount > 0) {
    parts.push(
      h(
        'section',
        { class: 'map-card', 'aria-labelledby': 'map-title' },
        h('h2', { id: 'map-title' }, t('home.map.title')),
        catalog && catalog.length > 1 ? h('p', { class: 'kicker' }, course.title) : null,
        h('p', { class: 'muted map-intro map-intro--wide' }, t('home.map.some', { summary: summaryText(ids) })),
        // The phone map shows counts, not squares, so it gets a sentence that does not mention squares.
        h('p', { class: 'muted map-intro map-intro--phone' }, t('home.map.some.phone', { summary: summaryText(ids) })),
        readingMap(course, nextId),
      ),
    );
  }
  if (after.length) parts.splice(catalog ? 2 : 1, 0, h('div', { class: 'home-notes stack' }, ...after));
  return { title: t('site.brand'), node: h('div', { class: 'home' }, ...parts) };
}
