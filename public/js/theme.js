const KEY = 'ui.theme';
const ORDER = ['auto', 'light', 'dark'];

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
    return ORDER.includes(v) ? v : 'auto';
  } catch {
    return 'auto';
  }
}

export function applyTheme(mode, root = document.documentElement) {
  if (mode === 'auto') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
}

export function initTheme(storage = defaultStorage()) {
  const mode = readTheme(storage);
  applyTheme(mode);
  return mode;
}

export function nextTheme(mode) {
  return ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length];
}

export function cycleTheme(storage = defaultStorage()) {
  const mode = nextTheme(readTheme(storage));
  try {
    if (storage) storage.setItem(KEY, mode);
  } catch {
    /* storage unavailable: the choice lasts for this page view only */
  }
  applyTheme(mode);
  return mode;
}
