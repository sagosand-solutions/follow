// Client-side guard: refuse to render data the question view cannot show correctly.
// This is not the author-side validator; it only enforces what the view relies on.

export const QUESTION_ID = /^[a-z][a-z0-9-]{2,79}$/;
const LOCAL_ID = /^[a-z][a-z0-9-]{0,79}$/;
const TYPES = ['output', 'crashing-input', 'remove-line', 'equivalence', 'complexity', 'ai-review'];
const LEVELS = ['beginner', 'intermediate', 'advanced'];

const filled = (v) => typeof v === 'string' && v.trim() !== '';

export const PRACTICE_PROFILE = 'beginner-practice';

// The answer profile of a question that passed checkQuestion: only the explicit field says "practice". A legacy 0.1 document
// and a 0.2 `standard` document are the same standard kind. Nothing is inferred from the id, level, course or option count.
export function isPractice(q) {
  return !!q && q.schema_version === '0.2' && q.answer_profile === PRACTICE_PROFILE;
}

// Returns an error string, or null when the question is acceptable.
export function checkQuestion(q, id) {
  if (!q || typeof q !== 'object' || Array.isArray(q)) return 'not an object';
  // 0.1 is the legacy standard question and has no profile; 0.2 needs an explicit known profile. Never a silent fallback.
  if (q.schema_version === '0.1') {
    if ('answer_profile' in q) return 'answer_profile on 0.1';
  } else if (q.schema_version === '0.2') {
    if (q.answer_profile !== 'standard' && q.answer_profile !== PRACTICE_PROFILE) return 'answer_profile';
  } else {
    return 'unsupported schema_version';
  }
  const practice = isPractice(q);
  if (typeof q.id !== 'string' || !QUESTION_ID.test(q.id) || q.id !== id) return 'id';
  if (!TYPES.includes(q.type)) return 'type';
  if (!LEVELS.includes(q.level)) return 'level';
  if (!filled(q.prompt)) return 'prompt';
  if (!filled(q.explanation)) return 'explanation';

  if (!Array.isArray(q.snippets) || q.snippets.length === 0) return 'snippets';
  const snippetIds = new Set();
  for (const s of q.snippets) {
    if (!s || typeof s !== 'object') return 'snippet';
    if (typeof s.id !== 'string' || !LOCAL_ID.test(s.id) || snippetIds.has(s.id)) return 'snippet id';
    snippetIds.add(s.id);
    if (!filled(s.code)) return 'snippet code';
    if (s.label !== undefined && !filled(s.label)) return 'snippet label';
    if (s.highlight_lines !== undefined) {
      if (!Array.isArray(s.highlight_lines) || !s.highlight_lines.every((n) => Number.isInteger(n) && n >= 1)) return 'highlight_lines';
    }
  }

  if (!Array.isArray(q.options)) return 'option count';
  if (practice ? q.options.length !== 4 : q.options.length < 5 || q.options.length > 8) return 'option count';
  const optionIds = new Set();
  let full = 0;
  let half = 0;
  let zero = 0;
  for (const o of q.options) {
    if (!o || typeof o !== 'object') return 'option';
    if (typeof o.id !== 'string' || !LOCAL_ID.test(o.id) || optionIds.has(o.id)) return 'option id';
    optionIds.add(o.id);
    if (!filled(o.text) || !filled(o.feedback)) return 'option text';
    if (o.score === 1) {
      full += 1;
      if (o.misconception !== null) return 'correct option has a misconception';
      if (o.partial_reason !== undefined) return 'correct option has partial_reason';
    } else if (o.score === 0.5) {
      half += 1;
      if (!filled(o.misconception) || !filled(o.partial_reason)) return 'half option fields';
    } else if (o.score === 0) {
      zero += 1;
      if (!filled(o.misconception)) return 'wrong option misconception';
      if (o.partial_reason !== undefined) return 'wrong option has partial_reason';
    } else {
      return 'option score';
    }
  }
  if (full !== 1 || half !== 1 || (practice ? zero !== 2 : zero < 3)) return 'option scores';
  if (!q.author || !filled(q.author.name)) return 'author';
  return null;
}
