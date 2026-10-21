import { faviconSvg } from './components/logo.js';

const KEY = 'ui.theme';
export const THEMES = ['auto', 'light', 'dark', 'paper', 'slate', 'contrast'];
let sessionMode = 'auto';
// Set when a choice could not be saved: the page-view choice then outranks any older stored value.
let sessionOverrides = false;

// For tests: forget the choice remembered for this page view.
export function resetSessionTheme() {
  sessionMode = 'auto';
  sessionOverrides = false;
}

function defaultStorage() {
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}

export function readTheme(storage = defaultStorage()) {
  if (sessionOverrides) return sessionMode;
  try {
    const v = storage && storage.getItem(KEY);
    if (THEMES.includes(v)) return v;
  } catch {
    /* fall through to the choice made in this page view */
  }
  return sessionMode;
}

export function applyTheme(mode, root = globalThis.document && globalThis.document.documentElement) {
  if (!root) return;
  if (mode === 'auto') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
  syncThemeColor(root);
}

// The page's own favicon link (favicon.svg in index.html), remembered per link before the first redraw.
const staticIcons = new WeakMap();

// The browser chrome follows the page background (meta theme-color), read from the theme's own --bg token, and the tab's
// icon is redrawn in the theme's stage colours. When a token cannot be read, the tab goes back to the static favicon,
// never keeping an earlier theme's icon.
function syncThemeColor(root) {
  const doc = root && root.ownerDocument;
  if (!doc || !globalThis.getComputedStyle) return;
  const style = globalThis.getComputedStyle(root);
  const token = (name) => style.getPropertyValue(name).trim();
  const meta = doc.querySelector('meta[name="theme-color"]');
  const bg = token('--bg');
  if (meta && bg) meta.setAttribute('content', bg);
  const icon = doc.querySelector('link[rel="icon"]');
  const svg = faviconSvg({ tile: token('--stage-bg'), caret: token('--accent-attention'), ink: token('--stage-ink') });
  if (!icon) return;
  if (!staticIcons.has(icon)) staticIcons.set(icon, icon.getAttribute('href'));
  const original = staticIcons.get(icon);
  if (svg) icon.setAttribute('href', `data:image/svg+xml,${encodeURIComponent(svg)}`);
  else if (original === null) icon.removeAttribute('href');      // a link that had no href goes back to having none
  else icon.setAttribute('href', original);
}

export function initTheme(storage = defaultStorage()) {
  const mode = readTheme(storage);
  sessionMode = mode;
  applyTheme(mode);
  // While Auto is chosen, a change of the operating system's colour or contrast setting changes the page, so the
  // browser chrome colour (meta theme-color) has to follow too.
  if (globalThis.matchMedia) {
    for (const query of ['(prefers-color-scheme: dark)', '(prefers-contrast: more)']) {
      const list = globalThis.matchMedia(query);
      if (list && list.addEventListener) {
        list.addEventListener('change', () => {
          if (readTheme(storage) === 'auto') syncThemeColor(globalThis.document && globalThis.document.documentElement);
        });
      }
    }
  }
  return mode;
}

// Choose a theme (one of THEMES). Saved under the existing key; when it cannot be saved, the choice lasts for this page view.
export function setTheme(mode, storage = defaultStorage()) {
  if (!THEMES.includes(mode)) return readTheme(storage);
  sessionMode = mode;
  try {
    if (storage) storage.setItem(KEY, mode);
  } catch {
    sessionOverrides = true;
  }
  applyTheme(mode);
  return mode;
}
