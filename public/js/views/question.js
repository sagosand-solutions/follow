import { h, icon } from '../dom.js';
import { t } from '../i18n.js';
import { renderText, renderInline } from '../text.js';
import { seededShuffle, questionSeed } from '../shuffle.js';
import { renderCodeBlock } from '../components/code-block.js';
import { store } from '../progress.js';
import { loadQuestion, loadCourse, loadConcepts, loadTrace, findLessonOf } from '../data.js';
import { CONFIG } from '../config.js';
import { navigate } from '../router.js';
import { currentAnswer } from '../answers.js';
import { errorNode } from './errors.js';
import { createStudySheet } from '../components/study.js';
import { createStepPlayer } from '../components/step-player.js';
import { pickTrace, sha256Hex, bindCase } from '../trace.js';

const UNKNOWN = '__unknown';
const LETTERS = 'ABCDEFGH';

function verdictOf(score) {
  if (score === 1) return { kind: 'correct', icon: 'correct', word: t('verdict.correct'), points: t('points.1') };
  if (score === 0.5) return { kind: 'partial', icon: 'partial', word: t('verdict.partial'), points: t('points.0.5') };
  return { kind: 'wrong', icon: 'wrong', word: t('verdict.wrong'), points: t('points.0') };
}

export async function questionView({ id }) {
  let q;
  try {
    q = await loadQuestion(id);
  } catch (err) {
    if (err.status === 404) return notFound();
    console.error(err);
    return { title: t('error.title'), node: errorNode(t('error.question')) };
  }
  let course = null;
  let concepts = null;
  try {
    course = await loadCourse();
  } catch {
    /* the question works without course context */
  }
  try {
    concepts = await loadConcepts();
  } catch {
    /* misconception titles fall back to ids */
  }
  const where = course ? findLessonOf(course, id) : null;
  const root = h('div', { class: 'question' });
  const ctx = { q, where, concepts, root };
  const record = store.latest(id);
  const usable = currentAnswer(q, record) ? record : null;
  draw(ctx, usable, !!record && !usable);
  const pageTitle = where ? `${where.lesson.title} · ${t('q.position', { n: where.position + 1, total: where.lesson.questions.length })}` : titleOf(q);
  return { title: pageTitle, node: root };
}

