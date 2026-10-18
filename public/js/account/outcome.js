// What happened in the checkout window, kept in memory for one render of the payment page. It carries no payment status: it
// only says whether the window was finished or closed, and for which purchase (the id is never shown; it only lets the payment
// page show the line for THAT purchase and not for some other pending one). It is read once, expires quickly, and is dropped as
// soon as the browser goes anywhere other than the payment page.
const WINDOW_MS = 15000;
let pending = null;

export function setCheckoutOutcome(kind, purchaseId, now = Date.now()) {
  pending = (kind === 'finished' || kind === 'closed') && typeof purchaseId === 'string' && purchaseId ? { kind, purchaseId, at: now } : null;
}

// -> { kind, purchaseId } or null
export function takeCheckoutOutcome(now = Date.now()) {
  const taken = pending;
  pending = null;
  return taken && now - taken.at <= WINDOW_MS ? { kind: taken.kind, purchaseId: taken.purchaseId } : null;
}

export function clearCheckoutOutcome() {
  pending = null;
}

if (typeof globalThis.addEventListener === 'function') {
  globalThis.addEventListener('hashchange', () => {
    if (!/^#\/purchase(?:[/?]|$)/.test(globalThis.location?.hash || '')) pending = null;
  });
}
