// Pure helpers for the step player. A trace step is the state BEFORE its line runs:
// `line` is the next line to execute, `locals` are the values that line will see,
// and `stdout_len` is how much output exists so far.

export function stdoutAt(trace, step) {
  return trace.stdout.slice(0, step.stdout_len);
}

// Rows for the variable table. Only compares inside the same frame (same function and depth);
// across a call or return everything is shown as unchanged, because the frame itself changed.
export function variableRows(prev, step) {
  const sameFrame = prev && prev.depth === step.depth && prev.function === step.function;
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
