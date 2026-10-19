// Pure catalog and multi-course rules. No DOM, storage or network: callers pass parsed JSON and answer records.
import { nextUp, hasProgress } from './course-nav.js';

const COURSE_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const DEFAULT_COURSE = 'python';

const count = (n) => (Number.isInteger(n) && n >= 0 ? n : null);
const AUDIENCES = ['beginner', 'experienced'];
const PATH_STEPS = [1, 2, 3];

// data/catalog.json -> a clean list, or null when the file is unusable (callers then behave as a one-course site).
// Entries with a bad id, title or description are skipped; the premium status is kept only when it is a known one.
// `audience` (beginner | experienced) and `path_step` (1-3, an integer used by one course only) are optional and kept only
// when valid: an unknown audience is no audience, never one guessed from a name or a level, and a step claimed twice is
// nobody's step.
export function parseCatalog(raw) {
  if (!raw || typeof raw !== 'object' || raw.data_version !== 1 || !Array.isArray(raw.courses)) return null;
  const seen = new Set();
  const courses = [];
  for (const c of raw.courses) {
    if (!c || typeof c !== 'object' || typeof c.id !== 'string' || !COURSE_ID.test(c.id) || seen.has(c.id)) continue;
    if (typeof c.title !== 'string' || !c.title || typeof c.description !== 'string') continue;
    seen.add(c.id);
    const premium = c.premium && c.premium.status === 'coming-soon' ? { status: 'coming-soon', questions: count(c.premium.questions) } : null;
    courses.push({
      id: c.id, title: c.title, description: c.description, free: count(c.free_questions), premium, start: typeof c.start_question === 'string' ? c.start_question : null,
      audience: AUDIENCES.includes(c.audience) ? c.audience : null, pathStep: PATH_STEPS.includes(c.path_step) ? c.path_step : null,
    });
  }
  const claims = new Map();
  for (const c of courses) if (c.pathStep !== null) claims.set(c.pathStep, (claims.get(c.pathStep) || 0) + 1);
  for (const c of courses) if (claims.get(c.pathStep) > 1) c.pathStep = null;
  return courses.length ? courses : null;
}

export const isCourseId = (id) => typeof id === 'string' && COURSE_ID.test(id);

// Python keeps its original addresses; other courses live under /course/<id>.
export const courseHref = (id = DEFAULT_COURSE) => (id === DEFAULT_COURSE ? '#/course' : `#/course/${id}`);
export function reviewHref(id = DEFAULT_COURSE, questionId = null) {
  const base = id === DEFAULT_COURSE ? '#/review' : `#/course/${id}/review`;
  return questionId ? `${base}?question=${encodeURIComponent(questionId)}` : base;
}

// Free questions across the catalog, for sentences that must not hardcode a number. Null when any course lacks a count.
export function freeTotal(courses) {
  if (!Array.isArray(courses) || !courses.length) return null;
  let sum = 0;
  for (const c of courses) {
    if (c.free === null) return null;
    sum += c.free;
  }
  return sum;
}

// The newest answer time among a course's questions, or -Infinity.
function latestTime(course, records) {
  const ids = new Set();
  for (const level of course.levels || []) for (const lesson of level.lessons || []) for (const id of lesson.questions || []) ids.add(id);
  let best = -Infinity;
  for (const r of Array.isArray(records) ? records : []) {
    if (r && ids.has(r.question_id) && Number.isFinite(r.t) && r.t > best) best = r.t;
  }
  return best;
}

// entries: [{ id, course }]. The course the reader touched last decides where Continue goes; a course with nothing
// left to read is skipped. Returns nextUp() of that course plus its id, or null.
export function continueAcross(entries, records) {
  const ranked = (entries || [])
    .filter((e) => e && e.course && hasProgress(e.course, records))
    .map((e, order) => ({ e, time: latestTime(e.course, records), order }))
    .sort((a, b) => b.time - a.time || a.order - b.order);
  for (const { e } of ranked) {
    const target = nextUp(e.course, records);
    if (target) return { ...target, courseId: e.id };
  }
  return null;
}

// The course whose progress is newest, or the default: the home page speaks about this one.
export function activeCourse(entries, records) {
  const list = (entries || []).filter((e) => e && e.course);
  let best = null;
  let bestTime = -Infinity;
  for (const e of list) {
    const time = hasProgress(e.course, records) ? latestTime(e.course, records) : -Infinity;
    if (time > bestTime) {
      best = e;
      bestTime = time;
    }
  }
  return best || list.find((e) => e.id === DEFAULT_COURSE) || list[0] || null;
}
