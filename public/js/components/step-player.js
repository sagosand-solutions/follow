// Step player: after answering, step through the code one line at a time (precomputed trace, nothing is executed here).
// The active line is the NEXT line to run; the variable table shows values before that line runs.

import { h } from '../dom.js';
import { t } from '../i18n.js';
import { stdoutAt, variableRows, clampStep } from '../trace.js';
import { setActiveLine } from './code-block.js';

function describe(step) {
  if (step.event === 'call') return step.function === '<module>' ? t('step.start') : t('step.call', { fn: step.function });
  if (step.event === 'return') return step.function === '<module>' ? t('step.end') : t('step.return', { fn: step.function, value: step.value });
  if (step.event === 'exception') return t('step.exception', { type: step.exception.type, message: step.exception.message });
  return t('step.line', { line: step.line });
}

const frameName = (step) => (step.function === '<module>' ? t('step.module') : step.function);

// traces: [{ id, label, trace, figure }] one per traced snippet; figure is its code block.
export function createStepPlayer(traces) {
  let which = 0;
  let index = 0;

  const title = h('h2', { id: 'sp-title', class: 'stage-tools__title' }, t('step.title'));
  const counter = h('span', { class: 'sp-counter' });
  const first = h('button', { type: 'button', class: 'button button--secondary button--small' }, t('step.first'));
  const back = h('button', { type: 'button', class: 'button button--secondary button--small' }, t('step.back'));
  const next = h('button', { type: 'button', class: 'button button--primary button--small' }, t('step.next'));
  const now = h('p', { class: 'sp-now' });
  const frame = h('p', { class: 'sp-frame muted small' });
  const caption = h('caption', {}, t('step.varsCaption'));
  const varBody = h('tbody');
  const vars = h('table', { class: 'sp-vars' }, caption, h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('step.name')), h('th', { scope: 'col' }, t('step.value')))), varBody);
  const empty = h('p', { class: 'muted small' }, t('step.noVars'));
  const out = h('pre', { class: 'sp-out', tabindex: '0', 'aria-label': t('step.output') });
  const outTitle = h('h3', { class: 'sp-h' }, t('step.output'));
  const live = h('p', { class: 'visually-hidden', role: 'status', 'aria-live': 'polite' });
  const picker = traces.length > 1 ? h('div', { class: 'sp-picker', role: 'group', 'aria-label': t('step.which') }) : null;

  function render(announce) {
    const cur = traces[which];
    const steps = cur.trace.steps;
    index = clampStep(index, steps.length);
    const step = steps[index];
    const prev = index > 0 ? steps[index - 1] : null;
    setActiveLine(cur.figure, step.line, announce);
    counter.textContent = t('step.counter', { n: index + 1, total: steps.length });
    first.disabled = index === 0;
    back.disabled = index === 0;
    next.disabled = index === steps.length - 1;
    now.textContent = describe(step);
    frame.textContent = t('step.frame', { fn: frameName(step), depth: step.depth });
    const rows = variableRows(prev, step);
    varBody.replaceChildren(
      ...rows.map((r) =>
        h('tr', { class: r.change === 'same' ? null : `sp-${r.change}` }, h('th', { scope: 'row' }, r.name), h('td', {}, h('code', {}, r.value), r.change === 'same' ? null : h('span', { class: 'sp-tag' }, t(`step.${r.change}`)))),
      ),
    );
    vars.hidden = rows.length === 0;
    empty.hidden = rows.length !== 0;
    out.textContent = stdoutAt(cur.trace, step) || t('step.noOutput');
    if (announce) {
      const changed = rows.filter((r) => r.change !== 'same').map((r) => `${r.name} = ${r.value}`);
      live.textContent = [`${t('step.counter', { n: index + 1, total: steps.length })}.`, describe(step), `${t('step.frame', { fn: frameName(step), depth: step.depth })}.`, changed.length ? `${t('step.changedList', { list: changed.join(', ') })}.` : ''].filter(Boolean).join(' ');
    }
  }

  const go = (to) => {
    index = to;
    render(true);
  };
  first.addEventListener('click', () => go(0));
  back.addEventListener('click', () => go(index - 1));
  next.addEventListener('click', () => go(index + 1));

  const root = h('section', { class: 'stage-tools sp', 'aria-labelledby': 'sp-title' }, title);
  root.addEventListener('keydown', (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey || e.target.closest('pre')) return;
    const last = traces[which].trace.steps.length - 1;
    let to = null;
    if (e.key === 'ArrowRight') to = index + 1;
    else if (e.key === 'ArrowLeft') to = index - 1;
    else if (e.key === 'Home') to = 0;
    else if (e.key === 'End') to = last;
    if (to === null) return;
    e.preventDefault();
    go(clampStep(to, last + 1));
  });

  if (picker) {
    traces.forEach((x, i) => {
      const b = h('button', { type: 'button', class: 'chip', 'aria-pressed': i === 0 ? 'true' : 'false' }, x.label);
      b.addEventListener('click', () => {
        setActiveLine(traces[which].figure, null);
        which = i;
        index = 0;
        picker.querySelectorAll('button').forEach((el, j) => el.setAttribute('aria-pressed', j === i ? 'true' : 'false'));
        render(true);
      });
      picker.append(b);
    });
    root.append(picker);
  }
  root.append(h('p', { class: 'muted small sp-hint' }, t('step.hint')), h('div', { class: 'sp-controls' }, first, back, next, counter), now, frame, vars, empty, outTitle, out, live);
  render(false);
  return root;
}
