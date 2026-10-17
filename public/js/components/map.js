// Reading map: one cell per question, coloured AND patterned by the stored outcome (never colour alone).
// Cells are decoration; the counts are given as text for assistive technology.

import { h } from '../dom.js';
import { t } from '../i18n.js';
import { store } from '../progress.js';
import { isCompactLevel } from '../course-nav.js';

export const OUTCOMES = ['full', 'partial', 'wrong', 'unsure', 'none'];

export function outcomeOf(record) {
  if (!record) return 'none';
  if (record.outcome === 'unknown') return 'unsure';
  if (record.score === 1) return 'full';
  if (record.score === 0.5) return 'partial';
  return 'wrong';
}

export function countOutcomes(ids) {
  const counts = { full: 0, partial: 0, wrong: 0, unsure: 0, none: 0 };
  for (const id of ids) counts[outcomeOf(store.latest(id))] += 1;
  return counts;
}

export function summaryText(ids) {
  return t('map.summary', countOutcomes(ids));
}

// `nextId` gets a ring: where to read next.
export function cells(ids, nextId = null) {
  const list = h('ul', { class: 'map__cells', 'aria-hidden': 'true' });
  for (const id of ids) {
    const kind = outcomeOf(store.latest(id));
    list.append(h('li', { class: `cell cell--${kind}${id === nextId ? ' is-next' : ''}` }));
  }
  return list;
}

export function legend() {
  return h(
    'ul',
    { class: 'legend', 'aria-label': t('map.legend') },
    OUTCOMES.map((kind) => h('li', {}, h('span', { class: `cell cell--${kind}`, 'aria-hidden': 'true' }), t(`map.${kind}`))),
  );
}

// course: parsed courses/python.json. Returns the full map (one row per level).
export function readingMap(course, nextId) {
  const rows = course.levels.map((level) => {
    const ids = level.lessons.flatMap((l) => l.questions);
    const label = h('p', { class: 'map__label' }, `${t(`level.${level.level}`)} · ${level.title}`);
    if (isCompactLevel(ids.length)) {
      const read = ids.filter((id) => store.latest(id)).length;
      return h(
        'div',
        { class: 'map__row map--compact' },
        label,
        h('div', {}, h('p', { class: 'map__count' }, t('course.lesson.progress', { answered: read, total: ids.length })), h('p', { class: 'visually-hidden' }, summaryText(ids))),
      );
    }
    // On a phone the row shows a count instead of every cell (the cells stay in the lesson cards below).
    const read = ids.filter((id) => store.latest(id)).length;
    return h('div', { class: 'map__row' }, label, h('div', {}, cells(ids, nextId), h('p', { class: 'map__count map__count--phone' }, t('course.lesson.progress', { answered: read, total: ids.length })), h('p', { class: 'visually-hidden' }, summaryText(ids))));
  });
  return h('div', { class: 'map' }, ...rows, legend());
}
