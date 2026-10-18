// Shared pieces for the scoring views: the rule list, the big number, the honest "practice" line and the reader's placement.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { RULES, formatDelta } from '../scoring/model.js';
import { canPublish, localScore, weekText } from '../scoring/board.js';

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

// The placement sentence, from a validated /scoring/me answer (scoring/board.js parseMe): the exact place, an exact "for
// comparison" place for a reader who is not on the list (it always says so), or no number at all: without a comparable
// published pool, or without an answer in this scope.
export function rankText(me) {
  if (me.moderation === 'hidden') return t('rank.hidden');
  const place = { place: me.rank.value, total: me.rank.participants };
  if (me.rank.kind === 'actual') return t('rank.actual', place);
  if (me.rank.kind === 'hypothetical') return t('rank.hypothetical', place);
  return me.scope_score === null ? t('rank.noScore') : t('rank.none');
}

// After publishing or unpublishing the view is drawn again; focus then returns to the new placement line, not the page title.
let placeFocus = false;
export const focusPlaceNext = () => { placeFocus = true; };
export function takePlaceFocus(node) {
  if (!placeFocus) return;
  placeFocus = false;
  const line = node.querySelector('.score-place__rank');
  if (line) line.focus();
}

// The reader's placement on one list. A signed-in account that is not on the list and not hidden gets "Publish my score"; a
// signed-out reader gets the practice note and "Sign in", never a publish button.
export function placementBlock({ me, signedIn, onPublish = null, onUnpublish = null, onShare = null, showScore = true }) {
  const score = showScore && me.scope_score ? h('p', { class: 'score-place__score' }, t('rank.score', { score: localScore(me.scope_score.score_display) })) : null;
  const status = h('p', { class: 'small muted', role: 'status' });
  const act = (fn, label) => {
    const button = h('button', { type: 'button', class: 'button button--secondary button--small' }, label);
    button.addEventListener('click', async () => {
      if (button.getAttribute('aria-disabled') === 'true') return;
      button.setAttribute('aria-disabled', 'true');
      const r = await fn();
      if (r && r.ok === false) { status.textContent = t('rank.publishFailed'); button.removeAttribute('aria-disabled'); }
    });
    return button;
  };
  const actions = [];
  if (canPublish(me, signedIn) && onPublish) actions.push(act(onPublish, t('rank.publish')));
  if (signedIn === true && me.published && me.moderation === 'ok' && onUnpublish) actions.push(act(onUnpublish, t('rank.unpublish')));
  // Sharing without a place is fine ("unranked"); only a missing score turns the button off, with its reason, still focusable.
  let shareNote = null;
  if (onShare) {
    const share = h('button', { type: 'button', class: 'button button--secondary button--small', 'aria-haspopup': 'dialog' }, t('share.open'));
    if (me.scope_score === null) {
      share.setAttribute('aria-disabled', 'true');
      share.setAttribute('aria-describedby', 'share-none');
      shareNote = h('p', { class: 'small muted', id: 'share-none' }, t('share.none'));
    }
    share.addEventListener('click', () => { if (share.getAttribute('aria-disabled') !== 'true') onShare(); });
    actions.push(share);
  }
  const anon = signedIn === true ? null : [h('p', { class: 'small muted' }, t('rank.practiceNote')), h('p', {}, h('a', { href: '#/signin' }, t('nav.signin')))];
  return h('div', { class: 'score-place' }, score, h('p', { class: 'score-place__rank', tabindex: '-1' }, rankText(me)), anon, actions.length ? h('div', { class: 'actions actions--row' }, actions) : null, shareNote, status);
}

// The week a list covers: dates in UTC, the reset in the reader's own time zone with its name. Null for "all time".
export function weekLine(window) {
  const w = weekText(window);
  return w ? h('p', { class: 'small muted' }, t('rank.week', { from: w.from, to: w.to, day: w.resetDay, time: w.resetTime, zone: w.zone })) : null;
}
