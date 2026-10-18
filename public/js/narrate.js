// The narrator line of the step player for beginner-practice questions: one sentence per step, built only from what the
// recording shows (new and changed values, new output) and never from guesses. Pure: no DOM. The result is a list of tokens,
// { text } or { code }, so names, values and output can be shown as code without building markup from strings.
//
// It speaks only where the recording proves the claim: a completed line with a provably same-frame "before" (moment.safe) and
// no names left out of the recording. Anything else returns null and the player keeps its usual sentence. A jump to a
// non-adjacent line is reported neutrally as "Next step: line N.", never as a skipped statement: the recording does not say
// whether the lines in between were statements, comments, blank or part of a longer expression.
import { t } from './i18n.js';
import { momentState } from './trace.js';

// A template with {slot}s -> tokens. A slot's value is plain text (string or number), one token, or a list of tokens.
export function fill(template, params) {
  const out = [];
  let last = 0;
  for (const m of template.matchAll(/\{(\w+)\}/g)) {
    if (m.index > last) out.push({ text: template.slice(last, m.index) });
    const value = params[m[1]];
    if (value === undefined) out.push({ text: m[0] });
    else if (Array.isArray(value)) out.push(...value);
    else if (typeof value === 'object') out.push(value);
    else out.push({ text: String(value) });
    last = m.index + m[0].length;
  }
  if (last < template.length) out.push({ text: template.slice(last) });
  return out;
}

const code = (value) => ({ code: String(value) });

// "a, b and c" for token lists, with the template's own separators.
const list = (items) => items.flatMap((item, i) => (i === 0 ? item : [{ text: i === items.length - 1 ? t('step.say.and') : t('step.say.comma') }, ...item]));

// The printed text, one code token per printed line, in the course's "then" style: `GO` then `2`.
function printed(added) {
  const lines = (added.endsWith('\n') ? added.slice(0, -1) : added).split('\n');
  return lines.flatMap((line, i) => [...(i ? [{ text: t('step.say.then') }] : []), line === '' ? { text: t('step.say.emptyLine') } : code(line)]);
}

export function narrate(trace, moments, index) {
  const moment = moments[index];
  if (!moment || moment.kind !== 'ran' || !moment.after || !moment.safe) return null;
  const state = momentState(trace, moment);
  if (state.mode !== 'after' || (state.omitted || 0) > 0) return null;
  if (state.scope && state.scope !== 'module') return null;   // inside functions the player's own sentence stays
  const rows = state.scope ? state.globals : state.rows;
  const before = state.scope ? moment.step.globals : moment.step.locals;
  const line = moment.step.line;
  const changes = rows.filter((r) => r.change !== 'same').map((r) => (r.change === 'new'
    ? fill(t('step.say.new'), { name: code(r.name), value: code(r.value) })
    : fill(t('step.say.changed'), { name: code(r.name), old: code(before[r.name]), value: code(r.value) })));
  let tokens;
  if (!changes.length && !state.added) tokens = fill(t('step.say.none'), { line });
  else if (!changes.length) tokens = fill(t('step.say.printsOnly'), { line, out: printed(state.added) });
  else {
    const parts = state.added ? [...changes, fill(t('step.say.itPrints'), { out: printed(state.added) })] : changes;
    tokens = fill(t('step.say.changes'), { line, changes: list(parts) });
  }
  const next = moments[index + 1];
  if (next && next.kind === 'ran' && next.step.depth === moment.step.depth && next.step.function === moment.step.function && next.step.line !== line + 1) {
    tokens.push({ text: ' ' }, ...fill(t('step.say.next'), { line: next.step.line }));
  }
  return tokens;
}

export const tokensText = (tokens) => tokens.map((tok) => tok.code ?? tok.text).join('');
