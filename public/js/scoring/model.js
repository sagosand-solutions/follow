// Pure scoring rules for the profile and leaderboard views (design/profile-leaderboard-v1, scoring contract v1).
// No DOM, storage, network or clock: callers pass parsed courses, answer records and `now`.

// Points per first try. The challenge counts the FIRST try only; later tries are practice.
export const RULES = Object.freeze({ version: 'r2', right: 1, partial: 0.5, unsure: 0, wrong: -0.5 });

export function outcomeOf(record) {
  if (!record) return null;
  if (record.outcome === 'unknown') return 'unsure';
  if (record.score === 1) return 'right';
  if (record.score === 0.5) return 'partial';
  return 'wrong';
}

export const pointsFor = (record, rules = RULES) => {
  const kind = outcomeOf(record);
  return kind ? rules[kind] : 0;
};

const valid = (r) => r && typeof r === 'object' && typeof r.question_id === 'string' && Number.isInteger(r.attempt) && r.attempt >= 1 && Number.isFinite(r.t);

// The first try per question (attempt 1, or the earliest when attempt 1 is missing after an import).
export function firstTries(records) {
  const first = new Map();
  for (const r of Array.isArray(records) ? records : []) {
    if (!valid(r)) continue;
    const prev = first.get(r.question_id);
    if (!prev || r.attempt < prev.attempt || (r.attempt === prev.attempt && r.t < prev.t)) first.set(r.question_id, r);
  }
  return first;
}

// The newest try per question.
export function latestTries(records) {
  const last = new Map();
  for (const r of Array.isArray(records) ? records : []) {
    if (!valid(r)) continue;
    const prev = last.get(r.question_id);
    if (!prev || r.attempt > prev.attempt || (r.attempt === prev.attempt && r.t >= prev.t)) last.set(r.question_id, r);
  }
  return last;
}

export function questionIds(course) {
  const ids = [];
  for (const level of (course && course.levels) || []) for (const lesson of level.lessons || []) for (const id of lesson.questions || []) if (!ids.includes(id)) ids.push(id);
  return ids;
}

// Course points on the contract's scale: 1000 x sum of first-try points / whole pool (unanswered count as 0).
export function coursePoints(sum, pool) {
  return Number.isInteger(pool) && pool > 0 ? (1000 * sum) / pool : null;
}

export function courseScore(course, records, rules = RULES) {
  const ids = questionIds(course);
  const first = firstTries(records);
  const latest = latestTries(records);
  const counts = { right: 0, partial: 0, unsure: 0, wrong: 0 };
  let sum = 0;
  let learned = 0;
  let improved = 0;
  for (const id of ids) {
    const r = first.get(id);
    if (r) {
      counts[outcomeOf(r)] += 1;
      sum += pointsFor(r, rules);
    }
    const last = latest.get(id);
    if (last && outcomeOf(last) === 'right') {
      learned += 1;
      if (r && outcomeOf(r) !== 'right') improved += 1;
    }
  }
  const answered = counts.right + counts.partial + counts.unsure + counts.wrong;
  return { pool: ids.length, answered, counts, sum, points: coursePoints(sum, ids.length), learned, improved };
}

// The total is the mean over ALL courses in the catalogue, not started ones counting as 0. Null without courses.
export function totalPoints(scores) {
  const list = (scores || []).filter((s) => s && s.points !== null && Number.isFinite(s.points));
  if (!list.length || list.length !== (scores || []).length) return null;
  return list.reduce((a, s) => a + s.points, 0) / list.length;
}

// Whole points for display only (ranking uses exact values); a real minus sign for negatives.
export function formatPoints(points) {
  if (points === null || !Number.isFinite(points)) return '–';
  const n = Math.round(points);
  return n < 0 ? `−${Math.abs(n)}` : String(n);
}

export function formatDelta(points) {
  if (points > 0) return `+${points}`;
  if (points < 0) return `−${Math.abs(points)}`;
  return '0';
}

// rows sorted by points, highest first -> rank with ties sharing a place (1, 1, 3).
export function sharedRanks(rows) {
  const sorted = [...rows].sort((a, b) => b.points - a.points);
  let rank = 0;
  return sorted.map((row, i) => {
    if (i === 0 || row.points !== sorted[i - 1].points) rank = i + 1;
    return { ...row, rank };
  });
}

// The visible part of a list: the top `top` rows and `around` rows on each side of the row with `meId`, with
// { gap: true } markers where rows are left out. Never the bottom of the list unless it is next to you.
export function boardWindow(ranked, meId, { top = 3, around = 2 } = {}) {
  const at = ranked.findIndex((r) => r.id === meId);
  const keep = new Set();
  for (let i = 0; i < Math.min(top, ranked.length); i += 1) keep.add(i);
  if (at >= 0) for (let i = Math.max(0, at - around); i <= Math.min(ranked.length - 1, at + around); i += 1) keep.add(i);
  const out = [];
  let last = -1;
  for (const i of [...keep].sort((a, b) => a - b)) {
    if (i > last + 1) out.push({ gap: true });
    out.push(ranked[i]);
    last = i;
  }
  if (last < ranked.length - 1) out.push({ gap: true });
  return out;
}

// Monday 00:00 UTC of the ISO week that contains `t` (ms).
export function weekStart(t) {
  const d = new Date(t);
  const day = (d.getUTCDay() + 6) % 7;
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - day);
}

// Calm history: one entry per week with activity or not, newest first, `weeks` weeks back from `now`.
export function recentWeeks(records, now, weeks = 8) {
  const current = weekStart(now);
  const out = [];
  for (let w = 0; w < weeks; w += 1) out.push({ start: current - w * 7 * 864e5, questions: new Set(), cleared: 0 });
  const latestBefore = new Map();
  const sorted = (Array.isArray(records) ? records : []).filter(valid).sort((a, b) => a.t - b.t);
  for (const r of sorted) {
    const idx = Math.round((current - weekStart(r.t)) / (7 * 864e5));
    const prev = latestBefore.get(r.question_id);
    latestBefore.set(r.question_id, r);
    if (idx < 0 || idx >= weeks) continue;
    out[idx].questions.add(r.question_id);
    // Cleared: a question that was missed or not sure before and is right now.
    if (prev && outcomeOf(prev) !== 'right' && outcomeOf(r) === 'right') out[idx].cleared += 1;
  }
  return out.map((w) => ({ start: w.start, read: w.questions.size, cleared: w.cleared }));
}
