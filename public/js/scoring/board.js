// Pure helpers for the server-scored profile and leaderboard views. No DOM, storage or network: callers pass the server's
// answers (already parsed JSON) and get validated shapes, text keys and display strings back. The client never ranks, sorts or
// computes points: rank, order and `score_display` always come from the server, and an answer that does not have the expected
// shape is refused (null) rather than repaired.

const FAMILIES = ['official', 'free'];
const RANK_KINDS = ['actual', 'hypothetical', 'none'];
const OUTCOMES = ['correct', 'partial', 'wrong', 'not_sure'];
// Exact centipoints as the server prints them: an optional minus, whole points, then exactly two decimals.
const DISPLAY = /^-?\d{1,7}\.\d{2}$/;
const VERSION = /^[A-Za-z0-9._-]{1,64}$/;
const REVISION = /^\d{1,20}$/;
const CURSOR = /^\d{1,9}\.\d{1,20}\.[0-9a-f]{24}$/;
const ALIAS_MAX = 64;

const isObject = (value) => !!value && typeof value === 'object' && !Array.isArray(value);
const count = (value) => Number.isInteger(value) && value >= 0;
const text = (re, value) => typeof value === 'string' && re.test(value);
const epoch = (value) => Number.isInteger(value) && value >= 0;

function validWindow(w) {
  if (!isObject(w)) return false;
  if (w.kind === 'all') return w.start === null && w.end === null;
  return w.kind === 'week' && epoch(w.start) && epoch(w.end) && w.end > w.start;
}

// The fields both answers share and that bind them to one board snapshot.
function validHead(d) {
  return isObject(d) && FAMILIES.includes(d.family) && text(VERSION, d.rules_version) && text(VERSION, d.catalog_version)
    && typeof d.scope === 'string' && validWindow(d.window) && text(REVISION, d.board_revision);
}

const validScore = (s) => isObject(s) && text(DISPLAY, s.score_display) && count(s.answered) && count(s.pool);

function validRank(r) {
  if (!isObject(r) || !RANK_KINDS.includes(r.kind) || !count(r.participants)) return false;
  if (r.kind === 'none') return r.value === null;
  return Number.isInteger(r.value) && r.value >= 1 && r.value <= r.participants + 1;
}

// GET /scoring/me -> the fields the views use, or null. Attempts keep only what the history shows (no question id); an attempt
// counts as ranked only when the server says `rank_eligible: true`.
export function parseMe(d) {
  if (!validHead(d) || !validScore(d.total) || !validRank(d.rank) || typeof d.published !== 'boolean') return null;
  if (d.scope_score !== null && !(validScore(d.scope_score) && d.scope_score.scope === d.scope)) return null;
  if (!['ok', 'hidden'].includes(d.moderation)) return null;
  if (d.own_cursor !== null && !text(CURSOR, d.own_cursor)) return null;
  // An actual place belongs only to a published, visible account; the other kinds never come with a cursor.
  if (d.rank.kind === 'actual' && (!d.published || d.moderation !== 'ok' || d.scope_score === null)) return null;
  if (d.rank.kind !== 'actual' && d.own_cursor !== null) return null;
  if (d.rank.kind !== 'none' && d.scope_score === null) return null;
  const courses = Array.isArray(d.courses) ? d.courses : null;
  if (!courses || !courses.every((c) => validScore(c) && typeof c.course_id === 'string')) return null;
  const attempts = Array.isArray(d.attempts) ? d.attempts : [];
  return {
    family: d.family, rules_version: d.rules_version, catalog_version: d.catalog_version, scope: d.scope,
    window: { kind: d.window.kind, start: d.window.start, end: d.window.end }, board_revision: d.board_revision,
    total: pick(d.total), scope_score: d.scope_score === null ? null : pick(d.scope_score),
    courses: courses.map((c) => ({ course_id: c.course_id, ...pick(c) })),
    attempts: attempts.filter((a) => isObject(a) && OUTCOMES.includes(a.outcome) && typeof a.course_id === 'string' && Number.isFinite(a.accepted_at))
      .map((a) => ({ course_id: a.course_id, outcome: a.outcome, accepted_at: a.accepted_at, rank_eligible: a.rank_eligible === true })),
    rank: { kind: d.rank.kind, value: d.rank.value, participants: d.rank.participants },
    published: d.published, moderation: d.moderation, own_cursor: d.own_cursor,
  };
}

