import { h } from '../dom.js';
import { t } from '../i18n.js';
import { errorNode } from './errors.js';

export async function notFoundView({ error } = {}) {
  if (error) return { title: t('error.title'), node: errorNode(t('error.generic')) };
  return {
    title: t('notfound.title'),
    node: h('div', { class: 'stack' }, h('h1', {}, t('notfound.title')), h('p', {}, t('notfound.text')), h('p', {}, h('a', { href: '#/' }, t('notfound.home')))),
  };
}
