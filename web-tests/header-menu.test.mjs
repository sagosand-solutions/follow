import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const root = new URL('../public/', import.meta.url).pathname;

// Just enough DOM for the menu wiring: attributes, listeners, child order and focus.
function el(name, cls = '') {
  const node = {
    name, className: cls, attrs: {}, children: [], parent: null, listeners: {},
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    dispatch(type, event = {}) { const e = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...event }; for (const fn of this.listeners[type] || []) fn(e); return e; },
    append(...kids) { for (const k of kids) { if (k.parent) k.parent.children.splice(k.parent.children.indexOf(k), 1); k.parent = this; this.children.push(k); } },
    insertBefore(kid, ref) {
      if (kid.parent) kid.parent.children.splice(kid.parent.children.indexOf(kid), 1);
      kid.parent = this;
      const i = ref ? this.children.indexOf(ref) : -1;
      if (i < 0) this.children.push(kid); else this.children.splice(i, 0, kid);
    },
    querySelector(sel) { const c = sel.slice(1); return this.children.find((k) => k.className.split(' ').includes(c)) || null; },
    focus() { focused = this; },
  };
  return node;
}
let focused = null;

function setup({ narrow = true } = {}) {
  focused = null;
  const nav = el('nav', 'nav');
  const toggle = el('button', 'nav__toggle');
  const list = el('div', 'nav__list');
  const courses = el('a');
  const about = el('a');
  const continueSlot = el('span', 'nav__continue');
  const account = el('span', 'nav__account');
  const theme = el('div', 'nav__theme');
  list.append(courses, about, continueSlot, account, theme);
  nav.append(toggle, list);
  const mq = { matches: narrow, listeners: [], addEventListener(type, fn) { this.listeners.push(fn); } };
  const win = el('window');
  return { nav, toggle, list, continueSlot, account, mq, win };
}

async function wire(parts) {
  const { wireNavMenu } = await import('../public/js/components/menu.js');
  return wireNavMenu({ nav: parts.nav, toggle: parts.toggle, list: parts.list, continueSlot: parts.continueSlot, narrow: parts.mq, win: parts.win });
}

test('Escape while the menu is closed does nothing: no focus move, the key is not swallowed', async () => {
  const p = setup();
  const m = await wire(p);
  const e = p.nav.dispatch('keydown', { key: 'Escape' });
  assert.equal(e.defaultPrevented, false);
  assert.equal(focused, null);
  assert.equal(m.isOpen(), false);
});

test('a route change closes the menu without taking focus from the router', async () => {
  const p = setup();
  const m = await wire(p);
  p.toggle.dispatch('click');
  assert.equal(m.isOpen(), true);
  p.win.dispatch('hashchange');
  assert.equal(m.isOpen(), false);
  assert.equal(p.toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(focused, null, 'focus is left to the router, which moves it to the new heading');
});

test('crossing the breakpoint closes the menu and moves Continue to match the layout', async () => {
  const p = setup({ narrow: true });
  const m = await wire(p);
  m.open();
  assert.equal(p.continueSlot.parent, p.nav, 'narrow: Continue sits in the bar');
  assert.ok(p.nav.children.indexOf(p.continueSlot) < p.nav.children.indexOf(p.toggle), 'narrow: Continue comes before Menu, as on screen');
  p.mq.matches = false;
  p.mq.listeners.forEach((fn) => fn());
  assert.equal(m.isOpen(), false);
  assert.equal(p.continueSlot.parent, p.list, 'wide: Continue is back in the list');
  assert.equal(p.list.children.indexOf(p.continueSlot) + 1, p.list.children.indexOf(p.account), 'wide: Continue sits right before the account link, as before');
});

test('wide screens keep the original order from the start', async () => {
  const p = setup({ narrow: false });
  await wire(p);
  assert.equal(p.continueSlot.parent, p.list);
  assert.equal(p.list.children.indexOf(p.continueSlot) + 1, p.list.children.indexOf(p.account));
});

test('the button toggles aria-expanded, and Escape closes the open menu and returns focus to the button', async () => {
  const p = setup();
  const m = await wire(p);
  assert.equal(p.toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(p.nav.getAttribute('data-open'), 'false');
  assert.equal(p.nav.getAttribute('data-collapsible'), '', 'the list is folded only once the script has wired it');
  p.toggle.dispatch('click');
  assert.equal(p.toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(p.nav.getAttribute('data-open'), 'true');
  const e = p.nav.dispatch('keydown', { key: 'Escape' });
  assert.equal(e.defaultPrevented, true);
  assert.equal(m.isOpen(), false);
  assert.equal(focused, p.toggle);
  p.toggle.dispatch('click');
  p.toggle.dispatch('click');
  assert.equal(m.isOpen(), false, 'a second click closes it again');
});

test('the header uses a disclosure, not an ARIA menu, and names the list it controls', () => {
  const src = readFileSync(`${root}js/main.js`, 'utf8');
  assert.match(src, /h\('button', \{ type: 'button', class: 'nav__toggle', 'aria-expanded': 'false', 'aria-controls': 'nav-list' \}, t\('nav\.menu'\)\)/);
  assert.match(src, /class: 'nav__list', id: 'nav-list'/);
  assert.doesNotMatch(src, /role: 'menu(item)?'/);
  const strings = JSON.parse(readFileSync(`${root}i18n/en.json`, 'utf8'));
  assert.equal(strings['nav.menu'], 'Menu', 'a visible word, not only an icon');
});

test('wide screens are unchanged: the button is hidden and the wrappers add no boxes outside the narrow block', () => {
  const css = readFileSync(`${root}css/base.css`, 'utf8');
  const narrowAt = css.indexOf('@media (max-width: 37.49rem)');
  const wide = css.slice(0, narrowAt);
  assert.match(wide, /\.nav__toggle, \.nav__theme-label \{ display: none; \}/);
  assert.match(wide, /\.nav__list, \.nav__theme \{ display: contents; \}/);
  const narrow = css.slice(narrowAt);
  assert.match(narrow, /\.nav\[data-collapsible\]\[data-open="false"\] \.nav__list \{ display: none; \}/, 'the list hides only when wired and closed, and only on narrow screens');
  // Nothing else about the new parts is styled outside the narrow block (forced colours aside).
  const outside = wide.replace(/@media \(forced-colors: active\) \{[\s\S]*?\n\}/g, '');
  const mentions = outside.match(/\.nav__(toggle|list|theme)[^{]*\{/g) || [];
  assert.deepEqual(mentions.map((m) => m.trim()), ['.nav__toggle, .nav__theme-label {', '.nav__list, .nav__theme {']);
});
