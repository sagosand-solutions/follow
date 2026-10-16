import { CONFIG } from './config.js';
import { initTheme, cycleTheme, readTheme } from './theme.js';
import { loadLocale, t } from './i18n.js';
import { h } from './dom.js';
import { registerRoute, startRouter, setNotFound } from './router.js';
import { store } from './progress.js';
import { loadCourse } from './data.js';
import { nextUp, hasProgress } from './course-nav.js';
import { homeView } from './views/home.js';
import { courseView } from './views/course.js';
import { lessonView } from './views/lesson.js';
import { questionView } from './views/question.js';
import { reviewView } from './views/review.js';
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

  const skip = document.querySelector('.skip-link');
  skip.textContent = t('site.skip');
  // The link must not change the hash: the router would treat '#main' as a route.
  skip.addEventListener('click', (e) => {
    e.preventDefault();
    main.focus();
    main.scrollIntoView({ block: 'start' });
  });
  const header = document.getElementById('site-header');
  const tagline = h('a', { class: 'brand', href: '#/' }, h('span', { class: 'brand__mark', 'aria-hidden': 'true' }, h('span'), h('span'), h('span')), h('span', { class: 'brand__text' }, CONFIG.productName ? CONFIG.productName : t('site.brand')));
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
  // The Continue link lives in the header and must follow every route change and
  // every answer change without re-rendering the whole header.
  const continueSlot = h('span', { class: 'nav__continue' });
  const accountSlot = h('span', { class: 'nav__account' });
  header.replaceChildren(
    h('div', { class: 'shell' }, tagline, h('nav', { 'aria-label': t('nav.label'), class: 'nav' }, h('a', { href: '#/course' }, t('nav.course')), h('a', { href: '#/about' }, t('nav.about')), continueSlot, accountSlot, themeButton)),
  );
  const refreshContinue = async () => {
    const course = await loadCourse().catch(() => null);
    const target = course && hasProgress(course, store.answers()) ? nextUp(course, store.answers()) : null;
    continueSlot.replaceChildren();
    if (!target) return;
    const label = t('course.continue.text', { lesson: target.lessonTitle, n: target.position, total: target.total });
    continueSlot.append(h('a', { class: 'nav__link--continue', href: `#/q/${target.questionId}`, 'aria-label': label }, t('continue.title')));
  };
  window.addEventListener('hashchange', refreshContinue);
  document.addEventListener('progresschange', refreshContinue);
  refreshContinue();
  document.getElementById('site-footer').replaceChildren(h('div', { class: 'shell' }, h('p', {}, t('footer.draft')), h('p', {}, t('footer.license'))));

  registerRoute('/', homeView);
  registerRoute('/course', courseView);
  registerRoute('/lesson/:id', lessonView);
  registerRoute('/q/:id', questionView);
  registerRoute('/review', reviewView);
  registerRoute('/about', aboutView);
  if (CONFIG.accountsUi) await enableAccounts(accountSlot);
  setNotFound(notFoundView);
  await startRouter(main, document.getElementById('live'));
}

boot();

// Account screens: loaded only when the flag is on, so the free site never fetches this code.
async function enableAccounts(slot) {
  const [views, account] = await Promise.all([import('./views/account.js'), import('./account/index.js')]);
  registerRoute('/premium', views.premiumView);
  registerRoute('/signin', views.signinView);
  registerRoute('/checkout', views.checkoutView);
  registerRoute('/purchase', views.purchaseView);
  registerRoute('/receipt', views.receiptView);
  registerRoute('/account', views.accountView);
  const update = async () => {
    const me = await account.getClient().me();
    slot.replaceChildren(h('a', { href: me.signed_in ? '#/account' : '#/signin' }, t(me.signed_in ? 'nav.account' : 'nav.signin')));
  };
  window.addEventListener('hashchange', update);
  await update();
}
