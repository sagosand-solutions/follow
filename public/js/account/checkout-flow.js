// The overlay half of checkout, as plain logic so it can be tested without a browser. The view supplies the effects.
// Rules (all from the security plan): the first outcome wins and is acted on once; a leave, an error or a missing opener never
// opens or navigates late; nothing here starts a second checkout, and the transaction id goes only to the opener.

// runOverlay({ result, loadOpener, args, ui, go, setOutcome, active, register }) -> 'finished'|'closed'|'failed'|'left'
//  result      the strict overlay answer { purchase_id, transaction_id }
//  loadOpener  async () -> open(args) | null, where open returns { result: Promise<'finished'|'closed'|null>, cancel() }
//  ui.failed() shows the error row; active() is false once the reader left the page; register(handle) lets the view cancel it
export async function runOverlay({ result, loadOpener, args, ui, go, setOutcome, active = () => true, register = () => {} }) {
  let open = null;
  try {
    open = await loadOpener();
  } catch {
    open = null;
  }
  if (!active()) return 'left';
  if (typeof open !== 'function') {
    ui.failed();
    return 'failed';
  }
  let handle;
  try {
    handle = open({ ...args, transactionId: result.transaction_id });
  } catch {
    ui.failed();
    return 'failed';
  }
  register(handle);
  const outcome = await handle.result;
  if ((outcome !== 'finished' && outcome !== 'closed') || !active()) return 'left';
  setOutcome(outcome, result.purchase_id);
  go('/purchase');
  return outcome;
}

// One guard for the checkout page, shared by every build of the view. A start locks it; it stays locked when the view is rebuilt
// without a navigation (a refresh), so a second start can only follow an explicit visit; the older build's late callbacks are
// ignored and its window is cancelled; leaving the page releases the lock and cancels any window.
export function createCheckoutGuard() {
  const state = { locked: false, build: 0, overlay: null };
  const dropOverlay = () => {
    if (state.overlay) state.overlay.cancel();
    state.overlay = null;
  };
  return {
    enter() { dropOverlay(); return ++state.build; },
    isCurrent(build) { return state.build === build; },
    get locked() { return state.locked; },
    lock() { state.locked = true; },
    register(handle) { state.overlay = handle; },
    release() { state.overlay = null; },
    leave() { dropOverlay(); state.locked = false; state.build += 1; },
  };
}
