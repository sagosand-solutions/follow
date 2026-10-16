const POLICY_VERSION = 'practice-policy-v1';
const BANK_SCHEMA_VERSION = 'practice-bank-v1';
const HISTORY_SCHEMA_VERSION = 'practice-history-v1';
const MAX_BANK_ITEMS = 512;
const MAX_HISTORY_EVENTS = 4096;
const MAX_CONTEXT_EXPOSURES = 4096;
const MAX_PREREQUISITES = 512;

const ITEM_KEYS = [
  'item_id', 'source_hash', 'rubric_version', 'primary_objective',
  'chapter_id', 'family_id', 'editorial_level', 'prerequisites',
];
const EVENT_KEYS = [
  'event_id', 'attempt_id', 'session_id', 'sequence', 'item_id',
  'source_hash', 'rubric_version', 'family_id', 'mode', 'try_kind',
  'outcome', 'score', 'assisted', 'study_exposure', 'trace_exposure',
];
const CONTEXT_KEYS = [
  'selected_objective', 'selected_chapter_id', 'allowed_prerequisite_objectives',
  'known_item_exposures', 'known_family_exposures', 'history_integrity',
];
export const REASON_CODES = Object.freeze([
  'invalid_input', 'invalid_seed', 'invalid_history', 'history_corrupt',
  'history_integrity_unknown', 'bank_empty', 'no_fresh_eligible_item',
  'no_intro_candidate_after_unknown', 'no_intro_candidate_after_no_response',
  'no_safe_follow_up_candidate', 'no_different_family_candidate', 'no_transfer_candidate',
  'start_from_course_entry', 'start_from_selected_objective', 'start_from_selected_chapter',
  'repeat_objective_with_different_family', 'transfer_same_objective',
  'transfer_allowlisted_objective', 'offer_introduction_after_unknown',
  'offer_introduction_after_no_response', 'follow_up_with_another_example',
  'follow_up_without_fresh_diagnosis',
]);
export const LIMITATION_CODES = Object.freeze([
  'local_advice_only', 'not_calibrated_or_diagnostic', 'course_remains_open',
  'history_not_known_clean', 'no_practice_evidence_yet', 'stale_item_version_not_scored',
  'unknown_response_not_scored', 'no_scored_response', 'assisted_or_retry_not_scored',
  'no_current_unassisted_scored_evidence', 'single_observation_only',
  'objective_is_allowlisted_not_mastered',
]);
const BASE_LIMITATIONS = [
  'local_advice_only',
  'not_calibrated_or_diagnostic',
  'course_remains_open',
];

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value, keys) {
  if (!isRecord(value)) return false;
  const actual = Object.keys(value);
  if (actual.length !== keys.length) return false;
  actual.sort();
  const expected = [...keys].sort();
  return actual.every((key, index) => key === expected[index]);
}

function validId(value) {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= 128
    && value.trim() === value
    && /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(value);
}

function validVersion(value) {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= 80
    && value.trim() === value;
}

function validHash(value) {
  return typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
}

function invalid(reasonCode = 'invalid_input', extraLimitations = []) {
  return {
    policy_version: POLICY_VERSION,
    status: 'invalid',
    reason_code: reasonCode,
    candidate_item_id: null,
    evidence_event_ids: [],
    limitations: [...BASE_LIMITATIONS, ...extraLimitations],
    course_open: true,
  };
}

function insufficient(reasonCode, extraLimitations = []) {
  return {
    policy_version: POLICY_VERSION,
    status: 'insufficient',
    reason_code: reasonCode,
    candidate_item_id: null,
    evidence_event_ids: [],
    limitations: [...BASE_LIMITATIONS, ...extraLimitations],
    course_open: true,
  };
}

function suggestion(reasonCode, item, evidenceEventIds = [], extraLimitations = []) {
  return {
    policy_version: POLICY_VERSION,
    status: 'suggestion',
    reason_code: reasonCode,
    candidate_item_id: item.item_id,
    evidence_event_ids: [...evidenceEventIds],
    limitations: [...BASE_LIMITATIONS, ...extraLimitations],
    course_open: true,
  };
}

function boundedUniqueIds(value, limit) {
  if (!Array.isArray(value) || value.length > limit) return null;
  const ids = [];
  const seen = new Set();
  for (const entry of value) {
    if (!validId(entry) || seen.has(entry)) return null;
    seen.add(entry);
    ids.push(entry);
  }
  return ids;
}

