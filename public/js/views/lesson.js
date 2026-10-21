import { h } from '../dom.js';
import { t } from '../i18n.js';
import { findCourseOf, loadQuestion, loadConcepts, flattenLessons, loadAnswerProfiles, loadCatalog } from '../data.js';
import { courseHref, reviewHref } from '../catalog.js';
import { store } from '../progress.js';
import { errorNode } from './errors.js';
import { currentAnswer } from '../answers.js';
import { cells } from '../components/map.js';
import { eyebrow, facts } from '../components/ui.js';
import { summaryNode } from '../components/summary.js';
import { lessonEnding } from './lesson-end.js';
import { practisedLine, courseEndCard } from '../components/beginner.js';
import { practiceSummary, courseFinished } from '../lesson-extras.js';
import { reviewQueue } from '../review.js';
import { CONFIG } from '../config.js';

function statusOf(answer) {
  // On screen the cells already show what is not answered yet; a screen reader hears it with the row.
  if (!answer) return h('span', { class: 'visually-hidden' }, t('lesson.status.none'));
  if (answer.outcome === 'unknown') return t('lesson.status.unknown');
  return t(`lesson.status.${answer.score}`);
}

export async function lessonView({ id }) {
  let course = null;
  try {
    const found = await findCourseOf({ lessonId: id });
    course = found ? found.course : null;
  } catch (err) {
    console.error(err);
    return { title: t('error.title'), node: errorNode(t('error.course')) };
  }
  const entry = course ? flattenLessons(course).find((e) => e.lesson.id === id) : null;
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

  const parts = [h('div', { class: 'course-head' }, eyebrow(`${t(`level.${entry.level.level}`)} · ${entry.level.title}`), h('h1', {}, lesson.title), summaryNode(lesson.summary, 'lead lesson-summary'), cells(lesson.questions)), h('section', { class: 'stack' }, h('h2', {}, t('lesson.questions')), list)];

  if (questions && answered.length === lesson.questions.length) {
    const full = answered.filter((a) => a.outcome === 'answered' && a.score === 1).length;
    const partial = answered.filter((a) => a.outcome === 'answered' && a.score === 0.5).length;
    const wrong = answered.filter((a) => a.outcome === 'answered' && a.score === 0).length;
    const unsure = answered.filter((a) => a.outcome === 'unknown').length;
    const again = h('div', {}, h('p', { class: 'muted' }, t('lesson.lookAgainNone')));
    try {
      const concepts = await loadConcepts(course.id).catch(() => null);
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
      const practised = practisedLine(practiceSummary(lesson), 'practised lesson-done__practised');
      if (practised) parts[0].append(practised);
    }
    // End of a finished lesson: where to go next and what builds on this lesson.
    const { next, buildsOn } = lessonEnding(course, lesson.id);
    // The result moment sits at the end of the course's last lesson once every question has a first try.
    const scoring = CONFIG.scoringUi && !next ? await import('../components/result-card.js') : null;
    // The validated answer profiles of the course: without them (or for a practice-only course) there is no result moment.
    const profiles = scoring ? await loadAnswerProfiles(course.id) : null;
    if (scoring && scoring.courseFinished(course, store.answers(), profiles)) {
      // "Right now" is checked against every question of the course as it is today (null when one cannot load).
      const all = flattenLessons(course).flatMap((e) => e.lesson.questions);
      const loaded = await Promise.all(all.map((qid) => loadQuestion(qid).catch(() => null)));
      const byId = new Map(all.map((qid, i) => [qid, loaded[i]]));
      const isRightNow = loaded.every(Boolean)
        ? (r) => {
            const cur = currentAnswer(byId.get(r.question_id), r);
            return !!cur && cur.outcome === 'answered' && cur.score === 1;
          }
        : null;
      parts.push(scoring.resultCard(course, store.answers(), isRightNow, profiles));
    }
    // The last lesson of a finished beginner course ends with the course-end card.
    if (!next && courseFinished(course, (qid) => !!store.latest(qid))) {
      const catalog = await loadCatalog();
      const ending = await courseEndCard(course, catalog ? catalog.find((c) => c.id === course.id) : null, catalog);
      if (ending) parts.push(ending);
    }
    const reviewCount = reviewQueue(course, store.answers()).length;
    parts.push(
      h(
        'section',
        { class: 'lesson-next' },
        next ? h('a', { class: 'button button--primary button--block', href: `#/lesson/${next.id}` }, t('lesson.next', { title: next.title })) : h('p', {}, t('lesson.endOfCourse')),
        reviewCount > 0 ? h('a', { class: 'button button--secondary button--block', href: reviewHref(course.id) }, t('review.button', { count: reviewCount })) : null,
        buildsOn.length ? h('h3', {}, t('lesson.buildsOnTitle')) : null,
        buildsOn.length ? h('ul', {}, buildsOn.map((l) => h('li', {}, h('a', { href: `#/lesson/${l.id}` }, l.title)))) : null,
      ),
    );
  }
  parts.push(h('p', {}, h('a', { href: courseHref(course.id) }, t('lesson.back'))));
  return { title: lesson.title, node: h('div', { class: 'stack stack--loose' }, ...parts) };
}
