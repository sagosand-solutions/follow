// A one-bit "seen" flag per browser, e.g. the step player's first run. Uses the same storage prefix as the rest of the
// app; in memory mode (storage blocked or failing) the flag is never kept, so the moment simply shows every time.
import { CONFIG } from './config.js';

function defaultStorage() {
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}

export function createSeenFlag(name, { storage = defaultStorage(), prefix = CONFIG.storagePrefix } = {}) {
  const key = `${prefix}.seen.${name}`;
  return {
    isSeen() {
      try {
        return !!storage && storage.getItem(key) === '1';
      } catch {
        return false;
      }
    },
    markSeen() {
      try {
        if (storage) storage.setItem(key, '1');
      } catch {
        /* memory mode: not kept */
      }
    },
  };
}
