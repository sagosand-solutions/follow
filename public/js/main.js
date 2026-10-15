import { CONFIG } from './config.js';
import { initTheme, cycleTheme, readTheme } from './theme.js';
import { loadLocale, t } from './i18n.js';
import { h } from './dom.js';
import { registerRoute, startRouter, setNotFound } from './router.js';
import { homeView } from './views/home.js';
import { courseView } from './views/course.js';
import { lessonView } from './views/lesson.js';
import { questionView } from './views/question.js';
import { aboutView } from './views/about.js';
import { notFoundView } from './views/notfound.js';

initTheme();

async function boot() {
  const main = document.getElementById('main');
  try {
    await loadLocale(CONFIG.defaultLocale);
  } catch (err) {
    console.error(err);
    main.replaceChildren(h('p', { role: 'alert' }, 'The site could not load its text. Please reload the page.'));
    return;
  }

  document.querySelector('.skip-link').textContent = t('site.skip');
  const header = document.getElementById('site-header');
  const tagline = h('a', { class: 'brand', href: '#/' }, CONFIG.productName ? `${CONFIG.productName} · ${t('site.tagline')}` : t('site.tagline'));
  const themeButton = h('button', { type: 'button', class: 'theme-toggle' });
  const label = () => {
    const mode = readTheme();
    themeButton.textContent = t(`theme.${mode}`);
    themeButton.setAttribute('aria-label', t('theme.label', { mode: t(`theme.${mode}`) }));
  };
  themeButton.addEventListener('click', () => {
    cycleTheme();
    label();
  });
  label();
  header.replaceChildren(
    h('div', { class: 'shell' }, tagline, h('nav', { 'aria-label': t('nav.label'), class: 'nav' }, h('a', { href: '#/course' }, t('nav.course')), h('a', { href: '#/about' }, t('nav.about')), themeButton)),
  );
  document.getElementById('site-footer').replaceChildren(h('div', { class: 'shell' }, h('p', {}, t('footer.draft')), h('p', {}, t('footer.license'))));

  registerRoute('/', homeView);
  registerRoute('/course', courseView);
  registerRoute('/lesson/:id', lessonView);
  registerRoute('/q/:id', questionView);
  registerRoute('/about', aboutView);
  setNotFound(notFoundView);
  await startRouter(main, document.getElementById('live'));
}

boot();
