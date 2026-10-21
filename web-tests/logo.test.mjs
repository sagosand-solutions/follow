import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { text } from './support/dom-stub.mjs';

const root = new URL('../public/', import.meta.url).pathname;
const { faviconSvg, brandLink, drawsWordmark, WORDMARK_NAME } = await import('../public/js/components/logo.js');
const { applyTheme } = await import('../public/js/theme.js');
const { CONFIG } = await import('../public/js/config.js');

// The default (light) theme's own tokens, read from the first :root block of tokens.css.
const tokens = readFileSync(`${root}css/tokens.css`, 'utf8');
const lightRoot = tokens.slice(tokens.indexOf(':root {'), tokens.indexOf('}', tokens.indexOf(':root {')));
const token = (name) => lightRoot.match(new RegExp(`${name}:\\s*(#[0-9a-f]+)`, 'i'))[1];

test('the static favicon is the logo in the default theme\'s stage colours', () => {
  const expected = faviconSvg({ tile: token('--stage-bg'), caret: token('--accent-attention'), ink: token('--stage-ink') });
  assert.equal(readFileSync(`${root}favicon.svg`, 'utf8'), expected);
});

test('the page links the favicon and the touch icon, and both files exist', () => {
  const html = readFileSync(`${root}index.html`, 'utf8');
  assert.match(html, /<link rel="icon" href="favicon\.svg" type="image\/svg\+xml">/);
  assert.match(html, /<link rel="apple-touch-icon" href="apple-touch-icon\.png">/);
  assert.equal(readFileSync(`${root}apple-touch-icon.png`).subarray(1, 4).toString(), 'PNG');
});

test('the favicon takes only plain hex colours, so a token can never write markup', () => {
  const ok = { tile: '#0e1428', caret: '#ffc83d', ink: '#fff' };
  assert.match(faviconSvg(ok), /^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
  for (const bad of ['', 'red', 'var(--stage-bg)', 'rgb(0, 0, 0)', '#12345', '#0e1428"/><script>', ' #0e1428', null, undefined]) {
    assert.equal(faviconSvg({ ...ok, tile: bad }), null, String(bad));
    assert.equal(faviconSvg({ ...ok, ink: bad }), null, String(bad));
  }
});

// A page root whose computed tokens are given, with a theme-color meta and a favicon link.
function page(values) {
  const meta = { attrs: { content: '#0b1020' }, setAttribute(k, v) { this.attrs[k] = v; } };
  const link = { attrs: { href: 'favicon.svg' }, setAttribute(k, v) { this.attrs[k] = v; } };
  const doc = { querySelector: (sel) => (sel === 'meta[name="theme-color"]' ? meta : sel === 'link[rel="icon"]' ? link : null) };
  const el = { ownerDocument: doc, setAttribute() {}, removeAttribute() {} };
  globalThis.getComputedStyle = () => ({ getPropertyValue: (name) => values[name] ?? '' });
  return { el, meta, link };
}

test('choosing a theme redraws the tab icon in that theme\'s stage colours', () => {
  const p = page({ '--bg': '#0b1020', '--stage-bg': '#1d1914', '--accent-attention': '#ffc83d', '--stage-ink': '#f1e9dc' });
  applyTheme('paper', p.el);
  const href = p.link.attrs.href;
  assert.ok(href.startsWith('data:image/svg+xml,'));
  assert.equal(decodeURIComponent(href.slice('data:image/svg+xml,'.length)), faviconSvg({ tile: '#1d1914', caret: '#ffc83d', ink: '#f1e9dc' }));
  assert.equal(p.meta.attrs.content, '#0b1020');
});

test('a token that cannot be read leaves the static favicon in place', () => {
  const p = page({ '--bg': '#ffffff', '--stage-bg': '', '--accent-attention': '#ffc83d', '--stage-ink': '#ffffff' });
  applyTheme('light', p.el);
  assert.equal(p.link.attrs.href, 'favicon.svg');
  assert.equal(p.meta.attrs.content, '#ffffff');
});

test('the header draws the wordmark only for its own name and names the link for assistive technology', () => {
  assert.equal(CONFIG.productName, WORDMARK_NAME);
  assert.ok(drawsWordmark('Readfirst'));
  const link = brandLink('Readfirst', 'Learn to read code');
  assert.equal(link.attrs.href, '#/');
  assert.ok(link.querySelector('.brand__mark'));
  assert.ok(link.querySelector('.brand__wordmark'));
  assert.equal(link.querySelector('.brand__mark').attrs['aria-hidden'], 'true');
  assert.equal(link.querySelector('.brand__wordmark').attrs['aria-hidden'], 'true');
  assert.equal(text(link.querySelector('.visually-hidden')), 'Readfirst');
  assert.equal(link.querySelector('.brand__text'), null);
});

test('another product name, or none, is shown as text beside the mark (never the drawn wordmark)', () => {
  for (const [name, shown] of [['Otherworld', 'Otherworld'], [null, 'Learn to read code']]) {
    assert.ok(!drawsWordmark(name));
    const link = brandLink(name, 'Learn to read code');
    assert.ok(link.querySelector('.brand__mark'));
    assert.equal(link.querySelector('.brand__wordmark'), null);
    assert.equal(text(link.querySelector('.brand__text')), shown);
  }
});

test('in forced colours the logo paints only with system colours', () => {
  const css = readFileSync(`${root}css/base.css`, 'utf8');
  const start = css.indexOf('@media (forced-colors: active)');
  const block = css.slice(start, css.indexOf('\n}\n', start));
  const fill = (cls) => (block.match(new RegExp(`\\.${cls}\\b[^{]*\\{[^}]*fill:\\s*([A-Za-z]+)`)) || [])[1];
  assert.equal(fill('brand__tile'), 'Canvas');
  assert.equal(fill('brand__caret'), 'Highlight');
  for (const cls of ['brand__line', 'brand__read', 'brand__first']) assert.equal(fill(cls), 'CanvasText', cls);
  assert.match(block, /\.brand__tile\s*\{[^}]*stroke:\s*CanvasText/);
});
