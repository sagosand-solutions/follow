import { h, icon } from '../dom.js';
import { t } from '../i18n.js';
import { renderText, renderInline } from '../text.js';
import { seededShuffle, questionSeed } from '../shuffle.js';
import { renderCodeBlock } from '../components/code-block.js';
import { store } from '../progress.js';
import { loadQuestion, findCourseOf, loadConcepts, loadTrace, findLessonOf } from '../data.js';
import { courseHref, reviewHref } from '../catalog.js';
import { CONFIG } from '../config.js';
import { navigate, refresh } from '../router.js';
import { reviewQueue, nextReview } from '../review.js';
import { currentAnswer } from '../answers.js';
import { errorNode } from './errors.js';
import { createStudySheet } from '../components/study.js';
import { eyebrow, noteBox, sourceCard, showInstructionNote, licenceInfo } from '../components/ui.js';
import { createStepPlayer } from '../components/step-player.js';
import { usableTraces } from '../trace.js';
import { changedToFit } from '../moments.js';
import { createSeenFlag } from '../seen.js';
import { premiumState } from '../account/premium.js';
import { checkQuestion } from '../question-check.js';

const UNKNOWN = '__unknown';
const LETTERS = 'ABCDEFGH';

function verdictOf(score) {
  if (score === 1) return { kind: 'correct', icon: 'correct', word: t('verdict.correct'), points: t('points.1') };
  if (score === 0.5) return { kind: 'partial', icon: 'partial', word: t('verdict.partial'), points: t('points.0.5') };
  return { kind: 'wrong', icon: 'wrong', word: t('verdict.wrong'), points: t('points.0') };
}

export async function questionView({ id, course: suppliedCourse = null, reviewMode = false }) {
  if (CONFIG.scoringUi) return (await import('../scoring/challenge.js')).challengeQuestionView({ id, course: suppliedCourse });
  let q;
  try {
    q = await loadQuestion(id);
  } catch (err) {
    if (err.status === 404) {
      // Not in the public course. When the account service is active, ask it: the question may be a premium one.
      const premium = await loadPremiumQuestion(id);
      if (!premium) return notFound();
      if (premium.state !== 'ok') return premiumNotice(premium.state, id);
      q = premium.question;
    } else {
      console.error(err);
      return { title: t('error.title'), node: errorNode(t('error.question')) };
    }
  }
  let course = suppliedCourse;
  let concepts = null;
  if (!course) {
    try {
      const entry = await findCourseOf({ questionId: id });
      course = entry ? entry.course : null;
    } catch {
      /* the question works without course context */
    }
  }
  try {
    concepts = await loadConcepts(course ? course.id : undefined);
  } catch {
    /* misconception titles fall back to ids */
  }
  const where = course ? findLessonOf(course, id) : null;
  const root = h('div', { class: 'question' });
  // Scoring helpers load only behind the flag (the default site never fetches them).
  const scoring = CONFIG.scoringUi ? { ...(await import('../components/score-ui.js')), ...(await import('../scoring/model.js')) } : null;
  const ctx = { q, where, concepts, root, course, reviewMode, scoring };
  const record = store.latest(id);
  const usable = currentAnswer(q, record) ? record : null;
  draw(ctx, usable, !!record && !usable);
  const pageTitle = where ? `${where.lesson.title} · ${t('q.position', { n: where.position + 1, total: where.lesson.questions.length })}` : titleOf(q);
  return { title: pageTitle, node: root };
}

// Premium questions come from the account service (never cached or stored here). Null when the service is not active.
async function loadPremiumQuestion(id) {
  if (!CONFIG.accountsUi) return null;
  const { getClient } = await import('../account/index.js');
  const client = getClient();
  if (client.mock !== false || typeof client.premiumQuestion !== 'function') return null;
  const answer = premiumState(await client.premiumQuestion(id));
  if (answer.state !== 'ok') return answer;
  // The same guard as for public questions: refuse data the view cannot show correctly.
  return checkQuestion(answer.question, id) ? { state: 'error' } : answer;
}

