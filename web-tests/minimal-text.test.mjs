import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { text } from './support/dom-stub.mjs';

// Only text that helps the reader start or understand: the pages a new visitor sees, and what appears once they answer.
const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const site = new URL('../public/data/', import.meta.url);
const read = (path) => JSON.parse(readFileSync(new URL(path, site), 'utf8'));
const catalog = read('catalog.json');
const B1 = catalog.courses.find((c) => c.id === 'read-like-a-computer');
const b1Course = read(`courses/${B1.id}.json`);

globalThis.fetch = async (url) => {
  const path = String(url).replace(/^(.*\/)?data\//, '').replace(/\?.*$/, '');
  let body = null;
  try { body = read(path); } catch { body = null; }
  return body ? { ok: true, status: 200, async json() { return body; } } : { ok: false, status: 404, async json() { return {}; } };
};

const { setMessages } = await import('../public/js/i18n.js');
setMessages(strings);
const { store } = await import('../public/js/progress.js');
const { courseView } = await import('../public/js/views/course.js');
const { lessonView } = await import('../public/js/views/lesson.js');
const { aboutView } = await import('../public/js/views/about.js');
const { homeView } = await import('../public/js/views/home.js');

const byClass = (node, cls) => node.all((n) => n.className.split(' ').includes(cls));
const answerFirst = () => store.recordAnswer({ questionId: B1.start_question, optionId: 'x', score: 1 });

test('home for a new visitor: the headline, two facts and one way in; no eyebrow, map or footnotes', async () => {
  store.reset();
  const page = (await homeView()).node;
  const all = text(page);
  assert.equal(byClass(page, 'eyebrow').length, 0);
  assert.equal(byClass(page, 'map-card').length, 0, 'no empty reading map before the first answer');
  assert.doesNotMatch(all, /4 courses|About 2 minutes|Early draft|Not started|in preparation/);
  // The one footnote left warns of a real loss: answers held only in memory (as here, without browser storage).
  assert.equal(store.persistent, false);
  assert.equal(byClass(page, 'hero__facts').map(text).join(), strings['home.facts.memory']);
  assert.match(all, /74 free questions/);
  const primary = page.all((n) => n.tag === 'a' && n.className.includes('button--primary'));
  assert.equal(primary.length, 1, 'one primary action');
  assert.equal(primary[0].attrs.href, `#/q/${B1.start_question}`);
});

test('home after the first answer shows the reader’s map', async () => {
  store.reset();
  answerFirst();
  const page = (await homeView()).node;
  assert.equal(byClass(page, 'map-card').length, 1);
  store.reset();
});

test('a course page before the first answer: one Start, the lessons, no empty map and no repeated words', async () => {
  store.reset();
  const page = (await courseView({ id: B1.id })).node;
  const all = text(page);
  const start = page.all((n) => n.tag === 'a' && text(n) === 'Start');
  assert.equal(start.length, 1);
  assert.equal(start[0].attrs.href, `#/q/${B1.start_question}`);
  assert.match(start[0].className, /button--primary/);
  assert.equal(byClass(page, 'map-card').length, 0);
  assert.doesNotMatch(all, /Not started|Builds on|1 level/);
  assert.match(all, /30 questions/);
  const head = byClass(page, 'level__head')[0];
  assert.match(head.className, /visually-hidden/, 'one level named like the course keeps its heading for assistive technology only');
});

test('a course page after the first answer: Continue and the map take over from Start', async () => {
  store.reset();
  answerFirst();
  const page = (await courseView({ id: B1.id })).node;
  assert.equal(page.all((n) => n.tag === 'a' && text(n) === 'Start').length, 0);
  assert.equal(byClass(page, 'map-card').length, 1);
  assert.match(text(page), /1 of 5 read/);
  const card = byClass(page, 'continue-card')[0];
  assert.equal(text(card), `${b1Course.levels[0].lessons[0].title}, question 2 of 5Continue`, 'the card says where; only the button says Continue');
  assert.match(card.all((n) => n.tag === 'a')[0].attrs['aria-label'], /^Continue: /);
  store.reset();
});

test('a course with several levels names them and says so', async () => {
  store.reset();
  const page = (await courseView({ id: 'python' })).node;
  assert.match(text(page), /3 levels/);
  assert.ok(byClass(page, 'level__head').every((n) => !n.className.includes('visually-hidden')));
});

test('“Builds on” only names a lesson that is not the one right before', async () => {
  store.reset();
  const lessons = b1Course.levels[0].lessons;
  const course = { ...b1Course, levels: [{ ...b1Course.levels[0], lessons: lessons.map((l, i) => (i === 2 ? { ...l, builds_on: [lessons[0].id] } : l)) }] };
  const data = await import('../public/js/data.js');
  data.clearDataCache();
  const original = globalThis.fetch;
  globalThis.fetch = async (url) => (/courses\/read-like-a-computer\.json/.test(String(url)) ? { ok: true, status: 200, async json() { return course; } } : original(url));
  const page = (await courseView({ id: B1.id })).node;
  const builds = byClass(page, 'lesson-card__builds');
  assert.equal(builds.length, 1);
  assert.equal(text(builds[0]), `Builds on: ${lessons[0].title}`);
  globalThis.fetch = original;
  data.clearDataCache();
});

test('a lesson page: the cells show how many questions, and an unanswered row says nothing', async () => {
  store.reset();
  const page = (await lessonView({ id: b1Course.levels[0].lessons[0].id })).node;
  const all = text(page);
  assert.doesNotMatch(all, /5 questions|Not answered/);
  assert.equal(byClass(page, 'lesson-link').length, 5);
});

test('About: no catalog count, two honest limits, and the privacy text unchanged', async () => {
  const page = (await aboutView()).node;
  const all = text(page);
  assert.doesNotMatch(all, /course catalog lists/);
  assert.equal(byClass(page, 'banner')[0].all((n) => n.tag === 'li').length, 2);
  assert.ok(all.includes(strings['about.privacy.text']));
});
