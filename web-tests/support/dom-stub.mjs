// A minimal DOM for view tests: elements keep their children, attributes and text; nothing is parsed or fetched.
class Node {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.className = ''; this.handlers = {}; this.parent = null; this.checked = false; this.hidden = false; this.textContent = ''; this.dataset = {}; this.style = {}; }
  get classList() { const n = this; return { add: (c) => { n.className = [...new Set([...n.className.split(' ').filter(Boolean), c])].join(' '); }, remove: (c) => { n.className = n.className.split(' ').filter((x) => x && x !== c).join(' '); }, contains: (c) => n.className.split(' ').includes(c), toggle() {} }; }
  setAttribute(k, v) { this.attrs[k] = v; if (k === 'class') this.className = v; if (k.startsWith('data-')) this.dataset[k.slice(5)] = v; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn); }
  removeEventListener() {}
  dispatchEvent(e) { for (const fn of this.handlers[e.type] || []) fn(e); if (this.parent && this.parent.dispatchEvent) this.parent.dispatchEvent(e); return true; }
  append(...nodes) { for (const n of nodes) { if (n && typeof n === 'object') n.parent = this; this.children.push(n); } }
  appendChild(node) { this.append(node); return node; }
  insertBefore(node) { this.append(node); return node; }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  focus() {} blur() {} scrollIntoView() {} getBoundingClientRect() { return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 }; }
  click() { if (this.tag === 'input') { this.checked = true; this.dispatchEvent({ type: 'change', target: this }); } this.dispatchEvent({ type: 'click', target: this, preventDefault() {} }); }
  all(pred, out = []) { if (pred(this)) out.push(this); for (const c of this.children) if (c && c.tag) c.all(pred, out); return out; }
  matches(sel) { return sel.split(',').some((part) => { const s = part.trim(); if (s.startsWith('.')) return this.className.split(' ').includes(s.slice(1)); if (s.startsWith('[data-option=')) return this.dataset.option === s.slice(14, -2); if (s.startsWith('#')) return this.attrs.id === s.slice(1); return this.tag === s; }); }
  querySelector(sel) { return this.all((n) => n !== this && n.matches(sel))[0] || null; }
  querySelectorAll(sel) { return this.all((n) => n !== this && n.matches(sel)); }
  closest() { return null; }
  text() { return (this.textContent || '') + this.children.map((c) => (c.tag ? c.text() : c.text)).join(''); }
  get value() { return this.attrs.value ?? ''; }
  set value(v) { this.attrs.value = v; }
  get firstChild() { return this.children[0] || null; }
  get isConnected() { return true; }
  get ownerDocument() { return globalThis.document; }
  get innerText() { return this.text(); }
}
const svgNode = (tag) => new Node(tag);
globalThis.document = { createElement: (t) => new Node(t), createDocumentFragment: () => new Node('#fragment'), createElementNS: (_ns, t) => svgNode(t), createTextNode: (text) => ({ text }), dispatchEvent() {}, addEventListener() {},
  getSelection: () => ({ rangeCount: 0, removeAllRanges() {}, addRange() {} }), createRange: () => ({ selectNodeContents() {} }), body: new Node('body'), documentElement: new Node('html') };
globalThis.window = { matchMedia: () => ({ matches: true }), scrollY: 0, innerHeight: 800, scrollTo() {}, addEventListener() {}, removeEventListener() {} };
globalThis.location = { hash: '' };
globalThis.requestAnimationFrame = (fn) => fn();
globalThis.Event = class { constructor(type) { this.type = type; } };
export const text = (n) => (n.text !== undefined && !n.tag ? n.text : n.text());
