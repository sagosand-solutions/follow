// Step player: after answering, step through the code one line at a time (precomputed trace, nothing is executed here).
// Each step shows a line that has JUST run, with the variables and output AFTER it (see buildMoments in trace.js).
// Call/return/yield/resume/unwind/exception steps keep their own recorded state and wording.

import { h } from '../dom.js';
import { t } from '../i18n.js';
import { buildMoments, momentState, clampStep } from '../trace.js';
import { FIRST_RUN_STEPS, FIRST_RUN_INTERVAL_MS } from '../moments.js';
import { setActiveLine } from './code-block.js';
import { narrate, tokensText } from '../narrate.js';

function describe(step, moment = null) {
  const main = step.function === '<module>';
  switch (step.event) {
    case 'call': return main ? t('step.start') : moment && moment.callerLine ? t('step.callFrom', { caller: moment.callerLine, fn: step.function, line: step.line }) : t('step.call', { fn: step.function, line: step.line });
    case 'resume': return moment && moment.callerLine ? t('step.resumeFrom', { caller: moment.callerLine, fn: step.function, line: step.line }) : t('step.resume', { fn: step.function, line: step.line });
    case 'yield': return t('step.yield', { fn: step.function, value: step.value, line: step.line });
    case 'return': return main ? t('step.end', { line: step.line }) : t('step.return', { fn: step.function, value: step.value, line: step.line });
    case 'unwind': return t(main ? 'step.unwindMain' : 'step.unwind', { fn: step.function, type: step.exception.type, line: step.line });
    case 'exception': return t('step.exception', { type: step.exception.type, message: step.exception.message, line: step.line });
    default: return t('step.ran', { line: step.line });
  }
}

const frameName = (step) => (step.function === '<module>' ? t('step.module') : step.function);

