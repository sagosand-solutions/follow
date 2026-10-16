// Optional lesson summary: plain text only. Characters such as < or ` are literal text (never markup);
// an absent or empty summary produces no node at all.
import { h } from '../dom.js';

export function summaryNode(summary, className, tag = 'p') {
  if (typeof summary !== 'string' || summary === '') return null;
  return h(tag, { class: className }, summary);
}
