import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { checkQuestion, isPractice } from '../public/js/question-check.js';
import { createStore } from '../public/js/store.js';
import { courseScore, firstTries, scoreWithProfiles, totalPoints } from '../public/js/scoring/model.js';
import { loadAnswerProfiles, practiceIdsOf, clearDataCache } from '../public/js/data.js';

const read = (file) => readFileSync(new URL(`../public/${file}`, import.meta.url), 'utf8');

function options(scores) {
  return scores.map((score, i) => ({ id: `o${i}`, text: `option ${i}`, feedback: 'f', score, misconception: score === 1 ? null : 'py.x', ...(score === 0.5 ? { partial_reason: 'p' } : {}) }));
}
function question(version, profile, scores, id = 'some-question') {
  return { schema_version: version, ...(profile === undefined ? {} : { answer_profile: profile }), id, type: 'output', level: 'beginner', prompt: 'p', explanation: 'e',
    snippets: [{ id: 's', code: 'print(1)' }], options: options(scores), author: { name: 'a' } };
}
const FIVE = [1, 0.5, 0, 0, 0];
const FOUR = [1, 0.5, 0, 0];

test('the client check accepts the legacy and standard shapes and the exact practice shape', () => {
  assert.equal(checkQuestion(question('0.1', undefined, FIVE), 'some-question'), null);
  assert.equal(checkQuestion(question('0.2', 'standard', FIVE), 'some-question'), null);
  assert.equal(checkQuestion(question('0.2', 'standard', [1, 0.5, 0, 0, 0, 0, 0, 0]), 'some-question'), null);
  assert.equal(checkQuestion(question('0.2', 'beginner-practice', FOUR), 'some-question'), null);
});

test('the client check refuses every other combination, never falling back', () => {
  const bad = {
    'legacy with four': question('0.1', undefined, FOUR), 'legacy with nine': question('0.1', undefined, [1, 0.5, 0, 0, 0, 0, 0, 0, 0]),
    'profile on 0.1 (standard)': question('0.1', 'standard', FIVE), 'profile on 0.1 (practice)': question('0.1', 'beginner-practice', FOUR),
    'missing profile in 0.2': question('0.2', undefined, FOUR), 'missing profile, five options': question('0.2', undefined, FIVE),
    'unknown profile': question('0.2', 'premium', FOUR), 'profile of another type': question('0.2', ['standard'], FIVE), 'null profile': question('0.2', null, FIVE),
    'unknown version': question('0.3', 'standard', FIVE), 'numeric version': question(0.2, 'standard', FIVE), 'no version': { ...question('0.2', 'standard', FIVE), schema_version: undefined },
    'standard with four': question('0.2', 'standard', FOUR), 'standard with three': question('0.2', 'standard', [1, 0.5, 0]),
    'practice with five': question('0.2', 'beginner-practice', FIVE), 'practice with three': question('0.2', 'beginner-practice', [1, 0.5, 0]),
    'practice with two halves': question('0.2', 'beginner-practice', [1, 0.5, 0.5, 0]), 'practice with three zeros and no half': question('0.2', 'beginner-practice', [1, 0, 0, 0]),
    'practice with one zero': question('0.2', 'beginner-practice', [1, 0.5, 0.5, 0.5]),
  };
  for (const [name, q] of Object.entries(bad)) assert.notEqual(checkQuestion(q, 'some-question'), null, name);
  const doubled = question('0.2', 'beginner-practice', FOUR);
  doubled.options[3].id = 'o2';
  assert.equal(checkQuestion(doubled, 'some-question'), 'option id', 'option ids stay unique');
  const noPartial = question('0.2', 'beginner-practice', FOUR);
  delete noPartial.options[1].partial_reason;
  assert.notEqual(checkQuestion(noPartial, 'some-question'), null);
});

test('only the explicit profile says practice: nothing is inferred from id, level, count or version', () => {
  assert.equal(isPractice(question('0.2', 'beginner-practice', FOUR)), true);
  for (const q of [question('0.1', undefined, FIVE), question('0.2', 'standard', FIVE), question('0.2', undefined, FOUR),
                   question('0.1', 'beginner-practice', FOUR), question('0.2', 'beginner-practice', FOUR, 'standard-looking-id'), null, undefined, {}]) {
    if (q && q.schema_version === '0.2' && q.answer_profile === 'beginner-practice') continue;
    assert.equal(isPractice(q), false);
  }
  assert.equal(isPractice({ ...question('0.2', 'standard', FOUR), level: 'beginner', id: 'beginner-practice-1' }), false, 'four options, a beginner level and a practice-like id are not the signal');
});