// traces: [{ id, label, trace, figure }] one per traced snippet; figure is its code block.
// options.firstRun: the first time in this browser, the first few steps play by themselves (never with reduced motion),
// then the player waits. options.onFirstRunDone() is called once the moment is over (skipped, interrupted or finished).
// options.narrate: beginner practice; each completed line gets one plain sentence built from the recording (narrate.js) in
// place of "Ran line N." Where the recording cannot prove the sentence, the usual one stays.
export function createStepPlayer(traces, { firstRun = false, onFirstRunDone = () => {}, narrate: narrated = false } = {}) {
  // each entry also carries `input`: { stdin, total } from bindCase
  const all = traces.map((x) => ({ ...x, moments: buildMoments(x.trace) }));
  let which = 0;
  let index = 0;

  const title = h('h2', { id: 'sp-title', class: 'stage-tools__title' }, t('step.title'));
  const counter = h('span', { class: 'sp-counter' });
  const first = h('button', { type: 'button', class: 'button button--secondary button--small' }, t('step.first'));
  const back = h('button', { type: 'button', class: 'button button--secondary button--small' }, t('step.back'));
  const next = h('button', { type: 'button', class: 'button button--primary button--small' }, t('step.next'));
  const now = h('p', { class: 'sp-now' });
  const frame = h('p', { class: 'sp-frame muted small' });
  const caption = h('caption', {});
  const varBody = h('tbody');
  const vars = h('table', { class: 'sp-vars' }, caption, h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('step.name')), h('th', { scope: 'col' }, t('step.value')))), varBody);
  const empty = h('p', { class: 'muted small' }, t('step.noVars'));
  // Format 0.3: the program's global data values are their own table, never merged into the locals table.
  const captionG = h('caption', {});
  const varBodyG = h('tbody');
  const varsG = h('table', { class: 'sp-vars sp-vars--globals' }, captionG, h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('step.name')), h('th', { scope: 'col' }, t('step.value')))), varBodyG);
  const globalsNote = h('p', { class: 'muted small sp-globals-note' });
  const omittedNote = h('p', { class: 'sp-omitted small', role: 'note' });
  const out = h('pre', { class: 'sp-out', tabindex: '0', 'aria-label': t('step.output') });
  const outTitle = h('h3', { class: 'sp-h' }, t('step.output'));
  const inputNote = h('p', { class: 'sp-input muted small' });
  const endNote = h('p', { class: 'sp-end', role: 'note' });
  const live = h('p', { class: 'visually-hidden', role: 'status', 'aria-live': 'polite' });
  const picker = traces.length > 1 ? h('div', { class: 'sp-picker', role: 'group', 'aria-label': t('step.which') }) : null;

  function render(announce) {
    const cur = all[which];
    const moments = cur.moments;
    index = clampStep(index, moments.length);
    const moment = moments[index];
    const step = moment.step;
    const state = momentState(cur.trace, moment);
    setActiveLine(cur.figure, step.line, announce, moment.kind === 'ran' && state.mode === 'after' ? 'ran' : 'event');
    const { stdin, total } = cur.input;
    inputNote.textContent = (stdin === '' ? t('step.noInput') : t('step.input', { stdin: stdin.replace(/\n/g, '↵') })) + (total > 1 ? ` ${t('step.ofCases', { n: total })}` : '');
    const last = index === moments.length - 1;
    if (last && cur.trace.truncated) endNote.textContent = t('step.truncated');
    else if (last && cur.trace.exception) endNote.textContent = t('step.endedWith', { type: cur.trace.exception.type, message: cur.trace.exception.message, line: cur.trace.exception.line });
    else if (last) endNote.textContent = t('step.endOfRecording');
    else endNote.textContent = '';
    endNote.hidden = !last;
    counter.textContent = t('step.counter', { n: index + 1, total: moments.length });
    first.disabled = index === 0;
    back.disabled = index === 0;
    next.disabled = index === moments.length - 1;
    const said = narrated ? narrate(cur.trace, moments, index) : null;
    if (said) now.replaceChildren(...said.map((tok) => (tok.code !== undefined ? h('code', {}, tok.code) : document.createTextNode(tok.text))));
    else now.textContent = moment.kind === 'ran' ? (state.mode === 'after' ? t('step.ran', { line: step.line }) : t('step.ranEnd', { line: step.line })) : moment.kind === 'interrupted' ? t('step.interrupted', { line: step.line, next: moment.after.line }) : describe(step, moment);
    frame.textContent = t('step.frame', { fn: frameName(step), depth: step.depth }) + (state.scope ? ` · ${t(`step.scope.${state.scope}`)}` : '');
    const rowNodes = (rows) => rows.map((r) => h('tr', { class: r.change === 'same' ? null : `sp-${r.change}` }, h('th', { scope: 'row' }, r.name), h('td', {}, h('code', {}, r.value), r.change === 'same' ? null : h('span', { class: 'sp-tag' }, t(`step.${r.change}`)))));
    const v3 = !!state.scope;
    const ending = state.mode === 'after' ? 'After' : state.mode === 'before' ? 'BeforeEnd' : 'Event';
    let localRows = state.rows;
    let globalRows = null;
    if (v3) {
      globalRows = state.globals;
      if (state.scope === 'module') {
        // At module level the variables ARE the globals: one table, titled as such.
        localRows = [];
      }
    }
    // Locals table (or, in 0.2 and at module level, the only table).
    const mainRows = v3 && state.scope === 'module' ? globalRows : localRows;
    caption.textContent = v3 ? (state.scope === 'module' ? t(`step.globals${ending}`) : t(`step.locals${ending}`, { fn: frameName(step) })) : t(state.mode === 'after' ? 'step.varsAfter' : state.mode === 'before' ? 'step.varsBeforeEnd' : 'step.varsCaptionEvent');
    varBody.replaceChildren(...rowNodes(mainRows));
    vars.hidden = mainRows.length === 0;
    empty.hidden = mainRows.length !== 0;
    // Globals table next to the locals of a function or class body.
    const showGlobals = v3 && state.scope !== 'module';
    captionG.textContent = t(`step.globals${ending}`);
    varBodyG.replaceChildren(...(showGlobals ? rowNodes(globalRows) : []));
    varsG.hidden = !showGlobals || globalRows.length === 0;
    // "No global changed" is said only when the recorded globals really did not change, never because the locals did not.
    globalsNote.textContent = showGlobals ? (globalRows.length === 0 ? (state.globalsOmitted === 0 ? t('step.noGlobals') : '') : state.globalsUnchanged ? t('step.globalsUnchanged') : '') : '';
    globalsNote.hidden = !globalsNote.textContent;
    omittedNote.textContent = v3 && state.omitted > 0 ? t('step.omitted', { n: state.omitted }) : '';
    omittedNote.hidden = !omittedNote.textContent;
    const rows = [...mainRows, ...(showGlobals ? globalRows : [])];
    const outNodes = [];
    if (!state.shown && !state.added) outNodes.push(t('step.noOutput'));
    if (state.shown) outNodes.push(state.shown);
    if (state.added) outNodes.push(h('span', { class: 'sp-newout' }, state.added));
    out.replaceChildren(...outNodes);
    if (announce) {
      const flagged = (list) => list.filter((r) => r.change !== 'same').map((r) => `${r.name} = ${r.value} (${t(`step.${r.change}`)})`);
      const changed = flagged(mainRows);
      const changedG = showGlobals ? flagged(globalRows) : [];
      live.textContent = [`${t('step.counter', { n: index + 1, total: moments.length })}.`, said ? tokensText(said) : `${now.textContent}`, changed.length ? `${t('step.changedList', { list: changed.join(', ') })}.` : '', changedG.length ? `${t('step.changedGlobals', { list: changedG.join(', ') })}.` : '', omittedNote.textContent, state.added ? `${t('step.printed', { text: state.added.replace(/\n/g, '↵') })}.` : '', last ? endNote.textContent : ''].filter(Boolean).join(' ');
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
    const last = all[which].moments.length - 1;
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
        setActiveLine(all[which].figure, null);
        which = i;
        index = 0;
        picker.querySelectorAll('button').forEach((el, j) => el.setAttribute('aria-pressed', j === i ? 'true' : 'false'));
        render(true);
      });
      picker.append(b);
    });
    root.append(picker);
  }
  root.append(h('p', { class: 'muted small sp-hint' }, t('step.hint')), inputNote, h('div', { class: 'sp-controls' }, first, back, next, counter), now, frame, endNote, vars, empty, varsG, globalsNote, omittedNote, outTitle, out, live);
  render(false);

  // First run: a quiet guided start. Any interaction, Skip, or the end of the three steps hands control to the reader.
  const calm = typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (firstRun && calm) onFirstRunDone();
  else if (firstRun) {
    let timer = null;
    const skip = h('button', { type: 'button', class: 'button button--secondary button--small' }, t('moment.firstRun.skip'));
    const note = h('p', { class: 'sp-firstrun', role: 'note' }, h('span', {}, t('moment.firstRun')), ' ', skip);
    const finish = () => {
      if (timer === null) return;
      clearInterval(timer);
      timer = null;
      note.remove();
      onFirstRunDone();
    };
    root.insertBefore(note, root.querySelector('.sp-controls'));
    live.textContent = t('moment.firstRun');
    let played = 0;
    timer = setInterval(() => {
      if (!root.isConnected) return finish();
      played += 1;
      if (played > FIRST_RUN_STEPS || index >= all[which].moments.length - 1) return finish();
      index += 1;
      render(false);
      if (played >= FIRST_RUN_STEPS) finish();
    }, FIRST_RUN_INTERVAL_MS);
    skip.addEventListener('click', finish);
    root.addEventListener('pointerdown', finish, { once: true });
    root.addEventListener('click', finish, { once: true });
    root.addEventListener('keydown', finish, { once: true });
  }
  return root;
}
