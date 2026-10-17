import test from 'node:test';
import assert from 'node:assert/strict';
import { setTheme, readTheme, resetSessionTheme, THEMES } from '../public/js/theme.js';

const fake = (initial = {}) => {
  const d = { ...initial };
  return { getItem: (k) => (k in d ? d[k] : null), setItem: (k, v) => { d[k] = v; }, removeItem: (k) => { delete d[k]; }, d };
};

test('the themes the app offers, in order', () => {
  assert.deepEqual(THEMES, ['auto', 'light', 'dark', 'paper', 'slate', 'contrast']);
});

test('every theme can be chosen and persists under the existing key', () => {
  for (const mode of THEMES) {
    resetSessionTheme();
    const st = fake();
    assert.equal(setTheme(mode, st), mode);
    assert.equal(st.d['ui.theme'], mode);
    assert.equal(readTheme(st), mode);
  }
});

test('an unknown value is ignored and nothing is stored', () => {
  resetSessionTheme();
  const st = fake({ 'ui.theme': 'paper' });
  assert.equal(setTheme('neon', st), 'paper');
  assert.equal(st.d['ui.theme'], 'paper');
});

test('a stored unknown value falls back to auto', () => {
  resetSessionTheme();
  assert.equal(readTheme(fake({ 'ui.theme': 'neon' })), 'auto');
});

test('works with missing storage: the choice lasts for this page view', () => {
  resetSessionTheme();
  assert.equal(setTheme('slate', null), 'slate');
  assert.equal(readTheme(null), 'slate');
});

test('a choice that cannot be saved outranks an older readable stored value', () => {
  resetSessionTheme();
  const backend = { getItem: () => 'dark', setItem() { throw new Error('quota'); } };
  assert.equal(setTheme('contrast', backend), 'contrast');
  assert.equal(readTheme(backend), 'contrast');
});
