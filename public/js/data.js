import { CONFIG } from './config.js';

const cache = new Map();

async function getJson(path) {
  if (cache.has(path)) return cache.get(path);
  const res = await fetch(`${CONFIG.dataPath}${path}`, { cache: 'no-cache' });
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
export const loadConcepts = () => getJson('concepts.json');

const ID = /^[a-z][a-z0-9-]{2,79}$/;

export async function loadQuestion(id) {
  if (!ID.test(id)) {
    const err = new Error('bad id');
    err.status = 404;
    throw err;
  }
  const q = await getJson(`questions/${id}.json`);
  validateQuestion(q, id);
  return q;
}

// Defensive client-side check: refuse to render a question that does not match what the view relies on.
export function validateQuestion(q, id) {
  const bad = (why) => {
    throw new Error(`question ${id}: ${why}`);
  };
  if (!q || typeof q !== 'object') bad('not an object');
  if (q.schema_version !== '0.1') bad('unsupported schema_version');
  if (q.id !== id) bad('id mismatch');
  if (typeof q.prompt !== 'string' || !q.prompt.trim()) bad('prompt');
  if (!Array.isArray(q.snippets) || q.snippets.length === 0) bad('snippets');
  if (!Array.isArray(q.options) || q.options.length < 2) bad('options');
  for (const s of q.snippets) if (typeof s.code !== 'string' || typeof s.id !== 'string') bad('snippet');
  const ids = new Set();
  for (const o of q.options) {
    if (typeof o.id !== 'string' || typeof o.text !== 'string' || typeof o.feedback !== 'string') bad('option fields');
    if (![0, 0.5, 1].includes(o.score)) bad('option score');
    if (ids.has(o.id)) bad('duplicate option id');
    ids.add(o.id);
  }
  if (q.options.filter((o) => o.score === 1).length !== 1) bad('exactly one correct option');
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
