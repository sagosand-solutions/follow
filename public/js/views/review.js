import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadCourse } from '../data.js';
import { store } from '../progress.js';
import { reviewQueue } from '../review.js';
import { questionView } from './question.js';
import { errorNode } from './errors.js';

export async function reviewView() {
  let course;
  try {
    course = await loadCourse();
  } catch (err) {
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
        h('a', { class: 'button button--secondary button--block', href: '#/course' }, t('review.back')),
      ),
    };
  }

  const rawQuery = globalThis.location.hash.split('?')[1] || '';
  const requestedId = new URLSearchParams(rawQuery).get('question');
  const current = queue.find((entry) => entry.questionId === requestedId) || queue[0];
  return questionView({ id: current.questionId, course, reviewMode: true });
}
