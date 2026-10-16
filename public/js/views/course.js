import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadCourse } from '../data.js';
import { store } from '../progress.js';
import { refresh } from '../router.js';
import { errorNode } from './errors.js';
import { cells, readingMap, summaryText } from '../components/map.js';
import { summaryNode } from '../components/summary.js';

export async function courseView() {
  let course;
  try {
    course = await loadCourse();
  } catch (err) {
    console.error(err);
    return { title: t('error.title'), node: errorNode(t('error.course')) };
  }
  const lessonTitles = new Map();
  for (const level of course.levels) for (const l of level.lessons) lessonTitles.set(l.id, l.title);

  const allIds = course.levels.flatMap((level) => level.lessons.flatMap((l) => l.questions));
  const nextId = allIds.find((id) => !store.latest(id));

  const levels = course.levels.map((level) =>
    h(
      'section',
      { class: 'level' },
      h('div', { class: 'level__head' }, h('p', { class: 'kicker' }, t(`level.${level.level}`)), h('h2', {}, level.title)),
      h(
        'ol',
        { class: 'lessons' },
        level.lessons.map((lesson) => {
          const answered = lesson.questions.filter((id) => store.latest(id)).length;
          const status = answered ? t('course.lesson.progress', { answered, total: lesson.questions.length }) : t('course.lesson.notStarted');
          const builds = (lesson.builds_on || []).map((id) => lessonTitles.get(id)).filter(Boolean);
          return h(
            'li',
            {},
            h(
              'a',
              { class: 'lesson-link', href: `#/lesson/${lesson.id}` },
              h('span', { class: 'lesson-link__title' }, lesson.title),
              h('span', { class: 'lesson-link__status' }, status),
              summaryNode(lesson.summary, 'lesson-link__summary', 'span'),
              cells(lesson.questions, nextId),
              h('span', { class: 'visually-hidden' }, summaryText(lesson.questions)),
            ),
            builds.length ? h('p', { class: 'muted small' }, t('course.lesson.buildsOn', { names: builds.join(', ') })) : null,
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
    const result = store.importJson(await file.text());
    message.textContent = result.ok ? (result.imported ? t('course.import.ok', { count: result.imported }) : t('course.import.none')) : t(`course.import.error.${result.error}`);
    fileInput.value = '';
    if (result.ok && result.imported) refresh();
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
      h('button', { type: 'button', class: 'button button--primary', onclick: () => { store.reset(); dialog.close(); refresh(); } }, t('course.reset.confirm')),
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

  return {
    title: t('course.title'),
    node: h('div', { class: 'stack stack--loose' }, h('div', { class: 'course-head' }, h('h1', {}, course.title || t('course.title')), h('p', { class: 'lead' }, t('course.lead'))), h('section', { class: 'map-card', 'aria-labelledby': 'map-title' }, h('h2', { id: 'map-title' }, t('home.map.title')), readingMap(course, nextId)), ...levels, limits),
  };
}
