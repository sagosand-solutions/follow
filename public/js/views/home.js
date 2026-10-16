import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadIndex, loadCourse, loadQuestion, allQuestionIds } from '../data.js';
import { store } from '../progress.js';
import { renderCodeBlock } from '../components/code-block.js';
import { readingMap, summaryText } from '../components/map.js';

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
  const nextId = ids.find((id) => !store.latest(id));

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
  if (answeredCount > 0) {
    intro.push(h('p', { class: 'welcome' }, nextId ? t('home.welcomeText', { count: answeredCount, total: ids.length }) : t('home.allDone')));
    if (nextId) actions.append(h('a', { class: 'button button--primary', href: `#/q/${nextId}` }, t('home.continue')));
    actions.append(h('a', { class: nextId ? 'button button--secondary' : 'button button--primary', href: '#/course' }, t('home.browse')));
  } else {
    intro.push(h('p', { class: 'lead' }, t('home.lead')));
    if (index && index.onboarding_question) actions.append(h('a', { class: 'button button--primary', href: `#/q/${index.onboarding_question}` }, t('home.start')));
    actions.append(h('a', { class: index && index.onboarding_question ? 'button button--secondary' : 'button button--primary', href: '#/course' }, t('home.browse')));
  }

  const hero = h('div', { class: 'hero' }, h('div', { class: 'hero__text' }, h('h1', {}, ...headline()), ...intro, actions, h('p', { class: 'hero__hint' }, t('home.limits'))), stage);

  const pillars = h(
    'ol',
    { class: 'pillars', 'aria-label': t('home.steps') },
    [1, 2, 3].map((n) => h('li', { class: 'pillar' }, h('span', { class: 'pillar__n', 'aria-hidden': 'true' }, `0${n}`), h('p', { class: 'pillar__title' }, t(`home.step${n}.title`)), h('p', { class: 'pillar__text' }, t(`home.step${n}.text`)))),
  );

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
  parts.push(pillars);
  return { title: t('site.brand'), node: h('div', { class: 'home' }, ...parts) };
}
