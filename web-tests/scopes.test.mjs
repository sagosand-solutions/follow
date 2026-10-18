import test from 'node:test';
import assert from 'node:assert/strict';
import { validScope } from '../public/js/account/http-client.js';

// The SAME matrix lives in server/tests/test_scoring_scope_parity.py: the client helper and the server's
// course-scope regex must agree on every case, so a scope the client sends always has the server's shape.
export const SCOPE_CASES = [
  ['total', true],
  ['course:a', true],
  ['course:a-1', true],
  ['course:abc-123', true],
  ['course:' + 'a' + 'b'.repeat(63), true],          // the 64-character maximum
  ['', false],
  ['Total', false],
  ['total ', false],
  [' total', false],
  ['course', false],
  ['course:', false],
  ['course:A', false],
  ['course:1a', false],
  ['course:a_b', false],
  ['course:a.b', false],
  ['course:-a', false],
  ['course:a/b', false],
  ['course:' + 'a'.repeat(65), false],               // 65 characters: one too many
  ['course:a\n', false],
  ['course:a\r', false],
  ['course:a\u2028', false],
  [null, false],
  [undefined, false],
  [7, false],
  [{}, false],
  [['total'], false],
];

test('validScope accepts exactly the documented server scopes and refuses the rest', () => {
  for (const [scope, expected] of SCOPE_CASES) {
    assert.equal(validScope(scope), expected, `validScope(${JSON.stringify(scope)})`);
  }
});
