// Local progress. Everything stays in this browser; if storage is unavailable the app keeps working in memory.

const OUTCOMES = ['answered', 'unknown'];
const SCORES = [0, 0.5, 1];

export function safeLocalStorage() {
  try {
    const s = globalThis.localStorage;
    if (!s) return null;
    const probe = '__probe__';
    s.setItem(probe, '1');
    s.removeItem(probe);
    return s;
  } catch {
    return null;
  }
}

function freshState() {
  return { v: 1, seed: null, answers: [] };
}

function isAnswer(a) {
  if (!a || typeof a !== 'object' || Array.isArray(a)) return false;
  const keys = Object.keys(a).sort().join(',');
  if (keys !== 'attempt,option_id,outcome,question_id,score,t') return false;
  if (typeof a.question_id !== 'string' || a.question_id === '') return false;
  if (!OUTCOMES.includes(a.outcome)) return false;
  if (!Number.isFinite(a.t)) return false;
  if (!Number.isInteger(a.attempt) || a.attempt < 1) return false;
  if (a.outcome === 'answered') {
    return typeof a.option_id === 'string' && a.option_id !== '' && SCORES.includes(a.score);
  }
  return a.option_id === null && a.score === null;
}

function isState(s) {
  if (!s || typeof s !== 'object' || Array.isArray(s)) return false;
  const keys = Object.keys(s).sort().join(',');
  if (keys !== 'answers,seed,v') return false;
  if (s.v !== 1) return false;
  if (s.seed !== null && (typeof s.seed !== 'string' || s.seed === '')) return false;
  return Array.isArray(s.answers) && s.answers.every(isAnswer);
}

function randomSeed() {
  const bytes = new Uint8Array(8);
  if (globalThis.crypto && globalThis.crypto.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

export function createStore({ storage = safeLocalStorage(), prefix, now = () => Date.now() } = {}) {
  if (typeof prefix !== 'string' || prefix === '') throw new TypeError('createStore: prefix is required');
  const key = `${prefix}.progress`;
  let backing = storage || null;
  let memory = null;
  const store = { persistent: backing !== null };

  function read() {
    if (backing) {
      try {
        const raw = backing.getItem(key);
        if (raw === null || raw === undefined) return freshState();
        const parsed = JSON.parse(raw);
        return isState(parsed) ? parsed : freshState();
      } catch {
        return freshState();
      }
    }
    return memory ? structuredCloneSafe(memory) : freshState();
  }

  function write(state) {
    memory = structuredCloneSafe(state);
    if (!backing) return;
    try {
      backing.setItem(key, JSON.stringify(state));
    } catch {
      backing = null;
      store.persistent = false;
    }
  }

  function record(entry) {
    const state = read();
    const attempt = state.answers.filter((a) => a.question_id === entry.question_id).length + 1;
    const answer = { ...entry, t: now(), attempt };
    state.answers.push(answer);
    write(state);
    return { ...answer };
  }

  store.seed = () => {
    const state = read();
    if (!state.seed) {
      state.seed = randomSeed();
      write(state);
    }
    return state.seed;
  };

  store.answers = () => read().answers.map((a) => ({ ...a }));

  store.recordAnswer = ({ questionId, optionId, score }) =>
    record({ question_id: questionId, option_id: optionId, outcome: 'answered', score });

  store.recordUnknown = ({ questionId }) =>
    record({ question_id: questionId, option_id: null, outcome: 'unknown', score: null });

  store.attempts = (questionId) =>
    read()
      .answers.filter((a) => a.question_id === questionId)
      .sort((a, b) => a.attempt - b.attempt || a.t - b.t)
      .map((a) => ({ ...a }));

  store.latest = (questionId) => {
    const list = store.attempts(questionId);
    return list.length ? list[list.length - 1] : null;
  };

  store.reset = () => {
    memory = null;
    if (!backing) return;
    try {
      backing.removeItem(key);
    } catch {
      backing = null;
      store.persistent = false;
    }
  };

  store.exportJson = () => {
    store.seed();
    return JSON.stringify(read(), null, 2);
  };

  store.importJson = (text) => {
    let incoming;
    try {
      incoming = JSON.parse(text);
    } catch {
      return { ok: false, error: 'This file is not valid JSON.' };
    }
    if (!isState(incoming)) return { ok: false, error: 'This file is not a progress export from this site.' };
    const state = read();
    const seen = new Set(state.answers.map(dedupeKey));
    let imported = 0;
    for (const a of incoming.answers) {
      const k = dedupeKey(a);
      if (seen.has(k)) continue;
      seen.add(k);
      state.answers.push({ ...a });
      imported += 1;
    }
    if (!state.seed && incoming.seed) state.seed = incoming.seed;
    write(state);
    return { ok: true, imported };
  };

  return store;
}

function dedupeKey(a) {
  return `${a.question_id}|${a.t}|${a.option_id}|${a.outcome}`;
}

function structuredCloneSafe(value) {
  return JSON.parse(JSON.stringify(value));
}
