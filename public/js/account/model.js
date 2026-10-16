// Pure helpers for the account screens (no DOM, no network).

export const PROVIDERS = ['google', 'apple', 'discord', 'metamask'];

export function formatPrice(amountMinor, currency, locale = 'en') {
  if (!Number.isInteger(amountMinor) || amountMinor < 0 || typeof currency !== 'string') return null;
  try {
    return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(amountMinor / 100);
  } catch {
    return null;
  }
}

// What the purchase area may say. Only the server's entitlement ever means "paid".
export function purchaseStatus({ entitlements = [], purchase = null }, productId) {
  const own = entitlements.filter((e) => e.product_id === productId);
  if (own.some((e) => e.state === 'active')) return 'active';
  if (own.some((e) => e.state === 'revoked')) return 'revoked';
  if (purchase && purchase.product_id === productId && purchase.state === 'pending') return 'pending';
  return 'none';
}

// The checkout button is usable only with a signed-in user, an enabled checkout, and the required acknowledgements ticked.
export function canPay({ signedIn, checkoutEnabled, acknowledged }) {
  return !!signedIn && !!checkoutEnabled && !!acknowledged;
}

// The reason text key to show beside a disabled Pay button (never colour alone).
export function payBlockedReason({ signedIn, checkoutEnabled, acknowledged }) {
  if (!signedIn) return 'checkout.blocked.signin';
  if (!checkoutEnabled) return 'checkout.blocked.disabled';
  if (!acknowledged) return 'checkout.blocked.consent';
  return null;
}

// "next" after sign-in must be one of our own routes; anything else is ignored.
const NEXT_ALLOWED = new Set(['premium', 'checkout', 'account']);
export function safeNext(value) {
  return NEXT_ALLOWED.has(value) ? value : 'account';
}
