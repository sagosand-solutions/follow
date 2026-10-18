// Tiny element builder. Strings become text nodes, never markup.
export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value === null || value === undefined) continue;
    if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key === 'class') node.className = value;
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(node, child);
    else if (typeof child === 'string' || typeof child === 'number') node.append(document.createTextNode(String(child)));
    else node.append(child);
  }
}

const SVG_NS = 'http://www.w3.org/2000/svg';
const ICONS = {
  correct: ['M4 10.5l4 4 8-9'],
  partial: ['M10 3a7 7 0 1 0 0 14z'],
  wrong: ['M5 5l10 10', 'M15 5L5 15'],
  unsure: ['M7.5 7.5a2.5 2.5 0 1 1 3.6 2.2c-.7.4-1.1.9-1.1 1.8', 'M10 15v.5'],
};

export function icon(name) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  svg.setAttribute('width', '20');
  svg.setAttribute('height', '20');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('class', `icon icon-${name}`);
  for (const d of ICONS[name] || []) {
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', d);
    path.setAttribute('fill', name === 'partial' ? 'currentColor' : 'none');
    path.setAttribute('stroke', 'currentColor');
    path.setAttribute('stroke-width', '2');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('stroke-linejoin', 'round');
    svg.append(path);
  }
  return svg;
}

// The answer moment: a larger ring with a check that draws itself (right) or a disc that fills to half (partly
// right). The CSS animates from an empty start to the final state, so with animations off the final icon shows.
export function momentIcon(kind, fresh) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 32 32');
  svg.setAttribute('width', '32');
  svg.setAttribute('height', '32');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('class', `moment-icon moment-icon--${kind}${fresh ? ' is-fresh' : ''}`);
  const ring = document.createElementNS(SVG_NS, 'circle');
  for (const [k, v] of Object.entries({ cx: 16, cy: 16, r: 13, class: 'moment-icon__ring' })) ring.setAttribute(k, String(v));
  svg.append(ring);
  if (kind === 'correct') {
    const tick = document.createElementNS(SVG_NS, 'path');
    for (const [k, v] of Object.entries({ d: 'M9.5 16.5l4.5 4.5 8.5-10', pathLength: 1, class: 'moment-icon__tick' })) tick.setAttribute(k, String(v));
    svg.append(tick);
  } else {
    // A circle of radius 5.5 with an 11-wide stroke paints a disc; its dash length is the filled share.
    const pie = document.createElementNS(SVG_NS, 'circle');
    for (const [k, v] of Object.entries({ cx: 16, cy: 16, r: 5.5, pathLength: 1, transform: 'rotate(-90 16 16)', class: 'moment-icon__pie' })) pie.setAttribute(k, String(v));
    svg.append(pie);
  }
  return svg;
}
