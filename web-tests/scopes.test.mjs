import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validScope } from '../public/js/account/http-client.js';

// The shared matrix lives in fixtures/scope-cases.json and is ALSO read by server/tests/test_scoring_scope_parity.py:
// the client helper and the server's course-scope regex must agree without a second hand-maintained copy.
const cases = JSON.parse(readFileSync(new URL('./fixtures/scope-cases.json', import.meta.url), 'utf8'));

test('validScope accepts exactly the documented server scopes and refuses the rest', () => {
  for (const [scope, expected] of cases) {
    assert.equal(validScope(scope), expected, `validScope(${JSON.stringify(scope)})`);
  }
  // Non-string values can never be a scope (the server would refuse them too).
  for (const bad of [null, undefined, 7, {}, ['total']]) {
    assert.equal(validScope(bad), false, String(bad));
  }
});
