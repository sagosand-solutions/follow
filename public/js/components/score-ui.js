// Shared pieces for the scoring views: the rule list, the big number, and the honest "practice" line.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { RULES, formatDelta } from '../scoring/model.js';

export function rulesList(rules = RULES) {
  const row = (value, key) => [h('dt', {}, formatDelta(value)), h('dd', {}, t(key))];
  return h('dl', { class: 'score-rules' }, ...row(rules.right, 'score.rule.right'), ...row(rules.partial, 'score.rule.partial'), ...row(rules.unsure, 'score.rule.unsure'), ...row(rules.wrong, 'score.rule.wrong'));
}

export function bigNumber(value, unit) {
  return h('p', { class: 'score-big' }, h('span', { class: 'score-big__value' }, value), ' ', h('span', { class: 'score-big__unit' }, unit));
}

// The note above the options on the first few scored questions.
export function rulesNote() {
  return h('section', { class: 'note score-note', 'aria-labelledby': 'score-note-title' }, h('p', { class: 'note__title', id: 'score-note-title' }, t('score.note.title')), rulesList(), h('p', { class: 'small muted' }, t('score.note.text')));
}

// Under the options: first try counts, any later try is practice.
export function tryLine(firstTry) {
  return h('p', { class: 'score-line small muted' }, firstTry ? t('score.line.first', { wrong: formatDelta(RULES.wrong) }) : t('score.line.practice'), ' ', h('a', { href: '#/leaderboard' }, t('score.line.how')));
}
