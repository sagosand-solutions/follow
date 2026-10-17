// What the question page says when a question is not in the public course and the account service is asked for it.
// Pure: maps the client's answer to a state; the view picks the wording.

// -> { state: 'ok', question } | { state: 'signin' | 'unavailable' | 'network' | 'error' }
export function premiumState(result) {
  if (result && result.ok === true && result.question && typeof result.question === 'object') return { state: 'ok', question: result.question };
  switch (result && result.code) {
    case 'signed_out': return { state: 'signin' };
    case 'not_found': return { state: 'unavailable' };
    case 'network_error': return { state: 'network' };
    default: return { state: 'error' };
  }
}
