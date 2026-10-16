import test from 'node:test';
import assert from 'node:assert/strict';
import {
  recommendPractice as implementation,
  PRACTICE_POLICY_VERSION,
  REASON_CODES,
  LIMITATION_CODES,
} from '../adaptive/policy.mjs';

function runPolicy(input) {
  const result = implementation(input);
  assert.ok(REASON_CODES.includes(result.reason_code), `undeclared reason code: ${result.reason_code}`);
  for (const limitation of result.limitations) {
    assert.ok(LIMITATION_CODES.includes(limitation), `undeclared limitation: ${limitation}`);
  }
  return result;
}

const HASH_A = 'a'.repeat(64);
const HASH_B = 'b'.repeat(64);

function item(itemId, {
  objective = 'values', chapter = 'foundations', family = itemId,
  level = 'beginner', prerequisites = [], sourceHash = HASH_A,
  rubricVersion = 'rubric-1',
} = {}) {
  return {
    item_id: itemId,
    source_hash: sourceHash,
    rubric_version: rubricVersion,
    primary_objective: objective,
    chapter_id: chapter,
    family_id: family,
    editorial_level: level,
    prerequisites: [...prerequisites],
  };
}

function defaultBank() {
  return {
    schema_version: 'practice-bank-v1',
    bank_version: 'test-bank-1',
    items: [
      item('entry-a', { family: 'family-a' }),
      item('entry-b', { family: 'family-b' }),
      item('values-mid-a', { family: 'family-c', level: 'intermediate' }),
      item('values-advanced-a', { family: 'family-d', level: 'advanced' }),
      item('loops-beginner', { objective: 'loops', chapter: 'loops', family: 'family-e' }),
      item('functions-a', {
        objective: 'functions', chapter: 'functions', family: 'family-f',
        level: 'intermediate', prerequisites: ['values'],
      }),
      item('functions-b', {
        objective: 'functions', chapter: 'functions', family: 'family-g',
        level: 'intermediate', prerequisites: ['values'],
      }),
    ],
  };
}

function context(overrides = {}) {
  return {
    selected_objective: null,
    selected_chapter_id: null,
    allowed_prerequisite_objectives: [],
    known_item_exposures: [],
    known_family_exposures: [],
    history_integrity: 'ok',
    ...overrides,
  };
}

function event(itemRecord, overrides = {}) {
  return {
    event_id: 'event-1',
    attempt_id: 'attempt-1',
    session_id: 'session-1',
    sequence: 1,
    item_id: itemRecord.item_id,
    source_hash: itemRecord.source_hash,
    rubric_version: itemRecord.rubric_version,
    family_id: itemRecord.family_id,
    mode: 'practice',
    try_kind: 'first',
    outcome: 'answered',
    score: 0,
    assisted: false,
    study_exposure: false,
    trace_exposure: false,
    ...overrides,
  };
}

function history(events = []) {
  return { schema_version: 'practice-history-v1', events };
}

function recommend({ bank = defaultBank(), events = [], contextOverrides = {}, seed = 'seed-1' } = {}) {
  return runPolicy({ bank, history: history(events), context: context(contextOverrides), seed });
}

test('reason and limitation vocabularies are closed, frozen and unique', () => {
  assert.ok(Object.isFrozen(REASON_CODES));
  assert.ok(Object.isFrozen(LIMITATION_CODES));
  assert.equal(new Set(REASON_CODES).size, REASON_CODES.length);
  assert.equal(new Set(LIMITATION_CODES).size, LIMITATION_CODES.length);
});

