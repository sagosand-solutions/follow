// Dev/test stand-in for the payment provider's checkout window. It loads nothing, makes no network call and keeps no id: it only
// shows a dialog and reports how it ended. A finished window never means paid; the payment page reads the server.
import { h } from '../dom.js';
import { t } from '../i18n.js';

// open({ title, price }) -> { result: Promise<'finished'|'closed'|null>, cancel() }. The first outcome wins; cancel() removes the dialog
// without an outcome (route change, error) and any later click or event is ignored.
export function openOverlayShim({ title = '', price = '' } = {}) {
  let settled = false;
  let resolve;
  const result = new Promise((r) => { resolve = r; });
  const close = h('button', { type: 'button', class: 'button button--secondary', autofocus: true }, t('checkout.overlay.close'));
  const complete = h('button', { type: 'button', class: 'button button--primary' }, t('checkout.overlay.complete'));
  const dialog = h('dialog', { class: 'dialog shim', 'aria-labelledby': 'shim-title', 'aria-describedby': 'shim-body' },
    h('p', { class: 'shim__label' }, t('checkout.overlay.label')),
    h('h2', { id: 'shim-title' }, [title, price].filter(Boolean).join(' · ')),
    h('p', { id: 'shim-body' }, t('checkout.overlay.body')),
    h('div', { class: 'actions actions--row' }, close, complete));
  const finish = (outcome) => {
    if (settled) return;
    settled = true;
    teardown();
    resolve(outcome);
  };
  const teardown = () => {
    if (dialog.open) dialog.close();
    dialog.remove();
  };
  close.addEventListener('click', () => finish('closed'));
  complete.addEventListener('click', () => finish('finished'));
  dialog.addEventListener('cancel', (event) => { event.preventDefault(); finish('closed'); });   // Esc is the same as Close
  document.body.append(dialog);
  dialog.showModal();
  close.focus();
  return {
    result,
    cancel() {
      if (settled) return;
      settled = true;
      teardown();
      resolve(null);          // no outcome: the caller ignores it
    },
  };
}
