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

// Advance through the current queue; if an answer removed the current item,
// continue at the first remaining question. Wrap while unresolved items remain.
export function nextReview(queue, currentId) {
  if (!Array.isArray(queue) || queue.length === 0) return null;
  const index = queue.findIndex((entry) => entry && entry.questionId === currentId);
  return queue[index < 0 ? 0 : (index + 1) % queue.length] || null;
}