test('empty history offers an open-course entry suggestion with a reproducible seeded choice', () => {
  const input = { bank: defaultBank(), history: history(), context: context(), seed: 'start' };
  const first = runPolicy(input);
  assert.deepEqual(runPolicy(input), first);
  assert.equal(first.policy_version, PRACTICE_POLICY_VERSION);
  assert.equal(first.bank_version, 'test-bank-1');
  assert.equal(first.status, 'suggestion');
  assert.equal(first.reason_code, 'start_from_course_entry');
  assert.ok(['entry-a', 'entry-b'].includes(first.candidate_item_id));
  assert.deepEqual(first.evidence_event_ids, []);
  assert.equal(first.course_open, true);
  assert.ok(!('theta' in first) && !('mastery' in first) && !('probability' in first));
});

test('different seeds can select among equivalent entry items without global randomness', () => {
  const choices = new Set();
  for (let index = 0; index < 40; index += 1) {
    const result = recommend({ seed: `seed-${index}` });
    choices.add(result.candidate_item_id);
  }
  assert.deepEqual(choices, new Set(['entry-a', 'entry-b', 'loops-beginner']));
});

test('inputs are not mutated', () => {
  const input = { bank: defaultBank(), history: history(), context: context(), seed: 'immutability' };
  const before = structuredClone(input);
  runPolicy(input);
  assert.deepEqual(input, before);
});

test('learner choice is honored and prerequisites require an explicit allowlist', () => {
  const bank = defaultBank();
  const chosen = context({ selected_objective: 'functions', selected_chapter_id: 'functions' });
  assert.equal(runPolicy({ bank, history: history(), context: chosen, seed: 1 }).status, 'insufficient');

  const allowed = { ...chosen, allowed_prerequisite_objectives: ['values'] };
  const result = runPolicy({ bank, history: history(), context: allowed, seed: 1 });
  assert.equal(result.status, 'suggestion');
  assert.ok(['functions-a', 'functions-b'].includes(result.candidate_item_id));
  assert.equal(result.course_open, true);
});

test('selected chapter starts with an entry item in that chapter', () => {
  const result = recommend({ contextOverrides: { selected_chapter_id: 'functions' } });
  assert.equal(result.reason_code, 'no_fresh_eligible_item');
  assert.equal(result.candidate_item_id, null); // no beginner function item without prerequisites
  assert.equal(result.status, 'insufficient');
});

test('first low or half score suggests same objective in another family', () => {
  const bank = defaultBank();
  const first = bank.items.find((candidate) => candidate.item_id === 'entry-a');
  for (const score of [0, 0.5]) {
    const record = event(first, { score, event_id: `scored-${score}`, attempt_id: `try-${score}` });
    const result = recommend({ bank, events: [record], seed: `score-${score}` });
    assert.equal(result.status, 'suggestion');
    assert.equal(result.reason_code, 'repeat_objective_with_different_family');
    assert.ok(['entry-b', 'values-mid-a', 'values-advanced-a'].includes(result.candidate_item_id));
    assert.deepEqual(result.evidence_event_ids, [record.event_id]);
    assert.ok(result.limitations.includes('single_observation_only'));
  }
});

test('full score prefers a fresh same-objective transfer at the same or higher editorial level', () => {
  const bank = defaultBank();
  const first = bank.items.find((candidate) => candidate.item_id === 'values-mid-a');
  const result = recommend({ bank, events: [event(first, { score: 1 })], seed: 'transfer' });
  assert.equal(result.reason_code, 'transfer_same_objective');
  assert.equal(result.candidate_item_id, 'values-advanced-a');
  assert.deepEqual(result.evidence_event_ids, ['event-1']);
  assert.ok(result.limitations.includes('single_observation_only'));
});

test('full-score fallback is limited to explicit prerequisite objectives', () => {
  const current = item('current', { level: 'advanced', family: 'done' });
  const adjacent = item('adjacent-entry', { objective: 'adjacent', family: 'fresh' });
  const bank = { schema_version: 'practice-bank-v1', bank_version: 'bank', items: [current, adjacent] };
  const result = recommend({
    bank,
    events: [event(current, { score: 1 })],
    contextOverrides: { allowed_prerequisite_objectives: ['adjacent'] },
  });
  assert.equal(result.reason_code, 'transfer_allowlisted_objective');
  assert.equal(result.candidate_item_id, 'adjacent-entry');
  assert.ok(result.limitations.includes('objective_is_allowlisted_not_mastered'));
});

