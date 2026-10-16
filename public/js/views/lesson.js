import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadCourse, loadQuestion, loadConcepts, flattenLessons } from '../data.js';
import { store } from '../progress.js';
import { errorNode } from './errors.js';
import { currentAnswer } from '../answers.js';
import { cells } from '../components/map.js';
import { eyebrow, facts, pluralTemplate } from '../components/ui.js';
import { summaryNode } from '../components/summary.js';
import { lessonEnding } from './lesson-end.js';
import { reviewQueue } from '../review.js';

function statusOf(answer) {
  if (!answer) return t('lesson.status.none');
  if (answer.outcome === 'unknown') return t('lesson.status.unknown');
  return t(`lesson.status.${answer.score}`);
}

export async function lessonView({ id }) {
  let course;
  try {
    course = await loadCourse();
  } catch (err) {
    console.error(err);
    return { title: t('error.title'), node: errorNode(t('error.course')) };
  }
  const entry = flattenLessons(course).find((e) => e.lesson.id === id);
  if (!entry) {
    return { title: t('notfound.title'), node: h('div', { class: 'stack' }, h('h1', {}, t('notfound.title')), h('p', {}, t('notfound.text')), h('p', {}, h('a', { href: '#/course' }, t('notfound.course')))) };
  }
  const { lesson } = entry;
  let questions = null;
  try {
    questions = await Promise.all(lesson.questions.map((qid) => loadQuestion(qid)));
  } catch (err) {
    console.error(err);
  }
  // Statuses come from the question as it is now, so a changed question never shows an outdated verdict.
  const latest = lesson.questions.map((qid, i) => {
    const record = store.latest(qid);
    if (!record) return null;
    if (!questions) return record;
    const cur = currentAnswer(questions[i], record);
    return cur ? { outcome: cur.outcome, score: cur.score } : null;
  });
  const answered = latest.filter(Boolean);

  const list = h(
    'ol',
    { class: 'lessons lessons--questions' },
    lesson.questions.map((qid, i) =>
      h('li', {}, h('a', { class: 'lesson-link', href: `#/q/${qid}` }, h('span', { class: 'lesson-link__title' }, t('q.position', { n: i + 1, total: lesson.questions.length })), h('span', { class: 'lesson-link__status' }, statusOf(latest[i])))),
    ),
  );

  const parts = [h('div', { class: 'course-head' }, eyebrow(`${t(`level.${entry.level.level}`)} · ${entry.level.title}`), h('h1', {}, lesson.title), summaryNode(lesson.summary, 'lead lesson-summary'), facts([{ template: pluralTemplate(lesson.questions.length, t('facts.question'), t('facts.questions')), bold: lesson.questions.length || null }], t('facts.label')), cells(lesson.questions)), h('section', { class: 'stack' }, h('h2', {}, t('lesson.questions')), list)];

  if (questions && answered.length === lesson.questions.length) {
    const full = answered.filter((a) => a.outcome === 'answered' && a.score === 1).length;
    const partial = answered.filter((a) => a.outcome === 'answered' && a.score === 0.5).length;
    const wrong = answered.filter((a) => a.outcome === 'answered' && a.score === 0).length;
    const unsure = answered.filter((a) => a.outcome === 'unknown').length;
    const again = h('div', {}, h('p', { class: 'muted' }, t('lesson.lookAgainNone')));
    try {
      const concepts = await loadConcepts().catch(() => null);
      const seen = new Map();
      questions.forEach((q, i) => {
        const cur = currentAnswer(q, store.latest(q.id));
        if (!cur || cur.outcome !== 'answered') return;
        const opt = cur.option;
        if (opt.score < 1 && opt.misconception) {
          const m = concepts && concepts.misconceptions.find((x) => x.id === opt.misconception);
          if (!seen.has(opt.misconception + q.id)) seen.set(opt.misconception + q.id, { qid: q.id, n: i + 1, title: m ? m.title : opt.misconception });
        }
      });
      if (seen.size) {
        again.replaceChildren(
          h('ul', { class: 'again' }, [...seen.values()].map((s) => h('li', {}, h('a', { href: `#/q/${s.qid}` }, `${t('q.position', { n: s.n, total: lesson.questions.length })}: ${s.title}`)))),
        );
      }
    } catch (err) {
      console.error(err);
    }
    parts.push(
      h('section', { class: 'summary' }, h('h2', {}, t('lesson.summary')), h('p', {}, t('lesson.answeredOf', { answered: answered.length, total: lesson.questions.length })), facts([{ template: t('lesson.count.full'), bold: full }, { template: t('lesson.count.partial'), bold: partial }, { template: t('lesson.count.wrong'), bold: wrong }, { template: t('lesson.count.unsure'), bold: unsure }].filter((x) => x.bold > 0), t('lesson.count.label')), h('h3', {}, t('lesson.lookAgain')), again),
    );
    // The lesson is finished: its cell strip closes into one bar and the heading says so.
    if (parts[0] && parts[0].classList) {
      parts[0].classList.add('lesson-done');
      const heading = parts[0].querySelector('h1');
      if (heading) heading.insertAdjacentElement('afterend', h('p', { class: 'lesson-done__line' }, t('moment.lessonDone')));
    }
    // End of a finished lesson: where to go next and what builds on this lesson.
    const { next, buildsOn } = lessonEnding(course, lesson.id);
    const reviewCount = reviewQueue(course, store.answers()).length;
    parts.push(
      h(
        'section',
        { class: 'lesson-next' },
        next ? h('a', { class: 'button button--primary button--block', href: `#/lesson/${next.id}` }, t('lesson.next', { title: next.title })) : h('p', {}, t('lesson.endOfCourse')),
        reviewCount > 0 ? h('a', { class: 'button button--secondary button--block', href: '#/review' }, t('review.button', { count: reviewCount })) : null,
        buildsOn.length ? h('h3', {}, t('lesson.buildsOnTitle')) : null,
        buildsOn.length ? h('ul', {}, buildsOn.map((l) => h('li', {}, h('a', { href: `#/lesson/${l.id}` }, l.title)))) : null,
      ),
    );
  }
  parts.push(h('p', {}, h('a', { href: '#/course' }, t('lesson.back'))));
  return { title: lesson.title, node: h('div', { class: 'stack stack--loose' }, ...parts) };
}
