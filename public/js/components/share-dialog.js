// "Share my score": a local, opt-in text and image of the score the reader is looking at. Nothing is sent anywhere (no network,
// no tracking, no automatic posting). The text is built only from a whitelist of the validated /scoring/me answer: product, list,
// scope, window, score, kind of place, catalog and rules. Never questions, options, answers, ids, tokens or URL parameters. The
// public name is added only by an explicit, unchecked-by-default box that exists only for a published, visible row of yours.
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { CONFIG } from '../config.js';
import { localScore, weekText } from '../scoring/board.js';

export const canShare = (me) => !!me && me.scope_score !== null;
export const canShowAlias = (me, alias) => !!me && me.published === true && me.moderation === 'ok' && typeof alias === 'string' && alias.length > 0;

// The parts, in reading order. `alias` is used only when canShowAlias says so; anything else is dropped here as well.
export function shareParts({ me, listLabel, scopeLabel, alias = null }) {
  const week = weekText(me.window, { timeZone: 'UTC' });
  const place = { place: me.rank.value, total: me.rank.participants };
  const hypothetical = () => (me.rank.participants === 1 ? t('share.hypothetical.one', place) : t('share.hypothetical', place));
  const rank = me.rank.kind === 'actual' ? t('share.actual', place) : me.rank.kind === 'hypothetical' ? hypothetical() : t('share.unranked');
  return {
    product: CONFIG.productName || t('site.brand'),
    alias: canShowAlias(me, alias) ? alias : null,
    list: t('share.listName', { list: listLabel }),
    scope: scopeLabel,
    window: week ? t('share.week', { from: week.from }) : t('share.allTime'),
    points: t('share.points', { score: localScore(me.scope_score.score_display) }),
    rank,
    versions: t('share.versions', { catalog: me.catalog_version, rules: me.rules_version }),
  };
}

export const shareText = (parts) => [parts.product, parts.alias, parts.list, parts.scope, parts.window, parts.points, parts.rank, parts.versions].filter(Boolean).join(' · ');

// The image: text only, in the page's own theme colours, drawn locally. Null when the browser cannot draw.
export function drawShareImage(parts, doc = document) {
  const canvas = doc.createElement('canvas');
  canvas.width = 1200;
  canvas.height = 630;
  const g = canvas.getContext ? canvas.getContext('2d') : null;
  if (!g) return null;
  const css = getComputedStyle(doc.documentElement);
  const color = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const font = (doc.body ? getComputedStyle(doc.body).fontFamily : '') || 'sans-serif';
  g.fillStyle = color('--surface', '#ffffff');
  g.fillRect(0, 0, canvas.width, canvas.height);
  g.fillStyle = color('--accent-attention', '#f5b700');
  g.fillRect(80, 96, 64, 8);
  const line = (text, y, size, weight, ink) => { g.fillStyle = ink; g.font = `${weight} ${size}px ${font}`; g.fillText(text, 80, y, 1040); };
  const ink = color('--ink', '#111111');
  const muted = color('--muted', '#555555');
  line(parts.product, 160, 32, 700, muted);
  if (parts.alias) line(parts.alias, 220, 40, 700, ink);
  line(parts.points, 330, 96, 800, ink);
  line(parts.rank, 400, 36, 700, ink);
  line([parts.list, parts.scope, parts.window].join(' · '), 470, 28, 400, muted);
  line(parts.versions, 520, 28, 400, muted);
  return canvas;
}

// Open the dialog. It closes and empties its preview as soon as who is signed in changes.
// `gen` is the authentication generation `me` was read in. If who is signed in has changed since, nothing opens; every action
// checks it again.
export function openShareDialog({ me, listLabel, scopeLabel, alias = null, client, gen }) {
  if (!canShare(me)) return null;                       // the button guards this too; without a score there is nothing to share
  if (!Number.isInteger(gen) || client.authGeneration() !== gen) return null;
  const opener = document.activeElement;
  const withAlias = canShowAlias(me, alias);
  const preview = h('p', { class: 'share__preview', id: 'share-preview' });
  const status = h('p', { class: 'small muted', role: 'status' });
  const box = withAlias ? h('input', { type: 'checkbox', id: 'share-alias' }) : null;
  const current = () => shareParts({ me, listLabel, scopeLabel, alias: box && box.checked ? alias : null });
  const show = () => { preview.textContent = shareText(current()); };
  if (box) box.addEventListener('change', show);
  show();
  const stale = () => client.authGeneration() !== gen;
  let dialog = null;
  const close = () => { preview.textContent = ''; dialog.close(); };
  const copy = h('button', { type: 'button', class: 'button button--primary' }, t('share.copy'));
  copy.addEventListener('click', async () => {
    if (stale()) { close(); return; }
    const text = shareText(current());
    try {
      await navigator.clipboard.writeText(text);
      status.textContent = t('share.copied');
    } catch {
      const selection = document.getSelection();
      const range = document.createRange();
      range.selectNodeContents(preview);
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = t('share.copyFailed');
    }
  });
  const download = h('button', { type: 'button', class: 'button button--secondary' }, t('share.download'));
  download.addEventListener('click', () => {
    if (stale()) { close(); return; }
    const canvas = drawShareImage(current());
    if (!canvas) { status.textContent = t('share.imageFailed'); return; }
    canvas.toBlob((blob) => {
      if (!blob || stale()) return;
      const url = URL.createObjectURL(blob);
      const a = h('a', { href: url, download: 'score.png' });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, 'image/png');
  });
  const done = h('button', { type: 'button', class: 'button button--secondary', autofocus: true }, t('share.close'));
  done.addEventListener('click', close);
  dialog = h('dialog', { class: 'dialog share', 'aria-labelledby': 'share-title', 'aria-describedby': 'share-lead' },
    h('h2', { id: 'share-title' }, t('share.title')),
    h('p', { id: 'share-lead', class: 'small muted' }, t('share.lead')),
    preview,
    box ? h('label', { class: 'share__alias', for: 'share-alias' }, box, ' ', t('share.alias')) : null,
    h('div', { class: 'actions actions--row' }, copy, download, done),
    status);
  const off = client.onAuthChange(close);
  dialog.addEventListener('close', () => {
    off();
    preview.textContent = '';
    dialog.remove();
    if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
  });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}
