# Practice recommendation policy v1

`recommendPractice({ bank, history, context, seed })` is an experimental,
local, deterministic policy for suggesting one practice item. It is not a
placement model, assessment, ability estimate, mastery tracker, or production UI.
Suggestions never lock the course: every result has `course_open: true`.

The implementation is isolated in `policy.mjs`. It has no imports, runtime
dependencies, storage, network, timers, or random-global use. Inputs are read
only. Malformed input, unsupported versions, conflicting event identities, or
bounds violations return a controlled `invalid`/`insufficient` result rather
than throwing or fabricating an answer.

## Frozen API

### Bank

```js
{
  schema_version: 'practice-bank-v1',
  bank_version: 'example-bank-1',
  items: [{
    item_id: 'item-values-1',
    source_hash: '<64 lowercase hex SHA-256>',
    rubric_version: 'rubric-1',
    primary_objective: 'values',
    chapter_id: 'foundations',
    family_id: 'literal-output-a',
    editorial_level: 'beginner', // beginner | intermediate | advanced
    prerequisites: [], // objective IDs, not mastery claims
  }],
}
```

The bank is deliberately independent of question schema 0.1 and the course
format. Item-family and objective metadata must be authored explicitly; the
policy does not infer either from question wording, tags, or estimated
difficulty. `source_hash` and `rubric_version` bind recorded events to the exact
content/rubric version.

### History

```js
{
  schema_version: 'practice-history-v1',
  events: [{
    event_id: 'event-1', attempt_id: 'attempt-1', session_id: 'session-1',
    sequence: 1, // unique ordinal ordering value; elapsed time is not a score
    item_id: 'item-values-1', source_hash: '<64 lowercase hex SHA-256>',
    rubric_version: 'rubric-1', family_id: 'literal-output-a',
    mode: 'practice', // practice | placement | exam
    try_kind: 'first', // first | retry
    outcome: 'answered', // answered | unknown | abandoned | unanswered
    score: 1, // only answered: 0 | 0.5 | 1; otherwise null
    assisted: false, study_exposure: false, trace_exposure: false,
  }],
}
```

An event is an immutable final record for one attempt. Exact duplicate records
with the same event and attempt IDs are idempotent. Reusing either ID for
conflicting data, or reusing a sequence number, invalidates the history. Events
from placement/exam, retries, assistance, study, and trace still count as known
exposure, but never as fresh practice evidence. A stale source/rubric version is
not rescored; its recorded item and family remain exposed. Retired items not in
the current bank can still retain family exposure through the event's explicit
`family_id` snapshot.

### Context

```js
{
  selected_objective: null, // objective ID or null
  selected_chapter_id: null, // chapter ID or null
  allowed_prerequisite_objectives: [], // explicit candidate allowlist, NOT mastered
  known_item_exposures: [],
  known_family_exposures: [],
  history_integrity: 'ok', // ok | unknown | corrupt
}
```

Legacy progress lacking these versioned fields must be represented as
`history_integrity: 'unknown'`, never silently upgraded to a clean history.
`corrupt` fails closed. The learner's selected objective/chapter takes priority
over advice from prior results. Candidate prerequisite access comes only from
the explicit objective allowlist (and the chosen objective); this is not a
claim that the learner mastered any prerequisite.

### Result

```js
{
  policy_version: 'practice-policy-v1',
  bank_version: 'example-bank-1', // null only if bank identity is invalid
  status: 'suggestion', // suggestion | insufficient | invalid
  reason_code: 'start_from_course_entry',
  candidate_item_id: 'item-values-1', // null unless suggestion
  evidence_event_ids: [],
  limitations: ['local_advice_only', 'not_calibrated_or_diagnostic', 'course_remains_open'],
  course_open: true,
}
```

`reason_code` and `limitations` are stable data for a separately reviewed UI,
not learner-facing prose. No theta, percentages, level diagnosis, mastery,
certificate, or inferred misconception is returned. `evidence_event_ids` lists
only an unassisted, unexposed, first-attempt, answered practice event with a
current source and rubric version. Exposure-only history is not reported as
performance evidence.

`REASON_CODES` and `LIMITATION_CODES` are exported frozen arrays; every
returned result is checked against these closed vocabularies in the tests. The
reason-code set is:

- Suggestions: `start_from_course_entry`, `start_from_selected_objective`,
  `start_from_selected_chapter`, `repeat_objective_with_different_family`,
  `transfer_same_objective`, `transfer_allowlisted_objective`,
  `offer_introduction_after_unknown`, `offer_introduction_after_no_response`,
  `follow_up_with_another_example`, `follow_up_without_fresh_diagnosis`.
- Insufficient: `history_integrity_unknown`, `bank_empty`,
  `no_fresh_eligible_item`, `no_intro_candidate_after_unknown`,
  `no_intro_candidate_after_no_response`, `no_safe_follow_up_candidate`,
  `no_different_family_candidate`, `no_transfer_candidate`.
- Invalid: `invalid_input`, `invalid_seed`, `invalid_history`, `history_corrupt`.

The frozen limitation-code set is `local_advice_only`,
`not_calibrated_or_diagnostic`, `course_remains_open`,
`history_not_known_clean`, `no_practice_evidence_yet`,
`stale_item_version_not_scored`, `unknown_response_not_scored`,
`no_scored_response`, `assisted_or_retry_not_scored`,
`no_current_unassisted_scored_evidence`, `single_observation_only`, and
`objective_is_allowlisted_not_mastered`.

## Decision policy

1. Validate exact shapes and explicit versions. The bank is capped at 512 items;
   history at 4,096 events; context exposure lists at 4,096 IDs; per-item
   prerequisite lists at 512. Identity arrays must be unique. Invalid or
   conflicting data never reaches selection.
2. Treat exposure separately from performance. Exclude every seen item and
   every family present in the supplied session history or known-exposure lists.
   This policy conservatively applies family cooldown to the entire supplied
   history; it invents no retention or 30-day window.
3. If the learner selected an objective/chapter, recommend only an eligible
   item in that choice. Otherwise, with no practice result, choose among
   beginner items with no prerequisites.
4. Use only the latest practice event ordered by its explicit `sequence`;
   placement/exam results do not update or steer the practice outcome. A clean
   first-attempt score of 0 or 0.5 suggests the same objective in a different
   family. A clean score of 1 prefers a same-objective transfer item at the same
   or higher editorial level, then a candidate in an explicitly allowlisted
   objective. An unknown/unanswered/abandoned event can offer a beginner
   introduction with no prerequisites, otherwise returns insufficient evidence.
5. Retries, assisted/exposed attempts, and stale versions may motivate a
   different-example follow-up, but are never used as fresh scored evidence.
   They cannot create a mastery or diagnostic claim. If no safe fresh candidate
   exists, return insufficient rather than repeat an item/family as independent
   evidence.
6. Candidate ties use stable item IDs plus a supplied seed, FNV-1a 32-bit, and
   modulo over the sorted eligible IDs (iterating JavaScript UTF-16 code units).
   This is deterministic reproducibility, not security or statistical randomness.
   Different seeds can select different
   equivalent items; no `Math.random()` or hidden time input is used.

The policy is a simulation of a transparent rule, not evidence that the rule
improves learning. It has no item parameters or ability update. Practice,
placement, and exam outcomes are not combined into one score. Human review,
pilot/retention evidence, and any UI integration are separate future gates.