function memoryStorage() {
  const data = new Map();
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), removeItem: (k) => data.delete(k), data };
}

test('answers to a practice question are recorded exactly like every other answer: no new field, nothing an older client cannot read', () => {
  const store = createStore({ storage: memoryStorage(), prefix: 't', now: () => 1000 });
  const records = [store.recordAnswer({ questionId: 'a', optionId: 'o0', score: 1 }), store.recordUnknown({ questionId: 'b' })];
  for (const record of records) assert.deepEqual(Object.keys(record).sort(), ['attempt', 'option_id', 'outcome', 'question_id', 'score', 't']);
  const view = read('js/views/question.js');
  assert.doesNotMatch(view, /practice: ctx\.practice \}\)/, 'the view passes no practice flag to the store');
  assert.doesNotMatch(read('js/store.js'), /profile|practice/i, 'the progress format has no profile field (an older client keeps reading it)');
});

test('stored progress, with practice answers in it, round-trips through export and import unchanged', () => {
  const store = createStore({ storage: memoryStorage(), prefix: 't', now: () => 5 });
  store.recordAnswer({ questionId: 'p1', optionId: 'o1', score: 0.5 });
  store.recordUnknown({ questionId: 'p2' });
  const exported = store.exportJson();
  const other = createStore({ storage: memoryStorage(), prefix: 'u' });
  assert.deepEqual(other.importJson(exported), { ok: true, imported: 2 });
  assert.deepEqual(other.answers(), store.answers());
});

const course = { id: 'c', title: 'C', levels: [{ level: 'beginner', lessons: [{ id: 'l', questions: ['p1', 'p2', 's1'] }] }] };
const rec = (id, score, extra = {}) => ({ question_id: id, option_id: 'o', outcome: 'answered', score, t: 10, attempt: 1, ...extra });
const profiles = (spec) => new Map(Object.entries(spec));
const MIXED = profiles({ p1: 'beginner-practice', p2: 'beginner-practice', s1: 'standard' });

test('practice questions are out of the scoring pool and out of points, answered or not', () => {
  const fresh = scoreWithProfiles(course, [], undefined, null, MIXED);
  assert.deepEqual({ pool: fresh.pool, answered: fresh.answered, points: fresh.points, unavailable: fresh.unavailable }, { pool: 1, answered: 0, points: 0, unavailable: false });
  const records = [rec('p1', 1), rec('p2', 0), rec('s1', 1)];
  const score = scoreWithProfiles(course, records, undefined, null, MIXED);
  assert.deepEqual({ pool: score.pool, answered: score.answered, sum: score.sum, points: score.points }, { pool: 1, answered: 1, sum: 1, points: 1000 });
  assert.deepEqual([...firstTries(records.filter((r) => r.question_id !== 'p1' && r.question_id !== 'p2')).keys()], ['s1']);
  const reloaded = scoreWithProfiles(course, JSON.parse(JSON.stringify(records)), undefined, null, MIXED);
  assert.deepEqual(reloaded, score, 'the same stored progress gives the same result after a reload');
});

test('a course of only practice questions has no pool, no points and stays out of the total (never zero)', () => {
  const only = profiles({ p1: 'beginner-practice', p2: 'beginner-practice', s1: 'beginner-practice' });
  const score = scoreWithProfiles(course, [rec('p1', 1), rec('p2', 1)], undefined, null, only);
  assert.deepEqual({ pool: score.pool, answered: score.answered, points: score.points, practiceOnly: score.practiceOnly, unavailable: score.unavailable }, { pool: 0, answered: 0, points: null, practiceOnly: true, unavailable: false });
  const standard = scoreWithProfiles({ ...course, levels: [{ level: 'beginner', lessons: [{ id: 'm', questions: ['s1'] }] }] }, [rec('s1', 1)], undefined, null, MIXED);
  assert.equal(totalPoints([score, standard].filter((s) => !s.practiceOnly)), 1000, 'the total ignores the practice-only course instead of averaging it in as zero');
});

