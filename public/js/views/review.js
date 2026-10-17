import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadCourse } from '../data.js';
import { courseHref, DEFAULT_COURSE } from '../catalog.js';
import { store } from '../progress.js';
import { reviewQueue } from '../review.js';
import { revisitedSummary } from '../moments.js';
import { findLessonOf } from '../data.js';
import { questionView } from './question.js';
import { errorNode } from './errors.js';
import { notFoundView } from './notfound.js';

export async function reviewView({ id = DEFAULT_COURSE } = {}) {
  let course;
  try {
    course = await loadCourse(id);
  } catch (err) {
    if (err.status === 404) return notFoundView({});
    console.error(err);
    return { title: t('error.title'), node: errorNode(t('error.course')) };
  }

  const queue = reviewQueue(course, store.answers());
  if (queue.length === 0) {
    return {
      title: t('review.title'),
      node: h(
        'div',
        { class: 'stack stack--loose review-empty' },
        h('h1', {}, t('review.title')),
        h('p', { class: 'muted' }, t('review.done')),
        ...revisitedLines(course),
        h('a', { class: 'button button--secondary button--block', href: courseHref(course.id) }, t('review.back')),
      ),
    };
  }

  const rawQuery = globalThis.location.hash.split('?')[1] || '';
  const requestedId = new URLSearchParams(rawQuery).get('question');
  const current = queue.find((entry) => entry.questionId === requestedId) || queue[0];
  return questionView({ id: current.questionId, course, reviewMode: true });
}

// "You went back to N questions" and where the latest one lives; nothing when there were no second attempts.
function revisitedLines(course) {
  const { count, lastId } = revisitedSummary(store.answers());
  if (count === 0) return [];
  const lines = [h('p', {}, count === 1 ? t('moment.revisited.one') : t('moment.revisited.many', { n: count }))];
  const where = lastId ? findLessonOf(course, lastId) : null;
  if (where) lines.push(h('p', { class: 'muted' }, t('moment.revisited.last'), ' ', h('a', { href: `#/lesson/${where.lesson.id}` }, where.lesson.title)));
  return lines;
}
