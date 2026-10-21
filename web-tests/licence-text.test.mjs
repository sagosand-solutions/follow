import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const root = new URL('../public/', import.meta.url).pathname;
const strings = JSON.parse(readFileSync(`${root}i18n/en.json`, 'utf8'));

test('site-wide licence text never claims that all content is open', () => {
  for (const key of ['about.license.text']) {
    const text = strings[key];
    assert.match(text, /free/i, `${key} names the free part`);
    assert.match(text, /own licence/i, `${key} says other content carries its own licence`);
    assert.doesNotMatch(text, /(^|[^a-z])all (course )?content[^.]*CC BY-SA/i, key);
  }
});

test('study notes show their own licence through licenceInfo, never the raw field', () => {
  const src = readFileSync(`${root}js/components/study.js`, 'utf8');
  assert.match(src, /licenceInfo\(/);
  assert.doesNotMatch(src, /note\.license \|\| ''/);
});

test('the phone map shows counts: cells are hidden only on narrow screens', () => {
  const css = readFileSync(`${root}css/components.css`, 'utf8');
  const phone = css.slice(css.indexOf('/* === course map on phones'));
  const media = phone.slice(phone.indexOf('@media (max-width: 37.49rem)'));
  assert.match(media, /\.map__row:not\(\.map--compact\) \.map__cells \{ display: none; \}/, 'cells are hidden inside the narrow-screen media block');
  assert.ok(phone.indexOf('.map__row:not(.map--compact) .map__cells') > phone.indexOf('@media (max-width: 37.49rem)'));
  assert.match(css, /\.map-intro--phone \{ display: none; \}/);
  assert.doesNotMatch(css, /\.map__count[^{]*\{[^}]*display: none/, 'the count is shown at every width');
  assert.match(css, /\.map-intro--wide \{ display: none; \}/);
});

test('the phone map sentence does not mention squares', () => {
  assert.doesNotMatch(strings['home.map.some.phone'], /square|cell/i);
  assert.match(strings['home.map.some'], /square/i, 'the wide sentence still describes the squares');
});

test('the footer is one quiet line: the draft notice, the licences in About and the source code', async () => {
  const { text } = await import('./support/dom-stub.mjs');
  const { siteFooter } = await import('../public/js/components/ui.js');
  const node = siteFooter({ draft: strings['footer.draft'], licences: strings['footer.licences'], source: strings['about.source'], repoUrl: 'https://example.invalid/repo' });
  const links = node.all((n) => n.tag === 'a');
  assert.deepEqual(links.map((a) => a.attrs.href), ['#/about', 'https://example.invalid/repo']);
  assert.equal(node.all((n) => n.tag === 'p').length, 1);
  assert.ok(text(node).startsWith(strings['footer.draft']), 'the draft notice stays in the footer');
  assert.equal(strings['footer.draft'], 'Early draft.');
  assert.doesNotMatch(JSON.stringify(strings['footer.draft']), /CC BY-SA|AGPL|MIT/, 'no licence claim in the footer; About states them');
});