test('unknown profiles mean no points at all: no standard assumption and no zero', () => {
  for (const [name, unknown] of Object.entries({ none: null, 'not a map': {}, 'missing one question': profiles({ p1: 'beginner-practice', s1: 'standard' }), empty: new Map() })) {
    const score = scoreWithProfiles(course, [rec('s1', 1)], undefined, null, unknown);
    assert.deepEqual({ unavailable: score.unavailable, points: score.points, answered: score.answered }, { unavailable: true, points: null, answered: 0 }, name);
  }
  const unavailable = scoreWithProfiles(course, [], undefined, null, null);
  const ok = scoreWithProfiles({ ...course, levels: [{ level: 'beginner', lessons: [{ id: 'm', questions: ['s1'] }] }] }, [rec('s1', 1)], undefined, null, MIXED);
  assert.equal(totalPoints([unavailable, ok]), null, 'one course with unknown profiles gives no total');
});

test('old stored records (no mark anywhere) score exactly as before when every profile is standard', () => {
  const all = profiles({ p1: 'standard', p2: 'standard', s1: 'standard' });
  const records = [rec('p1', 1), rec('p2', 0.5), rec('s1', 0)];
  const viaProfiles = scoreWithProfiles(course, records, undefined, null, all);
  const direct = courseScore(course, records);
  for (const key of ['pool', 'answered', 'sum', 'points', 'counts']) assert.deepEqual(viaProfiles[key], direct[key], key);
});

test('the result moment needs known profiles and a scoring pool', async () => {
  globalThis.document = { createElement: () => ({ setAttribute() {}, append() {}, addEventListener() {} }), createTextNode: (text) => ({ text }) };
  const { courseFinished } = await import('../public/js/components/result-card.js');
  const answers = [rec('p1', 1), rec('p2', 1), rec('s1', 1)];
  assert.equal(courseFinished(course, answers, MIXED), true, 'the standard question is answered');
  assert.equal(courseFinished(course, [rec('p1', 1), rec('p2', 1)], MIXED), false, 'practice answers alone finish nothing');
  assert.equal(courseFinished(course, answers, profiles({ p1: 'beginner-practice', p2: 'beginner-practice', s1: 'beginner-practice' })), false, 'a practice-only course has no result');
  assert.equal(courseFinished(course, answers, null), false, 'unknown profiles: no result');
  assert.equal(courseFinished(course, answers), false, 'no profiles given: no result');
});

function serveIndex(body, ok = true, status = 200) {
  clearDataCache();
  globalThis.fetch = async () => ({ ok, status, async json() { return body; } });
}
const entry = (id, answer_profile) => ({ id, type: 'output', level: 'beginner', difficulty: 0.2, concepts: ['py.x'], ...(answer_profile === undefined ? {} : { answer_profile }) });

test('the profile loader reads only a complete version-2 index and otherwise returns unknown', async () => {
  serveIndex({ data_version: 2, onboarding_question: 'a', questions: [entry('a', 'standard'), entry('b', 'beginner-practice')] });
  const loaded = await loadAnswerProfiles('python');
  assert.deepEqual([...loaded], [['a', 'standard'], ['b', 'beginner-practice']]);
  assert.deepEqual(practiceIdsOf(loaded), ['b']);
  assert.equal(practiceIdsOf(null), null);
  const refused = {
    'the old version': { data_version: 1, questions: [entry('a')] },
    'a missing profile': { data_version: 2, questions: [entry('a', 'standard'), entry('b')] },
    'an unknown profile': { data_version: 2, questions: [entry('a', 'premium')] },
    'a profile of another type': { data_version: 2, questions: [entry('a', ['standard'])] },
    'a duplicate id': { data_version: 2, questions: [entry('a', 'standard'), entry('a', 'standard')] },
    'no list': { data_version: 2 },
    'a future version': { data_version: 3, questions: [entry('a', 'standard')] },
    'not an object': 'index',
    null: null,
  };
  for (const [name, body] of Object.entries(refused)) {
    serveIndex(body);
    assert.equal(await loadAnswerProfiles('python'), null, name);
  }
  serveIndex({}, false, 404);
  assert.equal(await loadAnswerProfiles('python'), null, 'a missing index');
  globalThis.fetch = async () => { throw new Error('offline'); };
  clearDataCache();
  assert.equal(await loadAnswerProfiles('python'), null, 'an offline browser');
  assert.equal(await loadAnswerProfiles('../x'), null, 'a malformed course id');
  clearDataCache();
});

