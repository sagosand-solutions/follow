// Pure helpers for the step player. A trace `line` step is the state BEFORE its line runs:
// `line` is the next line to execute, `locals` are the values that line will see,
// and `stdout_len` is how much output exists so far. The player shows the state AFTER the line (see buildMoments).

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


// Rows for the variable table: every variable in `after`, flagged `new` or `changed` relative to `before`
// (plain values as strings, exactly as recorded). `before` null means nothing is flagged.
export function diffLocals(before, after) {
  return Object.entries(after).map(([name, value]) => {
    let change = 'same';
    if (before) {
      if (!(name in before)) change = 'new';
      else if (before[name] !== value) change = 'changed';
    }
    return { name, value, change };
  });
}

// What the player steps through, in the recorded order. A trace `line` step is the state BEFORE that line runs, so a
// line is shown as "ran" only when the recording has the observation that completes it: the next step in the SAME
// frame (frames are matched by depth; a frame is left only by return/unwind at its depth). Calls, resumes, yields,
// exceptions and returns are shown as their own events, in order, so no later result or output is shown early:
//   ran          a line completed; `after` is the completing step (state after the line). `safe` is true only when no
//                step at this depth or shallower lay between the two observations (same frame provably), so changes
//                may be flagged.
//   event        call / resume / yield / return / unwind / exception, with its own recorded state. `callerLine` is the
//                line in progress in the calling frame for call and resume.
//   interrupted  a line that never completed because an exception left it (a callee unwound into it, or it raised)
//                and execution carried on at `after` (a handler).
// A line still pending when the recording ends (truncated) is a `ran` moment with after: null. The empty module start
// (first call) is skipped.
export function buildMoments(trace) {
  const steps = trace.steps;
  const moments = [];
  const pending = new Map(); // depth -> { index, interrupted }
  const complete = (d, j) => {
    const p = pending.get(d);
    if (!p) return;
    const step = steps[p.index];
    if (p.interrupted) moments.push({ kind: 'interrupted', step, after: steps[j] });
    else {
      let safe = true;
      for (let k = p.index + 1; k < j; k += 1) if (steps[k].depth <= d) safe = false;
      moments.push({ kind: 'ran', step, after: steps[j], safe });
    }
    pending.delete(d);
  };
  steps.forEach((step, j) => {
    if (j === 0 && step.event === 'call' && step.function === '<module>') return;
    const d = step.depth;
    switch (step.event) {
      case 'line':
        complete(d, j);
        pending.set(d, { index: j, interrupted: false });
        break;
      case 'return': {
        complete(d, j);
        moments.push({ kind: 'event', step, callerLine: null });
        break;
      }
      case 'call':
      case 'resume': {
        const caller = pending.get(d - 1);
        moments.push({ kind: 'event', step, callerLine: caller ? steps[caller.index].line : null });
        break;
      }
      case 'exception': {
        const p = pending.get(d);
        if (p) p.interrupted = true;
        moments.push({ kind: 'event', step, callerLine: null });
        break;
      }
      case 'unwind': {
        pending.delete(d);
        const outer = pending.get(d - 1);
        if (outer) outer.interrupted = true;
        moments.push({ kind: 'event', step, callerLine: null });
        break;
      }
      default: // yield: a pause, the line is not complete
        moments.push({ kind: 'event', step, callerLine: null });
    }
  });
  // When the recording was cut short, the innermost pending line has no after-state; say so. (In a complete recording
  // a pending line belongs to a generator suspended at a yield, which is not an ending.)
  const open = [...pending.keys()].sort((x, y) => y - x)[0];
  if (trace.truncated && open !== undefined) moments.push({ kind: 'ran', step: steps[pending.get(open).index], after: null, safe: false });
  // Output shown so far never shrinks: a line that completes late (after its callee printed) only adds what is new
  // since the previous moment.
  let run = 0;
  for (const m of moments) {
    m.baseLen = run;
    const end = m.kind === 'ran' && m.after ? m.after.stdout_len : m.kind === 'interrupted' ? m.after.stdout_len : m.step.stdout_len;
    run = Math.max(run, end);
  }
  return moments;
}

// The state the table and output show for a moment, and what changed in it.
export function momentState(trace, moment) {
  const { step, after } = moment;
  if (moment.kind === 'ran' && after) {
    const points = Array.from(trace.stdout);
    const endLen = Math.min(after.stdout_len, points.length);
    const startLen = Math.min(Math.max(moment.baseLen || 0, step.stdout_len), endLen);
    const shown = points.slice(0, startLen).join('');
    const added = points.slice(startLen, endLen).join('');
    return { mode: 'after', rows: diffLocals(moment.safe ? step.locals : null, after.locals), shown, added };
  }
  if (moment.kind === 'interrupted') return { mode: 'event', rows: diffLocals(null, after.locals), shown: stdoutAt(trace, after), added: '' };
  const len = Math.max(moment.baseLen || 0, step.stdout_len);
  return { mode: moment.kind === 'ran' ? 'before' : 'event', rows: diffLocals(null, step.locals), shown: Array.from(trace.stdout).slice(0, len).join(''), added: '' };
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

// The envelope is checked before any field of any trace is read.
export function validEnvelope(file, questionId) {
  return !!file && typeof file === 'object' && file.schema_version === TRACE_VERSION && file.question_id === questionId && Array.isArray(file.traces);
}

// Decides which traces may be shown for a question: for each snippet, a trace that passes every check
// (envelope, steps array, source hash, case identity, stdin text and hash, truncation consistency).
// Returns [{ index, trace, input }]; anything doubtful is simply left out (fail closed). No DOM, no network.
export async function usableTraces(question, file, hash = sha256Hex) {
  if (!validEnvelope(file, question && question.id)) return [];
  const out = [];
  for (let i = 0; i < question.snippets.length; i += 1) {
    const snippet = question.snippets[i];
    const trace = pickTrace(file, snippet.id);
    if (!trace || !Array.isArray(trace.steps) || trace.steps.length === 0 || typeof trace.stdout !== 'string') continue;
    if ((await hash(snippet.code)) !== trace.source_sha256) continue;
    const input = bindCase(question, snippet.id, trace, file);
    if (!input || !(await stdinMatches(trace, hash))) continue;
    if (trace.truncated && trace.truncated_at_step !== trace.steps.length) continue;
    out.push({ index: i, trace, input });
  }
  return out;
}
