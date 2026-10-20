// Small, calm pieces for beginner courses: "You practised …" for a finished lesson, "New word" cards above a question's code,
// and the card at the end of the course. All text from the course files is shown as text (inline code at most), never as markup.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { renderInline } from '../text.js';
import { courseHref } from '../catalog.js';
import { loadCourse } from '../data.js';
import { forPeopleWhoCode } from './catalog-card.js';

// A whole translated sentence with one {slot} filled by nodes.
function sentence(template, slot, nodes) {
  const [before, after = ''] = template.split(`{${slot}}`);
  return [before, nodes, after].filter((x) => x !== '');
}

// "You practised reading what `print` shows." The fragment comes from the lesson (lesson-extras.js practiceSummary).
export function practisedLine(summary, className = 'practised', tag = 'p') {
  return summary ? h(tag, { class: className }, ...sentence(t('lesson.practised'), 'what', renderInline(summary))) : null;
}

// Which new words start open on this question: the reader's own last choice for the word, else open the first time the reader
// meets the word (on whichever question of the lesson that is, a direct link included) and folded after that, when it is a
// reminder. `memoryFor(intro)` gives that word's remembered choice ('open', 'closed' or 'seen'); without storage it remembers
// nothing, so every word opens every time.
export function wordsOpen(intros, memoryFor) {
  const open = new Map();
  for (const x of intros) {
    const memory = memoryFor(x);
    const choice = memory.get();
    if (choice === 'open' || choice === 'closed') open.set(x.id, choice === 'open');
    else if (choice === 'seen') open.set(x.id, false);
    else {
      open.set(x.id, true);
      memory.set('seen');
    }
  }
  return open;
}

const SIGN = { true: '\u2212', false: '+' };

// One new word: its term is a disclosure button (aria-expanded, aria-controls) with a visible + or − just before it, and the
// explanation and example below it, in line with the term. The button's name is the term; the state is announced from aria-expanded, so the sign itself is hidden.
function wordItem(x, isOpen, onToggle) {
  const panelId = `new-word-panel-${x.id}`;   // its own prefix: no word id can make it equal the label's id
  const sign = h('span', { class: 'new-word__sign', 'aria-hidden': 'true' }, SIGN[isOpen]);
  const panel = h('div', { class: 'new-word__text', id: panelId, hidden: !isOpen }, h('p', {}, renderInline(x.explanation)), h('p', { class: 'new-word__example' }, ...sentence(t('intro.example'), 'example', renderInline(x.example))));
  const button = h('button', { type: 'button', class: 'new-word__toggle', 'aria-expanded': String(isOpen), 'aria-controls': panelId }, sign, h('span', { class: 'new-word__term' }, renderInline(x.term)));
  button.addEventListener('click', () => {
    const now = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(now));
    if (now) panel.removeAttribute('hidden');
    else panel.setAttribute('hidden', '');
    sign.replaceChildren(document.createTextNode(SIGN[now]));
    onToggle(x.id, now);
  });
  return h('div', { class: 'new-word__item' }, h('h3', { class: 'new-word__head' }, button), panel);
}

// The question's introductions in one calm block above the code, one disclosure per word, so a folded word takes one line
// and the code stays as close to the top as it can on a phone. `open` maps word ids to their state (missing = open).
export function newWordCards(intros, { open = new Map(), onToggle = () => {} } = {}) {
  if (!intros.length) return [];
  const label = intros.length === 1 ? t('intro.label') : t('intro.labelMany');
  return [h('section', { class: 'new-word', 'aria-labelledby': 'new-word-label' },
    h('h2', { class: 'new-word__label', id: 'new-word-label' }, label),
    ...intros.map((x) => wordItem(x, open.get(x.id) !== false, onToggle)))];
}

// The path's "coming soon" steps by number (the same page text as the home path).
const soonTitle = (n) => (n === 1 ? t('path.soon.1.title') : n === 2 ? t('path.soon.2.title') : null);

// The end of a beginner course: what was practised, then the next step on the path (a link only when that course loads; a
// "coming soon" step is named without a link) and, for readers who want more now, the first course for people who already code.
export async function courseEndCard(course, entry, catalog) {
  if (!entry || entry.audience !== 'beginner') return null;
  const parts = [h('h2', { id: 'course-end-title' }, t('end.title', { course: course.title || entry.title })), h('p', {}, t('end.practised'))];
  const step = entry.pathStep;
  const next = step && catalog ? catalog.find((e) => e.pathStep === step + 1) : null;
  let nextLoads = false;
  if (next) {
    try { await loadCourse(next.id); nextLoads = true; } catch { nextLoads = false; }
  }
  if (next && nextLoads) parts.push(h('p', {}, h('a', { class: 'button button--primary', href: courseHref(next.id) }, t('end.next', { title: next.title }))));
  else if (step && soonTitle(step + 1)) parts.push(h('p', { class: 'muted' }, t('end.nextSoon', { title: soonTitle(step + 1) })));
  const more = forPeopleWhoCode(catalog)[0] || null;
  if (more) parts.push(h('p', {}, t('end.more'), ' ', h('a', { href: courseHref(more.id) }, more.title)));
  return h('section', { class: 'course-end', 'aria-labelledby': 'course-end-title' }, ...parts);
}