// Honest states for a premium question that cannot be shown: sign in, not available to you, no connection, or an error.
function premiumNotice(state, id) {
  const links = {
    signin: h('a', { class: 'button button--primary', href: '#/signin?next=premium' }, t('signin.title')),
    unavailable: h('a', { href: '#/premium' }, t('premium.q.see')),
    network: h('button', { type: 'button', class: 'button button--secondary', onclick: () => refresh() }, t('premium.q.retry')),
    error: h('a', { href: '#/course' }, t('notfound.course')),
  };
  const title = t(`premium.q.${state}.title`);
  return { title, node: h('div', { class: 'stack' }, h('h1', {}, title), h('p', {}, t(`premium.q.${state}.text`)), h('div', { class: 'actions' }, links[state]), h('p', {}, h('a', { href: '#/course' }, t('notfound.course')))) };
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
  if (!answered) ctx.fit = null;
  const chosenId = prev ? prev.option_id : null;

  const header = h('div', { class: 'question__head' });
  const meta = h('div', { class: 'question__meta' });
  if (ctx.reviewMode) {
    const queue = reviewQueue(ctx.course, store.answers());
    const index = queue.findIndex((entry) => entry.questionId === q.id);
    meta.append(h('p', { class: 'review__position muted' }, index >= 0 ? t('review.position', { n: index + 1, total: queue.length }) : t('review.title')));
  }
  if (where) {
    meta.append(eyebrow(`${where.lesson.title} · ${t('q.position', { n: where.position + 1, total: where.lesson.questions.length })}`));
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
  // Scoring (behind CONFIG.scoringUi): the first try counts, so the cost of each choice is said before it is made.
  const firstTry = !!ctx.scoring && !ctx.reviewMode && !store.latest(q.id);
  const unsureText = firstTry ? [t('q.unsure'), h('span', { class: 'option__sub' }, t('score.unsure.sub'))] : t('q.unsure');
  const unknownRow = mkOption(UNKNOWN, null, unsureText, 'option--unsure');
  if (answered && prev.outcome === 'unknown') unknownRow.querySelector('input').checked = true;
  group.append(unknownRow);

  const feedback = h('div', { class: 'feedback', id: 'feedback', role: 'region', 'aria-label': t('q.feedback') });
  const hint = h('p', { class: 'hint', id: 'hint' }, t('q.hint'));
  const check = h('button', { type: 'button', class: 'button button--primary button--block', 'aria-disabled': 'true', 'aria-describedby': 'hint' }, t('q.check'));
  const actions = h('div', { class: 'actions' }, check, hint, h('span', { class: 'hint hint--keys', 'aria-hidden': 'true' }, t('q.checkHint')));

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
    const before = store.latest(q.id);
    let record;
    if (input.value === UNKNOWN) record = store.recordUnknown({ questionId: q.id });
    else {
      const opt = q.options.find((o) => o.id === input.value);
      record = store.recordAnswer({ questionId: q.id, optionId: opt.id, score: opt.score });
    }
    ctx.fit = changedToFit(before, record);
    // Submitting does not change the hash, so tell the header to refresh now.
    document.dispatchEvent(new Event('progresschange'));
    draw(ctx, record);
    const heading = root.querySelector('.verdict__title');
    if (heading) {
      heading.focus({ preventScroll: true });
      // Bring the verdict into view (instant when the reader prefers reduced motion).
      const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      heading.scrollIntoView({ block: 'start', behavior: calm ? 'auto' : 'smooth' });
    }
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
  const scoreLine = ctx.scoring && !answered ? ctx.scoring.tryLine(firstTry) : null;
  const side = h('div', { class: 'q-side' }, stale ? h('p', { class: 'banner', role: 'status' }, t('q.stale')) : null, group, scoreLine, answered ? null : actions, feedback, sourceFor(q));
  const noteNode = showInstructionNote(answeredDistinct()) ? [noteBox({ title: t('q.note.title'), text: t('q.note.text') })] : [];
  if (firstTry && !answered && showInstructionNote(answeredDistinct())) noteNode.push(ctx.scoring.rulesNote());
  root.replaceChildren(h('div', { class: 'question__top stack' }, header, ctx.reviewMode ? h('p', { class: 'review__description muted' }, t('review.description')) : null, chips, title), ...noteNode, h('div', { class: 'q-grid' }, stageBox, side), ...(sheet ? [sheet.dialog] : []));

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
  const list = (await usableTraces(q, file)).map(({ index, trace, input }) => ({ id: q.snippets[index].id, input, label: q.snippets[index].label || t('step.snippetN', { n: index + 1 }), trace, figure: figures[index] }));
  if (!list.length || !box.isConnected) return;
  const seen = createSeenFlag('first-run');
  const player = createStepPlayer(list, { firstRun: !seen.isSeen(), onFirstRunDone: () => seen.markSeen() });
  box.append(player);
  // A pointer under the verdict, so the first-time reader finds the player (it lives next to the code).
  const after = box.closest('.question') ? box.closest('.question').querySelector('#feedback .q-next') : null;
  if (after) {
    const go = h('button', { type: 'button', class: 'button button--secondary button--block step-pointer' }, t('step.pointer'));
    go.addEventListener('click', () => {
      const title = player.querySelector('h2');
      if (title) {
        title.setAttribute('tabindex', '-1');
        title.focus({ preventScroll: true });
        player.scrollIntoView({ block: 'start', behavior: 'auto' });
      }
    });
    after.insertAdjacentElement('afterend', go);
  }
  // The player sits above the verdict on a phone, so it pushes the verdict down: if the verdict still has focus,
  // put it back in view (instantly, the reader has not moved).
  const verdict = box.ownerDocument.activeElement;
  if (verdict && verdict.classList && verdict.classList.contains('verdict__title')) verdict.scrollIntoView({ block: 'start', behavior: 'auto' });
}

function progressBar(ids, currentId) {
  const bar = h('div', { class: 'progress', 'aria-hidden': 'true' });
  for (const id of ids) {
    const done = !!store.latest(id);
    bar.append(h('span', { class: `progress__seg${id === currentId ? ' is-current' : done ? ' is-done' : ''}` }));
  }
  return bar;
}

// Distinct questions answered so far in this browser (the instruction note fades after the first few).
function answeredDistinct() {
  return new Set(store.answers().map((a) => a.question_id)).size;
}

function sourceFor(q) {
  const name = q.author && q.author.name ? q.author.name : null;
  if (!name) return null;
  const lic = licenceInfo(q.license);
  if (lic.link) return sourceCard({ title: t('q.source.title'), text: t('q.source.text', { author: name }), link: { href: CONFIG.licenseUrl, label: t(lic.key) } });
  // Not the open licence: say what the question's own field says, without a link to a licence it is not under.
  return sourceCard({ title: t('q.source.title'), text: `${t('q.source.byline', { author: name })} ${t(lic.key, lic.params)}` });
}

function navActions(ctx) {
  const { where, q } = ctx;
  const buttons = h('div', { class: 'actions actions--row q-next' });
  let next;
  if (ctx.reviewMode) {
    next = h('button', {
      type: 'button', class: 'button button--primary button--block', 'data-action': 'next',
      onclick: () => {
        const target = nextReview(reviewQueue(ctx.course, store.answers()), q.id, ctx.course);
        navigate(reviewHref(ctx.course.id, target ? target.questionId : null).slice(1));
      },
    }, t('q.next'));
  } else if (where && where.position + 1 < where.lesson.questions.length) {
    const nextId = where.lesson.questions[where.position + 1];
    next = h('button', { type: 'button', class: 'button button--primary button--block', 'data-action': 'next', onclick: () => navigate(`/q/${nextId}`) }, t('q.next'));
  } else if (where) {
    next = h('button', { type: 'button', class: 'button button--primary button--block', 'data-action': 'next', onclick: () => navigate(`/lesson/${where.lesson.id}`) }, t('q.finish'));
  } else {
    next = h('button', { type: 'button', class: 'button button--primary button--block', 'data-action': 'next', onclick: () => navigate(courseHref(ctx.course ? ctx.course.id : undefined).slice(1)) }, t('q.backToCourse'));
  }
  const again = h(
    'button',
    {
      type: 'button',
      class: 'button button--secondary button--block',
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
  buttons.append(next, again, h('span', { class: 'hint', 'aria-hidden': 'true' }, t('q.nextHint')));
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

  // Scoring: the first try's points in neutral ink next to the verdict; any later try is practice.
  if (ctx.scoring) frag.push(h('p', { class: 'score-delta small' }, prev.attempt === 1 ? t('score.delta', { delta: ctx.scoring.formatDelta(ctx.scoring.pointsFor(prev, ctx.scoring.RULES)) }) : t('score.line.practice')));
  // The way on sits right under the verdict, not after all the explanations.
  frag.push(navActions(ctx));
  // A quiet moment when a not-sure or wrong answer now fits (only after an earlier answer, never on a first answer).
  if (ctx.fit && !unknown && chosen && chosen.score === 1) {
    const earlier = ctx.fit.kind === 'wrong' ? q.options.find((o) => o.id === ctx.fit.optionId) : null;
    const body = ctx.fit.kind === 'unknown' ? t('moment.fit.unknown') : earlier && earlier.misconception ? t('moment.fit.wrong', { title: misconceptionTitle(ctx, earlier.misconception) }) : t('moment.fit.wrongPlain');
    frag.push(h('section', { class: 'moment moment--fit' }, h('h2', { class: 'moment__title' }, t('moment.fit.title')), h('p', {}, body)));
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

// With the scoring UI on, the first-try points line under the verdict is the only number, so the heading drops its own.
function verdictHeading(iconName, word, points) {
  return h('h2', { class: 'verdict__title', tabindex: '-1' }, icon(iconName), h('span', {}, word), CONFIG.scoringUi ? null : h('span', { class: 'verdict__points' }, ` · ${points}`));
}
