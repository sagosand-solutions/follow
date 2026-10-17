import { CONFIG } from './config.js';
import { checkQuestion, QUESTION_ID } from './question-check.js';
import { parseCatalog, isCourseId, DEFAULT_COURSE } from './catalog.js';

const cache = new Map();
const versionQuery = () => (CONFIG.release ? `?v=${CONFIG.release}` : '');

async function getJson(path) {
  if (cache.has(path)) return cache.get(path);
  const res = await fetch(`${CONFIG.dataPath}${path}${versionQuery()}`, { cache: 'no-cache' });
  if (!res.ok) {
    const err = new Error(`${path}: ${res.status}`);
    err.status = res.status;
    throw err;
  }
  const json = await res.json();
  cache.set(path, json);
  return json;
}

let catalogMemo = null;

export function clearDataCache() {
  cache.clear();
  catalogMemo = null;
}

// Python's derived files keep their original names; every other course has <name>-<id>.json.
const derived = (name, courseId) => (courseId === DEFAULT_COURSE ? `${name}.json` : `${name}-${courseId}.json`);
const badCourse = () => Object.assign(new Error('bad course'), { status: 404 });

export const loadIndex = (courseId = DEFAULT_COURSE) => (isCourseId(courseId) ? getJson(derived('index', courseId)) : Promise.reject(badCourse()));
export const loadCourse = (courseId = DEFAULT_COURSE) => (isCourseId(courseId) ? getJson(`courses/${courseId}.json`) : Promise.reject(badCourse()));

// The course list, or null when there is no usable catalog (the site then shows the Python course alone).
export function loadCatalog() {
  if (!catalogMemo) {
    catalogMemo = getJson('catalog.json').then(parseCatalog, () => null);
  }
  return catalogMemo;
}

// Every course the site knows, as [{ id, course }]; a course file that fails to load is left out.
export async function loadCourses() {
  const catalog = await loadCatalog();
  const ids = catalog ? catalog.map((c) => c.id) : [DEFAULT_COURSE];
  const loaded = await Promise.all(ids.map((id) => loadCourse(id).then((course) => ({ id, course }), () => null)));
  return loaded.filter(Boolean);
}

// Question and lesson ids are globally unique, so one id belongs to one course. Python is searched first.
export async function findCourseOf({ questionId = null, lessonId = null }) {
  const entries = await loadCourses();
  if (!entries.length) throw new Error('no course could be loaded');
  for (const entry of entries) {
    for (const { lesson } of flattenLessons(entry.course)) {
      if (lesson.id === lessonId || (questionId && lesson.questions.includes(questionId))) return entry;
    }
  }
  return null;
}
// Step trace for a question (data/traces/<id>.json). Returns null when there is none (404).
export async function loadTrace(id) {
  if (typeof id !== 'string' || !QUESTION_ID.test(id)) return null;
  try {
    return await getJson(`traces/${id}.json`);
  } catch (err) {
    if (err.status === 404) return null;
    throw err;
  }
}

export const loadConcepts = (courseId = DEFAULT_COURSE) => (isCourseId(courseId) ? getJson(derived('concepts', courseId)) : Promise.reject(badCourse()));

// Study notes: data/study/<concept id with dots as hyphens>.json. A missing file (404) means no notes yet.
export const loadStudy = (conceptId) => getJson(`study/${String(conceptId).replace(/[^a-z0-9-]/gi, '-')}.json`);

export async function loadQuestion(id) {
  if (typeof id !== 'string' || !QUESTION_ID.test(id)) {
    const err = new Error('bad id');
    err.status = 404;
    throw err;
  }
  const path = `questions/${id}.json`;
  const q = await getJson(path);
  const problem = checkQuestion(q, id);
  if (problem) {
    cache.delete(path); // never keep malformed data: a retry fetches it again
    throw new Error(`question ${id}: ${problem}`);
  }
  return q;
}

// Course helpers
export function flattenLessons(course) {
  const out = [];
  for (const level of course.levels || []) {
    for (const lesson of level.lessons || []) out.push({ level, lesson });
  }
  return out;
}

export function findLessonOf(course, questionId) {
  for (const entry of flattenLessons(course)) {
    const pos = entry.lesson.questions.indexOf(questionId);
    if (pos !== -1) return { ...entry, position: pos };
  }
  return null;
}

export function allQuestionIds(course) {
  return flattenLessons(course).flatMap((e) => e.lesson.questions);
}