function validateBank(bank) {
  if (!hasExactKeys(bank, ['schema_version', 'bank_version', 'items'])
      || bank.schema_version !== BANK_SCHEMA_VERSION
      || !validVersion(bank.bank_version)
      || !Array.isArray(bank.items)
      || bank.items.length > MAX_BANK_ITEMS) return null;

  const items = [];
  const byId = new Map();
  for (const source of bank.items) {
    if (!hasExactKeys(source, ITEM_KEYS)
        || !validId(source.item_id)
        || !validHash(source.source_hash)
        || !validVersion(source.rubric_version)
        || !validId(source.primary_objective)
        || !validId(source.chapter_id)
        || !validId(source.family_id)
        || !['beginner', 'intermediate', 'advanced'].includes(source.editorial_level)) return null;
    const prerequisites = boundedUniqueIds(source.prerequisites, MAX_PREREQUISITES);
    if (prerequisites === null || prerequisites.includes(source.primary_objective)) return null;
    if (byId.has(source.item_id)) return null;
    const item = { ...source, prerequisites };
    items.push(item);
    byId.set(item.item_id, item);
  }
  return { items, byId };
}

function validateContext(context) {
  if (!hasExactKeys(context, CONTEXT_KEYS)) return null;
  const selectedObjective = context.selected_objective;
  const selectedChapterId = context.selected_chapter_id;
  if (!(selectedObjective === null || validId(selectedObjective))
      || !(selectedChapterId === null || validId(selectedChapterId))
      || !['ok', 'unknown', 'corrupt'].includes(context.history_integrity)) return null;
  const allowedPrerequisites = boundedUniqueIds(context.allowed_prerequisite_objectives, MAX_CONTEXT_EXPOSURES);
  const knownItems = boundedUniqueIds(context.known_item_exposures, MAX_CONTEXT_EXPOSURES);
  const knownFamilies = boundedUniqueIds(context.known_family_exposures, MAX_CONTEXT_EXPOSURES);
  if (allowedPrerequisites === null || knownItems === null || knownFamilies === null) return null;
  return {
    selectedObjective, selectedChapterId, allowedPrerequisites, knownItems, knownFamilies,
    historyIntegrity: context.history_integrity,
  };
}

function normalizeEvent(event) {
  return EVENT_KEYS.map((key) => event[key]);
}

function validateHistory(history, bankById) {
  if (!hasExactKeys(history, ['schema_version', 'events'])
      || history.schema_version !== HISTORY_SCHEMA_VERSION
      || !Array.isArray(history.events)
      || history.events.length > MAX_HISTORY_EVENTS) return null;

  const byEventId = new Map();
  const byAttemptId = new Map();
  const sequences = new Set();
  const events = [];
  for (const source of history.events) {
    if (!hasExactKeys(source, EVENT_KEYS)
        || !validId(source.event_id)
        || !validId(source.attempt_id)
        || !validId(source.session_id)
        || !Number.isSafeInteger(source.sequence)
        || source.sequence < 0
        || !validId(source.item_id)
        || !validHash(source.source_hash)
        || !validVersion(source.rubric_version)
        || !validId(source.family_id)
        || !['practice', 'placement', 'exam'].includes(source.mode)
        || !['first', 'retry'].includes(source.try_kind)
        || !['answered', 'unknown', 'abandoned', 'unanswered'].includes(source.outcome)
        || typeof source.assisted !== 'boolean'
        || typeof source.study_exposure !== 'boolean'
        || typeof source.trace_exposure !== 'boolean') return null;

    const scoreValid = source.outcome === 'answered'
      ? (source.score === 0 || source.score === 0.5 || source.score === 1)
      : source.score === null;
    if (!scoreValid || ((source.study_exposure || source.trace_exposure) && !source.assisted)) return null;

    const normalized = JSON.stringify(normalizeEvent(source));
    const eventDuplicate = byEventId.get(source.event_id);
    const attemptDuplicate = byAttemptId.get(source.attempt_id);
    if (eventDuplicate || attemptDuplicate) {
      if (eventDuplicate && attemptDuplicate
          && eventDuplicate.normalized === normalized
          && attemptDuplicate.normalized === normalized
          && eventDuplicate.event === attemptDuplicate.event) continue;
      return null;
    }
    if (sequences.has(source.sequence)) return null;
    sequences.add(source.sequence);

    const event = { ...source };
    const item = bankById.get(event.item_id);
    event.current_item_version = Boolean(item
      && item.source_hash === event.source_hash
      && item.rubric_version === event.rubric_version
      && item.family_id === event.family_id);

    const record = { event, normalized };
    byEventId.set(event.event_id, record);
    byAttemptId.set(event.attempt_id, record);
    events.push(event);
  }
  events.sort((left, right) => left.sequence - right.sequence);
  return events;
}

