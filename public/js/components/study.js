// Study layer: a sheet (native <dialog>) with notes for the concepts a question practises.
// Notes are plain-text JSON files (data/study/<concept>.json). Missing notes are said so, never invented.

import { h } from '../dom.js';
import { t } from '../i18n.js';
import { renderText, renderInline } from '../text.js';
import { renderCodeBlock } from './code-block.js';
import { loadStudy } from '../data.js';

function noteNode(note) {
  const sections = (note.sections || []).map((s) => {
    const parts = [h('h3', {}, s.heading), h('div', { class: 'prose' }, renderText(s.body || ''))];
    if (s.formula) parts.push(h('pre', { class: 'formula', tabindex: '0', 'aria-label': t('study.formula') }, s.formula));
    if (s.example) parts.push(renderCodeBlock({ source: s.example.code, language: s.example.language || 'python', label: s.example.caption || null }));
    if (s.deeper) {
      return h('details', { class: 'note-deeper' }, h('summary', {}, t('study.deeper', { heading: s.heading })), h('div', { class: 'note-deeper__body' }, ...parts.slice(1)));
    }
    return h('section', { class: 'note-section' }, ...parts);
  });
  return h(
    'article',
    { class: 'note' },
    note.status === 'draft' ? h('p', { class: 'draft-flag' }, t('study.draft')) : null,
    note.summary ? h('p', { class: 'note__summary' }, renderInline(note.summary)) : null,
    ...sections,
    note.author && note.author.name ? h('p', { class: 'attribution' }, t('study.by', { author: note.author.name }), ' ', h('span', {}, note.license || '')) : null,
  );
}

// concepts: [{id, title}]. Returns { open(conceptId?), buttonFor }.
export function createStudySheet(allConcepts) {
  // Concepts that have notes come first; the others stay reachable and say so honestly.
  let concepts = allConcepts;
  let sorted = false;
  async function sortByNotes() {
    if (sorted) return;
    sorted = true;
    const has = await Promise.all(allConcepts.map((c) => loadStudy(c.id).then(() => true, () => false)));
    concepts = [...allConcepts.filter((_, i) => has[i]), ...allConcepts.filter((_, i) => !has[i])];
  }
  const title = h('h2', { id: 'study-title', class: 'sheet__title', tabindex: '-1' });
  const body = h('div', { class: 'sheet__body' });
  const close = h('button', { type: 'button', class: 'button button--secondary button--small' }, t('study.close'));
  const dialog = h('dialog', { class: 'sheet', 'aria-labelledby': 'study-title' }, h('div', { class: 'sheet__head' }, title, close), body);
  let opener = null;
  let current = null;

  async function show(conceptId) {
    current = conceptId;
    const c = concepts.find((x) => x.id === conceptId);
    title.textContent = c ? c.title : conceptId;
    const tabs = h('div', { class: 'sheet__tabs', role: 'group', 'aria-label': t('study.concepts') });
    if (concepts.length > 1) {
      for (const x of concepts) {
        const b = h('button', { type: 'button', 'aria-current': x.id === conceptId ? 'true' : 'false' }, x.title);
        b.addEventListener('click', () => {
          show(x.id);
          title.focus();
        });
        tabs.append(b);
      }
    }
    body.replaceChildren(concepts.length > 1 ? tabs : null, h('p', { class: 'muted' }, t('study.loading')));
    let note = null;
    try {
      note = await loadStudy(conceptId);
    } catch (err) {
      if (err.status !== 404) console.error(err);
    }
    if (current !== conceptId) return;
    body.replaceChildren(concepts.length > 1 ? tabs : null, note ? noteNode(note) : h('p', {}, t('study.none')));
  }

  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) dialog.close();
  });
  dialog.addEventListener('close', () => {
    if (opener && opener.isConnected) opener.focus();
  });

  return {
    dialog,
    async open(conceptId, from) {
      opener = from || null;
      dialog.showModal();
      await sortByNotes();
      show(conceptId && allConcepts.length === 1 ? conceptId : concepts[0].id);
      title.focus();
    },
  };
}
