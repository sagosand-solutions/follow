import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Visits are counted with Cloudflare Web Analytics: one deferred beacon with the site's own token, and About says so.
const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const strings = JSON.parse(readFileSync(new URL('../public/i18n/en.json', import.meta.url), 'utf8'));
const TOKEN = '66de7433e9ab4965b7978f8b3843ad07';

test('index.html loads exactly one Cloudflare beacon, deferred, with the site token, just before </body>', () => {
  const beacons = html.match(/<script\b[^>]*cloudflareinsights[^>]*>/g) || [];
  assert.equal(beacons.length, 1);
  const [tag] = beacons;
  assert.match(tag, /\sdefer[\s>]/);
  assert.match(tag, /\ssrc="https:\/\/static\.cloudflareinsights\.com\/beacon\.min\.js"/);
  const config = tag.match(/\sdata-cf-beacon='([^']*)'/);
  assert.ok(config, 'the beacon carries its configuration');
  assert.deepEqual(JSON.parse(config[1]), { token: TOKEN });
  assert.match(html, /<script defer [^>]*><\/script>\n<\/body>/, 'the beacon is the last thing in the body');
  assert.equal((html.match(/data-cf-beacon/g) || []).length, 1);
});

test('About names Cloudflare Web Analytics, says the site is cookie-free, and no longer claims no analytics', () => {
  const text = strings['about.privacy.text'];
  assert.match(text, /Cloudflare Web Analytics/);
  assert.match(text, /^No cookies\b/, 'the first thing said is that there are no cookies');
  assert.match(text, /sets no cookies and does not follow you across sites/);
  assert.doesNotMatch(text, /no personal data/, 'no broader promise than the provider documents');
  assert.doesNotMatch(text, /no cookies, analytics/);
});
