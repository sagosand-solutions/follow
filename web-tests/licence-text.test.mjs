import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const root = new URL('../public/', import.meta.url).pathname;
const strings = JSON.parse(readFileSync(`${root}i18n/en.json`, 'utf8'));

test('site-wide licence text never claims that all content is open', () => {
  for (const key of ['footer.license', 'about.license.text']) {
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
  assert.match(css, /\.map__count--phone, \.map-intro--phone \{ display: none; \}/);
  assert.match(css, /\.map-intro--wide \{ display: none; \}/);
});

test('the phone map sentence does not mention squares', () => {
  for (const key of ['home.map.empty.phone', 'home.map.some.phone']) assert.doesNotMatch(strings[key], /square|cell/i, key);
  assert.match(strings['home.map.empty'], /square/i, 'the wide sentence still describes the squares');
});
