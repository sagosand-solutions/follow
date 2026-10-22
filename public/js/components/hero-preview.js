// The hero's preview of a question: a real question's code, its prompt and a few of its choices, drawn on the code stage.
// The whole box is one link with its own name; what it shows is a picture of the question, so it is hidden from assistive
// technology and nothing inside it can take focus or be answered.
import { h } from '../dom.js';
import { tokenizePython } from '../highlight.js';
import { renderInline } from '../text.js';

const SHOWN = 3;

// A choice's answer, without its explanation: its first sentence, without the closing full stop.
export const answerPart = (text) => {
  const str = String(text).trim();
  const at = str.search(/\.(\s|$)/);
  return at === -1 ? str : str.slice(0, at);
};

// Up to three choices in the file's order, skipping one whose answer reads the same as a choice already shown.
export function previewChoices(options, count = SHOWN) {
  const seen = new Set();
  const out = [];
  for (const o of options || []) {
    const answer = answerPart(o.text);
    if (seen.has(answer)) continue;
    seen.add(answer);
    out.push(answer);
    if (out.length === count) break;
  }
  return out;
}

function codeLines(source) {
  const lines = tokenizePython(source);
  return h('code', { class: 'code__code', translate: 'no', dir: 'ltr', 'data-digits': Math.min(String(lines.length).length, 4) }, lines.map((tokens, i) => h('span', { class: 'line', 'data-line': i + 1 }, tokens.map((tok) => (tok.type === 'plain' ? tok.text : h('span', { class: `tok tok-${tok.type}` }, tok.text))))));
}

// question: a parsed question file; href: where the box leads; name: the link's accessible name.
export function heroPreview(question, { href, name, label = 'Python' }) {
  const snippet = question.snippets[0];
  return h(
    'a',
    { class: 'code hero-stage hero-preview', href, 'aria-label': name },
    h(
      'div',
      { class: 'hero-preview__picture', 'aria-hidden': 'true' },
      h('div', { class: 'code__caption' }, h('span', { class: 'code__label' }, snippet.label || label)),
      h('div', { class: 'hero-preview__code' }, h('pre', { class: 'code__pre' }, codeLines(snippet.code))),
      h('div', { class: 'hero-preview__ask' }, h('div', { class: 'hero-preview__prompt' }, renderInline(question.prompt)), h('div', { class: 'hero-preview__choices' }, previewChoices(question.options).map((c) => h('div', { class: 'hero-preview__choice' }, renderInline(c))))),
    ),
  );
}
