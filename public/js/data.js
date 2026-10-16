import { CONFIG } from './config.js';
import { checkQuestion, QUESTION_ID } from './question-check.js';

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

export function clearDataCache() {
  cache.clear();
}

export const loadIndex = () => getJson('index.json');
export const loadCourse = () => getJson('courses/python.json');
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

export const loadConcepts = () => getJson('concepts.json');

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