function fnv1a32(text) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

function seededChoice(items, seed) {
  if (items.length === 0) return null;
  const ordered = [...items].sort((left, right) => (
    left.item_id < right.item_id ? -1 : left.item_id > right.item_id ? 1 : 0
  ));
  const seedText = `${typeof seed}:${String(seed)}\u0000${ordered.map((item) => item.item_id).join('\u0000')}`;
  return ordered[fnv1a32(seedText) % ordered.length];
}

function selectEligible({ items, context, seed, targetObjective = null, includeAllowlistedObjectives = false }) {
  const allowedObjectives = new Set(context.allowedPrerequisites);
  if (targetObjective !== null) allowedObjectives.add(targetObjective);
  const targetObjectives = new Set();
  if (targetObjective !== null) targetObjectives.add(targetObjective);
  if (includeAllowlistedObjectives) {
    for (const objective of context.allowedPrerequisites) targetObjectives.add(objective);
  }
  return seededChoice(items.filter((item) => {
    if (!targetObjectives.has(item.primary_objective)) return false;
    if (context.selectedChapterId !== null && item.chapter_id !== context.selectedChapterId) return false;
    if (!item.prerequisites.every((prerequisite) => allowedObjectives.has(prerequisite))) return false;
    return true;
  }), seed);
}

function makeExposureSets(context, events, bankById) {
  const itemIds = new Set(context.knownItems);
  const familyIds = new Set(context.knownFamilies);
  for (const event of events) {
    itemIds.add(event.item_id);
    familyIds.add(event.family_id);
    const currentItem = bankById.get(event.item_id);
    if (currentItem) familyIds.add(currentItem.family_id);
  }
  return { itemIds, familyIds };
}

function available(items, exposures) {
  return items.filter((item) => !exposures.itemIds.has(item.item_id)
    && !exposures.familyIds.has(item.family_id));
}

