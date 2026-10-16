// Pure navigation rules shared by the header, the course page and the home page.
// No DOM, no storage, no Date and no randomness: callers pass the parsed course
// and the stored answer records and get a plain target back.

function lessonsInOrder(course) {
  if (!course || typeof course !== 'object' || !Array.isArray(course.levels)) return [];
  const out = [];
  for (const level of course.levels) {
    if (!level || !Array.isArray(level.lessons)) continue;
    for (const lesson of level.lessons) {
      if (lesson && typeof lesson.id === 'string' && Array.isArray(lesson.questions)) out.push(lesson);
    }
  }
  return out;
}

// One entry per question, in course order (levels -> lessons -> questions).
function questionsInOrder(course) {
  const out = [];
  for (const lesson of lessonsInOrder(course)) {
    lesson.questions.forEach((questionId, i) => {
      out.push({ questionId, lessonId: lesson.id, lessonTitle: lesson.title, position: i + 1, total: lesson.questions.length });
    });
  }
  return out;
}

// Newest finite timestamp per question id. Malformed records and non-finite
// timestamps are ignored; unknown ids are kept here and filtered by the caller.
function latestTimes(records) {
  const best = new Map();
  if (!Array.isArray(records)) return best;
  for (const record of records) {
    if (!record || typeof record !== 'object') continue;
    const id = record.question_id;
    if (typeof id !== 'string' || id === '') continue;
    if (!Number.isFinite(record.t)) continue;
    const previous = best.get(id);
    if (previous === undefined || record.t > previous) best.set(id, record.t);
  }
  return best;
}

// True when at least one stored record belongs to a question in this course.
export function hasProgress(course, records) {
  const ids = new Set(questionsInOrder(course).map((entry) => entry.questionId));
  if (!ids.size) return false;
  for (const id of latestTimes(records).keys()) if (ids.has(id)) return true;
  return false;
}

// The next place to read: the first unanswered question after the last answered
// one in course order, or the first unanswered question in the course when there
// is no such question. Returns null when the course has no questions or every
// question is answered. Ties on the newest timestamp go to the later question in
// course order.
export function nextUp(course, records) {
  const order = questionsInOrder(course);
  if (!order.length) return null;
  const times = latestTimes(records);

  let lastIndex = -1;
  let lastTime = -Infinity;
  let answered = 0;
  order.forEach((entry, i) => {
    const time = times.get(entry.questionId);
    if (time === undefined) return;
    answered += 1;
    if (time > lastTime || (time === lastTime && i > lastIndex)) {
      lastTime = time;
      lastIndex = i;
    }
  });
  if (answered === order.length) return null;

  const afterLast = lastIndex === -1 ? null : order.find((entry, i) => i > lastIndex && !times.has(entry.questionId));
  const target = afterLast || order.find((entry) => !times.has(entry.questionId));
  if (!target) return null;
  return {
    questionId: target.questionId,
    lessonId: target.lessonId,
    lessonTitle: target.lessonTitle,
    position: target.position,
    total: target.total,
    reason: afterLast ? 'after-last' : 'first-unanswered',
  };
}

// The next lesson in course order, or null for the last lesson or an unknown id.
export function nextLesson(course, lessonId) {
  const lessons = lessonsInOrder(course);
  const index = lessons.findIndex((lesson) => lesson.id === lessonId);
  if (index === -1 || index + 1 >= lessons.length) return null;
  const next = lessons[index + 1];
  return { id: next.id, title: next.title };
}

// Lessons whose builds_on lists this lesson, in course order.
export function buildsOnThis(course, lessonId) {
  return lessonsInOrder(course)
    .filter((lesson) => Array.isArray(lesson.builds_on) && lesson.builds_on.includes(lessonId))
    .map((lesson) => ({ id: lesson.id, title: lesson.title }));
}
