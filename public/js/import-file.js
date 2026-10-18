// Reading an imported progress file, with the byte limit checked BEFORE any read. Pure (no DOM): the caller passes a
// File-like object, so a test can assert that an over-limit file is refused without ever calling text().
import { MAX_IMPORT_BYTES } from './store.js';

export const IMPORT_ERRORS = ['invalid-format', 'too-large', 'unreadable'];

export async function readImportFile(file) {
  if (!file || typeof file.size !== 'number') return { ok: false, error: 'invalid-format' };
  if (file.size > MAX_IMPORT_BYTES) return { ok: false, error: 'too-large' };   // no read, no store change
  try {
    const text = await file.text();
    return { ok: true, text };
  } catch {
    return { ok: false, error: 'unreadable' };
  }
}
