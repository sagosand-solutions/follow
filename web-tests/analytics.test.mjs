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
  assert.ok(html.trimEnd().endsWith(`${tag}</script>\n</body>\n</html>`), 'the beacon is the last thing in the body');
  assert.equal((html.match(/data-cf-beacon/g) || []).length, 1);
});

test('the beacon is the only script loaded from another site', () => {
  const external = [...html.matchAll(/<script\b[^>]*\ssrc\s*=\s*["']?([^"'\s>]+)/gi)].map((m) => m[1]).filter((src) => /^(https?:)?\/\//.test(src));
  assert.deepEqual(external, ['https://static.cloudflareinsights.com/beacon.min.js']);
});

test('About names Cloudflare Web Analytics, says the site is cookie-free, and no longer claims no analytics', () => {
  const text = strings['about.privacy.text'];
  assert.match(text, /Cloudflare Web Analytics/);
  assert.match(text, /^No cookies\b/, 'the first thing said is that there are no cookies');
  assert.match(text, /sets no cookies and does not follow you across sites/);
  assert.doesNotMatch(text, /no personal data/, 'no broader promise than the provider documents');
  assert.doesNotMatch(text, /no cookies, analytics/);
});

// A script written into the page could add another site's script without a src attribute in the HTML.
test('no inline script can load another: the only inline script is the import map, and it maps to this site only', () => {
  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)].map((m) => ({ attrs: m[1], body: m[2].trim() }));
  assert.equal(scripts.length, 3, 'the import map, the app and the beacon');
  const inline = scripts.filter((s) => s.body);
  assert.deepEqual(inline.map((s) => s.attrs.trim()), ['type="importmap"']);
  const { imports, ...rest } = JSON.parse(inline[0].body);
  assert.deepEqual(Object.keys(rest), []);
  for (const [name, target] of Object.entries(imports)) assert.match(target, /^\.\/js\/[\w/.-]+\.js\?v=\d+$/, `${name} stays on this site`);
  const sources = scripts.filter((s) => !s.body).map((s) => s.attrs.match(/\ssrc="([^"]+)"/)[1]);
  assert.equal(sources.length, 2);
  assert.match(sources[0], /^js\/main\.js\?v=\d+$/);
  assert.equal(sources[1], 'https://static.cloudflareinsights.com/beacon.min.js');
  assert.doesNotMatch(html, /\son\w+\s*=/i, 'no inline event handlers');
});
