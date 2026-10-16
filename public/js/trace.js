// Pure helpers for the step player. A trace step is the state BEFORE its line runs:
// `line` is the next line to execute, `locals` are the values that line will see,
// and `stdout_len` is how much output exists so far.

// stdout_len counts Unicode code points (Python len), not UTF-16 units: slice by code points.
export function stdoutAt(trace, step) {
  const points = Array.from(trace.stdout);
  return points.length === step.stdout_len ? trace.stdout : points.slice(0, step.stdout_len).join('');
}

export const TRACE_VERSION = '0.2';

// Binds a trace to the question's current verification case by identity (schema 0.2): the trace must name this
// snippet and the FIRST case of that snippet by `case_id`, carry that case's current stdin text, and its stdout
// must equal the case's expected stdout. Fails closed (null) on any mismatch, on a version other than 0.2, and
// when the snippet has no case. The hash of stdin is checked separately (async) by `stdinMatches`.
// Returns { stdin, total } where `total` is the number of verification cases of that snippet.
export function bindCase(question, snippetId, trace, file = null) {
  if (!question || !trace || trace.snippet_id !== snippetId) return null;
  if (file && (file.schema_version !== TRACE_VERSION || file.question_id !== question.id)) return null;
  const all = question.verification && Array.isArray(question.verification.cases) ? question.verification.cases : [];
  const mine = all.filter((c) => c.snippet_id === snippetId);
  if (mine.length === 0) return null;
  const first = mine[0];
  if (trace.case_id !== first.id) return null;
  const stdin = typeof first.stdin === 'string' ? first.stdin : '';
  if (trace.stdin !== stdin) return null;
  const expected = first.expected;
  if (expected && typeof expected.stdout === 'string' && expected.stdout !== trace.stdout) return null;
  return { stdin, total: mine.length };
}

export async function stdinMatches(trace, hash = sha256Hex) {
  const digest = await hash(trace.stdin);
  return digest !== null && digest === trace.stdin_sha256;
}

// Does the step name the line that runs NEXT? Only `line` events do; every other event happened at its line.
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

// Which question snippet a trace belongs to, and whether the trace's recorded source hash equals the source on screen.
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