test('every consumer that scores gets the profiles first and treats unknown as unknown', () => {
  assert.match(read('js/views/lesson.js'), /await loadAnswerProfiles\(course\.id\)[\s\S]*courseFinished\(course, store\.answers\(\), profiles\)[\s\S]*resultCard\(course, store\.answers\(\), isRightNow, profiles\)/);
  // The profile and leaderboard views are server-only (registered only with the flag on): they never compute a local score,
  // pool or sample board, so the 027 isolation is enforced on the server/model path rather than hidden in a local view.
  for (const file of ['js/views/profile.js', 'js/views/leaderboard.js']) {
    assert.doesNotMatch(read(file), /loadAnswerProfiles|scoreWithProfiles|courseScore|mockBoard/, file);
  }
  assert.match(read('js/views/profile.js'), /client\.scoringMe\(/);
  assert.match(read('js/views/leaderboard.js'), /client\.scoringLeaderboards\(/);
  assert.doesNotMatch(read('js/views/leaderboard.js') + read('js/views/profile.js') + read('js/components/result-card.js'), /courseScore\(/, 'no scoring path uses the profile-blind function');
});

test('the question view has no challenge link, no points and no scoring lines for a practice question', () => {
  const view = read('js/views/question.js');
  assert.match(view, /practice: isPractice\(q\)/);
  assert.match(view, /if \(CONFIG\.scoringUi && !ctx\.reviewMode && !ctx\.practice\) header\.append\(/);
  assert.match(view, /ctx\.practice \? '' : v\.points/);
  assert.match(view, /ctx\.practice \? '' : t\('points\.none'\)/);
  assert.match(view, /!!ctx\.scoring && !ctx\.practice && !ctx\.reviewMode/);
  assert.match(view, /ctx\.practice \? h\('span', \{ class: 'chip' \}, t\('q\.practice'\)\)/);
  assert.match(view, /ctx\.practice \? h\('p', \{ class: 'muted small' \}, t\('q\.practice\.note'\)\)/);
});

test('the challenge view refuses a practice question before any scoring request, with the flag on or off', async () => {
  const requests = [];
  const practiceDoc = question('0.2', 'beginner-practice', FOUR, 'practice-one');
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    if (String(url).includes('questions/practice-one.json')) return { ok: true, status: 200, async json() { return practiceDoc; } };
    return { ok: false, status: 404, async json() { return {}; } };
  };
  const made = [];
  globalThis.document = { createElement: (tag) => { const n = { tag, attrs: {}, children: [], className: '', setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {}, append(...x) { this.children.push(...x); } }; made.push(n); return n; }, createTextNode: (text) => ({ text }) };
  const { CONFIG } = await import('../public/js/config.js');
  const { challengeQuestionView } = await import('../public/js/scoring/challenge.js');
  const text = (n) => (n.text !== undefined ? n.text : (n.children || []).map(text).join(''));
  for (const accountsApi of [null, '/api']) {
    CONFIG.accountsApi = accountsApi;
    const view = await challengeQuestionView({ id: 'practice-one' });
    assert.match(text(view.node), /practice question/i);
    assert.match(text(view.node), /never scored/i);
  }
  assert.equal(requests.some((url) => /scoring|issue|answers|leaderboards/.test(url)), false, `no scoring request: ${requests.join(', ')}`);
  assert.ok(requests.some((url) => url.includes('questions/practice-one.json')), 'only the public question document was read');
  CONFIG.accountsApi = null;
});

test('the challenge view leaves an unreadable or standard question to the server', () => {
  const challenge = read('js/scoring/challenge.js');
  assert.match(challenge, /try \{ return isPractice\(await loadQuestion\(id\)\); \} catch \{ return false; \}/);
  assert.match(challenge, /if \(await isPracticeQuestion\(id\)\) \{[\s\S]*?\}\n  if \(!CONFIG\.accountsApi\)/);
});

test('the adaptive policy is independent of the answer profile and carries no option-count calibration', () => {
  const policy = readFileSync(new URL('../adaptive/policy.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(policy, /answer_profile|beginner-practice|options\.length|schema_version === '0\.[12]'|guess|chance/i);
});
