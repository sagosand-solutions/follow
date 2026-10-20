// Text artifacts of a 0.3 question: typed display text that is only ever shown, never run and never traced. Pure checks, no DOM.
// The author-side rules (validator/artifacts.py) are the same ones; the URL cases in web-tests/fixtures are shared by both.

const LOCAL_ID = /^[a-z][a-z0-9-]{0,79}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const FORBIDDEN_CHARS = /[\u0000-\u0008\u000B-\u001F\u007F-\u009F\u2028\u2029]/;
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

export const ARTIFACT_FORMATS = { text: 'plain', diff: 'unified-diff', 'http-transcript': 'http-1.1-transcript', 'test-report': 'plain', policy: 'plain' };
export const ARTIFACT_LIMITS = { count: 3, chars: 4000, lines: 80, label: 80 };

const exactKeys = (value, required, optional = []) => {
  const keys = Object.keys(value);
  return required.every((k) => keys.includes(k)) && keys.every((k) => required.includes(k) || optional.includes(k));
};

// `scheme://authority`; the authority ends at white space (as Python's \s sees it, not U+FEFF), a path, query, fragment or quote.
const URL_AUTHORITY = /[A-Za-z][A-Za-z0-9+.\-]*:\/\/([^\t\n\v\f\r \u0085\u00a0\u1680 - \u2028\u2029\u202f\u205f\u3000/?#"'<>]*)/g;
const RESERVED_SUFFIXES = ['example.com', 'example.net', 'example.org', 'example', 'test', 'invalid'];
const IPV4 = /^(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})$/;
const DOCUMENTATION_NETS = [[192, 0, 2], [198, 51, 100], [203, 0, 113]];

// A synthetic-text policy, not a network control: the names an example may use (documentation domains and TLDs, localhost, loopback,
// the TEST-NETs). localhost and loopback can be a real local service, and nothing ever fetches a URL: no safety decision rests on this.
function reservedHost(authority) {
  if (authority.includes('@') || authority.startsWith('[')) return false;
  let host = authority.replace(/[.,;:)\]}]+$/, '');
  const parts = host.split(':');
  if (parts.length === 2 && /^[0-9]+$/.test(parts[1])) host = parts[0];
  host = host.toLowerCase().replace(/\.+$/, '');
  if (!host) return false;
  if (host === 'localhost') return true;
  const ip = IPV4.exec(host);
  if (ip) {
    const octets = ip.slice(1).map(Number);
    if (octets.some((n) => n > 255)) return false;
    return host === '127.0.0.1' || DOCUMENTATION_NETS.some((net) => net.every((n, i) => octets[i] === n));
  }
  return RESERVED_SUFFIXES.some((s) => host === s || host.endsWith(`.${s}`));
}

// Authorities in `text` that are not reserved test hosts.
export function urlFindings(text) {
  return [...text.matchAll(URL_AUTHORITY)].map((m) => m[1]).filter((a) => !reservedHost(a));
}

const lineCount = (text) => text.split('\n').length - 1 + (text === '' || text.endsWith('\n') ? 0 : 1);

// Returns an error string, or null when every artifact is acceptable. `taken` holds the snippet ids already used.
export function checkArtifacts(list, taken = new Set()) {
  if (!Array.isArray(list) || list.length < 1 || list.length > ARTIFACT_LIMITS.count) return 'artifacts';
  const ids = new Set(taken);
  for (const a of list) {
    if (!a || typeof a !== 'object' || Array.isArray(a)) return 'artifact';
    if (!exactKeys(a, ['id', 'kind', 'format', 'text', 'sha256', 'provenance'], ['label'])) return 'artifact keys';
    if (typeof a.id !== 'string' || !LOCAL_ID.test(a.id) || ids.has(a.id)) return 'artifact id';
    ids.add(a.id);
    if (!Object.hasOwn(ARTIFACT_FORMATS, a.kind) || a.format !== ARTIFACT_FORMATS[a.kind]) return 'artifact kind';
    if (typeof a.text !== 'string' || a.text.trim() === '' || Array.from(a.text).length > ARTIFACT_LIMITS.chars) return 'artifact text';
    if (FORBIDDEN_CHARS.test(a.text) || LONE_SURROGATE.test(a.text)) return 'artifact text characters';
    if (lineCount(a.text) > ARTIFACT_LIMITS.lines) return 'artifact lines';
    if (urlFindings(a.text).length) return 'artifact url';
    if (typeof a.sha256 !== 'string' || !HEX64.test(a.sha256)) return 'artifact sha256';
    const p = a.provenance;
    if (!p || typeof p !== 'object' || Array.isArray(p) || !exactKeys(p, ['fixture_id', 'fixture_output_sha256'])) return 'artifact provenance';
    if (typeof p.fixture_id !== 'string' || !LOCAL_ID.test(p.fixture_id) || typeof p.fixture_output_sha256 !== 'string' || !HEX64.test(p.fixture_output_sha256)) return 'artifact provenance';
    if (a.label !== undefined && (typeof a.label !== 'string' || a.label.trim() === '' || Array.from(a.label).length > ARTIFACT_LIMITS.label || /[\u0000-\u001F\u007F-\u009F\u2028\u2029]/.test(a.label) || LONE_SURROGATE.test(a.label))) return 'artifact label';
  }
  return null;
}
