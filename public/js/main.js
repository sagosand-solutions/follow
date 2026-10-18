import { CONFIG } from './config.js';
import { initTheme, setTheme, readTheme, THEMES } from './theme.js';
import { loadLocale, t } from './i18n.js';
import { h } from './dom.js';
import { registerRoute, startRouter, setNotFound } from './router.js';
import { store } from './progress.js';
import { loadCourses, loadCatalog } from './data.js';
import { continueAcross } from './catalog.js';
import { homeView } from './views/home.js';
import { courseView } from './views/course.js';
import { lessonView } from './views/lesson.js';
import { questionView } from './views/question.js';
import { reviewView } from './views/review.js';
import { aboutView } from './views/about.js';
import { notFoundView } from './views/notfound.js';
import { wireNavMenu } from './components/menu.js';

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
  const catalog = await loadCatalog();
  const manyCourses = !!catalog && catalog.length > 1;
  const header = document.getElementById('site-header');
  const tagline = h('a', { class: 'brand', href: '#/' }, h('span', { class: 'brand__mark', 'aria-hidden': 'true' }, h('span'), h('span'), h('span')), h('span', { class: 'brand__text' }, CONFIG.productName ? CONFIG.productName : t('site.brand')));
  // One native select: keyboard and screen-reader friendly, and it scales to any number of themes.
  const themeButton = h('select', { class: 'theme-select', 'aria-label': t('theme.label'), name: 'theme' }, THEMES.map((mode) => h('option', { value: mode }, t(`theme.name.${mode}`))));
  themeButton.value = readTheme();
  themeButton.addEventListener('change', () => {
    themeButton.value = setTheme(themeButton.value);
  });
  // The Continue link lives in the header and must follow every route change and
  // every answer change without re-rendering the whole header.
  const continueSlot = h('span', { class: 'nav__continue' });
  const accountSlot = h('span', { class: 'nav__account' });
  // On a phone the list folds behind a Menu button (a disclosure); on wider screens the button is hidden and nothing changes.
  const themeBox = h('div', { class: 'nav__theme' }, h('span', { class: 'nav__theme-label', 'aria-hidden': 'true' }, t('theme.label')), themeButton);
  const navList = h('div', { class: 'nav__list', id: 'nav-list' }, h('a', { href: manyCourses ? '#/' : '#/course' }, t(manyCourses ? 'nav.courses' : 'nav.course')), CONFIG.scoringUi ? h('a', { href: '#/profile' }, t('nav.profile')) : null, h('a', { href: '#/about' }, t('nav.about')), continueSlot, accountSlot, themeBox);
  const navToggle = h('button', { type: 'button', class: 'nav__toggle', 'aria-expanded': 'false', 'aria-controls': 'nav-list' }, t('nav.menu'));
  const nav = h('nav', { 'aria-label': t('nav.label'), class: 'nav' }, navToggle, navList);
  header.replaceChildren(h('div', { class: 'shell' }, tagline, nav));
  wireNavMenu({ nav, toggle: navToggle, list: navList, continueSlot, narrow: window.matchMedia('(max-width: 37.49rem)') });
  const refreshContinue = async () => {
    const target = continueAcross(await loadCourses().catch(() => []), store.answers());
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
  registerRoute('/course/:id', courseView);
  registerRoute('/course/:id/review', reviewView);
  registerRoute('/lesson/:id', lessonView);
  registerRoute('/q/:id', questionView);
  registerRoute('/review', reviewView);
  registerRoute('/about', aboutView);
  // Scoring views load only when the flag is on, so the default site never fetches their code.
  if (CONFIG.scoringUi) {
    registerRoute('/challenge/:id', async (params) => (await import('./scoring/challenge.js')).challengeQuestionView(params));
    registerRoute('/profile', async (params) => (await import('./views/profile.js')).profileView(params));
    registerRoute('/leaderboard', async (params) => (await import('./views/leaderboard.js')).leaderboardView(params));
  }
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