function asPolicy(input) {
  if (!hasExactKeys(input, ['bank', 'history', 'context', 'seed'])) return invalid();
  if (!(typeof input.seed === 'string' && input.seed.length <= 128
      || Number.isSafeInteger(input.seed))) return invalid('invalid_seed');

  const bank = validateBank(input.bank);
  const context = validateContext(input.context);
  if (bank === null || context === null) return invalid();
  const events = validateHistory(input.history, bank.byId);
  if (events === null) return invalid('invalid_history');
  if (context.historyIntegrity === 'corrupt') return invalid('history_corrupt');
  if (context.historyIntegrity === 'unknown') {
    return insufficient('history_integrity_unknown', ['history_not_known_clean']);
  }
  if (bank.items.length === 0) return insufficient('bank_empty');

  const exposures = makeExposureSets(context, events, bank.byId);
  const freshItems = available(bank.items, exposures);
  if (freshItems.length === 0) return insufficient('no_fresh_eligible_item');

  const { selectedObjective, selectedChapterId } = context;
  if (selectedObjective !== null || selectedChapterId !== null) {
    const contextForStart = { ...context, selectedObjective };
    const startPool = selectedObjective !== null
      ? freshItems.filter((item) => item.primary_objective === selectedObjective)
      : freshItems.filter((item) => item.editorial_level === 'beginner'
        && item.prerequisites.length === 0
        && (selectedChapterId === null || item.chapter_id === selectedChapterId));
    const candidate = selectedObjective === null
      ? seededChoice(startPool, input.seed)
      : selectEligible({
        items: startPool,
        context: contextForStart,
        seed: input.seed,
        targetObjective: selectedObjective,
      });
    return candidate
      ? suggestion(selectedObjective ? 'start_from_selected_objective' : 'start_from_selected_chapter', candidate)
      : insufficient('no_fresh_eligible_item');
  }

  const practiceEvents = events.filter((event) => event.mode === 'practice');
  const latest = practiceEvents.length ? practiceEvents[practiceEvents.length - 1] : null;
  if (latest === null) {
    const candidate = seededChoice(freshItems.filter((item) => item.editorial_level === 'beginner'
      && item.prerequisites.length === 0), input.seed);
    return candidate
      ? suggestion('start_from_course_entry', candidate, [], ['no_practice_evidence_yet'])
      : insufficient('no_fresh_eligible_item', ['no_practice_evidence_yet']);
  }

  const latestItem = bank.byId.get(latest.item_id) ?? null;
  const targetObjective = latestItem?.primary_objective ?? null;
  const latestLimitations = [];
  if (!latest.current_item_version) latestLimitations.push('stale_item_version_not_scored');
  if (latest.outcome !== 'answered') {
    latestLimitations.push(latest.outcome === 'unknown'
      ? 'unknown_response_not_scored'
      : 'no_scored_response');
    if (latest.try_kind === 'retry' || latest.assisted || latest.study_exposure || latest.trace_exposure) {
      latestLimitations.push('assisted_or_retry_not_scored');
    }
    const candidate = selectEligible({
      items: freshItems.filter((item) => item.editorial_level === 'beginner' && item.prerequisites.length === 0),
      context,
      seed: input.seed,
      targetObjective,
      includeAllowlistedObjectives: true,
    });
    return candidate
      ? suggestion(latest.outcome === 'unknown'
        ? 'offer_introduction_after_unknown'
        : 'offer_introduction_after_no_response', candidate, [], latestLimitations)
      : insufficient(latest.outcome === 'unknown'
        ? 'no_intro_candidate_after_unknown'
        : 'no_intro_candidate_after_no_response', latestLimitations);
  }
  if (latest.try_kind === 'retry' || latest.assisted || latest.study_exposure || latest.trace_exposure) {
    latestLimitations.push('assisted_or_retry_not_scored');
    const candidate = targetObjective === null ? null : selectEligible({
      items: freshItems,
      context,
      seed: input.seed,
      targetObjective,
    });
    return candidate
      ? suggestion('follow_up_with_another_example', candidate, [], latestLimitations)
      : insufficient('no_safe_follow_up_candidate', latestLimitations);
  }

  if (!latest.current_item_version || targetObjective === null) {
    latestLimitations.push('no_current_unassisted_scored_evidence');
    const candidate = targetObjective === null ? null : selectEligible({
      items: freshItems,
      context,
      seed: input.seed,
      targetObjective,
    });
    return candidate
      ? suggestion('follow_up_without_fresh_diagnosis', candidate, [], latestLimitations)
      : insufficient('no_safe_follow_up_candidate', latestLimitations);
  }

  const evidence = [latest.event_id];
  if (latest.score === 0 || latest.score === 0.5) {
    const candidate = selectEligible({ items: freshItems, context, seed: input.seed, targetObjective });
    return candidate
      ? suggestion('repeat_objective_with_different_family', candidate, evidence, ['single_observation_only'])
      : insufficient('no_different_family_candidate', ['single_observation_only']);
  }

  const currentLevel = { beginner: 0, intermediate: 1, advanced: 2 }[latestItem.editorial_level];
  const transferItems = freshItems.filter((item) => item.primary_objective === targetObjective
    && { beginner: 0, intermediate: 1, advanced: 2 }[item.editorial_level] >= currentLevel);
  const transfer = selectEligible({ items: transferItems, context, seed: input.seed, targetObjective });
  if (transfer) return suggestion('transfer_same_objective', transfer, evidence, ['single_observation_only']);

  const prerequisiteTransfer = selectEligible({
    items: freshItems,
    context,
    seed: input.seed,
    targetObjective: null,
    includeAllowlistedObjectives: true,
  });
  return prerequisiteTransfer
    ? suggestion('transfer_allowlisted_objective', prerequisiteTransfer, evidence,
      ['single_observation_only', 'objective_is_allowlisted_not_mastered'])
    : insufficient('no_transfer_candidate', ['single_observation_only']);
}

export function recommendPractice(input) {
  try {
    const result = asPolicy(input);
    result.bank_version = isRecord(input?.bank) && validVersion(input.bank.bank_version)
      ? input.bank.bank_version
      : null;
    return result;
  } catch {
    return { ...invalid(), bank_version: null };
  }
}

export const PRACTICE_POLICY_VERSION = POLICY_VERSION;