const pick = (s) => ({ score_display: s.score_display, answered: s.answered, pool: s.pool });

function validEntry(e) {
  return isObject(e) && Number.isInteger(e.rank) && e.rank >= 1 && typeof e.alias === 'string' && e.alias.length >= 1
    && e.alias.length <= ALIAS_MAX && validScore(e) && (e.is_me === undefined || e.is_me === true);
}

// GET /leaderboards -> the page in the server's order, or null. Ranks never go down the page, and at most one row is yours.
export function parsePage(d) {
  if (!validHead(d) || !count(d.participants) || !Array.isArray(d.entries) || !d.entries.every(validEntry)) return null;
  if (d.next_cursor !== null && !text(CURSOR, d.next_cursor)) return null;
  if (d.entries.some((e, i) => i > 0 && e.rank < d.entries[i - 1].rank)) return null;
  if (d.entries.filter((e) => e.is_me).length > 1) return null;
  return {
    family: d.family, rules_version: d.rules_version, catalog_version: d.catalog_version, scope: d.scope,
    window: { kind: d.window.kind, start: d.window.start, end: d.window.end }, board_revision: d.board_revision,
    participants: d.participants, next_cursor: d.next_cursor,
    entries: d.entries.map((e) => ({ rank: e.rank, alias: e.alias, ...pick(e), me: e.is_me === true })),
  };
}

// True when two answers describe the same board at the same moment. Anything else is a stale read: start again, never mix.
export function sameSnapshot(a, b) {
  return !!a && !!b && a.family === b.family && a.catalog_version === b.catalog_version && a.rules_version === b.rules_version
    && a.scope === b.scope && a.window.kind === b.window.kind && a.window.start === b.window.start && a.window.end === b.window.end
    && a.board_revision === b.board_revision;
}

// "Publish my score" is offered only to a signed-in account that is not on the list and not hidden. `signedIn` comes from the
// account's own /me, never from the scoring answer, so a score played signed out is never offered for publishing.
export const canPublish = (me, signedIn) => signedIn === true && !me.published && me.moderation === 'ok';

// The server's exact two-decimal string, shown with a real minus sign and the locale's decimal mark. No floating point: the
// digits are the server's, only the characters around them change. Anything that is not such a string is shown as a dash.
export function localScore(display, locale = 'en') {
  if (!text(DISPLAY, display)) return '–';
  let mark = '.';
  try {
    mark = new Intl.NumberFormat(locale).formatToParts(1.5).find((p) => p.type === 'decimal')?.value || '.';
  } catch { /* an unknown locale keeps the point */ }
  const negative = display.startsWith('-') && !/^-0+\.00$/.test(display);
  const [whole, cents] = display.replace(/^-/, '').split('.');
  return `${negative ? '−' : ''}${whole}${mark}${cents}`;
}

// The week a list covers, for the reader: dates in UTC (the scoring rule) and the reset in the reader's own time zone, always
// with the zone's name. `timeZone` and `locale` are parameters so the text can be checked for any reader.
export function weekText(window, { locale = 'en', timeZone } = {}) {
  if (!window || window.kind !== 'week') return null;
  const day = (s, zone) => new Intl.DateTimeFormat(locale, { weekday: 'short', day: 'numeric', month: 'short', timeZone: zone }).format(new Date(s * 1000));
  const local = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short', ...(timeZone ? { timeZone } : {}) });
  const parts = local.formatToParts(new Date(window.end * 1000));
  const get = (type) => parts.find((p) => p.type === type)?.value || '';
  return {
    from: day(window.start, 'UTC'),
    to: day(window.end, 'UTC'),
    resetDay: day(window.end, timeZone),
    resetTime: `${get('hour')}:${get('minute')}`,
    zone: get('timeZoneName'),
  };
}

// The rows a "load more" adds: the page's own rows, numbered for the status line ("Rows 51–100 loaded.").
export function loadedRange(before, page) {
  return page.entries.length ? { from: before + 1, to: before + page.entries.length } : null;
}
