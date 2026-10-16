// Code block: line numbers (CSS counters), syntax highlighting, marked lines, keyboard line cursor, copy.
// Builds nodes with createElement/textContent only.

import { tokenizePython, splitLines } from '../highlight.js';

const DEFAULT_STRINGS = {
  copy: 'Copy',
  copied: 'Copied',
  copyFailed: 'Press Ctrl+C to copy',
  ariaLabel: (lines, language) => `Code, ${lines} ${lines === 1 ? 'line' : 'lines'}, ${language}`,
  markedSummary: (lines) => `${lines.length === 1 ? 'Line' : 'Lines'} ${lines.join(', ')} marked`,
  lineMarked: (n) => `Line ${n} marked`,
  lineUnmarked: (n) => `Line ${n} unmarked`,
  cursorLine: (n, marked) => `Line ${n}${marked ? ', marked' : ''}`,
};

const STATE = new WeakMap();

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v === null || v === undefined) continue;
    node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children) node.append(c);
  return node;
}

function tokenize(language, source) {
  if (language === 'python') return tokenizePython(source);
  return splitLines(source).map((line) => (line === '' ? [] : [{ type: 'plain', text: line }]));
}

function capitalise(s) {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

export function renderCodeBlock({ source, language = 'python', label = null, highlightLines = [], strings = {} }) {
  const s = { ...DEFAULT_STRINGS, ...strings };
  const lines = tokenize(language, source);
  const count = lines.length;
  const displayLabel = label || capitalise(language);

  const lineEls = lines.map((tokens, i) => {
    const line = el('span', { class: 'line', 'data-line': i + 1 });
    for (const tok of tokens) {
      if (tok.type === 'plain') line.append(document.createTextNode(tok.text));
      else line.append(el('span', { class: `tok tok-${tok.type}` }, tok.text));
    }
    return line;
  });

  const code = el('code', { class: 'code__code', translate: 'no', dir: 'ltr', 'data-digits': Math.min(String(count).length, 4) });
  lineEls.forEach((l) => code.append(l));

  const marks = el('span', { class: 'code__marks' });
  const copy = el('button', { type: 'button', class: 'code__copy' }, s.copy);
  const status = el('p', { class: 'code__status', role: 'status', 'aria-live': 'polite' });
  const scroll = el(
    'div',
    { class: 'code__scroll', role: 'group', tabindex: 0, 'aria-label': s.ariaLabel(count, displayLabel) },
    el('pre', { class: 'code__pre' }, code),
  );
  const figure = el(
    'figure',
    { class: 'code', 'data-language': language },
    el('figcaption', { class: 'code__caption' }, el('span', { class: 'code__label' }, displayLabel), marks, copy),
    scroll,
    status,
  );

  const state = { marked: new Set(), cursor: 0, lineEls, marks, status, s, count, timer: null };
  STATE.set(figure, state);
  applyMarks(state, figure, highlightLines);

  const say = (text) => {
    status.textContent = text;
  };

  // Mouse/touch: clicking a line toggles a personal mark, unless the user is selecting text.
  code.addEventListener('click', (e) => {
    const line = e.target.closest ? e.target.closest('.line') : null;
    if (!line) return;
    const sel = document.getSelection();
    if (sel && !sel.isCollapsed && sel.toString() !== '') return;
    toggle(Number(line.dataset.line));
  });

  function toggle(n) {
    if (state.marked.has(n)) state.marked.delete(n);
    else state.marked.add(n);
    applyMarks(state, figure, [...state.marked]);
    say(state.marked.has(n) ? s.lineMarked(n) : s.lineUnmarked(n));
  }

  function moveCursor(n) {
    if (state.cursor) lineEls[state.cursor - 1].classList.remove('is-cursor');
    state.cursor = n;
    if (n) {
      const target = lineEls[n - 1];
      target.classList.add('is-cursor');
      target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      say(s.cursorLine(n, state.marked.has(n)));
    }
  }

  scroll.addEventListener('keydown', (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey || count === 0) return;
    const c = state.cursor;
    let handled = true;
    if (e.key === 'ArrowDown') moveCursor(Math.min(c + 1, count));
    else if (e.key === 'ArrowUp' && c > 0) moveCursor(Math.max(c - 1, 1));
    else if (e.key === 'Home' && c > 0) moveCursor(1);
    else if (e.key === 'End' && c > 0) moveCursor(count);
    else if ((e.key === ' ' || e.key === 'Enter') && c > 0) toggle(c);
    else if (e.key === 'Escape' && c > 0) moveCursor(0);
    else handled = false;
    if (handled) e.preventDefault();
  });

  scroll.addEventListener('blur', () => {
    if (state.cursor) moveCursor(0);
  });

  copy.addEventListener('click', async () => {
    clearTimeout(state.timer);
    try {
      await navigator.clipboard.writeText(source);
      say(s.copied);
    } catch {
      const range = document.createRange();
      range.selectNodeContents(code);
      const sel = document.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      say(s.copyFailed);
    }
    state.timer = setTimeout(() => say(''), 2000);
  });

  // Copying a selection yields source text only: no line numbers, one newline per line.
  figure.addEventListener('copy', (e) => {
    const sel = document.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0 || !e.clipboardData) return;
    const range = sel.getRangeAt(0);
    if (!code.contains(range.commonAncestorContainer)) return;
    const parts = [];
    for (const lineEl of lineEls) {
      if (!range.intersectsNode(lineEl)) continue;
      const r = document.createRange();
      r.selectNodeContents(lineEl);
      if (range.compareBoundaryPoints(Range.START_TO_START, r) > 0) r.setStart(range.startContainer, range.startOffset);
      if (range.compareBoundaryPoints(Range.END_TO_END, r) < 0) r.setEnd(range.endContainer, range.endOffset);
      parts.push(r.toString());
    }
    e.clipboardData.setData('text/plain', parts.join('\n'));
    e.preventDefault();
  });

  return figure;
}

function applyMarks(state, figure, lines) {
  state.marked = new Set(lines.filter((n) => Number.isInteger(n) && n >= 1 && n <= state.count));
  state.lineEls.forEach((l, i) => l.classList.toggle('is-marked', state.marked.has(i + 1)));
  const sorted = [...state.marked].sort((a, b) => a - b);
  figure.dataset.marked = sorted.join(',');
  state.marks.textContent = sorted.length ? state.s.markedSummary(sorted) : '';
}

export function setMarks(figure, lines) {
  const state = STATE.get(figure);
  if (state) applyMarks(state, figure, lines);
}

// The line the step player will run next. Distinct from author marks and personal marks.
export function setActiveLine(figure, n, scroll = true) {
  const state = STATE.get(figure);
  if (!state) return;
  state.lineEls.forEach((l, i) => l.classList.toggle('is-active', i + 1 === n));
  if (scroll && n && state.lineEls[n - 1]) state.lineEls[n - 1].scrollIntoView({ block: 'nearest', inline: 'nearest' });
}
