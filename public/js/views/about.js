import { h } from '../dom.js';
import { t } from '../i18n.js';
import { CONFIG } from '../config.js';

export async function aboutView() {
  return {
    title: t('about.title'),
    node: h(
      'div',
      { class: 'stack stack--loose prose-page' },
      h('h1', {}, t('about.title')),
      h('p', {}, t('about.p1')),
      h('p', {}, t('about.p2')),
      h('section', { class: 'banner' }, h('h2', { class: 'banner__title' }, t('about.limits.title')), h('ul', {}, [1, 2, 3].map((n) => h('li', {}, t(`about.limits.${n}`))))),
      h('section', {}, h('h2', {}, t('about.privacy.title')), h('p', {}, t('about.privacy.text'))),
      h('section', {}, h('h2', {}, t('about.license.title')), h('p', {}, t('about.license.text')), h('p', {}, h('a', { href: CONFIG.licenseUrl }, t('q.license')), ' · ', h('a', { href: CONFIG.repoUrl }, t('about.source')))),
    ),
  };
}
