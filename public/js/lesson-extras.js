// Optional lesson fields for beginner courses, validated before anything is shown. Pure: no DOM, storage or network.
//   practice_summary  a short plain-text fragment shown as "You practised …" once the lesson is fully answered. It says
//                     what was practised, never what the reader can now do (answers may have been wrong or "Not sure").
//   introductions     [{ id, before_question_ids, term, explanation, example }]: "New word" cards shown above the code of
//                     each listed question (one card can come back before several), every time the question opens (direct
//                     links and reopening included), never hidden by a "seen" flag.
// Anything that does not have exactly the expected shape is left out; a missing field shows nothing.

const INTRO_KEYS = ['before_question_ids', 'example', 'explanation', 'id', 'term'];
const INTRO_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const LIMITS = { term: 60, explanation: 240, example: 240 };
const plain = (value, max) => typeof value === 'string' && value.trim() === value && value.length > 0 && value.length <= max && !/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(value);

export const SUMMARY_MAX = 80;

export function practiceSummary(lesson) {
  return lesson && plain(lesson.practice_summary, SUMMARY_MAX) ? lesson.practice_summary : null;
}

// A non-empty list of different questions, all in this lesson.
const questionList = (list, lesson) => Array.isArray(list) && list.length > 0 && new Set(list).size === list.length && list.every((id) => typeof id === 'string' && lesson.questions.includes(id));

// The lesson's valid introductions, in order. An id used twice is nobody's; a card naming a question outside the lesson is dropped.
export function introductions(lesson) {
  if (!lesson || !Array.isArray(lesson.introductions) || !Array.isArray(lesson.questions)) return [];
  const shaped = lesson.introductions.filter((x) => x && typeof x === 'object' && !Array.isArray(x)
    && Object.keys(x).sort().join() === INTRO_KEYS.join() && typeof x.id === 'string' && INTRO_ID.test(x.id)
    && questionList(x.before_question_ids, lesson) && plain(x.term, LIMITS.term) && plain(x.explanation, LIMITS.explanation) && plain(x.example, LIMITS.example));
  const ids = shaped.map((x) => x.id);
  return shaped.filter((x) => ids.indexOf(x.id) === ids.lastIndexOf(x.id)).map((x) => ({ id: x.id, questionIds: [...x.before_question_ids], term: x.term, explanation: x.explanation, example: x.example }));
}

export const introductionsFor = (lesson, questionId) => introductions(lesson).filter((x) => x.questionIds.includes(questionId));

// Every question of the course has an answer in `isAnswered` (a function of the question id).
export function courseFinished(course, isAnswered) {
  const ids = (course && course.levels ? course.levels : []).flatMap((l) => l.lessons.flatMap((x) => x.questions));
  return ids.length > 0 && ids.every((id) => isAnswered(id));
}
