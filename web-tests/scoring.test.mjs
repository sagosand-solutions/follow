import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { RULES, firstTries, courseScore, coursePoints, totalPoints, formatPoints, formatDelta, sharedRanks, boardWindow, weekStart, recentWeeks, pointsFor } from '../public/js/scoring/model.js';
import { mockBoard } from '../public/js/scoring/mock.js';

const course = (...qs) => ({ levels: [{ lessons: [{ id: 'l', questions: qs }] }] });
const right = (q, attempt, t) => ({ question_id: q, attempt, t, outcome: 'answered', score: 1, option_id: 'a' });
const half = (q, attempt, t) => ({ question_id: q, attempt, t, outcome: 'answered', score: 0.5, option_id: 'b' });
const wrong = (q, attempt, t) => ({ question_id: q, attempt, t, outcome: 'answered', score: 0, option_id: 'c' });
const unsure = (q, attempt, t) => ({ question_id: q, attempt, t, outcome: 'unknown', score: null, option_id: null });

test('rules: +1, +0.5, 0 for not sure, -0.25 for a chosen wrong option', () => {
  assert.deepEqual([pointsFor(right('a', 1, 1)), pointsFor(half('a', 1, 1)), pointsFor(unsure('a', 1, 1)), pointsFor(wrong('a', 1, 1))], [RULES.right, RULES.partial, RULES.unsure, RULES.wrong]);
  assert.deepEqual([RULES.right, RULES.partial, RULES.unsure, RULES.wrong], [1, 0.5, 0, -0.25]);
});

test('only the first try counts; later tries change learning, never points', () => {
  const c = course('a', 'b');
  const s = courseScore(c, [wrong('a', 1, 1), right('a', 2, 2), right('a', 3, 3)]);
  assert.equal(s.sum, -0.25);
  assert.equal(s.answered, 1);
  assert.equal(s.learned, 1);
  assert.equal(s.improved, 1);
  assert.equal(s.points, (1000 * -0.25) / 2);
});

test("the contract's example: 1 right, 1 half, 1 wrong, 1 not sure in a pool of 10 gives 125; total over 3 courses 41.67", () => {
  const c = course(...'abcdefghij');
  const s = courseScore(c, [right('a', 1, 1), half('b', 1, 1), wrong('c', 1, 1), unsure('d', 1, 1)]);
  assert.equal(s.points, 125);
  const total = totalPoints([s, { points: 0 }, { points: 0 }]);
  assert.equal(Math.round(total * 100) / 100, 41.67);
  assert.equal(coursePoints(1, 0), null);
  assert.equal(totalPoints([]), null);
  assert.equal(totalPoints([s, { points: null }]), null);
});

test('range is -250 to 1000 and display uses a real minus sign', () => {
  assert.equal(courseScore(course('a', 'b'), [wrong('a', 1, 1), wrong('b', 1, 1)]).points, -250);
  assert.equal(courseScore(course('a'), [right('a', 1, 1)]).points, 1000);
  assert.equal(formatPoints(-250), '−250');
  assert.equal(formatPoints(612.4), '612');
  assert.equal(formatPoints(null), '–');
  assert.equal(formatDelta(-0.25), '−0.25');
  assert.equal(formatDelta(0.5), '+0.5');
  assert.equal(formatDelta(0), '0');
});

test('firstTries ignores malformed records and falls back to the earliest attempt', () => {
  const m = firstTries([null, { question_id: 'a' }, right('a', 2, 5), wrong('a', 3, 6)]);
  assert.equal(m.get('a').attempt, 2);
});

test('shared ranks 1, 1, 3 and a window of top 3 plus your neighbours', () => {
  const ranked = sharedRanks([{ id: 'x', points: 5 }, { id: 'y', points: 9 }, { id: 'z', points: 9 }]);
  assert.deepEqual(ranked.map((r) => r.rank), [1, 1, 3]);
  const many = sharedRanks(Array.from({ length: 20 }, (_, i) => ({ id: `r${i}`, points: 100 - i })));
  const view = boardWindow(many, 'r10');
  assert.deepEqual(view.map((r) => (r.gap ? 'gap' : r.id)), ['r0', 'r1', 'r2', 'gap', 'r8', 'r9', 'r10', 'r11', 'r12', 'gap']);
  assert.deepEqual(boardWindow(many, 'nobody').map((r) => (r.gap ? 'gap' : r.id)), ['r0', 'r1', 'r2', 'gap']);
  assert.ok(!boardWindow(many, 'r1').some((r) => r.id === 'r19'), 'never the bottom of the list');
});

test('ISO weeks start on Monday 00:00 UTC and the history is calm', () => {
  const sunday = Date.UTC(2026, 9, 18, 23, 0);
  assert.equal(weekStart(sunday), Date.UTC(2026, 9, 12));
  assert.equal(weekStart(Date.UTC(2026, 9, 12, 0, 0)), Date.UTC(2026, 9, 12));
  const weeks = recentWeeks([wrong('a', 1, Date.UTC(2026, 9, 5, 10)), right('a', 2, Date.UTC(2026, 9, 13, 10)), right('b', 1, Date.UTC(2026, 9, 14))], sunday, 3);
  assert.deepEqual(weeks.map((w) => [w.read, w.cleared]), [[2, 1], [1, 0], [0, 0]]);
});

test('mock boards are deterministic and stay inside the scale', () => {
  const a = mockBoard({ family: 'free', window: 'all', course: 'all', pool: 46 });
  const b = mockBoard({ family: 'free', window: 'all', course: 'all', pool: 46 });
  assert.deepEqual(a, b);
  assert.equal(a.participants, 212);
  for (const r of a.rows) assert.ok(r.points >= -250 && r.points <= 1000 && r.answered >= 1 && r.answered <= 46);
});

test('scoring UI is off by default and its routes exist only behind the flag', async () => {
  const config = await readFile(new URL('../public/js/config.js', import.meta.url), 'utf8');
  const main = await readFile(new URL('../public/js/main.js', import.meta.url), 'utf8');
  assert.match(config, /scoringUi: false/);
  assert.match(main, /if \(CONFIG\.scoringUi\) \{\n\s+registerRoute\('\/profile', profileView\);\n\s+registerRoute\('\/leaderboard', leaderboardView\);/);
});

test('no casino words in the scoring copy', async () => {
  const en = JSON.parse(await readFile(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
  const text = Object.entries(en).filter(([k]) => /^(score|profile|board|result)\./.test(k)).map(([, v]) => v).join(' ').toLowerCase();
  for (const word of ['streak', 'penalty', 'lost', 'hurry', 'only today', 'limited', 'jackpot', 'win big']) assert.ok(!text.includes(word), word);
});
