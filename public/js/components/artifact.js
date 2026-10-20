// A text artifact as the reader sees it: a labelled block of text. The text is set as one text node, never parsed: no markup,
// no markdown, no link, no image, no highlighter, nothing that can start a request. It is not code and it is not run.
import { h } from '../dom.js';
import { t } from '../i18n.js';

// Literal keys, so the key checks can see every one of them.
const KIND_NAMES = {
  text: 'artifact.kind.text', diff: 'artifact.kind.diff', 'http-transcript': 'artifact.kind.http-transcript',
  'test-report': 'artifact.kind.test-report', policy: 'artifact.kind.policy',
};

export function renderArtifact(artifact) {
  const label = artifact.label || t(KIND_NAMES[artifact.kind]);
  return h('figure', { class: 'artifact', 'data-kind': artifact.kind },
    h('figcaption', { class: 'artifact__caption' }, h('span', { class: 'artifact__label' }, label), h('span', { class: 'artifact__note' }, t('artifact.note'))),
    h('pre', { class: 'artifact__text', tabindex: '0', role: 'region', 'aria-label': label }, artifact.text));
}
