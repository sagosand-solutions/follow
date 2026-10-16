import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadIndex, loadCourse, loadQuestion, allQuestionIds } from '../data.js';
import { store } from '../progress.js';
import { renderCodeBlock } from '../components/code-block.js';
import { readingMap, summaryText } from '../components/map.js';
import { nextUp, homeShortcuts, homeHintKey } from '../course-nav.js';
import { reviewQueue } from '../review.js';
import { eyebrow, facts, courseCounts, pluralTemplate } from '../components/ui.js';
import { CONFIG } from '../config.js';

// The headline carries one highlighted phrase; both strings are translated whole.
function headline() {
  const full = t('home.title');
  const mark = t('home.titleMark');
  const at = mark ? full.indexOf(mark) : -1;
  if (at === -1) return [full];
  return [full.slice(0, at), h('span', { class: 'hl' }, mark), full.slice(at + mark.length)];
}

export async function homeView() {
  let index = null;
  let course = null;
  try {
    index = await loadIndex();
  } catch {
    /* start button is hidden when no onboarding question is known */
  }
  try {
    course = await loadCourse();
  } catch {
    /* course link still works and shows its own error */
  }
  const ids = course ? allQuestionIds(course) : [];
  const answeredCount = ids.filter((id) => store.latest(id)).length;
  // The same Continue rule everywhere: the home link must match the header and the course page.
  const records = store.answers();
  const target = course ? nextUp(course, records) : null;
  const reviewCount = course ? reviewQueue(course, records).length : 0;
  const nextId = target ? target.questionId : null;

  // The real onboarding code is the hero: the visitor sees what they will be reading.
  let stage = null;
  if (index && index.onboarding_question) {
    try {
      const q = await loadQuestion(index.onboarding_question);
      const s = q.snippets[0];
      stage = renderCodeBlock({ source: s.code, language: q.language || 'python', label: s.label || null, highlightLines: [] });
      stage.classList.add('hero-stage');
    } catch {
      stage = null;
    }
  }

  const actions = h('div', { class: 'actions actions--row' });
  const intro = [];
  const after = [];
  if (answeredCount > 0) {
    intro.push(h('p', { class: 'welcome' }, nextId ? t('home.welcomeText', { count: answeredCount, total: ids.length }) : t('home.allDone')));
    if (nextId) actions.append(h('a', { class: 'button button--primary button--block', href: `#/q/${nextId}` }, t('home.continue')));
    actions.append(h('a', { class: nextId ? 'button button--secondary button--block' : 'button button--primary button--block', href: '#/course' }, t('home.browse')));
    if (reviewCount > 0) actions.append(h('a', { class: 'button button--secondary button--block', href: '#/review' }, t('review.button', { count: reviewCount })));
  } else {
    intro.push(h('p', { class: 'lead' }, t('home.lead')));
    if (index && index.onboarding_question) actions.append(h('a', { class: 'button button--primary button--block', href: `#/q/${index.onboarding_question}` }, t('home.start')));
    actions.append(h('a', { class: index && index.onboarding_question ? 'button button--secondary button--block' : 'button button--primary button--block', href: '#/course' }, t('home.browse')));
    // Honest facts and shortcuts to existing lessons; never a promise the current storage mode cannot keep.
    after.push(h('p', { class: 'hero__facts' }, store.persistent ? t('home.facts.saved') : t('home.facts.memory')));
    const shortcuts = course ? homeShortcuts(course) : [];
    if (shortcuts.length) {
      after.push(
        h(
          'details',
          { class: 'hero__shortcuts' },
          h('summary', {}, t('home.shortcuts.lead')),
          h('div', { class: 'hero__shortcut-links' }, ...shortcuts.map((s) => h('a', { href: `#/lesson/${s.id}` }, t('home.shortcuts.start', { title: s.title })))),
        ),
      );
    }
  }

  // Fact pills: the numbers come from the course data; "No account needed" is true only while the account UI is off.
  const counts = courseCounts(course);
  const pills = facts(
    [
      { template: pluralTemplate(counts.questions, t('facts.question'), t('facts.questions')), bold: counts.questions || null },
      { template: pluralTemplate(counts.lessons, t('facts.lesson'), t('facts.lessons')), bold: counts.lessons || null },
      CONFIG.accountsUi ? null : { template: t('facts.noAccount') },
    ],
    t('facts.label'),
  );
  const hero = h('div', { class: 'hero' }, h('div', { class: 'hero__text' }, eyebrow(t('home.eyebrow')), h('h1', {}, ...headline()), ...intro, pills, actions, ...after, h('p', { class: 'hero__hint' }, t(homeHintKey({ hasProgress: answeredCount > 0, persistent: store.persistent })))), stage);

  const parts = [hero];
  if (course) {
    parts.push(
      h(
        'section',
        { class: 'map-card', 'aria-labelledby': 'map-title' },
        h('h2', { id: 'map-title' }, t('home.map.title')),
        h('p', { class: 'muted' }, answeredCount ? t('home.map.some', { summary: summaryText(ids) }) : t('home.map.empty')),
        readingMap(course, nextId),
      ),
    );
  }
  return { title: t('site.brand'), node: h('div', { class: 'home' }, ...parts) };
}
