import test from 'node:test';
import assert from 'node:assert/strict';
import { cycleTheme, readTheme, nextTheme, resetSessionTheme } from '../public/js/theme.js';

const fake = (initial = {}) => {
  const d = { ...initial };
  return { getItem: (k) => (k in d ? d[k] : null), setItem: (k, v) => { d[k] = v; }, removeItem: (k) => { delete d[k]; }, d };
};

test('nextTheme order', () => {
  assert.equal(nextTheme('auto'), 'light');
  assert.equal(nextTheme('light'), 'dark');
  assert.equal(nextTheme('dark'), 'auto');
});

test('cycles and persists with working storage', () => {
  const st = fake();
  assert.equal(cycleTheme(st), 'light');
  assert.equal(st.d['ui.theme'], 'light');
  assert.equal(readTheme(st), 'light');
  assert.equal(cycleTheme(st), 'dark');
});

test('cycles through all modes when storage is missing', () => {
  resetSessionTheme();
  const seen = [cycleTheme(null), cycleTheme(null), cycleTheme(null)];
  assert.deepEqual(seen, ['light', 'dark', 'auto']);
  assert.equal(readTheme(null), 'auto');
});

test('cycles when storage throws on read and write', () => {
  resetSessionTheme();
  const broken = { getItem() { throw new Error('x'); }, setItem() { throw new Error('x'); } };
  const seen = [cycleTheme(broken), cycleTheme(broken), cycleTheme(broken)];
  assert.deepEqual(seen, ['light', 'dark', 'auto']);
});

test('a choice that cannot be saved outranks an older readable stored value', () => {
  resetSessionTheme();
  const backend = { getItem: () => 'dark', setItem() { throw new Error('quota'); } };
  assert.deepEqual([cycleTheme(backend), cycleTheme(backend), cycleTheme(backend)], ['auto', 'light', 'dark']);
  assert.equal(readTheme(backend), 'dark');
});
