const KEY = 'ui.theme';
const ORDER = ['auto', 'light', 'dark'];
let sessionMode = 'auto';

// For tests: forget the choice remembered for this page view.
export function resetSessionTheme() {
  sessionMode = 'auto';
}

function defaultStorage() {
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}

export function readTheme(storage = defaultStorage()) {
  try {
    const v = storage && storage.getItem(KEY);
    if (ORDER.includes(v)) return v;
  } catch {
    /* fall through to the choice made in this page view */
  }
  return sessionMode;
}

export function applyTheme(mode, root = globalThis.document && globalThis.document.documentElement) {
  if (!root) return;
  if (mode === 'auto') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
}

export function initTheme(storage = defaultStorage()) {
  const mode = readTheme(storage);
  sessionMode = mode;
  applyTheme(mode);
  return mode;
}

export function nextTheme(mode) {
  return ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length];
}

export function cycleTheme(storage = defaultStorage()) {
  const mode = nextTheme(readTheme(storage));
  sessionMode = mode;
  try {
    if (storage) storage.setItem(KEY, mode);
  } catch {
    /* storage unavailable: the choice lasts for this page view only */
  }
  applyTheme(mode);
  return mode;
}
