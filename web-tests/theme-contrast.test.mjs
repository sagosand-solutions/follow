import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Contrast and completeness for every theme, straight from tokens.css (the same pairs as the Designer's contrast script).
const css = readFileSync(new URL('../public/css/tokens.css', import.meta.url), 'utf8');

// Collect the declarations of one rule by its selector text.
function declarations(selectorPattern, from = 0) {
  const re = new RegExp(`${selectorPattern}\\s*\\{([^}]*)\\}`, 'g');
  re.lastIndex = from;
  const m = re.exec(css);
  assert.ok(m, `rule not found: ${selectorPattern}`);
  return Object.fromEntries([...m[1].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((x) => [x[1], x[2].trim()]));
}

const root = declarations(':root');
const darkMedia = (() => {
  const at = css.indexOf('@media (prefers-color-scheme: dark)');
  return declarations(':root:not\\(\\[data-theme="light"\\]\\)', at);
})();
const THEME_NAMES = ['dark', 'paper', 'slate', 'contrast'];
const themes = { light: root, auto_dark: { ...root, ...darkMedia } };
// The manual Night block only overrides what differs from :root; the new themes redefine everything.
for (const n of THEME_NAMES) themes[n] = n === 'dark' ? { ...root, ...declarations(`:root\\[data-theme="${n}"\\]`) } : declarations(`:root\\[data-theme="${n}"\\]`);
const contrastMedia = (() => {
  const at = css.indexOf('@media (prefers-contrast: more)');
  return declarations(':root:not\\(\\[data-theme\\]\\)', at);
})();

const colourTokens = Object.keys(root).filter((k) => /^#|^rgba?\(|^transparent|^none$/.test(root[k]) || k.startsWith('--shadow') || k === '--scrim' || k === '--attention-wash');

function lum(hex) {
  const h = hex.replace('#', '');
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

const SYN = ['keyword', 'string', 'number', 'comment', 'function', 'builtin', 'decorator'];
const TEXT = [['ink', 'bg'], ['ink', 'surface'], ['muted', 'bg'], ['muted', 'surface'], ['link', 'bg'], ['link', 'surface'], ['accent-attention-text', 'bg'], ['accent-attention-text', 'surface'],
  ['outcome-right', 'outcome-right-bg'], ['outcome-partial', 'outcome-partial-bg'], ['outcome-wrong', 'outcome-wrong-bg'], ['on-accent-attention', 'accent-attention'], ['on-accent-attention', 'accent-attention-hover'], ['ink', 'surface-2'], ['muted', 'surface-2'],
  ['stage-ink', 'stage-bg'], ['stage-muted', 'stage-bg'], ['stage-linenum', 'stage-bg'], ['stage-link', 'stage-bg'], ['stage-ink', 'stage-mark'], ['stage-linenum', 'stage-mark'], ['stage-ink', 'stage-bg-raised'], ['stage-muted', 'stage-bg-raised'],
  ...SYN.map((n) => [`tok-${n}`, 'stage-bg']), ...SYN.map((n) => [`tok-${n}`, 'stage-mark'])];
const UI = [['line-strong', 'bg'], ['line-strong', 'surface'], ['focus', 'bg'], ['focus', 'surface'], ['accent-line', 'bg'], ['accent-line', 'surface'], ['stage-mark-edge', 'stage-bg'], ['stage-focus', 'stage-bg'],
  ['mark-right', 'bg'], ['mark-partial', 'bg'], ['mark-wrong', 'bg'], ['outcome-unknown', 'bg'], ['mark-right', 'surface'], ['mark-partial', 'surface'], ['mark-wrong', 'surface'], ['outcome-unknown', 'surface']];

for (const [name, t] of Object.entries(themes)) {
  test(`theme ${name}: every colour pair meets WCAG (AAA text for contrast)`, () => {
    const textMin = name === 'contrast' ? 7 : 4.5;
    const uiMin = name === 'contrast' ? 4.5 : 3;
    const failures = [];
    for (const [list, min] of [[TEXT, textMin], [UI, uiMin]]) {
      for (const [a, b] of list) {
        const r = ratio(t[`--${a}`], t[`--${b}`]);
        if (r < min) failures.push(`${a} on ${b}: ${r.toFixed(2)} < ${min}`);
      }
    }
    assert.deepEqual(failures, []);
  });
}

test('every new theme defines exactly the colour and effect tokens of :root', () => {
  for (const n of ['paper', 'slate', 'contrast']) {
    const missing = colourTokens.filter((k) => !(k in themes[n]));
    const extra = Object.keys(themes[n]).filter((k) => !(k in root));
    assert.deepEqual({ n, missing, extra }, { n, missing: [], extra: [] });
  }
});

test('Auto follows prefers-contrast: more with exactly the Contrast theme values', () => {
  assert.deepEqual(contrastMedia, themes.contrast);
});

test('the OS-driven Night values and the manual Night values are identical', () => {
  const manual = declarations(':root\\[data-theme="dark"\\]');
  assert.deepEqual(manual, darkMedia);
});
