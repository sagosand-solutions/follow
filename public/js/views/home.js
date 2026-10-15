import { h } from '../dom.js';
import { t } from '../i18n.js';
import { loadIndex, loadCourse, allQuestionIds } from '../data.js';
import { store } from '../progress.js';

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

  const actions = h('div', { class: 'actions actions--row' });
  const intro = [];
  if (answeredCount > 0) {
    intro.push(h('h2', { class: 'welcome' }, t('home.welcome')));
    intro.push(h('p', {}, nextId ? t('home.welcomeText', { count: answeredCount, total: ids.length }) : t('home.allDone')));
    if (nextId) actions.append(h('a', { class: 'button button--primary', href: `#/q/${nextId}` }, t('home.continue')));
    actions.append(h('a', { class: nextId ? 'button button--secondary' : 'button button--primary', href: '#/course' }, t('home.browse')));
  } else {
    if (index && index.onboarding_question) actions.append(h('a', { class: 'button button--primary', href: `#/q/${index.onboarding_question}` }, t('home.start')));
    actions.append(h('a', { class: index && index.onboarding_question ? 'button button--secondary' : 'button button--primary', href: '#/course' }, t('home.browse')));
  }

  const steps = h(
    'ol',
    { class: 'steps', 'aria-label': t('home.steps') },
    [1, 2, 3].map((n) => h('li', {}, h('span', { class: 'steps__n', 'aria-hidden': 'true' }, String(n)), h('div', {}, h('p', { class: 'steps__title' }, t(`home.step${n}.title`)), h('p', { class: 'steps__text' }, t(`home.step${n}.text`))))),
  );

  const node = h(
    'div',
    { class: 'stack stack--loose' },
    h('div', { class: 'hero' }, h('h1', {}, t('home.title')), h('p', { class: 'lead' }, t('home.lead')), ...intro, actions),
    steps,
    h('p', { class: 'banner' }, t('home.draft')),
    h('p', { class: 'muted' }, t('home.limits')),
  );
  return { title: t('site.tagline'), node };
}
