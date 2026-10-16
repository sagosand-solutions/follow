// Pure helpers for the step player. A trace step is the state BEFORE its line runs:
// `line` is the next line to execute, `locals` are the values that line will see,
// and `stdout_len` is how much output exists so far.

// stdout_len counts Unicode code points (Python len), not UTF-16 units: slice by code points.
export function stdoutAt(trace, step) {
  const points = Array.from(trace.stdout);
  return points.length === step.stdout_len ? trace.stdout : points.slice(0, step.stdout_len).join('');
}

// Does a trace belong to exactly this question, snippet and input case? Returns null (fail closed) or
// { stdin, total, input } where `stdin` is the recorded input text ('' when the snippet reads none) and
// `total` the number of verification cases of that snippet (only the first one is replayed).
export function bindCase(question, snippetId, trace) {
  if (!question || !trace || trace.snippet_id !== snippetId) return null;
  const all = (question.verification && Array.isArray(question.verification.cases) ? question.verification.cases : []);
  const mine = all.map((c, i) => ({ c, i })).filter((x) => x.c.snippet_id === snippetId);
  if (mine.length === 0) return trace.stdin_case === 0 ? { stdin: '', total: 0 } : null;
  const first = mine[0];
  if (trace.stdin_case !== first.i) return null;
  const expected = first.c.expected;
  if (expected && typeof expected.stdout === 'string' && expected.stdout !== trace.stdout) return null;
  return { stdin: typeof first.c.stdin === 'string' ? first.c.stdin : '', total: mine.length };
}

// Does the step name the line that runs NEXT? Only `line` events do; call/return/exception are events that
// already happened (or, for generators, a yield recorded as a return and a resume recorded as a call).
export const isNextLine = (step) => step.event === 'line';

// Rows for the variable table. Values are compared only between two consecutive `line` steps with the same
// function name and depth (the trace has no frame identity, so nothing is claimed beyond that).
export function variableRows(prev, step) {
  const sameFrame = prev && prev.event === 'line' && step.event === 'line' && prev.depth === step.depth && prev.function === step.function;
  return Object.entries(step.locals).map(([name, value]) => {
    let change = 'same';
    if (sameFrame) {
      if (!(name in prev.locals)) change = 'new';
      else if (prev.locals[name] !== value) change = 'changed';
    }
    return { name, value, change };
  });
}

// Which question snippet a trace belongs to, and whether the trace was made from exactly this source.
export function pickTrace(file, snippetId) {
  if (!file || !Array.isArray(file.traces)) return null;
  return file.traces.find((t) => t.snippet_id === snippetId) || null;
}

export async function sha256Hex(text, subtle = globalThis.crypto && globalThis.crypto.subtle) {
  if (!subtle) return null;
  const buf = await subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export function clampStep(index, count) {
  if (count <= 0) return 0;
  return Math.min(Math.max(index, 0), count - 1);
}
