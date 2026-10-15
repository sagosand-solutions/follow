import { renderCodeBlock } from '../js/components/code-block.js';

const long = (n) => Array.from({ length: n }, (_, i) => `value_${i + 1} = ${i + 1} * 2  # line ${i + 1}`).join('\n') + '\n';

const samples = [
  ['Short Python snippet', { source: 'score = 7\nif score > 5:\n    print("high")\nif score > 3:\n    print("medium")\nelse:\n    print("low")\n' }],
  ['Highlighted lines 2 and 4', { source: 'score = 7\nif score > 5:\n    print("high")\nif score > 3:\n    print("medium")\n', highlightLines: [2, 4] }],
  ['Every token type', { source: '@decorator\ndef greet(name="world"):\n    """Say hello."""\n    n = 0x1F + 3.5e2 + 10  # numbers\n    s = f"hi {name}" + \'x\'\n    return len(s) if s and not None else True\n' }],
  ['40 lines (two-digit gutter)', { source: long(40) }],
  ['120 lines (three-digit gutter)', { source: long(120) }],
  ['A 200-character line (scrolls inside the block)', { source: 'x = "' + 'long text '.repeat(20) + '"\ny = 2\n', highlightLines: [2] }],
  ['Tab and non-ASCII text', { source: 'def f():\n\treturn "héllo wörld 日本語"  # kommentar åäö\n' }],
  ['Markup must stay literal', { source: '<script>alert(1)</script>\ns = "&amp; <b>bold</b>"\n' }],
  ['Empty source', { source: '' }],
  ['Only a newline', { source: '\n' }],
  ['Two lines, bleed variant', { source: 'a = 1\nb = 2\n', label: 'Variant B', extraClass: 'code--bleed' }],
  ['Non-Python language (plain)', { source: 'SELECT * FROM t;\n', language: 'sql' }],
];

const container = document.getElementById('blocks');
for (const [title, options] of samples) {
  const h = document.createElement('h2');
  h.textContent = title;
  container.append(h);
  const block = renderCodeBlock(options);
  if (options.extraClass) block.classList.add(options.extraClass);
  container.append(block);
}

const themes = ['auto', 'light', 'dark'];
let current = 0;
const button = document.getElementById('theme');
button.addEventListener('click', () => {
  current = (current + 1) % themes.length;
  const theme = themes[current];
  if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', theme);
  button.textContent = `Theme: ${theme}`;
});
