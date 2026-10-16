// Small presentational components for the clean look: eyebrow label, fact pills, instruction note, source card.
// Text is always given as whole translated strings; a fact pill takes a template with ONE {b} placeholder that is
// rendered in bold, so translations may put the number anywhere.
import { h } from '../dom.js';

export function eyebrow(text) {
  return h('p', { class: 'eyebrow' }, text);
}

export function factPill(template, bold) {
  const at = template.indexOf('{b}');
  if (at === -1) return h('li', {}, template);
  return h('li', {}, template.slice(0, at) || null, h('strong', {}, String(bold)), template.slice(at + 3) || null);
}

// items: [{ template, bold }]. Pills with a missing value are left out, never shown as "undefined" or "0".
export function facts(items, label = null) {
  const ok = items.filter((i) => i && typeof i.template === 'string' && i.bold !== undefined && i.bold !== null && i.bold !== '');
  return h('ul', { class: 'facts', 'aria-label': label }, ok.map((i) => factPill(i.template, i.bold)));
}

export function noteBox({ title, text, icon = '”' }) {
  return h('div', { class: 'note' }, h('span', { class: 'note__icon', 'aria-hidden': 'true' }, icon), h('p', { class: 'note__title' }, title), text ? h('p', { class: 'note__text' }, text) : null);
}

export function sourceCard({ title, text }) {
  return h('section', { class: 'source' }, h('h2', { class: 'source__title' }, title), h('p', { class: 'source__text' }, text));
}

// Numbers for the pills, computed from the data (never typed in).
export function courseCounts(course) {
  const levels = (course && course.levels) || [];
  const lessons = levels.flatMap((l) => l.lessons || []);
  const questions = new Set(lessons.flatMap((l) => l.questions || []));
  return { levels: levels.length, lessons: lessons.length, questions: questions.size };
}
