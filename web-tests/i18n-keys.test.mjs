// Static i18n check: every literal t('key')/t("key") in public/js must exist in en.json, and every dynamic key family
// must be declared here explicitly with its pinned number of concrete keys. Missing keys FAIL; unused keys are only
// reported (never deleted here). A new dynamic family, or a new member of a pinned family, fails until declared.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';

const en = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const JS_ROOT = new URL('../public/js/', import.meta.url);

// Explicit, bounded registry: family prefix -> the exact number of concrete en.json keys it owns today. This is not a
// wildcard: an undeclared dynamic template fails, and a family whose key count changes fails until the pin is updated.
const DYNAMIC_FAMILIES = {
  'about.limits.': 4, 'board.': 29, 'board.list.': 3, 'board.period.': 3, 'board.tag.': 2,
  'course.import.error.': 4, 'lesson.status.': 5, 'level.': 3, 'map.': 7, 'mock.scenario.': 5,
  'premium.q.': 10, 'provider.': 5, 'purchase.after.': 2, 'step.': 68, 'step.scope.': 3,
  'step.globals': 4, 'step.locals': 3, 'theme.name.': 6, 'type.': 6,
  'path.soon.1.': 2, 'path.soon.2.': 2,
};
// The two `${key}.…` call sites build their key from PATH_SOON (catalog-card.js): name them explicitly here.
const TEMPLATE_FAMILIES = { '${key}.title': ['path.soon.1.', 'path.soon.2.'], '${key}.text': ['path.soon.1.', 'path.soon.2.'] };

function jsFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = new URL(name, dir);
    if (statSync(path).isDirectory()) out.push(...jsFiles(new URL(`${name}/`, dir)));
    else if (name.endsWith('.js')) out.push(path);
  }
  return out;
}

function readSources() {
  const sources = {};
  for (const path of jsFiles(JS_ROOT)) sources[path.pathname] = readFileSync(path, 'utf8');
  return sources;
}

export function scan(sources) {
  const literals = new Set();
  const templates = new Set();
  for (const source of Object.values(sources)) {
    for (const match of source.matchAll(/\bt\(\s*(['"])([^'"]+)\1/g)) literals.add(match[2]);
    for (const match of source.matchAll(/\bt\(\s*`([^`]*)`/g)) templates.add(match[1]);
  }
  return { literals, templates };
}

const familyOf = (inner) => {
  const at = inner.indexOf('${');
  return at === -1 ? inner : inner.slice(0, at);
};

export function missingLiterals(sources, messages) {
  return [...scan(sources).literals].filter((key) => !(key in messages)).sort();
}

export function undeclaredFamilies(sources) {
  const bad = new Set();
  for (const template of scan(sources).templates) {
    const names = TEMPLATE_FAMILIES[template] || [familyOf(template)];
    for (const name of names) if (!(name in DYNAMIC_FAMILIES)) bad.add(template);
  }
  return [...bad].sort();
}

test('every literal t() key exists in en.json', () => {
  assert.deepEqual(missingLiterals(readSources(), en), []);
});

test('every dynamic key family is declared and keeps its pinned key count', () => {
  assert.deepEqual(undeclaredFamilies(readSources()), []);
  for (const [prefix, count] of Object.entries(DYNAMIC_FAMILIES)) {
    const keys = Object.keys(en).filter((key) => key.startsWith(prefix));
    assert.equal(keys.length, count, `family ${prefix}`);
  }
});

test('the check catches a missing literal key (synthetic)', () => {
  assert.deepEqual(missingLiterals({ 'x.js': 't("no.such.key") + t(\'also.missing\')' }, en), ['also.missing', 'no.such.key']);
});

test('unused keys are reported, never treated as an error', () => {
  const { literals, templates } = scan(readSources());
  const used = new Set(literals);
  for (const template of templates) {
    const names = TEMPLATE_FAMILIES[template] || [familyOf(template)];
    for (const name of names) for (const key of Object.keys(en)) if (key.startsWith(name)) used.add(key);
  }
  const unused = Object.keys(en).filter((key) => !used.has(key));
  if (unused.length) console.warn(`unused i18n keys (report only): ${unused.sort().join(', ')}`);
  assert.ok(true);
});
