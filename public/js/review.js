// Pure review-queue rules. Course and progress records are supplied by callers;
// this module has no DOM, storage, time, or randomness dependencies.
function questionsInOrder(course) {
  if (!course || typeof course !== 'object' || !Array.isArray(course.levels)) return [];
  const out = [];
  for (const level of course.levels) {
    if (!level || !Array.isArray(level.lessons)) continue;
    for (const lesson of level.lessons) {
      if (!lesson || typeof lesson.id !== 'string' || !Array.isArray(lesson.questions)) continue;
      lesson.questions.forEach((questionId, index) => {
        if (typeof questionId !== 'string' || questionId === '') return;
        out.push({
          order: out.length,
          questionId,
          lessonId: lesson.id,
          lessonTitle: typeof lesson.title === 'string' ? lesson.title : '',
          position: index + 1,
          total: lesson.questions.length,
        });
      });
    }
  }
  return out;
}

function latestByQuestion(records) {
  const latest = new Map();
  if (!Array.isArray(records)) return latest;
  for (const record of records) {
    if (!record || typeof record !== 'object' || typeof record.question_id !== 'string' || !record.question_id) continue;
    if (!Number.isInteger(record.attempt) || record.attempt < 1 || !Number.isFinite(record.t)) continue;
    const previous = latest.get(record.question_id);
    if (!previous || record.attempt > previous.attempt ||
        (record.attempt === previous.attempt && record.t >= previous.t)) {
      latest.set(record.question_id, record);
    }
  }
  return latest;
}

// Questions are returned in course order when their latest record is unknown or score 0.
export function reviewQueue(course, records) {
  const latest = latestByQuestion(records);
  return questionsInOrder(course).filter(({ questionId }) => {
    const record = latest.get(questionId);
    return record && (record.outcome === 'unknown' || (record.outcome === 'answered' && record.score === 0));
  });
}

// Advance through the current queue in course order. If the answer removed the current item from the queue, continue
// with the first remaining item AFTER it in course order (needs `course`), wrapping to the first remaining item; without
// `course` it restarts at the first remaining item. Null when nothing is left.
export function nextReview(queue, currentId, course = null) {
  if (!Array.isArray(queue) || queue.length === 0) return null;
  const index = queue.findIndex((entry) => entry && entry.questionId === currentId);
  if (index >= 0) return queue[(index + 1) % queue.length] || null;
  const here = course ? questionsInOrder(course).find((e) => e.questionId === currentId) : null;
  if (!here) return queue[0] || null;
  return queue.find((entry) => entry.order > here.order) || queue[0] || null;
}
