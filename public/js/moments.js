// Rules for the small "moments" along the path. Pure: no DOM, no storage, no clock. Every moment only reports
// what the records say; nothing here claims mastery.

const validTime = (r) => r && typeof r === 'object' && typeof r.question_id === 'string' && Number.isFinite(r.t);

// "This time it fits": a question whose previous answer was "not sure" or wrong now has a fully right answer.
// Returns { kind: 'wrong', optionId } or { kind: 'unknown' } for the earlier answer, else null.
export function changedToFit(previous, next) {
  if (!previous || !next || next.outcome !== 'answered' || next.score !== 1) return null;
  if (previous.outcome === 'unknown') return { kind: 'unknown' };
  if (previous.outcome === 'answered' && previous.score === 0 && typeof previous.option_id === 'string') return { kind: 'wrong', optionId: previous.option_id };
  return null;
}

// Where the learner was reading: the lesson and 1-based position of the most recently answered question.
// course: parsed course file; records: store.answers(). Null when nothing valid was answered.
export function lastRead(course, records) {
  if (!Array.isArray(records) || !course || !Array.isArray(course.levels)) return null;
  const answered = records.filter(validTime);
  if (!answered.length) return null;
  const known = new Map();
  for (const level of course.levels) {
    for (const lesson of level.lessons || []) {
      (lesson.questions || []).forEach((id, i) => known.set(id, { lessonId: lesson.id, lessonTitle: lesson.title, position: i + 1 }));
    }
  }
  let best = null;
  for (const r of answered) {
    if (!known.has(r.question_id)) continue;
    if (!best || r.t >= best.t) best = r;
  }
  return best ? { questionId: best.question_id, ...known.get(best.question_id) } : null;
}

// How many questions were answered more than once ("went back to"), and the most recent of them.
export function revisitedSummary(records) {
  if (!Array.isArray(records)) return { count: 0, lastId: null };
  const again = records.filter((r) => validTime(r) && Number.isInteger(r.attempt) && r.attempt >= 2);
  const ids = new Set(again.map((r) => r.question_id));
  let last = null;
  for (const r of again) if (!last || r.t >= last.t) last = r;
  return { count: ids.size, lastId: last ? last.question_id : null };
}

// First run in the step player: how many steps play by themselves, and when.
export const FIRST_RUN_STEPS = 3;
export const FIRST_RUN_INTERVAL_MS = 900;