test('unknown is not score zero and offers an eligible beginner introduction', () => {
  const bank = defaultBank();
  const first = bank.items.find((candidate) => candidate.item_id === 'values-mid-a');
  const record = event(first, { outcome: 'unknown', score: null });
  const result = recommend({ bank, events: [record], seed: 'unknown' });
  assert.equal(result.reason_code, 'offer_introduction_after_unknown');
  assert.ok(['entry-a', 'entry-b'].includes(result.candidate_item_id));
  assert.deepEqual(result.evidence_event_ids, []);
  assert.ok(result.limitations.includes('unknown_response_not_scored'));
});

test('all-unknown evidence never defaults to an intermediate recommendation', () => {
  const bank = defaultBank();
  const records = ['entry-a', 'entry-b', 'values-mid-a'].map((id, index) => {
    const found = bank.items.find((candidate) => candidate.item_id === id);
    return event(found, {
      event_id: `unknown-${index}`, attempt_id: `unknown-attempt-${index}`,
      sequence: index + 1, outcome: 'unknown', score: null,
    });
  });
  const result = recommend({ bank, events: records });
  assert.equal(result.status, 'insufficient');
  assert.equal(result.candidate_item_id, null);
  assert.equal(result.reason_code, 'no_intro_candidate_after_unknown');
  assert.deepEqual(result.evidence_event_ids, []);
});

test('unanswered and abandoned attempts offer an introduction or stop, never infer an intermediate level', () => {
  const bank = defaultBank();
  const first = bank.items.find((candidate) => candidate.item_id === 'values-mid-a');
  for (const outcome of ['unanswered', 'abandoned']) {
    const record = event(first, {
      outcome, score: null, event_id: `no-response-${outcome}`, attempt_id: `no-response-attempt-${outcome}`,
    });
    const result = recommend({ bank, events: [record] });
    assert.equal(result.reason_code, 'offer_introduction_after_no_response');
    assert.ok(['entry-a', 'entry-b'].includes(result.candidate_item_id));
    assert.deepEqual(result.evidence_event_ids, []);
  }
});

test('assisted and retry attempts can suggest another example but never fresh evidence', () => {
  const bank = defaultBank();
  const first = bank.items.find((candidate) => candidate.item_id === 'entry-a');
  for (const overrides of [
    { assisted: true },
    { try_kind: 'retry' },
    { study_exposure: true, assisted: true },
    { trace_exposure: true, assisted: true },
  ]) {
    const record = event(first, overrides);
    const result = recommend({ bank, events: [record] });
    assert.equal(result.status, 'suggestion');
    assert.equal(result.reason_code, 'follow_up_with_another_example');
    assert.deepEqual(result.evidence_event_ids, []);
    assert.ok(result.limitations.includes('assisted_or_retry_not_scored'));
  }
});

test('a retry supersedes its first answer for advice but neither attempt is fresh diagnostic evidence', () => {
  const bank = defaultBank();
  const first = bank.items.find((candidate) => candidate.item_id === 'entry-a');
  const original = event(first, { score: 1, sequence: 1, event_id: 'first-event', attempt_id: 'first-attempt' });
  const retry = event(first, {
    score: 1, sequence: 2, event_id: 'retry-event', attempt_id: 'retry-attempt', try_kind: 'retry',
  });
  const result = recommend({ bank, events: [original, retry] });
  assert.equal(result.reason_code, 'follow_up_with_another_example');
  assert.deepEqual(result.evidence_event_ids, []);
  assert.ok(result.limitations.includes('assisted_or_retry_not_scored'));
});

