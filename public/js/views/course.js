import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadCourse, loadCatalog } from '../data.js';
import { reviewHref, DEFAULT_COURSE } from '../catalog.js';
import { premiumNote, alreadyCodeSign, firstQuestionOf } from '../components/catalog-card.js';
import { practisedLine, courseEndCard } from '../components/beginner.js';
import { practiceSummary, courseFinished } from '../lesson-extras.js';
import { store } from '../progress.js';
import { readImportFile } from '../import-file.js';
import { refresh } from '../router.js';
import { errorNode } from './errors.js';
import { notFoundView } from './notfound.js';
import { cells, readingMap, summaryText } from '../components/map.js';
import { summaryNode } from '../components/summary.js';
import { eyebrow, facts, courseCounts, pluralTemplate } from '../components/ui.js';
import { nextUp, hasProgress, readState, homeShortcuts } from '../course-nav.js';
import { reviewQueue } from '../review.js';

export async function courseView({ id = DEFAULT_COURSE } = {}) {
  let course;
  try {
    course = await loadCourse(id);
  } catch (err) {
    if (err.status === 404) return notFoundView({});
    console.error(err);
    return { title: t('error.title'), node: errorNode(t('error.course')) };
  }
  const catalog = await loadCatalog();
  const entry = catalog ? catalog.find((c) => c.id === id) : null;
  const lessonTitles = new Map();
  for (const level of course.levels) for (const l of level.lessons) lessonTitles.set(l.id, l.title);
  // "Builds on" is only said when it is not simply the lesson right before (the list already shows that order).
  const order = course.levels.flatMap((level) => level.lessons.map((l) => l.id));
  const buildsOn = (lesson) => {
    const ids = (lesson.builds_on || []).filter((x) => lessonTitles.has(x));
    return ids.length === 1 && ids[0] === order[order.indexOf(lesson.id) - 1] ? [] : ids.map((x) => lessonTitles.get(x));
  };
  // One level named like the course needs no heading of its own on screen.
  const quietLevel = (level) => course.levels.length === 1 && level.title === course.title;

  const records = store.answers();
  const target = hasProgress(course, records) ? nextUp(course, records) : null;
  const reviewCount = reviewQueue(course, records).length;
  const reviewAction = reviewCount > 0
    ? h('div', { class: 'actions actions--row' }, h('a', { class: 'button button--secondary button--block', href: reviewHref(id) }, t('review.button', { count: reviewCount })))
    : null;
  const nextId = target ? target.questionId : null;

  const levels = course.levels.map((level) =>
    h(
      'section',
      { class: 'level' },
      h('div', { class: quietLevel(level) ? 'level__head visually-hidden' : 'level__head' }, h('p', { class: 'kicker' }, t(`level.${level.level}`)), h('h2', {}, level.title)),
      h(
        'ol',
        { class: 'lessons' },
        level.lessons.map((lesson) => {
          const answered = lesson.questions.filter((id) => store.latest(id)).length;
          const state = readState(answered, lesson.questions.length);
          // The cells already show a lesson not started, so only progress is said in words.
          const status = state === 'none' ? '' : t('course.lesson.progress', { answered, total: lesson.questions.length });
          const builds = buildsOn(lesson);
          return h(
            'li',
            { class: 'lesson-card' },
            h(
              'a',
              { class: 'lesson-card__link', href: `#/lesson/${lesson.id}`, 'aria-labelledby': `card-title-${lesson.id} card-status-${lesson.id}` },
              h('span', { class: 'lesson-card__title', id: `card-title-${lesson.id}` }, lesson.title),
              h('span', { class: 'lesson-card__status', id: `card-status-${lesson.id}` }, status),
              summaryNode(lesson.summary, 'lesson-card__summary', 'span'),
              state === 'all' ? practisedLine(practiceSummary(lesson), 'practised lesson-card__practised', 'span') : null,
              cells(lesson.questions, nextId),
              h('span', { class: 'visually-hidden' }, summaryText(lesson.questions)),
            ),
            builds.length ? h('p', { class: 'lesson-card__builds muted small' }, t('course.lesson.buildsOn', { names: builds.join(', ') })) : null,
          );
        }),
      ),
    ),
  );

  const message = h('p', { class: 'small', role: 'status' });
  const fileInput = h('input', { type: 'file', accept: 'application/json,.json', class: 'visually-hidden', tabindex: '-1', 'aria-hidden': 'true' });
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    const read = await readImportFile(file);
    if (!read.ok) {                                  // refused before reading, or unreadable: no store change
      message.textContent = t(`course.import.error.${read.error}`);
      fileInput.value = '';
      return;
    }
    const result = store.importJson(read.text);
    message.textContent = result.ok ? (result.imported ? t('course.import.ok', { count: result.imported }) : t('course.import.none')) : t(`course.import.error.${result.error}`);
    fileInput.value = '';
    if (result.ok && result.imported) {
      refresh();
      document.dispatchEvent(new Event('progresschange'));
    }
  });

  const dialog = h(
    'dialog',
    { class: 'dialog', 'aria-labelledby': 'reset-title' },
    h('h2', { id: 'reset-title' }, t('course.reset.confirmTitle')),
    h('p', {}, t('course.reset.confirmText')),
    h(
      'div',
      { class: 'actions actions--row' },
      h('button', { type: 'button', class: 'button button--secondary', autofocus: true, onclick: () => dialog.close() }, t('course.reset.cancel')),
      h('button', { type: 'button', class: 'button button--primary', onclick: () => { store.reset(); dialog.close(); refresh(); document.dispatchEvent(new Event('progresschange')); } }, t('course.reset.confirm')),
    ),
  );

  const exportButton = h('button', {
    type: 'button',
    class: 'button button--secondary button--small',
    onclick: () => {
      const blob = new Blob([store.exportJson()], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = h('a', { href: url, download: 'reading-progress.json' });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  }, t('course.export'));

  const limits = h(
    'section',
    { class: 'banner' },
    h('h2', { class: 'banner__title' }, t('course.limits.title')),
    h('p', {}, store.persistent ? t('course.limits.text') : t('course.limits.memory')),
    h(
      'div',
      { class: 'actions actions--row' },
      exportButton,
      h('button', { type: 'button', class: 'button button--secondary button--small', onclick: () => fileInput.click() }, t('course.import')),
      h('button', { type: 'button', class: 'button button--secondary button--small', onclick: () => dialog.showModal() }, t('course.reset')),
    ),
    message,
    fileInput,
    dialog,
  );

  // Python-only start points for readers who already know some Python; shown until something is answered.
  const picks = id === DEFAULT_COURSE && !hasProgress(course, records) ? homeShortcuts(course) : [];
  const shortcuts = picks.length
    ? h('details', { class: 'hero__shortcuts' }, h('summary', {}, t('home.shortcuts.lead')), h('div', { class: 'hero__shortcut-links' }, ...picks.map((s) => h('a', { href: `#/lesson/${s.id}` }, t('home.shortcuts.start', { title: s.title })))))
    : null;
  // A finished beginner course says so first, with the next step on the path.
  const ending = courseFinished(course, (qid) => !!store.latest(qid)) ? await courseEndCard(course, entry, catalog) : null;
  const continueText = target ? t('course.continue.text', { lesson: target.lessonTitle, n: target.position, total: target.total }) : null;
  // Before the first answer the way forward is one button to the course's first question (Continue takes over after that).
  const first = !hasProgress(course, records) ? firstQuestionOf(entry, course) : null;
  const startAction = first ? h('div', { class: 'actions actions--row' }, h('a', { class: 'button button--primary', href: `#/q/${first}` }, t('course.start'))) : null;
  // The map is the reader's own record: it appears once something is answered (the lesson cards show their cells before).
  const mapCard = hasProgress(course, records) ? h('section', { class: 'map-card', 'aria-labelledby': 'map-title' }, h('h2', { id: 'map-title' }, t('home.map.title')), readingMap(course, nextId)) : null;
  const continueCard = target
    ? h(
        'section',
        { class: 'continue-card', 'aria-label': t('continue.title') },
        // The button says Continue; the card only says where (the button's own name keeps both for assistive technology).
        h('p', { class: 'continue-card__where' }, t('course.continue.where', { lesson: target.lessonTitle, n: target.position, total: target.total })),
        h('a', { class: 'button button--primary', href: `#/q/${target.questionId}`, 'aria-label': continueText }, t('continue.title')),
      )
    : null;

  return {
    title: course.title || t('course.title'),
    node: h('div', { class: 'stack stack--loose' }, h('div', { class: 'course-head' }, eyebrow(entry && entry.audience === 'beginner' ? (entry.premium ? t('course.eyebrow.beginner') : t('course.eyebrow.beginnerFree')) : id === DEFAULT_COURSE ? t('course.eyebrow') : t('course.eyebrow.other')), h('h1', {}, course.title || t('course.title')), h('p', { class: 'lead' }, entry && entry.description ? entry.description : t('course.lead')), (() => { const c = courseCounts(course); return facts([{ template: pluralTemplate(c.questions, t('facts.question'), t('facts.questions')), bold: c.questions || null }, { template: pluralTemplate(c.lessons, t('facts.lesson'), t('facts.lessons')), bold: c.lessons || null }, c.levels > 1 ? { template: t('facts.levels'), bold: c.levels } : null], t('facts.label')); })(), startAction), entry && entry.audience === 'beginner' ? alreadyCodeSign(catalog) : null, ending, continueCard, reviewAction, shortcuts, entry ? premiumNote(entry) : null, mapCard, ...levels, limits),
  };
}
