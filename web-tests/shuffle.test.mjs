import test from 'node:test';
import assert from 'node:assert/strict';
import { seededShuffle, questionSeed } from '../public/js/shuffle.js';

test('same seed gives same order', () => {
  const a = seededShuffle([1, 2, 3, 4, 5], 'x');
  const b = seededShuffle([1, 2, 3, 4, 5], 'x');
  assert.deepEqual(a, b);
});

test('input is not mutated', () => {
  const input = [1, 2, 3, 4, 5];
  seededShuffle(input, 'x');
  assert.deepEqual(input, [1, 2, 3, 4, 5]);
});

test('result is a permutation for many seeds and lengths', () => {
  for (let n = 0; n <= 10; n += 1) {
    const items = Array.from({ length: n }, (_, i) => i);
    for (let s = 0; s < 200; s += 1) {
      const out = seededShuffle(items, `seed-${s}-${n}`);
      assert.deepEqual([...out].sort((x, y) => x - y), items);
    }
  }
});

test('different seeds usually differ', () => {
  const items = [1, 2, 3, 4, 5, 6];
  const seen = new Set();
  for (let s = 0; s < 50; s += 1) seen.add(seededShuffle(items, `k${s}`).join(','));
  assert.ok(seen.size > 20);
});

test('first position is roughly uniform', () => {
  const counts = [0, 0, 0, 0, 0];
  const n = 5000;
  for (let s = 0; s < n; s += 1) counts[seededShuffle([0, 1, 2, 3, 4], `abc:q${s}`)[0]] += 1;
  for (const c of counts) {
    assert.ok(c / n > 0.17 && c / n < 0.23, `share ${c / n}`);
  }
});

test('questionSeed joins user seed and question id', () => {
  assert.equal(questionSeed('u', 'q'), 'u:q');
});
