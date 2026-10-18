// Made-up leaderboard data for the scoring views while the server API does not exist (CONFIG.scoringUi, test mode).
// Deterministic: the same list, period and course always give the same rows. Every page that uses it says so.

const NAMES = ['quiet_lambda', 'Mira K', 'pyreader', 'tab_and_space', 'nullable', 'off_by_one', 'Reader 4821', 'lazy_eval', 'Sam R', 'join_on_me', 'idempotent', 'yield_from', 'Noor', 'backoff', 'slice_of_pi', 'Reader 1077', 'mutable_default', 'T. Okafor', 'early_return', 'rowcount'];

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hash(text) {
  let h = 2166136261;
  for (const ch of text) h = Math.imul(h ^ ch.codePointAt(0), 16777619);
  return h >>> 0;
}

// { family: 'challenge'|'free', window: 'week'|'all', course: id|'all', pool } -> { participants, rows }
export function mockBoard({ family, window, course, pool = 36 }) {
  const random = rng(hash(`${family}|${window}|${course}`));
  const participants = (family === 'free' ? 212 : 140) / (window === 'week' ? 4 : 1);
  const rows = [];
  for (let i = 0; i < Math.round(participants); i += 1) {
    const answered = Math.max(1, Math.round(pool * (window === 'week' ? 0.15 + random() * 0.5 : 0.3 + random() * 0.7)));
    // Points on the 1000 scale: a skill share of the answered questions, minus a few confident misses.
    const skill = random();
    const points = Math.round((1000 * answered * (skill * 0.95 - (1 - skill) * 0.1)) / pool);
    const name = i < NAMES.length ? NAMES[i] : `Reader ${1000 + Math.floor(random() * 9000)}`;
    rows.push({ id: `m${i}`, name, points, answered, pool });
  }
  return { participants: rows.length, rows };
}