function titleOf(q) {
  const first = q.prompt.split('\n')[0].replace(/`/g, '');
  return first.length > 60 ? `${first.slice(0, 57)}…` : first;
}

function notFound() {
  return {
    title: t('notfound.title'),
    node: h('div', { class: 'stack' }, h('h1', {}, t('notfound.title')), h('p', {}, t('notfound.question')), h('p', {}, h('a', { href: '#/course' }, t('notfound.course')))),
  };
}

function misconceptionTitle(ctx, id) {
  const m = ctx.concepts && (ctx.concepts.misconceptions || []).find((x) => x.id === id);
  return m ? m.title : id;
}

// Draws either the unanswered state (prev === null) or the answered state for `prev`.
function codeStrings() {
  return {
    copy: t('code.copy'),
    copied: t('code.copied'),
    copyFailed: t('code.copyFailed'),
    ariaLabel: (n, language) => t(n === 1 ? 'code.aria.one' : 'code.aria.many', { lines: n, language }),
    markedSummary: (lines) => t(lines.length === 1 ? 'code.marked.one' : 'code.marked.many', { lines: lines.join(', ') }),
    lineMarked: (n) => t('code.lineMarked', { n }),
    lineUnmarked: (n) => t('code.lineUnmarked', { n }),
    cursorLine: (n, marked) => t(marked ? 'code.cursorMarked' : 'code.cursor', { n }),
  };
}

function draw(ctx, prev, stale = false) {
  const { q, where, root } = ctx;
  const seed = questionSeed(store.seed(), q.id);
  const order = seededShuffle(q.options, seed);
  const answered = !!prev;
  const chosenId = prev ? prev.option_id : null;

  const header = h('div', { class: 'question__head' });
  const meta = h('div', { class: 'question__meta' });
  if (where) {
    meta.append(h('p', { class: 'kicker' }, `${where.lesson.title} · ${t('q.position', { n: where.position + 1, total: where.lesson.questions.length })}`));
    meta.append(progressBar(where.lesson.questions, q.id));
  }
  header.append(meta);
  const chips = h('p', { class: 'chips' }, h('span', { class: 'chip' }, t(`type.${q.type}`)), q.level ? h('span', { class: 'chip' }, t(`level.${q.level}`)) : null);

  // Study layer: notes for the concepts this question practises, in a sheet that keeps the question in place.
  const known = ctx.concepts ? ctx.concepts.concepts || [] : [];
  const studyConcepts = (q.concepts || []).map((cid) => known.find((c) => c.id === cid) || { id: cid, title: cid });
  let sheet = null;
  if (CONFIG.studyLayer && studyConcepts.length) {
    sheet = createStudySheet(studyConcepts);
    const open = h('button', { type: 'button', class: 'button button--secondary button--small study-open', 'aria-haspopup': 'dialog' }, t('study.open'));
    open.addEventListener('click', () => sheet.open(studyConcepts[0].id, open));
    header.append(open);
  }

  const title = h('h1', { id: 'q-title', class: q.prompt.length > 90 ? 'question__title question__title--long' : 'question__title' }, renderInline(q.prompt));

  const snippets = q.snippets.map((s) =>
    renderCodeBlock({
      source: s.code,
      language: q.language || 'python',
      label: s.label || null,
      highlightLines: s.highlight_lines || [],
      strings: codeStrings(),
    }),
  );

  const group = h('div', { class: 'options', role: 'radiogroup', 'aria-labelledby': 'q-title' });
  const inputs = [];
  const mkOption = (value, letter, content, extraClass) => {
    const input = h('input', { type: 'radio', name: 'answer', value, id: `opt-${value}`, class: 'option__input' });
    if (answered && chosenId === value) input.checked = true;
    if (answered) input.disabled = true;
    inputs.push(input);
    return h('label', { class: `option ${extraClass || ''}`.trim(), for: `opt-${value}`, 'data-option': value }, input, h('span', { class: 'option__ring', 'aria-hidden': 'true' }), letter ? h('span', { class: 'option__letter' }, letter) : null, h('span', { class: 'option__text' }, content));
  };
  const letters = new Map();
  order.forEach((o, i) => {
    letters.set(o.id, LETTERS[i]);
    group.append(mkOption(o.id, LETTERS[i], renderInline(o.text)));
  });
  group.append(h('hr', { class: 'options__divider' }));
  const unknownRow = mkOption(UNKNOWN, null, t('q.unsure'), 'option--unsure');
  if (answered && prev.outcome === 'unknown') unknownRow.querySelector('input').checked = true;
  group.append(unknownRow);

  const feedback = h('div', { class: 'feedback', id: 'feedback', role: 'region', 'aria-label': t('q.feedback') });
  const hint = h('p', { class: 'hint', id: 'hint' }, t('q.hint'));
  const check = h('button', { type: 'button', class: 'button button--primary', 'aria-disabled': 'true', 'aria-describedby': 'hint' }, t('q.check'));
  const actions = h('div', { class: 'actions' }, check, hint);

  const selected = () => inputs.find((i) => i.checked);
  const sync = () => {
    const on = !!selected();
    check.setAttribute('aria-disabled', on ? 'false' : 'true');
    hint.hidden = on;
  };
  group.addEventListener('change', sync);

  const submit = () => {
    const input = selected();
    if (!input || answered) return;
    let record;
    if (input.value === UNKNOWN) record = store.recordUnknown({ questionId: q.id });
    else {
      const opt = q.options.find((o) => o.id === input.value);
      record = store.recordAnswer({ questionId: q.id, optionId: opt.id, score: opt.score });
    }
    draw(ctx, record);
    const heading = root.querySelector('.verdict__title');
    if (heading) heading.focus();
  };
  check.addEventListener('click', () => {
    if (check.getAttribute('aria-disabled') === 'true') {
      hint.hidden = false;
      hint.classList.add('hint--nudge');
      return;
    }
    submit();
  });

  const stageBox = h('div', { class: 'q-stage', role: snippets.length > 1 ? 'group' : null, 'aria-label': snippets.length > 1 ? t('q.code') : null }, ...snippets);
  const side = h('div', { class: 'q-side' }, stale ? h('p', { class: 'banner', role: 'status' }, t('q.stale')) : null, group, answered ? null : actions, feedback, answered ? navActions(ctx) : null, attribution(q));
  root.replaceChildren(h('div', { class: 'question__top stack' }, header, chips, title), h('div', { class: 'q-grid' }, stageBox, side), ...(sheet ? [sheet.dialog] : []));

  if (answered) mountStepper(q, stageBox, snippets);
  if (answered) {
    renderFeedback(ctx, feedback, prev, letters);
  }
  sync();
  if (answered) hint.hidden = true;

  root.onkeydown = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      if (answered) {
        const next = root.querySelector('[data-action="next"]');
        if (next) next.click();
      } else check.click();
    }
  };
}

// The trace shows the answer, so it exists only after the learner has answered. A trace is used only when it
// was recorded from code with the same SHA-256 as the snippet on screen; otherwise the player is simply not shown.
async function mountStepper(q, box, figures) {
  let file = null;
  try {
    file = await loadTrace(q.id);
  } catch (err) {
    console.error(err);
    return;
  }
  if (!file) return;
  const list = [];
  for (let i = 0; i < q.snippets.length; i += 1) {
    const s = q.snippets[i];
    const trace = pickTrace(file, s.id);
    if (!trace || !trace.steps.length) continue;
    const digest = await sha256Hex(s.code);
    if (digest !== trace.source_sha256 || file.question_id !== q.id || file.schema_version !== '0.1') continue;
    const input = bindCase(q, s.id, trace);
    if (!input) continue;
    list.push({ id: s.id, input, label: s.label || t('step.snippetN', { n: i + 1 }), trace, figure: figures[i] });
  }
  if (!list.length || !box.isConnected) return;
  box.append(createStepPlayer(list));
}

function progressBar(ids, currentId) {
  const bar = h('div', { class: 'progress', 'aria-hidden': 'true' });
  for (const id of ids) {
    const done = !!store.latest(id);
    bar.append(h('span', { class: `progress__seg${id === currentId ? ' is-current' : done ? ' is-done' : ''}` }));
  }
  return bar;
}

function attribution(q) {
  const name = q.author && q.author.name ? q.author.name : null;
  return name ? h('p', { class: 'attribution' }, `${t('q.attribution', { author: name })} `, h('a', { href: CONFIG.licenseUrl }, t('q.license'))) : null;
}

function navActions(ctx) {
  const { where, q } = ctx;
  const buttons = h('div', { class: 'actions' });
  let next;
  if (where && where.position + 1 < where.lesson.questions.length) {
    const nextId = where.lesson.questions[where.position + 1];
    next = h('button', { type: 'button', class: 'button button--primary', 'data-action': 'next', onclick: () => navigate(`/q/${nextId}`) }, t('q.next'));
  } else if (where) {
    next = h('button', { type: 'button', class: 'button button--primary', 'data-action': 'next', onclick: () => navigate(`/lesson/${where.lesson.id}`) }, t('q.finish'));
  } else {
    next = h('button', { type: 'button', class: 'button button--primary', 'data-action': 'next', onclick: () => navigate('/course') }, t('q.backToCourse'));
  }
  const again = h(
    'button',
    {
      type: 'button',
      class: 'button button--secondary',
      onclick: () => {
        draw(ctx, null);
        const title = ctx.root.querySelector('h1');
        if (title) {
          title.setAttribute('tabindex', '-1');
          title.focus();
        }
      },
    },
    t('q.retry'),
  );
  buttons.append(next, again);
  return buttons;
}

function optionBlock(ctx, opt, letter, withTitle) {
  const v = verdictOf(opt.score);
  const parts = [];
  if (withTitle) {
    parts.push(
      h('p', { class: 'fb__answer' }, h('span', { class: 'fb__letter' }, t('q.letter', { letter })), ' ', h('span', {}, renderInline(opt.text))),
    );
  }
  parts.push(h('div', { class: 'prose' }, renderText(opt.feedback)));
  if (opt.score !== 1 && opt.misconception) {
    parts.push(h('p', { class: 'chips' }, h('span', { class: 'chip chip--misc' }, t('q.misconception', { title: misconceptionTitle(ctx, opt.misconception) }))));
  }
  if (opt.score === 0.5 && opt.partial_reason) {
    parts.push(h('div', { class: 'partial' }, h('p', { class: 'partial__label' }, t('q.partialReason')), h('div', { class: 'prose' }, renderText(opt.partial_reason))));
  }
  return { v, parts };
}

function renderFeedback(ctx, container, prev, letters) {
  const { q } = ctx;
  const unknown = prev.outcome === 'unknown';
  const chosen = unknown ? null : q.options.find((o) => o.id === prev.option_id);
  const correct = q.options.find((o) => o.score === 1);
  const frag = [];

  if (unknown) {
    frag.push(
      h('section', { class: 'verdict verdict--unsure' }, verdictHeading('unsure', t('verdict.unsure'), t('points.none')), h('p', {}, t('q.noticeUnsure'))),
    );
  } else {
    const { v, parts } = optionBlock(ctx, chosen, letters.get(chosen.id), true);
    frag.push(h('section', { class: `verdict verdict--${v.kind}` }, verdictHeading(v.icon, v.word, v.points), ...parts));
  }

  if (unknown || chosen.id !== correct.id) {
    const { parts } = optionBlock(ctx, correct, letters.get(correct.id), true);
    frag.push(h('section', { class: 'verdict verdict--correct verdict--quiet' }, h('h2', { class: 'fb__heading' }, t('q.correctAnswer')), ...parts));
  }

  const others = q.options.filter((o) => o.id !== correct.id && (!chosen || o.id !== chosen.id));
  if (others.length) {
    const list = h('div', { class: 'others__list' });
    const details = others.map((o) => {
      const { v, parts } = optionBlock(ctx, o, letters.get(o.id), false);
      const d = h(
        'details',
        { class: `other other--${v.kind}` },
        h('summary', {}, h('span', { class: 'fb__letter' }, t('q.letter', { letter: letters.get(o.id) })), ' ', h('span', { class: 'other__text' }, renderInline(o.text)), ' ', h('span', { class: 'other__verdict' }, icon(v.icon), v.word)),
        h('div', { class: 'other__body' }, ...parts),
      );
      list.append(d);
      return d;
    });
    const toggle = h('button', { type: 'button', class: 'button button--secondary button--small' }, t('q.showAll'));
    toggle.addEventListener('click', () => {
      const open = details.some((d) => !d.open);
      details.forEach((d) => {
        d.open = open;
      });
      toggle.textContent = open ? t('q.hideAll') : t('q.showAll');
    });
    frag.push(h('section', { class: 'others' }, h('h2', { class: 'fb__heading' }, t('q.otherAnswers')), toggle, list));
  }

  if (q.explanation) {
    frag.push(h('section', { class: 'explanation' }, h('h2', { class: 'fb__heading' }, t('q.explanation')), h('div', { class: 'prose' }, renderText(q.explanation))));
  }
  container.replaceChildren(...frag);
}

function verdictHeading(iconName, word, points) {
  return h('h2', { class: 'verdict__title', tabindex: '-1' }, icon(iconName), h('span', {}, word), h('span', { class: 'verdict__points' }, ` · ${points}`));
}
