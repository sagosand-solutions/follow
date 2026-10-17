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

// The browser chrome follows the page background (meta theme-color), read from the theme's own --bg token.
function syncThemeColor(root) {
  const doc = root && root.ownerDocument;
  if (!doc || !globalThis.getComputedStyle) return;
  const meta = doc.querySelector('meta[name="theme-color"]');
  const bg = globalThis.getComputedStyle(root).getPropertyValue('--bg').trim();
  if (meta && bg) meta.setAttribute('content', bg);
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
