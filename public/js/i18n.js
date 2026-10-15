import { CONFIG } from './config.js';

let messages = {};
const warned = new Set();

export function setMessages(obj) {
  messages = obj || {};
}

export async function loadLocale(locale = CONFIG.defaultLocale) {
  const res = await fetch(`${CONFIG.i18nPath}${locale}.json`, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`i18n ${res.status}`);
  setMessages(await res.json());
}

export function t(key, params = {}) {
  const template = messages[key];
  if (typeof template !== 'string') {
    if (!warned.has(key)) {
      warned.add(key);
      console.warn(`Missing UI string: ${key}`);
    }
    return key;
  }
  return template.replace(/\{(\w+)\}/g, (m, name) => (name in params ? String(params[name]) : m));
}
