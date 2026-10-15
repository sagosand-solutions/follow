import { h } from '../dom.js';
import { t } from '../i18n.js';
import { refresh } from '../router.js';
import { clearDataCache } from '../data.js';

export function errorNode(message) {
  return h(
    'div',
    { class: 'stack' },
    h('h1', {}, t('error.title')),
    h('p', { role: 'alert' }, message),
    h('p', {}, h('button', { type: 'button', class: 'button button--primary', onclick: () => { clearDataCache(); refresh(); } }, t('error.retry'))),
  );
}