test('stale source/rubric records retain item and family exposure but are not rescored', () => {
  const bank = defaultBank();
  const old = bank.items.find((candidate) => candidate.item_id === 'entry-a');
  const stale = event(old, { source_hash: HASH_B, event_id: 'old-event' });
  const result = recommend({ bank, events: [stale] });
  assert.equal(result.status, 'suggestion');
  assert.equal(result.reason_code, 'follow_up_without_fresh_diagnosis');
  assert.notEqual(result.candidate_item_id, 'entry-a');
  const selected = bank.items.find((candidate) => candidate.item_id === result.candidate_item_id);
  assert.notEqual(selected.family_id, stale.family_id);
  assert.deepEqual(result.evidence_event_ids, []);
  assert.ok(result.limitations.includes('stale_item_version_not_scored'));
});

test('stale family metadata conservatively retains both recorded and current family exposure', () => {
  const oldFamily = 'family-before-edit';
  const current = item('same-id', { family: 'family-after-edit' });
  const sibling = item('sibling', { family: 'family-after-edit' });
  const alternate = item('alternate', { family: 'unrelated-family' });
  const bank = { schema_version: 'practice-bank-v1', bank_version: 'bank', items: [current, sibling, alternate] };
  const stale = event(current, { family_id: oldFamily, score: 0 });
  const result = recommend({ bank, events: [stale] });
  assert.equal(result.status, 'suggestion');
  assert.notEqual(result.candidate_item_id, 'sibling');
  assert.ok(['alternate'].includes(result.candidate_item_id));
  assert.ok(result.limitations.includes('stale_item_version_not_scored'));
});

test('placement and exam scores do not steer practice outcome, while their exposures remain excluded', () => {
  const bank = defaultBank();
  const practiceItem = bank.items.find((candidate) => candidate.item_id === 'values-mid-a');
  const examItem = bank.items.find((candidate) => candidate.item_id === 'loops-beginner');
  const practice = event(practiceItem, { score: 1, event_id: 'practice', attempt_id: 'practice-attempt', sequence: 1 });
  const exam = event(examItem, {
    mode: 'exam', score: 0, event_id: 'exam', attempt_id: 'exam-attempt', sequence: 2,
  });
  const result = recommend({ bank, events: [practice, exam] });
  assert.equal(result.reason_code, 'transfer_same_objective');
  assert.deepEqual(result.evidence_event_ids, ['practice']);
  assert.notEqual(result.candidate_item_id, examItem.item_id);
});

test('session history conservatively excludes all seen families, not only the last one', () => {
  const bank = defaultBank();
  const seen = bank.items.find((candidate) => candidate.item_id === 'entry-a');
  bank.items.push(item('duplicate-family-new-id', { family: seen.family_id }));
  const result = recommend({ bank, events: [event(seen)] });
  assert.notEqual(result.candidate_item_id, 'duplicate-family-new-id');
  assert.notEqual(result.candidate_item_id, seen.item_id);
});

test('identical event/attempt duplicates are idempotent', () => {
  const bank = defaultBank();
  const first = bank.items.find((candidate) => candidate.item_id === 'entry-a');
  const record = event(first);
  const once = recommend({ bank, events: [record] });
  const twice = recommend({ bank, events: [record, { ...record }] });
  assert.deepEqual(twice, once);
});

test('conflicting event or attempt identities fail closed', () => {
  const bank = defaultBank();
  const first = bank.items.find((candidate) => candidate.item_id === 'entry-a');
  const record = event(first);
  const conflict = { ...record, score: 1 };
  const duplicateAttempt = { ...record, event_id: 'different-event' };
  for (const events of [[record, conflict], [record, duplicateAttempt]]) {
    const result = recommend({ bank, events });
    assert.equal(result.status, 'invalid');
    assert.equal(result.reason_code, 'invalid_history');
    assert.equal(result.candidate_item_id, null);
  }
});

