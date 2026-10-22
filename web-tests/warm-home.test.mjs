import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { text } from './support/dom-stub.mjs';

// A warmer home: one symbol per course, a progress ring in place of the written count, clear card states, and calm motion.
const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const site = new URL('../public/data/', import.meta.url);
const read = (path) => JSON.parse(readFileSync(new URL(path, site), 'utf8'));
const catalogRaw = read('catalog.json');
const B1 = catalogRaw.courses.find((c) => c.id === 'read-like-a-computer');
const css = (name) => readFileSync(new URL(`../public/css/${name}`, import.meta.url), 'utf8');

globalThis.fetch = async (url) => {
  const path = String(url).replace(/^(.*\/)?data\//, '').replace(/\?.*$/, '');
  let body = null;
  try { body = read(path); } catch { body = null; }
  return body ? { ok: true, status: 200, async json() { return body; } } : { ok: false, status: 404, async json() { return {}; } };
};

const { setMessages } = await import('../public/js/i18n.js');
setMessages(strings);
const { store } = await import('../public/js/progress.js');
const { COURSE_ICONS, iconName, courseIcon, progressBadge } = await import('../public/js/components/course-icons.js');
const { catalogSection } = await import('../public/js/components/catalog-card.js');
const { parseCatalog } = await import('../public/js/catalog.js');
const { homeView } = await import('../public/js/views/home.js');

const byClass = (node, cls) => node.all((n) => n.className.split(' ').includes(cls));
const courseMap = async (entries) => {
  const m = new Map();
  for (const e of entries) { try { m.set(e.id, read(`courses/${e.id}.json`)); } catch { /* not published */ } }
  return m;
};

test('every course on the page has its own symbol; an unknown id and the courses still to come get the neutral one', () => {
  for (const c of catalogRaw.courses) assert.equal(iconName(c.id), c.id, c.id);
  assert.equal(iconName('small-programs'), 'small-programs', 'the path’s coming-soon step');
  for (const id of ['git', 'http', 'testing', 'regex', 'review', 'nope', undefined]) assert.equal(iconName(id), 'course', String(id));
  for (const name of ['git', 'http', 'testing', 'regex', 'review']) assert.ok(COURSE_ICONS[name], `${name} is drawn for later`);
});

test('the symbols share one line style and are decoration', () => {
  for (const [name, parts] of Object.entries(COURSE_ICONS)) {
    for (const [tag, attrs] of parts) {
      assert.ok(['path', 'circle', 'rect'].includes(tag), `${name}: ${tag}`);
      assert.ok(!('stroke-width' in attrs) && !('stroke' in attrs && attrs.stroke !== 'none'), `${name}: the stroke comes from the svg`);
    }
  }
  const svg = courseIcon('python');
  assert.equal(svg.attrs.viewBox, '0 0 24 24');
  assert.equal(svg.attrs['stroke-width'], '1.75');
  assert.equal(svg.attrs.stroke, 'currentColor');
  assert.equal(svg.attrs['stroke-linecap'], 'round');
  assert.equal(svg.attrs['aria-hidden'], 'true');
});

test('the ring: a track always, a filled arc for the answered share, and a name only when it replaces the count', () => {
  const fills = (b) => b.all((n) => n.attrs.class === 'progress-ring__fill');
  const none = progressBadge('sql', { answered: 0, total: 5 });
  assert.equal(fills(none).length, 0);
  assert.equal(none.all((n) => n.attrs.class === 'progress-ring__track').length, 1);
  assert.equal(none.attrs['aria-hidden'], 'true');
  const third = progressBadge('read-like-a-computer', { answered: 10, total: 30, label: '10 of 30 read' });
  assert.equal(fills(third)[0].attrs['stroke-dasharray'], `${10 / 30} 1`);
  assert.equal(fills(third)[0].attrs.pathLength, '1');
  assert.equal(fills(third)[0].attrs.transform, 'rotate(-90 24 24)', 'the arc starts at twelve o’clock');
  assert.equal(third.children[0].attrs['aria-hidden'], 'true', 'the named badge’s drawing is not read on its own');
  assert.equal(third.attrs.role, 'img');
  assert.equal(third.attrs['aria-label'], '10 of 30 read');
  assert.equal(third.attrs['aria-hidden'], undefined);
  assert.match(third.className, /course-badge--started/);
  const over = progressBadge('python', { answered: 40, total: 34 });
  assert.equal(fills(over)[0].attrs['stroke-dasharray'], '1 1');
  assert.match(over.className, /course-badge--done/);
});

test('card states: a started course has the accent edge and a named ring; one not started is neutral; coming soon has its symbol', async () => {
  store.reset();
  const entries = parseCatalog(catalogRaw);
  const fresh = catalogSection(entries, await courseMap(entries));
  assert.equal(byClass(fresh, 'course-card--started').length, 0);
  for (const badge of byClass(fresh, 'course-badge')) assert.equal(badge.attrs['aria-hidden'], 'true', 'nothing to say before the first answer');
  const soon = byClass(fresh, 'path__step--soon')[0];
  assert.equal(byClass(soon, 'course-badge').length, 1);
  assert.equal(soon.all((n) => n.attrs.class === 'course-icon course-icon--small-programs').length, 1);
  assert.equal(byClass(fresh, 'course-card__progress').length, 0);

  const ids = read(`courses/${B1.id}.json`).levels.flatMap((l) => l.lessons.flatMap((x) => x.questions)).slice(0, 10);
  ids.forEach((id) => store.recordAnswer({ questionId: id, optionId: 'x', score: 1 }));
  const after = catalogSection(entries, await courseMap(entries));
  const started = byClass(after, 'course-card--started');
  assert.equal(started.length, 1);
  assert.match(text(started[0]), /Read like a computer/);
  assert.doesNotMatch(text(after), /10 of 30/, 'no written count: the ring carries it');
  const badge = byClass(started[0], 'course-badge')[0];
  assert.deepEqual([badge.attrs.role, badge.attrs['aria-label']], ['img', '10 of 30 read']);
  // Each card's symbol comes before its title, so it sits top left.
  const head = byClass(started[0], 'course-card__head')[0];
  assert.deepEqual(head.children.map((c) => c.className), ['course-badge course-badge--started', 'course-card__title']);
  store.reset();
});

test('the hero repeats the ring beside the sentence that says the count, as decoration', async () => {
  store.reset();
  store.recordAnswer({ questionId: B1.start_question, optionId: 'x', score: 1 });
  const page = (await homeView()).node;
  const row = byClass(page, 'welcome-row')[0];
  assert.ok(row, 'the welcome row is there');
  const badge = byClass(row, 'course-badge')[0];
  assert.equal(badge.attrs['aria-hidden'], 'true');
  assert.match(text(row), /1 of 30/);
  store.reset();
  const fresh = (await homeView()).node;
  assert.equal(byClass(fresh, 'welcome-row').length, 0, 'a new visitor has no ring in the hero');
});

test('light and paper are warm off-white, not pure white', () => {
  const tokens = css('tokens.css');
  const block = (sel) => tokens.slice(tokens.indexOf(sel), tokens.indexOf('}', tokens.indexOf(sel)));
  for (const b of [block(':root {'), block(':root[data-theme="paper"]')]) {
    for (const k of ['--bg', '--surface']) {
      const v = b.match(new RegExp(`${k}:\\s*(#[0-9a-f]{6})`))[1];
      assert.notEqual(v, '#ffffff', k);
      const [r, , bl] = [1, 3, 5].map((i) => parseInt(v.slice(i, i + 2), 16));
      assert.ok(r > bl, `${k} ${v} leans warm`);
    }
  }
});

test('motion: hover lifts a card 2 px with transform and shadow only, Continue gets an accent shadow, reduced motion keeps cards still', () => {
  const c = css('components.css');
  assert.match(c, /\.course-card \{[^}]*transition: transform var\(--dur\) var\(--ease\), box-shadow var\(--dur\) var\(--ease\); \}/);
  assert.match(c, /\.course-card:hover \{ transform: translateY\(-2px\); box-shadow: var\(--shadow-card-hover\); \}/);
  assert.match(c, /\.course-card:hover \.course-badge, \.course-card:focus-within \.course-badge \{ color: var\(--icon-hover\); \}/);
  assert.match(c, /@media \(prefers-reduced-motion: reduce\) \{ \.course-card:hover \{ transform: none; \} \}/);
  assert.match(css('base.css'), /\.button--primary:hover \{ background: var\(--accent-attention-hover\); box-shadow: var\(--shadow-accent-hover\); \}/);
  assert.match(c, /\.path__step--soon \.course-badge \{ opacity: 0\.5; \}/);
  // The started edge is thicker as well as coloured, and forced colours keep the states.
  assert.match(c, /\.course-card--started \{ border-color: var\(--card-edge-started\); box-shadow: inset 0 0 0 1px var\(--card-edge-started\); \}/);
  assert.match(c, /\.progress-ring__track \{ stroke: var\(--ring-track\); stroke-width: 1\.5; \}/);
  // A ring that carries a value shows what is left at >= 3:1 (the control border colour, checked in every theme).
  assert.match(c, /\.course-badge--started \.progress-ring__track \{ stroke: var\(--line-strong\); \}/);
  assert.match(c, /\.progress-ring__fill \{ stroke: var\(--ring-fill\); stroke-width: 3;/);
  assert.match(c, /@media \(forced-colors: active\) \{\n  \.course-badge \{ color: CanvasText; \}\n  \.progress-ring__track \{ stroke: GrayText; \}\n  \.progress-ring__fill \{ stroke: Highlight; \}/);
  assert.match(c, /  \.course-card\.course-card--started \{ border: 2px solid Highlight; box-shadow: none; \}/, 'wins over the plain forced-colours card border');
});

test('layout: a hairline before the courses, the skip line close under its heading, coming-soon steps dashed', () => {
  const c = css('components.css');
  assert.match(c, /\.home > \.catalog \{ padding-block-start: var\(--s-7\); border-block-start: 1px solid var\(--line\); \}/);
  assert.match(c, /\.path__skipline \{ margin-block-start: calc\(-1 \* var\(--s-2\)\); \}/);
  assert.match(c, /\.path__step--soon \.path__body \{[^}]*border: 1px dashed var\(--line-strong\);/);
  assert.match(c, /\.course-card__text \{ color: var\(--muted\); line-height: 1\.6; \}/);
});

test('the whole card is the title link: one focus stop with its ring around the card, other controls on top, nothing for coming soon', async () => {
  const c = css('components.css');
  assert.match(c, /\.course-card \{ position: relative; \}/);
  assert.match(c, /\.course-card__title a::after \{ content: ""; position: absolute; inset: 0; border-radius: var\(--radius\); \}/);
  assert.match(c, /\.course-card__title a:focus-visible \{ outline: none; \}/);
  assert.match(c, /\.course-card:has\(\.course-card__title a:focus-visible\) \{ outline: 3px solid var\(--focus\); outline-offset: 2px; \}/, 'the ring goes around the card');
  assert.match(c, /\.course-card a:not\(\.course-card__title a\), \.course-card button \{ position: relative; z-index: 1; \}/, 'other controls keep their own targets');
  // Hover and the pointer belong to course cards only; a coming-soon step is not one and has no link.
  assert.doesNotMatch(c, /path__step--soon[^{]*:hover/);
  store.reset();
  const entries = parseCatalog(catalogRaw);
  const node = catalogSection(entries, await courseMap(entries));
  for (const card of byClass(node, 'course-card')) {
    const focusable = card.all((n) => n.tag === 'a' || n.tag === 'button' || (n.attrs.tabindex !== undefined && n.attrs.tabindex !== '-1'));
    assert.equal(focusable.length, 1, 'one focus stop per card');
    assert.equal(focusable[0].parent.className, 'course-card__title');
    assert.match(focusable[0].attrs.href, /^#\/course(\/|$)/);
  }
  const soon = byClass(node, 'path__step--soon')[0];
  assert.equal(byClass(soon, 'course-card').length, 0);
  assert.equal(soon.all((n) => n.tag === 'a' || n.tag === 'button').length, 0);
});
