// Small presentational components for the clean look: eyebrow label, fact pills, instruction note, source card, footer.
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

// items: [{ template, bold }]. A pill whose template has {b} but no value is left out, never shown as "undefined" or "0".
export function facts(items, label = null) {
  const ok = items.filter((i) => i && typeof i.template === 'string' && (!i.template.includes('{b}') || (i.bold !== undefined && i.bold !== null && i.bold !== '')));
  return h('ul', { class: 'facts', 'aria-label': label }, ok.map((i) => factPill(i.template, i.bold)));
}

export function noteBox({ title, text, icon = '”' }) {
  return h('div', { class: 'note' }, h('span', { class: 'note__icon', 'aria-hidden': 'true' }, icon), h('p', { class: 'note__title' }, title), text ? h('p', { class: 'note__text' }, text) : null);
}

// The footer: one quiet line. The licences are spelled out in About (each question names its own), and the source code
// is one click away from every page.
export function siteFooter({ draft, licences, source, repoUrl }) {
  return h('div', { class: 'shell' }, h('p', { class: 'site-footer__line' }, draft, ' · ', h('a', { href: '#/about' }, licences), ' · ', h('a', { href: repoUrl }, source)));
}

// link: { href, label } is optional (e.g. the licence text).
export function sourceCard({ title, text, link = null }) {
  return h('section', { class: 'source' }, h('h2', { class: 'source__title visually-hidden' }, title), h('p', { class: 'source__text' }, text, link ? ' ' : null, link ? h('a', { href: link.href }, link.label) : null));
}

// The instruction note helps the first questions and is noise afterwards: shown until this many questions are answered.
export const NOTE_LIMIT = 3;
export function showInstructionNote(answeredCount, limit = NOTE_LIMIT) {
  return Number.isInteger(answeredCount) && answeredCount < limit;
}

// Pick the singular or plural template by the number ("1 question", "2 questions").
export function pluralTemplate(n, one, other) {
  return n === 1 ? one : other;
}

// Numbers for the pills, computed from the data (never typed in).
export function courseCounts(course) {
  const levels = (course && course.levels) || [];
  const lessons = levels.flatMap((l) => l.lessons || []);
  const questions = new Set(lessons.flatMap((l) => l.questions || []));
  return { levels: levels.length, lessons: lessons.length, questions: questions.size };
}

// What the source card says about a question's licence, from its own `license` field (never assumed).
// Returns { key, params, link } where link is true only for the open licence whose text we can link.
export function licenceInfo(license) {
  if (license === 'CC-BY-SA-4.0') return { key: 'q.license', params: {}, link: true };
  if (license === 'LicenseRef-Proprietary') return { key: 'licence.proprietary', params: {}, link: false };
  return { key: 'licence.other', params: { license: typeof license === 'string' && license ? license : 'unknown' }, link: false };
}
