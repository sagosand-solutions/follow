import { h, icon } from '../dom.js';
import { t } from '../i18n.js';
import { renderInline, renderText } from '../text.js';
import { renderCodeBlock } from '../components/code-block.js';
import { courseHref } from '../catalog.js';
import { CONFIG } from '../config.js';
import { getClient } from '../account/index.js';

function message(title, text, action = null) {
  return { title, node: h('section', { class: 'stack', role: 'status' }, h('h1', {}, title), h('p', {}, text), action) };
}

function randomKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (!globalThis.crypto?.getRandomValues) throw new Error('Secure randomness is unavailable.');
  globalThis.crypto.getRandomValues(bytes);
  return [...bytes].map((x) => x.toString(16).padStart(2, '0')).join('');
}

function scoreLabel(attempt) {
  const labels = { correct: 'Correct', partial: 'Partly correct', not_sure: 'Not sure', wrong: 'Incorrect' };
  const units = { 2: '+1', 1: '+0.5', 0: '0', '-1': '−0.5' };
  const value = units[String(attempt?.score_units)];
  return `${labels[attempt?.outcome] || 'Answer recorded'}${value === undefined ? '' : ` · ${value}`}`;
}

export async function challengeQuestionView({ id, course = null }) {
  if (!CONFIG.accountsApi) return message('Scoring unavailable', 'The scoring service is not configured. Your local practice progress has not been changed.');
  let client;
  try { client = getClient(); } catch { return message('Scoring unavailable', 'The scoring service could not be initialized.'); }
  if (client.mock !== false || typeof client.scoringIssue !== 'function' || typeof client.scoringAnswer !== 'function') {
    return message('Scoring unavailable', 'A live scoring API is not configured. No local score has been submitted.');
  }
  const issued = await client.scoringIssue(id);
  if (!issued.ok) {
    if (issued.code === 'locked' && issued.attempt) return message('Already answered', scoreLabel(issued.attempt), h('a', { href: courseHref(course?.id).slice(1) }, 'Back to course'));
    if (issued.code === 'signed_out') return message('Sign in required', 'Sign in to open a server-issued scoring question.', h('a', { class: 'button button--primary', href: '#/signin?next=account' }, 'Sign in'));
    if (issued.code === 'unavailable') return message('Scoring is off', 'The scoring service is currently unavailable. No local score has been submitted.');
    return message('Question unavailable', 'This question cannot be issued for scoring. No local score has been submitted.', h('a', { href: courseHref(course?.id).slice(1) }, 'Back to course'));
  }

  const q = issued.question;
  if (typeof issued.issue_id !== 'string' || !['official', 'free'].includes(issued.family) || typeof issued.course_id !== 'string' || !q || q.id !== id || typeof q.prompt !== 'string' || !Array.isArray(q.options) || q.options.length < 5 || q.options.length > 8 || !Array.isArray(q.snippets) || q.snippets.some((snippet) => !snippet || typeof snippet.code !== 'string')) return message('Question unavailable', 'The scoring service returned an invalid question.');
  const root = h('div', { class: 'question' });
  const letters = 'ABCDEFGH';
  const options = h('div', { class: 'options', role: 'radiogroup', 'aria-label': 'Answer choices' });
  const radios = [];
  for (const [index, option] of q.options.entries()) {
    if (!option || typeof option.option_id !== 'string' || typeof option.text !== 'string' || index >= letters.length) return message('Question unavailable', 'The scoring service returned invalid answer choices.');
    const input = h('input', { type: 'radio', name: 'scoring-answer', value: option.option_id, id: `scoring-${index}`, class: 'option__input' });
    radios.push(input);
    options.append(h('label', { class: 'option', for: input.id }, input, h('span', { class: 'option__ring', 'aria-hidden': 'true' }), h('span', { class: 'option__letter' }, letters[index]), h('span', { class: 'option__text' }, renderInline(option.text))));
  }
  options.append(h('hr', { class: 'options__divider' }));
  const unsure = h('input', { type: 'radio', name: 'scoring-answer', value: '', id: 'scoring-unsure', class: 'option__input' });
  radios.push(unsure);
  options.append(h('label', { class: 'option option--unsure', for: unsure.id }, unsure, h('span', { class: 'option__ring', 'aria-hidden': 'true' }), h('span', { class: 'option__text' }, 'Not sure')));
  const status = h('p', { class: 'hint', role: 'status', hidden: true });
  const submit = h('button', { type: 'button', class: 'button button--primary button--block', disabled: true }, 'Submit first answer');
  options.addEventListener('change', () => { submit.disabled = !radios.some((radio) => radio.checked); });
  const actions = h('div', { class: 'actions' }, submit, status);
  const lettersById = new Map(q.options.map((option, i) => [option.option_id, letters[i]]));
  let key;
  try { key = randomKey(); } catch { return message('Scoring unavailable', 'A secure answer token could not be created. No answer was submitted.'); }
  submit.addEventListener('click', async () => {
    const selected = radios.find((radio) => radio.checked);
    if (!selected || submit.disabled) return;
    submit.disabled = true;
    radios.forEach((radio) => { radio.disabled = true; });
    status.hidden = false;
    status.textContent = 'Submitting your first answer…';
    const result = await client.scoringAnswer({ issue_id: issued.issue_id, outcome: selected.value ? 'answer' : 'not_sure', option_id: selected.value || null, idempotency_key: key });
    if (!result.ok) {
      if (result.code === 'locked' && result.attempt) {
        const heading = h('h2', { tabindex: '-1' }, 'Already answered');
        const locked = h('section', { class: 'verdict verdict--unsure', role: 'status' }, heading, h('p', {}, scoreLabel(result.attempt)));
        root.replaceChildren(root.firstChild, locked);
        heading.focus();
        return;
      }
      status.textContent = result.code === 'unavailable'
        ? 'Scoring is off or temporarily unavailable. Your answer was not confirmed.'
        : result.code === 'network_error' ? 'Connection lost. Retry safely; this submission uses the same idempotency key.'
          : result.code === 'expired' || result.code === 'stale' ? 'This question issue expired or changed. Reload the question before answering.'
            : result.message || 'The answer could not be confirmed.';
      status.hidden = false;
      if (['network_error', 'unavailable', 'rate_limited'].includes(result.code)) submit.disabled = false;
      return;
    }
    const attempt = result.attempt;
    const solution = result.solution;
    const parts = [h('h2', { class: 'verdict__title', tabindex: '-1' }, icon(attempt?.outcome === 'correct' ? 'correct' : attempt?.outcome === 'partial' ? 'partial' : attempt?.outcome === 'wrong' ? 'wrong' : 'unsure'), h('span', {}, scoreLabel(attempt)))];
    if (attempt?.outcome !== 'not_sure' && selected.value) {
      const text = q.options.find((option) => option.option_id === selected.value)?.text || '';
      parts.push(h('p', { class: 'fb__answer' }, h('span', { class: 'fb__letter' }, lettersById.get(selected.value) || ''), ' ', renderInline(text)));
      if (solution?.chosen_feedback) parts.push(h('div', { class: 'prose' }, renderText(solution.chosen_feedback)));
      if (solution?.partial_reason) parts.push(h('div', { class: 'partial' }, h('p', {}, 'Why this is partly right'), h('div', { class: 'prose' }, renderText(solution.partial_reason))));
    }
    if (solution?.correct_option_ids?.length) {
      for (const optionId of solution.correct_option_ids) {
        const option = q.options.find((item) => item.option_id === optionId);
        if (option) parts.push(h('section', { class: 'verdict verdict--correct verdict--quiet' }, h('h3', {}, 'Correct answer'), h('p', {}, renderInline(option.text))));
      }
    }
    if (solution?.explanation) parts.push(h('section', { class: 'explanation' }, h('h3', {}, 'Explanation'), h('div', { class: 'prose' }, renderText(solution.explanation))));
    const next = h('a', { class: 'button button--primary', href: courseHref(course?.id).slice(1) }, 'Back to course');
    root.replaceChildren(root.firstChild, h('section', { class: `verdict verdict--${attempt?.outcome || 'unsure'}` }, ...parts, next));
    root.querySelector('.verdict__title')?.focus();
  });

  const snippets = q.snippets.map((snippet) => renderCodeBlock({
    source: snippet.code,
    language: q.language || 'text',
    label: null,
    highlightLines: Array.isArray(snippet.highlight_lines) ? snippet.highlight_lines : [],
    strings: { copy: 'Copy', copied: 'Copied', copyFailed: 'Copy failed', ariaLabel: (n, language) => `${language} code, ${n} lines`, markedSummary: (lines) => `Highlighted lines: ${lines.join(', ')}`, lineMarked: (n) => `line ${n} highlighted`, lineUnmarked: (n) => `line ${n} not highlighted`, cursorLine: (n) => `line ${n}` },
  }));
  const title = h('h1', { class: 'question__title' }, renderInline(q.prompt));
  const familyLabel = issued.family === 'free' ? 'Free practice' : 'Official challenge';
  const first = h('div', { class: 'question__top stack' }, h('p', { class: 'chip' }, `${familyLabel} · ${issued.course_id}`), title);
  root.replaceChildren(first, h('div', { class: 'q-grid' }, h('div', { class: 'q-stage' }, ...snippets), h('div', { class: 'q-side' }, options, actions)));
  return { title: q.prompt.split('\n')[0], node: root };
}