test('explicit sequence, not array order or timestamps, determines the latest practice outcome', () => {
  const bank = defaultBank();
  const lowItem = bank.items.find((candidate) => candidate.item_id === 'entry-a');
  const fullItem = bank.items.find((candidate) => candidate.item_id === 'values-mid-a');
  const old = event(lowItem, { sequence: 1, event_id: 'older', attempt_id: 'older-attempt', score: 0 });
  const latest = event(fullItem, { sequence: 2, event_id: 'latest', attempt_id: 'latest-attempt', score: 1 });
  const result = recommend({ bank, events: [latest, old] });
  assert.equal(result.reason_code, 'transfer_same_objective');
  assert.deepEqual(result.evidence_event_ids, ['latest']);
});

test('duplicate sequence numbers are corrupt history', () => {
  const bank = defaultBank();
  const first = bank.items.find((candidate) => candidate.item_id === 'entry-a');
  const other = bank.items.find((candidate) => candidate.item_id === 'entry-b');
  const a = event(first);
  const b = event(other, { event_id: 'event-2', attempt_id: 'attempt-2' });
  const result = recommend({ bank, events: [a, b] });
  assert.equal(result.status, 'invalid');
});

test('unknown legacy integrity is insufficient and corrupt integrity is invalid', () => {
  const unknown = recommend({ contextOverrides: { history_integrity: 'unknown' } });
  assert.equal(unknown.status, 'insufficient');
  assert.equal(unknown.reason_code, 'history_integrity_unknown');
  assert.equal(unknown.candidate_item_id, null);
  const corrupt = recommend({ contextOverrides: { history_integrity: 'corrupt' } });
  assert.equal(corrupt.status, 'invalid');
  assert.equal(corrupt.reason_code, 'history_corrupt');
});

test('empty bank and no eligible fresh item return controlled insufficient results', () => {
  const empty = { schema_version: 'practice-bank-v1', bank_version: 'empty', items: [] };
  const result = runPolicy({ bank: empty, history: history(), context: context(), seed: 0 });
  assert.equal(result.status, 'insufficient');
  assert.equal(result.reason_code, 'bank_empty');
  const full = defaultBank();
  const events = full.items.map((record, index) => event(record, {
    event_id: `seen-${index}`, attempt_id: `seen-attempt-${index}`, sequence: index + 1,
    outcome: 'unknown', score: null,
  }));
  const noFresh = recommend({ bank: full, events });
  assert.equal(noFresh.status, 'insufficient');
  assert.equal(noFresh.reason_code, 'no_fresh_eligible_item');
});

test('malformed inputs and configured size caps return invalid without throwing', () => {
  const good = { bank: defaultBank(), history: history(), context: context(), seed: 'x' };
  const malformed = [
    null,
    {},
    { ...good, bank: null },
    { ...good, history: { schema_version: 'wrong', events: [] } },
    { ...good, context: { ...good.context, ignored: true } },
    { ...good, seed: {} },
  ];
  for (const input of malformed) {
    assert.doesNotThrow(() => runPolicy(input));
    assert.equal(runPolicy(input).status, 'invalid');
  }
  const oversizedBank = {
    schema_version: 'practice-bank-v1', bank_version: 'large',
    items: Array.from({ length: 513 }, (_, index) => item(`item-${index}`)),
  };
  assert.equal(runPolicy({ ...good, bank: oversizedBank }).reason_code, 'invalid_input');
  const oversizedHistory = history(Array.from({ length: 4097 }, (_, index) => ({
    ...event(item(`past-${index}`)), event_id: `event-${index}`, attempt_id: `attempt-${index}`,
    sequence: index,
  })));
  assert.equal(runPolicy({ ...good, history: oversizedHistory }).reason_code, 'invalid_history');
});

test('exposure flags without assisted metadata are rejected as inconsistent', () => {
  const first = defaultBank().items[0];
  const result = recommend({ events: [event(first, { trace_exposure: true })] });
  assert.equal(result.status, 'invalid');
});
